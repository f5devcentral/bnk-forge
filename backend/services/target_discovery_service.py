"""
Target Discovery Service — auto-discover LLM services on a K8s cluster
and create BenchmarkTarget + ProxyDeployment records.

Phase 5b: One-click cluster scan that finds:
  1. LLM inference services (vLLM, TGI, LiteLLM, Ollama, Triton, etc.)
  2. Auto-creates BenchmarkTarget records for each discovered LLM service
  3. Then runs proxy discovery on each new target (via ProxyDiscoveryService)

Detection heuristics (ordered by confidence):
  - Known labels/names: vllm, tgi, text-generation, litellm, ollama, triton
  - Known ports: 8000 (vLLM), 8080 (TGI/generic), 11434 (Ollama), 8001 (Triton)
  - Container image patterns: vllm/vllm, ghcr.io/huggingface/text-generation-inference
  - GPU resource requests (nvidia.com/gpu) on backing pods
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from kubernetes import client as k8s_client
from kubernetes.client.rest import ApiException
from sqlalchemy.orm import Session

from core.errors import NotFoundError
from models.benchmark import BenchmarkTarget
from services.kubernetes import KubernetesService
from services.proxy_deploy_service import _model_from_container, _served_model_from_container

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Heuristics for identifying LLM inference services
# ---------------------------------------------------------------------------

# Service/deployment name substrings that indicate an LLM inference server
LLM_NAME_PATTERNS = frozenset({
    "vllm", "tgi", "text-generation", "litellm", "ollama",
    "triton", "inference", "llm", "genai", "chat",
})

# Known inference server ports (service.spec.ports[].port)
LLM_PORTS = frozenset({8000, 8080, 11434, 8001, 5000, 3000})

# Container image substrings that indicate LLM inference
LLM_IMAGE_PATTERNS = frozenset({
    "vllm/vllm", "vllm-openai",
    "text-generation-inference", "huggingface/tgi",
    "litellm/litellm",
    "ollama/ollama",
    "nvcr.io/nvidia/tritonserver",
    "ghcr.io/huggingface",
})

# Namespaces to skip (infrastructure, not user workloads)
SKIP_NAMESPACES = frozenset({
    "kube-system", "kube-public", "kube-node-lease",
    "cert-manager", "ingress-nginx", "metallb-system",
    "monitoring", "prometheus", "grafana",
    "envoy-gateway-system", "haproxy-controller",
    "f5-bnk", "f5-operator", "f5-utils",
    "local-path-storage", "calico-system", "tigera-operator",
})

# Default LLM endpoint (OpenAI-compatible chat completions)
DEFAULT_LLM_ENDPOINT = "/v1/chat/completions"


# ---------------------------------------------------------------------------
# Discovery result
# ---------------------------------------------------------------------------

class DiscoveredLLMService:
    """An LLM service found during cluster scan."""

    __slots__ = (
        "service_name", "namespace", "base_url", "port",
        "confidence", "reason", "model_hint",
        "gpu_count", "image", "labels",
    )

    def __init__(
        self,
        service_name: str,
        namespace: str,
        base_url: str,
        port: int,
        *,
        confidence: str = "low",  # high | medium | low
        reason: str = "",
        model_hint: str | None = None,
        gpu_count: int = 0,
        image: str | None = None,
        labels: dict[str, str] | None = None,
    ):
        self.service_name = service_name
        self.namespace = namespace
        self.base_url = base_url
        self.port = port
        self.confidence = confidence
        self.reason = reason
        self.model_hint = model_hint
        self.gpu_count = gpu_count
        self.image = image
        self.labels = labels or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "service_name": self.service_name,
            "namespace": self.namespace,
            "base_url": self.base_url,
            "port": self.port,
            "confidence": self.confidence,
            "reason": self.reason,
            "model_hint": self.model_hint,
            "gpu_count": self.gpu_count,
            "image": self.image,
        }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class TargetDiscoveryService:
    """Scan a K8s cluster for LLM services and auto-create BenchmarkTargets."""

    def __init__(self, db: Session):
        self.db = db
        self.k8s = KubernetesService(db)

    def discover_targets(
        self,
        cluster_id: int,
        *,
        auto_create: bool = True,
        run_proxy_discovery: bool = True,
        selected_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        """Scan a cluster for LLM services and optionally create targets + discover proxies.

        Args:
            cluster_id: ID of the registered KubernetesCluster.
            auto_create: If True, create BenchmarkTarget records for discovered LLMs.
            run_proxy_discovery: If True, also run proxy discovery on each new target.
            selected_urls: If provided, only create targets for services matching these base_urls.

        Returns:
            Dict with discovered services, created targets, and proxy discovery results.
        """
        from models.kubernetes import KubernetesCluster

        cluster = self.db.query(KubernetesCluster).filter(
            KubernetesCluster.id == cluster_id,
        ).first()
        if not cluster:
            raise NotFoundError("kubernetes_cluster", cluster_id)

        api_client = self.k8s.load_kubeconfig(cluster)

        # 1. Scan for LLM services across all namespaces
        discovered = self._scan_all_namespaces(api_client)

        # 2. Enrich with GPU info from backing pods
        self._enrich_with_pod_info(api_client, discovered)

        # 3. De-duplicate (same service found via multiple heuristics)
        discovered = self._deduplicate(discovered)

        # Sort by confidence (high first), then by name
        confidence_order = {"high": 0, "medium": 1, "low": 2}
        discovered.sort(key=lambda d: (confidence_order.get(d.confidence, 3), d.namespace, d.service_name))

        result: dict[str, Any] = {
            "cluster_id": cluster_id,
            "cluster_name": cluster.name,
            "discovered_services": [d.to_dict() for d in discovered],
            "discovered_count": len(discovered),
            "created_targets": [],
            "proxy_results": [],
        }

        # 4. Auto-create BenchmarkTarget records (optionally filtered by selected URLs)
        if auto_create and discovered:
            to_create = discovered
            if selected_urls is not None:
                selected_set = set(selected_urls)
                to_create = [d for d in discovered if d.base_url in selected_set]

            created_targets = self._create_targets(cluster_id, cluster.name, to_create)
            result["created_targets"] = [
                {"id": t.id, "name": t.name, "llm_base_url": t.llm_base_url, "status": "new"}
                for t in created_targets
            ]

            # 5. Run proxy discovery on each new target
            if run_proxy_discovery and created_targets:
                from services.proxy_discovery_service import ProxyDiscoveryService
                proxy_svc = ProxyDiscoveryService(self.db)
                for target in created_targets:
                    try:
                        proxy_results = proxy_svc.discover_all(target, auto_create=True)
                        found_count = sum(1 for r in proxy_results if r["found"])
                        result["proxy_results"].append({
                            "target_id": target.id,
                            "target_name": target.name,
                            "discovered_proxies": found_count,
                            "total_scanned": len(proxy_results),
                        })
                    except Exception as exc:
                        logger.warning(
                            "Proxy discovery failed for target %s: %s",
                            target.name, exc,
                        )
                        result["proxy_results"].append({
                            "target_id": target.id,
                            "target_name": target.name,
                            "error": str(exc),
                        })

            # 6. Auto-generate BenchmarkConfigs for each target×proxy combination
            result["created_configs"] = self._create_configs(created_targets)

        return result

    # ------------------------------------------------------------------
    # Namespace scanner
    # ------------------------------------------------------------------

    def _scan_all_namespaces(
        self,
        api_client: k8s_client.ApiClient,
    ) -> list[DiscoveredLLMService]:
        """Scan all non-system namespaces for LLM services in parallel."""
        core = k8s_client.CoreV1Api(api_client)

        # Get all namespaces, filter out system ones
        try:
            ns_list = core.list_namespace(_request_timeout=10)
            namespaces = [
                ns.metadata.name for ns in ns_list.items
                if ns.metadata.name not in SKIP_NAMESPACES
            ]
        except ApiException as e:
            logger.error("Failed to list namespaces: %s", e.reason)
            return []

        if not namespaces:
            return []

        # Scan each namespace in parallel
        discovered: list[DiscoveredLLMService] = []

        with ThreadPoolExecutor(max_workers=min(len(namespaces), 10)) as executor:
            futures = {
                executor.submit(self._scan_namespace, api_client, ns): ns
                for ns in namespaces
            }
            for future in as_completed(futures):
                ns = futures[future]
                try:
                    results = future.result()
                    discovered.extend(results)
                except Exception as exc:
                    logger.debug("Failed to scan namespace %s: %s", ns, exc)

        return discovered

    def _model_from_service_pods(
        self,
        core: k8s_client.CoreV1Api,
        namespace: str,
        selector: dict | None,
    ) -> str | None:
        """Extract the request model name from the backing vLLM pods.

        Benchmark requests put this in the OpenAI ``model`` field, so it must be the
        vLLM ``--served-model-name`` when set; otherwise vLLM defaults the served name
        to ``--model`` (incl. the positional ``vllm serve <model>`` / shell-wrapped
        forms), so we fall back to that. Reuses the proxy-deploy parsers. Returns None
        if the Service has no selector, no pods, or no model arg.
        """
        if not selector:
            return None
        label_selector = ",".join(f"{k}={v}" for k, v in selector.items())
        try:
            pods = core.list_namespaced_pod(
                namespace=namespace, label_selector=label_selector, _request_timeout=10,
            )
        except ApiException:
            return None
        for pod in (pods.items or []):
            containers = (pod.spec.containers if pod.spec else None) or []
            # Prefer a vLLM-looking container, then scan the rest.
            ordered = sorted(
                containers,
                key=lambda c: 0 if "vllm" in ((c.name or "") + (c.image or "")).lower() else 1,
            )
            for c in ordered:
                spec = {"command": c.command or [], "args": c.args or []}
                # served-model-name wins (what requests use); else --model / positional.
                model = _served_model_from_container(spec) or _model_from_container(spec)
                if model:
                    return model
        return None

    def _scan_namespace(
        self,
        api_client: k8s_client.ApiClient,
        namespace: str,
    ) -> list[DiscoveredLLMService]:
        """Scan a single namespace for LLM inference services."""
        core = k8s_client.CoreV1Api(api_client)
        found: list[DiscoveredLLMService] = []

        try:
            svcs = core.list_namespaced_service(
                namespace=namespace, _request_timeout=10,
            )
        except ApiException:
            return []

        for svc in svcs.items:
            name = svc.metadata.name or ""
            labels = svc.metadata.labels or {}
            ports = svc.spec.ports or []

            if not ports:
                continue

            # Skip Kubernetes internal services
            if name in ("kubernetes", "kube-dns"):
                continue

            # Score the service
            confidence = "low"
            reasons: list[str] = []

            # Check 1: Name matches known LLM patterns (HIGH confidence)
            name_lower = name.lower()
            for pattern in LLM_NAME_PATTERNS:
                if pattern in name_lower:
                    confidence = "high"
                    reasons.append(f"name contains '{pattern}'")
                    break

            # Check 2: Labels match known LLM patterns
            all_labels_str = " ".join(f"{k}={v}" for k, v in labels.items()).lower()
            for pattern in LLM_NAME_PATTERNS:
                if pattern in all_labels_str:
                    if confidence != "high":
                        confidence = "high"
                    reasons.append(f"label contains '{pattern}'")
                    break

            # Check 3: Service port matches known inference ports
            svc_ports = {p.port for p in ports}
            known_ports = svc_ports & LLM_PORTS
            if known_ports:
                if confidence == "low":
                    confidence = "medium"
                reasons.append(f"port(s) {sorted(known_ports)} match inference defaults")

            # Only include if we have at least some signal
            if not reasons:
                continue

            port = ports[0].port
            base_url = f"http://{name}.{namespace}:{port}"

            # Model id for OpenAI requests: prefer the backing vLLM pod's
            # --served-model-name (falling back to --model / positional), e.g.
            # Qwen/Qwen3-32B. Service labels/annotations (e.g. model=qwen3-32b) are
            # short aliases the model server won't accept, so they're only a fallback.
            model_hint = (
                self._model_from_service_pods(core, namespace, svc.spec.selector)
                or labels.get("model")
                or labels.get("llm-model")
                or (svc.metadata.annotations or {}).get("model")
                or None
            )

            found.append(DiscoveredLLMService(
                service_name=name,
                namespace=namespace,
                base_url=base_url,
                port=port,
                confidence=confidence,
                reason="; ".join(reasons),
                model_hint=model_hint,
                labels=labels,
            ))

        return found

    # ------------------------------------------------------------------
    # Pod enrichment (GPU detection, image matching)
    # ------------------------------------------------------------------

    def _enrich_with_pod_info(
        self,
        api_client: k8s_client.ApiClient,
        discovered: list[DiscoveredLLMService],
    ) -> None:
        """Enrich discovered services with backing pod info (GPU, images)."""
        if not discovered:
            return

        core = k8s_client.CoreV1Api(api_client)

        # Group by namespace for efficient pod listing
        by_namespace: dict[str, list[DiscoveredLLMService]] = {}
        for svc in discovered:
            by_namespace.setdefault(svc.namespace, []).append(svc)

        for namespace, services in by_namespace.items():
            try:
                pods = core.list_namespaced_pod(
                    namespace=namespace, _request_timeout=10,
                )
            except ApiException:
                continue

            for svc in services:
                # Find pods that match this service (by label selector or name prefix)
                matching_pods = [
                    p for p in pods.items
                    if p.metadata.name.startswith(svc.service_name)
                    or _pod_matches_service(p, svc.service_name, svc.labels)
                ]

                for pod in matching_pods:
                    for container in (pod.spec.containers or []):
                        image = container.image or ""

                        # Check image against known LLM patterns
                        for img_pattern in LLM_IMAGE_PATTERNS:
                            if img_pattern in image.lower():
                                svc.image = image
                                if svc.confidence != "high":
                                    svc.confidence = "high"
                                    svc.reason += f"; image matches '{img_pattern}'"
                                break

                        # Check for GPU requests
                        resources = container.resources
                        if resources and resources.requests:
                            gpu = resources.requests.get("nvidia.com/gpu")
                            if gpu:
                                svc.gpu_count = max(svc.gpu_count, int(gpu))
                                if svc.confidence == "low":
                                    svc.confidence = "medium"
                                    svc.reason += "; pod requests nvidia.com/gpu"

                    # Only check first matching pod (optimization)
                    if matching_pods:
                        break

    # ------------------------------------------------------------------
    # De-duplication
    # ------------------------------------------------------------------

    def _deduplicate(
        self,
        discovered: list[DiscoveredLLMService],
    ) -> list[DiscoveredLLMService]:
        """Remove duplicates by (namespace, service_name), keeping highest confidence."""
        seen: dict[tuple[str, str], DiscoveredLLMService] = {}
        confidence_rank = {"high": 3, "medium": 2, "low": 1}

        for svc in discovered:
            key = (svc.namespace, svc.service_name)
            existing = seen.get(key)
            if existing is None or confidence_rank.get(svc.confidence, 0) > confidence_rank.get(existing.confidence, 0):
                seen[key] = svc

        return list(seen.values())

    # ------------------------------------------------------------------
    # Create BenchmarkTarget records
    # ------------------------------------------------------------------

    def _create_targets(
        self,
        cluster_id: int,
        cluster_name: str,
        discovered: list[DiscoveredLLMService],
    ) -> list[BenchmarkTarget]:
        """Create BenchmarkTarget records for discovered LLM services.

        Skips services that already have a matching target (same cluster + base_url).
        """
        from services.benchmark_target_service import BenchmarkTargetService
        target_svc = BenchmarkTargetService(self.db)

        # Get existing targets for this cluster to avoid duplicates
        existing_targets, _ = target_svc.list_targets(cluster_id=cluster_id)
        existing_urls = {t.llm_base_url for t in existing_targets}
        existing_names = {t.name for t in existing_targets}

        created: list[BenchmarkTarget] = []

        for svc in discovered:
            # Skip if a target already points to this URL
            if svc.base_url in existing_urls:
                logger.debug("Skipping %s — target already exists for %s", svc.service_name, svc.base_url)
                continue

            # Generate a unique name
            base_name = f"{svc.service_name}-{svc.namespace}"
            name = base_name
            counter = 1
            while name in existing_names:
                name = f"{base_name}-{counter}"
                counter += 1

            # Determine model name — use hint or default
            model = svc.model_hint or f"auto-discovered/{svc.service_name}"

            # Build description with discovery info
            desc_parts = ["Auto-discovered from cluster scan"]
            if svc.confidence:
                desc_parts.append(f"confidence={svc.confidence}")
            if svc.gpu_count > 0:
                desc_parts.append(f"GPUs={svc.gpu_count}")
            if svc.image:
                desc_parts.append(f"image={svc.image}")

            try:
                target = target_svc.create_target({
                    "name": name,
                    "description": "; ".join(desc_parts),
                    "cluster_id": cluster_id,
                    "llm_base_url": svc.base_url,
                    "llm_model": model,
                    "llm_namespace": svc.namespace,
                    "llm_endpoint": DEFAULT_LLM_ENDPOINT,
                    "proxy_namespace": "perf-proxies",
                    "tags": {
                        "auto_discovered": True,
                        "confidence": svc.confidence,
                        "reason": svc.reason,
                    },
                })
                created.append(target)
                existing_names.add(name)
                existing_urls.add(svc.base_url)
                logger.info(
                    "Auto-created target: name=%s url=%s confidence=%s",
                    name, svc.base_url, svc.confidence,
                )
            except Exception as exc:
                logger.warning("Failed to create target for %s: %s", svc.service_name, exc)

        return created

    # ------------------------------------------------------------------
    # Auto-generate BenchmarkConfigs
    # ------------------------------------------------------------------

    def _create_configs(
        self,
        targets: list[BenchmarkTarget],
    ) -> list[dict[str, Any]]:
        """Auto-generate BenchmarkConfig presets for each target×proxy combination.

        Creates a ready-to-run config JSON that maps directly to aiperf CLI
        options (``aiperf profile --<option>``) so the config can be used both
        by the Forge agent and for manual CLI runs.

        See: https://github.com/ai-dynamo/aiperf/blob/main/docs/cli-options.md
        """
        from models.benchmark import BenchmarkConfig, ProxyDeployment
        from models.enums import ProxyDeploymentStatus

        existing_names = {
            c.name for c in self.db.query(BenchmarkConfig.name).all()
        }

        created: list[dict[str, Any]] = []

        for target in targets:
            proxies = (
                self.db.query(ProxyDeployment)
                .filter(
                    ProxyDeployment.target_id == target.id,
                    ProxyDeployment.status == ProxyDeploymentStatus.DISCOVERED,
                )
                .all()
            )

            for proxy in proxies:
                config_name = f"{target.name}-{proxy.proxy_type}"
                if config_name in existing_names:
                    continue

                base_url = proxy.proxy_url or target.llm_base_url

                # Config keys map 1:1 to aiperf CLI flags:
                #   url             → --url
                #   model           → --model
                #   endpoint_type   → --endpoint-type
                #   endpoint        → --endpoint (custom endpoint path)
                #   streaming       → --streaming
                #   request_count   → --request-count
                #   concurrency     → --concurrency
                #   request_timeout_seconds → --request-timeout-seconds
                #   warmup_request_count    → --warmup-request-count
                #   isl             → --isl (input sequence length mean)
                #   osl             → --osl (output sequence length mean)
                #
                # Forge-only metadata (not aiperf flags):
                #   _proxy_type     → proxy type label (for Forge UI)
                #   _target_name    → target name label (for Forge UI)
                config_json = {
                    # aiperf CLI options
                    "url": base_url,
                    "model": target.llm_model,
                    "endpoint_type": "chat",
                    "endpoint": target.llm_endpoint or "/v1/chat/completions",
                    "streaming": True,
                    "request_count": 100,
                    "concurrency": 10,
                    "warmup_request_count": 5,
                    "request_timeout_seconds": 600,
                    "isl": 550,
                    "osl": 150,
                    # Forge metadata (prefixed with _ to distinguish from CLI args)
                    "_proxy_type": proxy.proxy_type,
                    "_target_name": target.name,
                }

                config = BenchmarkConfig(
                    name=config_name,
                    description=f"Auto-generated from cluster scan: {target.name} via {proxy.proxy_type}",
                    tool="aiperf",
                    config_json=config_json,
                )
                self.db.add(config)
                existing_names.add(config_name)
                created.append({
                    "name": config_name,
                    "target_name": target.name,
                    "proxy_type": proxy.proxy_type,
                })
                logger.info("Auto-created config: %s", config_name)

        self.db.flush()
        return created


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _pod_matches_service(pod, service_name: str, svc_labels: dict[str, str]) -> bool:
    """Check if a pod likely belongs to a service (by label overlap or name prefix)."""
    pod_labels = pod.metadata.labels or {}

    # Match by common label keys
    for key in ("app", "app.kubernetes.io/name", "app.kubernetes.io/instance"):
        svc_val = svc_labels.get(key, "").lower()
        pod_val = pod_labels.get(key, "").lower()
        if svc_val and pod_val and svc_val == pod_val:
            return True

    # Match by pod name containing service name
    pod_name = (pod.metadata.name or "").lower()
    if service_name.lower() in pod_name:
        return True

    return False
