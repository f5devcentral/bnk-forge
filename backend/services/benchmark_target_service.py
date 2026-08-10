"""
Benchmark Target Service — DB and business logic for benchmark targets
and proxy deployments.

Phase 4b: Manages K8s cluster + LLM endpoint targets and the proxy
deployments (envoy, nginx, haproxy, f5-bnk) that route traffic to them.

⚠️  Proxy Helm deployment/uninstall is deferred to Phase 4c.
    This service handles CRUD and status tracking only.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import desc
from sqlalchemy.orm import joinedload

from core.errors import BadRequestError, ConflictError, NotFoundError
from models.benchmark import BenchmarkTarget, ProxyDeployment
from models.enums import BenchmarkTargetStatus, ProxyDeploymentStatus
from services.base_service import BaseService
from services.proxy_deploy_service import _safe_release_name

logger = logging.getLogger(__name__)

# Valid proxy types (nodeport = direct to LLM, no proxy layer)
VALID_PROXY_TYPES = frozenset(
    {"envoy", "nginx", "haproxy", "f5-bnk", "nodeport", "envoy-ai-gateway", "llm-d-router"}
)

# Real Helm chart references — used when deploying proxies that aren't already on cluster.
# Verified against official docs as of 2026-03-13.
DEFAULT_HELM_CHARTS: dict[str, dict[str, str]] = {
    "envoy": {
        "chart": "oci://docker.io/envoyproxy/gateway-helm",
        "version": "v1.7.1",
        "repo": "",  # OCI — no repo add needed
    },
    "nginx": {
        "chart": "ingress-nginx/ingress-nginx",
        "version": "4.15.0",
        "repo": "https://kubernetes.github.io/ingress-nginx",
    },
    "haproxy": {
        "chart": "haproxytech/haproxy",
        "version": "1.28.0",
        "repo": "https://haproxytech.github.io/helm-charts",
    },
    # F5 BNK uses Gateway API CRDs (GatewayClass + Gateway + HTTPRoute),
    # not a single Helm chart. Discovery re-uses the BNK page's fetch logic.
    "f5-bnk": {
        "chart": "",  # No single chart — uses Gateway API CRDs
        "version": "",
        "repo": "",
    },
    # nodeport = direct to LLM service, no proxy layer to deploy
    "nodeport": {
        "chart": "",
        "version": "",
        "repo": "",
    },
    # Envoy AI Gateway — LLM-aware gateway. Multi-step OCI install: Envoy Gateway
    # base + AI Gateway CRDs + AI Gateway controller. The row's `chart`/`version`
    # below identify the PRIMARY release (the AI Gateway controller); the auxiliary
    # releases (gateway-helm, ai-gateway-crds-helm) are pinned in proxy_deploy_service.
    # Versions verified against ai-gateway repo release v0.6.0 (~/go/src/ai-gateway,
    # site/versioned_docs/version-0.6/_vars.json: aigwVersion=0.6.0, egVersion=1.7.0).
    # The data plane behind the AI Gateway is a per-target GAIE InferencePool +
    # Endpoint Picker (EPP) with approximate prefix-cache-aware routing, applied as
    # raw manifests in proxy_deploy_service (no separate Helm chart).
    "envoy-ai-gateway": {
        "chart": "oci://docker.io/envoyproxy/ai-gateway-helm",
        "version": "v0.6.0",
        "repo": "",  # OCI — no repo add needed
    },
    # llm-d-router — the llm-d inference scheduler with PRECISE (KV-event-driven)
    # prefix-cache-aware routing. The PRIMARY release below is the GAIE InferencePool
    # chart (EPP + InferencePool); proxy_deploy_service runs the EPP as the llm-d
    # inference-scheduler image with a UDS tokenizer sidecar and fronts it with
    # AGENTGATEWAY (its own control plane + `agentgateway` GatewayClass) — NOT Envoy
    # AI Gateway; llm-d-router shares no gateway infra with envoy-ai-gateway.
    # Versions verified against ~/go/src/llm-d guides/precise-prefix-cache-aware
    # (inferencepool chart v1.4.0; llm-d-inference-scheduler:v0.7.1).
    "llm-d-router": {
        "chart": "oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool",
        "version": "v1.4.0",
        "repo": "",  # OCI — no repo add needed
    },
}


class BenchmarkTargetService(BaseService):
    """Service layer for benchmark targets and proxy deployments."""

    # ================================================================
    # Target CRUD
    # ================================================================

    def list_targets(
        self,
        status: str | None = None,
        cluster_id: int | None = None,
    ) -> tuple[list[BenchmarkTarget], int]:
        """List all benchmark targets with optional filters."""
        query = self.db.query(BenchmarkTarget).options(
            joinedload(BenchmarkTarget.proxy_deployments),
        )
        if status:
            query = query.filter(BenchmarkTarget.status == status)
        if cluster_id:
            query = query.filter(BenchmarkTarget.cluster_id == cluster_id)

        targets = query.order_by(desc(BenchmarkTarget.created_at)).all()
        # Use unique() to deduplicate rows caused by joinedload on a collection
        targets = list({t.id: t for t in targets}.values())
        total = len(targets)
        return targets, total

    def get_target(self, target_id: int, with_details: bool = False) -> BenchmarkTarget:
        """Get a benchmark target by ID."""
        query = self.db.query(BenchmarkTarget)
        if with_details:
            query = query.options(
                joinedload(BenchmarkTarget.proxy_deployments),
            )
        target = query.filter(BenchmarkTarget.id == target_id).first()
        if not target:
            raise NotFoundError("benchmark_target", target_id)
        return target

    def create_target(self, data: dict) -> BenchmarkTarget:
        """Create a new benchmark target."""
        # Check unique name
        existing = self.db.query(BenchmarkTarget).filter(
            BenchmarkTarget.name == data["name"]
        ).first()
        if existing:
            raise ConflictError("benchmark_target", f"Target with name '{data['name']}' already exists")

        # Validate cluster_id references a real cluster
        from models.kubernetes import KubernetesCluster
        cluster = self.db.query(KubernetesCluster).filter(
            KubernetesCluster.id == data["cluster_id"]
        ).first()
        if not cluster:
            raise NotFoundError("kubernetes_cluster", data["cluster_id"])

        target = BenchmarkTarget(**data)
        self.db.add(target)
        self.db.flush()
        logger.info("Created benchmark target: id=%d name=%s cluster_id=%d",
                     target.id, target.name, target.cluster_id)
        return target

    def update_target(self, target_id: int, data: dict) -> BenchmarkTarget:
        """Update a benchmark target."""
        target = self.get_target(target_id)

        # Check unique name if changing
        if data.get("name") and data["name"] != target.name:
            existing = self.db.query(BenchmarkTarget).filter(
                BenchmarkTarget.name == data["name"]
            ).first()
            if existing:
                raise ConflictError("benchmark_target", f"Target with name '{data['name']}' already exists")

        # Validate cluster_id if changing
        if data.get("cluster_id") and data["cluster_id"] != target.cluster_id:
            from models.kubernetes import KubernetesCluster
            cluster = self.db.query(KubernetesCluster).filter(
                KubernetesCluster.id == data["cluster_id"]
            ).first()
            if not cluster:
                raise NotFoundError("kubernetes_cluster", data["cluster_id"])

        for key, value in data.items():
            if value is not None:
                setattr(target, key, value)

        target.updated_at = datetime.now(UTC)
        self.db.flush()
        return target

    def delete_target(self, target_id: int) -> None:
        """Delete a benchmark target (cascades to proxy deployments)."""
        target = self.get_target(target_id)
        self.db.delete(target)
        self.db.flush()
        logger.info("Deleted benchmark target: id=%d name=%s", target_id, target.name)

    def validate_target(self, target_id: int) -> BenchmarkTarget:
        """Test connectivity to the target's LLM endpoint.

        Probes the target's health by sending a lightweight request to
        the LLM base URL.  Falls back to a TCP connect check when the
        endpoint doesn't support GET on its root path.

        Updates target status to ACTIVE (reachable) or ERROR (unreachable).
        """
        import socket
        from urllib.parse import urlparse

        import requests as http_requests

        target = self.get_target(target_id)
        now = datetime.now(UTC)
        target.last_validated = now

        probe_url = target.llm_base_url.rstrip('/')
        parsed = urlparse(probe_url)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        # --- Layer 1: HTTP probe (GET on models or health endpoint) ---
        http_ok = False
        http_msg = ""
        for probe_path in ["/v1/models", "/health", "/healthz", "/"]:
            try:
                resp = http_requests.get(
                    f"{probe_url}{probe_path}",
                    timeout=10,
                    verify=False,  # Internal K8s services often use self-signed certs
                )
                if resp.status_code < 500:
                    http_ok = True
                    http_msg = f"HTTP {resp.status_code} on {probe_path}"
                    break
            except http_requests.ConnectionError:
                http_msg = f"Connection refused on {probe_path}"
            except http_requests.Timeout:
                http_msg = f"Timeout on {probe_path}"
            except Exception as e:
                http_msg = f"Error on {probe_path}: {type(e).__name__}"

        # --- Layer 2: TCP fallback if HTTP failed ---
        if not http_ok:
            try:
                sock = socket.create_connection((host, port), timeout=5)
                sock.close()
                http_ok = True
                http_msg = f"TCP connect OK to {host}:{port} (HTTP probes failed: {http_msg})"
            except (TimeoutError, OSError) as e:
                http_msg = f"Unreachable — TCP connect to {host}:{port} failed: {e}"

        if http_ok:
            target.status = BenchmarkTargetStatus.ACTIVE
            target.validation_msg = f"Validated: {http_msg}"
            logger.info("Benchmark target %d validated: %s", target_id, http_msg)
        else:
            target.status = BenchmarkTargetStatus.ERROR
            target.validation_msg = f"Validation failed: {http_msg}"
            logger.warning("Benchmark target %d validation failed: %s", target_id, http_msg)

        target.updated_at = now
        self.db.flush()
        return target

    # ================================================================
    # Proxy Deployment CRUD
    # ================================================================

    def list_proxy_deployments(self, target_id: int) -> list[ProxyDeployment]:
        """List all proxy deployments for a target."""
        # Ensure target exists
        self.get_target(target_id)
        return (
            self.db.query(ProxyDeployment)
            .filter(ProxyDeployment.target_id == target_id)
            .order_by(ProxyDeployment.proxy_type)
            .all()
        )

    def get_proxy_deployment(self, target_id: int, proxy_id: int) -> ProxyDeployment:
        """Get a proxy deployment by ID, scoped to a target."""
        deploy = (
            self.db.query(ProxyDeployment)
            .filter(
                ProxyDeployment.id == proxy_id,
                ProxyDeployment.target_id == target_id,
            )
            .first()
        )
        if not deploy:
            raise NotFoundError("proxy_deployment", proxy_id)
        return deploy

    def deploy_proxy(self, target_id: int, data: dict) -> ProxyDeployment:
        """Create a proxy deployment record for a target.

        Phase 4c will add actual Helm install logic. For now, creates
        the record in 'pending' status with default chart info.
        """
        target = self.get_target(target_id)
        proxy_type = data["proxy_type"]

        # Validate proxy type
        if proxy_type not in VALID_PROXY_TYPES:
            raise BadRequestError(
                f"Invalid proxy type '{proxy_type}'. Must be one of: {', '.join(sorted(VALID_PROXY_TYPES))}",
                code="INVALID_PROXY_TYPE",
            )

        # Check for existing deployment of same type on this target
        existing = (
            self.db.query(ProxyDeployment)
            .filter(
                ProxyDeployment.target_id == target_id,
                ProxyDeployment.proxy_type == proxy_type,
            )
            .first()
        )
        if existing:
            raise ConflictError(
                "proxy_deployment",
                f"Proxy type '{proxy_type}' already deployed to target '{target.name}' (id={existing.id})",
            )

        # Get default chart info
        defaults = DEFAULT_HELM_CHARTS.get(proxy_type, {})

        deploy = ProxyDeployment(
            target_id=target_id,
            proxy_type=proxy_type,
            helm_release=data.get("helm_release") or _safe_release_name(proxy_type, target.name),
            helm_chart=data.get("helm_chart") or defaults.get("chart"),
            helm_version=data.get("helm_version") or defaults.get("version"),
            helm_values=data.get("helm_values"),
            status=ProxyDeploymentStatus.PENDING,
        )
        self.db.add(deploy)
        self.db.flush()
        logger.info(
            "Created proxy deployment: id=%d target=%s proxy=%s chart=%s",
            deploy.id, target.name, proxy_type, deploy.helm_chart,
        )
        return deploy

    def update_proxy_deployment(self, target_id: int, proxy_id: int, data: dict) -> ProxyDeployment:
        """Update a proxy deployment (status, URLs, Helm values)."""
        deploy = self.get_proxy_deployment(target_id, proxy_id)

        for key, value in data.items():
            if value is not None:
                setattr(deploy, key, value)

        # If status changed to "ready", set deployed_at
        if data.get("status") == ProxyDeploymentStatus.READY and not deploy.deployed_at:
            deploy.deployed_at = datetime.now(UTC)

        deploy.updated_at = datetime.now(UTC)
        self.db.flush()
        return deploy

    def delete_proxy_deployment(self, target_id: int, proxy_id: int) -> None:
        """Delete (uninstall) a proxy deployment.

        Phase 4c will add actual Helm uninstall logic.
        """
        deploy = self.get_proxy_deployment(target_id, proxy_id)
        self.db.delete(deploy)
        self.db.flush()
        logger.info("Deleted proxy deployment: id=%d target_id=%d proxy=%s",
                     proxy_id, target_id, deploy.proxy_type)

    def redeploy_proxy(self, target_id: int, proxy_id: int) -> ProxyDeployment:
        """Redeploy a proxy after config change.

        Phase 4c will add actual Helm upgrade logic. For now, resets
        status to 'pending'.
        """
        deploy = self.get_proxy_deployment(target_id, proxy_id)
        deploy.status = ProxyDeploymentStatus.PENDING
        deploy.status_message = "Redeploy requested — awaiting Helm install (Phase 4c)"
        deploy.deployed_at = None
        deploy.updated_at = datetime.now(UTC)
        self.db.flush()
        return deploy
