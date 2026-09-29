"""
Proxy Discovery Service — scan a K8s cluster for existing proxy deployments.

Phase 5: Discovery-first architecture. Instead of deploying proxies from
scratch via Helm, we first scan the cluster for proxies that are already
running (deployed by customers/ops). Discovered proxies are auto-populated
as ProxyDeployment records with status='discovered'.

**Two modes:**

1. **Target-aware** (``target`` supplied) — each scanner only returns
   ``found=True`` when the proxy actually routes traffic to the specific
   target LLM service. Writes ProxyDeployment records when auto_create=True.

2. **Inventory** (``target=None``) — cluster-wide enumeration; enumerates
   *all* IngressClasses and GatewayClasses via dynamic detection (D-019),
   reverses-maps backends from all-namespace Ingress / HTTPRoute, and
   returns every controller it finds — including unknown ones (Traefik,
   Kong, Contour). Never writes ProxyDeployment rows. NodePort is excluded
   (no cluster-wide analogue).

Follows the same pattern as services/bnk/fetch.py:
  - One I/O module (this file) — all K8s API calls
  - ThreadPoolExecutor for parallel fetches
  - Pure dict results for the route layer to consume
"""

import ipaddress
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any

from kubernetes import client as k8s_client
from kubernetes.client.rest import ApiException
from sqlalchemy.orm import Session

from models.benchmark import BenchmarkTarget, ProxyDeployment
from models.enums import ProxyDeploymentStatus
from services.kubernetes import KubernetesService

logger = logging.getLogger(__name__)

# Max bytes of a ConfigMap data value to parse for proxy backends (bounds CPU time)
_MAX_CONFIGMAP_PARSE_BYTES = 256 * 1024


# ---------------------------------------------------------------------------
# Discovery result types
# ---------------------------------------------------------------------------

class ProxyDiscoveryResult:
    """Result of scanning for a single proxy type on a cluster."""

    __slots__ = (
        "proxy_type", "found", "proxy_url", "external_url",
        "namespace", "details", "error",
    )

    def __init__(
        self,
        proxy_type: str,
        *,
        found: bool = False,
        proxy_url: str | None = None,
        external_url: str | None = None,
        namespace: str | None = None,
        details: dict[str, Any] | None = None,
        error: str | None = None,
    ):
        self.proxy_type = proxy_type
        self.found = found
        self.proxy_url = proxy_url
        self.external_url = external_url
        self.namespace = namespace
        self.details = details or {}
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "proxy_type": self.proxy_type,
            "found": self.found,
            "proxy_url": self.proxy_url,
            "external_url": self.external_url,
            "namespace": self.namespace,
            "details": self.details,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class ProxyDiscoveryService:
    """Scan a K8s cluster for existing proxy deployments."""

    def __init__(self, db: Session):
        self.db = db
        self.k8s = KubernetesService(db)

    def discover_all(
        self,
        target: BenchmarkTarget | None = None,
        *,
        cluster_id: int | None = None,
        auto_create: bool = True,
    ) -> list[dict[str, Any]]:
        """Discover proxy deployments on a cluster.

        When *target* is supplied the original target-aware path runs:
        each scanner only returns ``found=True`` when the proxy routes to
        the target's LLM endpoint, and ProxyDeployment rows are written
        when *auto_create* is True.

        When *target* is None the inventory path runs: all IngressClasses
        and GatewayClasses are enumerated dynamically (D-019), backends are
        reverse-mapped from all-namespace Ingress / HTTPRoute, and every
        detected controller is returned — including unknown ones.
        *auto_create* is forced off; no ProxyDeployment rows are written.
        *cluster_id* is required in inventory mode.

        Returns:
            List of discovery result dicts (ProxyDiscoveryResult.to_dict()
            shape for target mode; inventory item shape in inventory mode).
        """
        if target is None:
            # --- Inventory mode ---
            if cluster_id is None:
                raise ValueError("cluster_id is required when target is None")
            cluster = self.k8s.get_cluster(cluster_id)
            api_client = self.k8s.load_kubeconfig(cluster)
            return self.discover_inventory(api_client, cluster_id=cluster_id)

        # --- Target-aware mode (original path, unchanged) ---
        cluster = self.k8s.get_cluster(target.cluster_id)
        api_client = self.k8s.load_kubeconfig(cluster)

        # Fire all discovery probes in parallel
        scanners = {
            "envoy": self._scan_envoy,
            "nginx": self._scan_nginx,
            "haproxy": self._scan_haproxy,
            "f5-bnk": self._scan_f5_bnk,
            "nodeport": self._scan_nodeport,
        }

        results: list[ProxyDiscoveryResult] = []

        with ThreadPoolExecutor(max_workers=len(scanners)) as executor:
            futures = {
                executor.submit(fn, api_client, target): proxy_type
                for proxy_type, fn in scanners.items()
            }
            for future in as_completed(futures):
                proxy_type = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    logger.warning("Discovery failed for %s: %s", proxy_type, exc)
                    result = ProxyDiscoveryResult(
                        proxy_type, error=str(exc),
                    )
                results.append(result)

        # Sort by proxy type for consistent ordering
        results.sort(key=lambda r: r.proxy_type)

        # Auto-create/update ProxyDeployment records
        if auto_create:
            self._sync_proxy_records(target, results)

        return [r.to_dict() for r in results]

    # ------------------------------------------------------------------
    # Inventory mode (target-independent, read-only, D-019 dynamic)
    # ------------------------------------------------------------------

    def discover_inventory(
        self,
        api_client: k8s_client.ApiClient,
        cluster_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Enumerate all proxy / ingress controllers on the cluster.

        Thin entry point for the scan caller (which already holds an
        api_client). Dynamically lists every IngressClass and GatewayClass,
        reverse-maps backends, and applies the controller overlay only as a
        display label — unknown controllers are kept, not dropped (D-019).

        Never writes ProxyDeployment rows.

        Returns:
            List of inventory result dicts (keys: proxy_type, display_name,
            controller, kind, found, namespace, proxy_url, external_url,
            backends, is_bnk, details, error).
        """
        custom = k8s_client.CustomObjectsApi(api_client)

        # --- Enumerate IngressClasses ---
        ingress_classes = _safe_list_cluster_custom(
            custom,
            group="networking.k8s.io",
            version="v1",
            plural="ingressclasses",
        )

        # --- Enumerate GatewayClasses ---
        gateway_classes = _safe_list_cluster_custom(
            custom,
            group="gateway.networking.k8s.io",
            version="v1",
            plural="gatewayclasses",
        )

        # --- Fetch all-namespace Ingress + HTTPRoute for backend mapping ---
        networking = k8s_client.NetworkingV1Api(api_client)
        all_ingresses = _safe_list_all_ingresses(networking)

        httproutes = _safe_list_all_custom(
            custom,
            group="gateway.networking.k8s.io",
            version="v1",
            plural="httproutes",
        )

        # --- Fetch all-namespace Gateways (to resolve namespace for GatewayClass items) ---
        gateways = _safe_list_all_custom(
            custom,
            group="gateway.networking.k8s.io",
            version="v1",
            plural="gateways",
        )

        items: list[dict[str, Any]] = []

        # Process IngressClasses
        for ic in ingress_classes:
            controller = ic.get("spec", {}).get("controller", "") or ""
            ic_name = ic.get("metadata", {}).get("name", "")
            # Pass kind="IngressClass" so CIS is not misclassified as BNK.
            # Also propagate the class name so the legacy bare "f5" IngressClass
            # (no controller field, name == "f5") is treated as CIS.
            proxy_type, display_name = _classify_controller(
                controller, kind="IngressClass", class_name=ic_name
            )
            is_bnk = proxy_type == "f5-bnk"

            backends = _map_ingress_backends(all_ingresses, ic_name)

            items.append({
                "proxy_type": proxy_type,
                "display_name": display_name,
                "controller": controller,
                "kind": "IngressClass",
                "found": True,
                "namespace": None,
                "proxy_url": None,
                "external_url": None,
                "backends": backends,
                "is_bnk": is_bnk,
                "details": {"ingress_class_name": ic_name},
                "error": None,
            })

        # Process GatewayClasses
        for gc in gateway_classes:
            controller = gc.get("spec", {}).get("controllerName", "") or ""
            gc_name = gc.get("metadata", {}).get("name", "")
            proxy_type, display_name = _classify_controller(controller, kind="GatewayClass")
            is_bnk = proxy_type == "f5-bnk"

            # Find gateways that belong to this GatewayClass
            gc_gateways = [
                gw for gw in gateways
                if gw.get("spec", {}).get("gatewayClassName") == gc_name
            ]

            # Namespace: from first gateway, else None
            gw_namespace = None
            if gc_gateways:
                gw_namespace = gc_gateways[0].get("metadata", {}).get("namespace")

            backends = _map_httproute_backends(httproutes, {g.get("metadata", {}).get("name") for g in gc_gateways}, gateways)

            items.append({
                "proxy_type": proxy_type,
                "display_name": display_name,
                "controller": controller,
                "kind": "GatewayClass",
                "found": True,
                "namespace": gw_namespace,
                "proxy_url": None,
                "external_url": None,
                "backends": backends,
                "is_bnk": is_bnk,
                "details": {
                    "gateway_class_name": gc_name,
                    "gateway_count": len(gc_gateways),
                },
                "error": None,
            })

        # --- Enumerate Workload Proxy Deployments (standalone/Helm workloads) ---
        apps = k8s_client.AppsV1Api(api_client)
        core = k8s_client.CoreV1Api(api_client)

        all_deployments = _safe_list_all_deployments(apps)

        # Track controllers already covered by IngressClass / GatewayClass to avoid duplication
        covered_controllers = {
            ic.get("spec", {}).get("controller", "").lower()
            for ic in ingress_classes
            if ic.get("spec", {}).get("controller")
        }
        covered_controllers |= {
            gc.get("spec", {}).get("controllerName", "").lower()
            for gc in gateway_classes
            if gc.get("spec", {}).get("controllerName")
        }

        # Query active ProxyDeployments scoped strictly to the scanned cluster (INV-1)
        db_deploy_map: dict[str, Any] = {}
        if self.db and cluster_id is not None:
            try:
                db_deploys = (
                    self.db.query(ProxyDeployment)
                    .join(BenchmarkTarget, ProxyDeployment.target_id == BenchmarkTarget.id)
                    .filter(
                        BenchmarkTarget.cluster_id == cluster_id,
                        ProxyDeployment.status.in_([
                            ProxyDeploymentStatus.READY,
                            ProxyDeploymentStatus.DISCOVERED,
                        ])
                    )
                    .all()
                )
                for d in db_deploys:
                    if d.helm_release:
                        db_deploy_map[d.helm_release.lower()] = d
                    if d.proxy_url:
                        db_deploy_map[d.proxy_url.lower()] = d
            except Exception as e:
                logger.debug("Failed to query ProxyDeployment records for inventory: %s", e)

        for dep in all_deployments:
            dep_name = dep.metadata.name or ""
            dep_ns = dep.metadata.namespace or "default"
            labels = dep.metadata.labels or {}
            containers = (
                dep.spec.template.spec.containers
                if dep.spec and dep.spec.template and dep.spec.template.spec
                else []
            )
            images = [c.image or "" for c in containers]

            # Skip system/internal namespaces
            if dep_ns in ("kube-system", "cert-manager", "kube-node-lease", "kube-public"):
                continue

            # Identify if this deployment is a proxy workload
            proxy_type, display_name = _classify_deployment(dep_name, labels, images)
            if not proxy_type:
                continue

            # If this is BNK / CIS controller, it is represented by GatewayClass or IngressClass
            if any(cov in dep_name.lower() for cov in ["f5-cne", "f5-bigip-ctlr"]):
                continue

            # A controller already listed via its IngressClass / GatewayClass
            # (e.g. ingress-nginx-controller, envoy-gateway): skip the duplicate.
            if _is_covered_controller_workload(dep_name, labels, images, covered_controllers):
                continue

            # Extract backends from ConfigMaps in this namespace
            backends = _extract_backends_from_configmaps(
                core, dep_ns, proxy_type, _deployment_configmap_names(dep),
            )

            # Check matching DB proxy deployment for backend enrichment
            matched_db_deploy = (
                db_deploy_map.get(dep_name.lower())
                or db_deploy_map.get(labels.get("app.kubernetes.io/instance", "").lower())
            )
            if matched_db_deploy and matched_db_deploy.target:
                tgt = matched_db_deploy.target
                tgt_svc = _extract_svc_name(tgt.llm_base_url)
                tgt_ns = tgt.llm_namespace or "default"
                if not any(b["service"] == tgt_svc for b in backends):
                    backends.append({
                        "service": tgt_svc,
                        "namespace": tgt_ns,
                        "via": f"Benchmark Target ({tgt.name})",
                    })

            # Resolve service for this deployment
            proxy_url, external_url = self._find_proxy_service_for_deployment(core, dep)
            if matched_db_deploy:
                proxy_url = proxy_url or matched_db_deploy.proxy_url
                external_url = external_url or matched_db_deploy.external_url

            # Check helm release metadata if present
            helm_release = labels.get("app.kubernetes.io/instance") or labels.get("helm.sh/chart")

            # Format clean display name
            formatted_display = f"{display_name} ({dep_name})" if dep_ns == "default" else f"{display_name} ({dep_ns}/{dep_name})"

            items.append({
                "proxy_type": proxy_type,
                "display_name": formatted_display,
                "controller": images[0] if images else dep_name,
                "kind": "Deployment",
                "found": True,
                "namespace": dep_ns,
                "proxy_url": proxy_url,
                "external_url": external_url,
                "backends": backends,
                "is_bnk": False,
                "details": {
                    "deployment_name": dep_name,
                    "namespace": dep_ns,
                    "image": images[0] if images else None,
                    "helm_release": helm_release,
                },
                "error": None,
            })

        # Sort for stable ordering: BNK last (it's the migration target), others alpha
        items.sort(key=lambda x: (x["is_bnk"], x["proxy_type"], x.get("details", {}).get("ingress_class_name") or x.get("details", {}).get("gateway_class_name") or x.get("details", {}).get("deployment_name") or ""))
        logger.info(
            "Proxy inventory: %d IngressClass(es) + %d GatewayClass(es) + %d total items",
            len(ingress_classes), len(gateway_classes), len(items),
        )
        return items

    # ------------------------------------------------------------------
    # Per-proxy-type scanners
    # ------------------------------------------------------------------

    def _scan_envoy(
        self,
        api_client: k8s_client.ApiClient,
        target: BenchmarkTarget,
    ) -> ProxyDiscoveryResult:
        """Detect Envoy Gateway — only found if it routes to this target's LLM.

        Checks for Envoy GatewayClass + Gateways + HTTPRoutes with backendRef
        matching the target's LLM service (same pattern as F5 BNK discovery).
        """
        custom = k8s_client.CustomObjectsApi(api_client)
        apps = k8s_client.AppsV1Api(api_client)

        # 1. Look for GatewayClass with envoy controller
        gateway_classes = _safe_list_cluster_custom(
            custom,
            group="gateway.networking.k8s.io",
            version="v1",
            plural="gatewayclasses",
        )
        envoy_gc = [
            gc for gc in gateway_classes
            if "envoy" in (gc.get("spec", {}).get("controllerName", "")).lower()
        ]

        # 2. Look for envoy-gateway-system namespace deployments
        envoy_deploys = _safe_list_namespaced_deployments(
            apps, "envoy-gateway-system",
        )

        if not envoy_gc and not envoy_deploys:
            return ProxyDiscoveryResult("envoy")

        # 3. Context check: does Envoy actually route to this target's LLM?
        target_svc_name = _extract_svc_name(target.llm_base_url)
        target_svc_ns = target.llm_namespace or "default"

        # Check Gateway API HTTPRoutes on Envoy gateways
        envoy_gc_names = {gc.get("metadata", {}).get("name") for gc in envoy_gc}
        gateways = _safe_list_all_custom(
            custom,
            group="gateway.networking.k8s.io",
            version="v1",
            plural="gateways",
        )
        envoy_gateways = [
            gw for gw in gateways
            if gw.get("spec", {}).get("gatewayClassName") in envoy_gc_names
        ]

        httproutes = _safe_list_all_custom(
            custom,
            group="gateway.networking.k8s.io",
            version="v1",
            plural="httproutes",
        )

        has_route = False
        matched_gw_name = None
        for gw in envoy_gateways:
            gw_name = gw.get("metadata", {}).get("name", "")
            gw_ns = gw.get("metadata", {}).get("namespace", "")
            routes = _find_routes_to_backend(
                httproutes, gw_name, gw_ns, target_svc_name, target_svc_ns,
            )
            if routes:
                has_route = True
                matched_gw_name = gw_name
                break

        # Also check Ingress objects with envoy/nginx class that route to this LLM
        if not has_route:
            has_route = _has_ingress_to_backend(
                api_client, target_svc_name, target_svc_ns, ingress_class_filter="envoy",
            )

        if not has_route:
            logger.info(
                "Envoy discovery: infra found but no route to '%s.%s' — not marking as discovered",
                target_svc_name, target_svc_ns,
            )
            return ProxyDiscoveryResult(
                "envoy",
                found=False,
                details={
                    "gateway_classes": [gc.get("metadata", {}).get("name") for gc in envoy_gc],
                    "deployments": [d.metadata.name for d in envoy_deploys],
                    "reason": f"No Envoy gateway/ingress routes to {target_svc_name}.{target_svc_ns}",
                },
            )

        # 4. Find the envoy proxy service to get the URL
        core = k8s_client.CoreV1Api(api_client)
        proxy_url, external_url = self._find_proxy_service(
            core, "envoy-gateway-system",
            label_selector="app.kubernetes.io/component=proxy",
        )

        if not proxy_url:
            proxy_url, external_url = self._find_proxy_service(
                core, target.proxy_namespace or "perf-proxies",
                label_selector="app.kubernetes.io/component=proxy",
            )

        if not proxy_url:
            proxy_url, external_url = self._find_service_by_name_pattern(
                core, "envoy",
            )

        logger.info(
            "Envoy discovery: found route to '%s.%s' via gateway '%s'",
            target_svc_name, target_svc_ns, matched_gw_name or "ingress",
        )

        return ProxyDiscoveryResult(
            "envoy",
            found=True,
            proxy_url=proxy_url,
            external_url=external_url,
            namespace="envoy-gateway-system",
            details={
                "gateway_classes": [gc.get("metadata", {}).get("name") for gc in envoy_gc],
                "deployments": [d.metadata.name for d in envoy_deploys],
                "matched_gateway": matched_gw_name,
            },
        )

    def _scan_nginx(
        self,
        api_client: k8s_client.ApiClient,
        target: BenchmarkTarget,
    ) -> ProxyDiscoveryResult:
        """Detect NGINX Ingress — only found if it routes to this target's LLM.

        Checks for IngressClass + Ingress objects with backend service matching
        the target's LLM service.
        """
        custom = k8s_client.CustomObjectsApi(api_client)
        core = k8s_client.CoreV1Api(api_client)
        apps = k8s_client.AppsV1Api(api_client)

        # 1. Look for IngressClass with nginx controller
        ingress_classes = _safe_list_cluster_custom(
            custom,
            group="networking.k8s.io",
            version="v1",
            plural="ingressclasses",
        )
        nginx_ic = [
            ic for ic in ingress_classes
            if "nginx" in (ic.get("spec", {}).get("controller", "")).lower()
        ]

        # 2. Look for nginx-ingress deployments
        nginx_ns = "ingress-nginx"
        nginx_deploys = _safe_list_namespaced_deployments(
            apps, nginx_ns,
            label_selector="app.kubernetes.io/name=ingress-nginx",
        )

        # Also check kube-system for some installations
        if not nginx_deploys:
            nginx_deploys = _safe_list_namespaced_deployments(
                apps, "kube-system",
                label_selector="app.kubernetes.io/name=ingress-nginx",
            )
            if nginx_deploys:
                nginx_ns = "kube-system"

        if not nginx_ic and not nginx_deploys:
            return ProxyDiscoveryResult("nginx")

        # 3. Context check: does NGINX actually route to this target's LLM?
        target_svc_name = _extract_svc_name(target.llm_base_url)
        target_svc_ns = target.llm_namespace or "default"

        has_route = _has_ingress_to_backend(
            api_client, target_svc_name, target_svc_ns, ingress_class_filter="nginx",
        )
        if not has_route:
            has_route = _has_configmap_to_backend(
                core, nginx_deploys, target_svc_name, target_svc_ns, proxy_type="nginx"
            )
        if not has_route and self.db:
            has_route = _has_target_deployment_match(
                self.db, target, nginx_deploys, proxy_type="nginx"
            )

        if not has_route:
            logger.info(
                "NGINX discovery: infra found but no Ingress/route routes to '%s.%s' — not marking as discovered",
                target_svc_name, target_svc_ns,
            )
            return ProxyDiscoveryResult(
                "nginx",
                found=False,
                details={
                    "ingress_classes": [ic.get("metadata", {}).get("name") for ic in nginx_ic],
                    "deployments": [d.metadata.name for d in nginx_deploys],
                    "reason": f"No NGINX Ingress/route routes to {target_svc_name}.{target_svc_ns}",
                },
            )

        # 4. Find the nginx controller service
        proxy_url, external_url = self._find_proxy_service(
            core, nginx_ns,
            label_selector="app.kubernetes.io/name=ingress-nginx",
        )
        if not proxy_url:
            proxy_url, external_url = self._find_service_by_name_pattern(
                core, "nginx", namespace=nginx_ns,
            )

        logger.info(
            "NGINX discovery: found routing to '%s.%s'",
            target_svc_name, target_svc_ns,
        )

        return ProxyDiscoveryResult(
            "nginx",
            found=True,
            proxy_url=proxy_url,
            external_url=external_url,
            namespace=nginx_ns,
            details={
                "ingress_classes": [ic.get("metadata", {}).get("name") for ic in nginx_ic],
                "deployments": [d.metadata.name for d in nginx_deploys],
            },
        )

    def _scan_haproxy(
        self,
        api_client: k8s_client.ApiClient,
        target: BenchmarkTarget,
    ) -> ProxyDiscoveryResult:
        """Detect HAProxy — only found if it routes to this target's LLM.

        Checks for HAProxy deployments + Ingress objects or configmaps with backend service
        matching the target's LLM service.
        """
        apps = k8s_client.AppsV1Api(api_client)
        core = k8s_client.CoreV1Api(api_client)

        # Look for haproxy deployments across common namespaces
        haproxy_deploys = []
        haproxy_ns = None

        for ns in ("haproxy-controller", "haproxy", "default", target.proxy_namespace or "perf-proxies"):
            deploys = _safe_list_namespaced_deployments(apps, ns)
            ha_deploys = [
                d for d in deploys
                if "haproxy" in (d.metadata.name or "").lower()
                or "haproxy" in (d.metadata.labels or {}).get("app", "").lower()
                or "haproxy" in (d.metadata.labels or {}).get("app.kubernetes.io/name", "").lower()
            ]
            if ha_deploys:
                haproxy_deploys = ha_deploys
                haproxy_ns = ns
                break

        # Also check for IngressClass with haproxy
        custom = k8s_client.CustomObjectsApi(api_client)
        ingress_classes = _safe_list_cluster_custom(
            custom,
            group="networking.k8s.io",
            version="v1",
            plural="ingressclasses",
        )
        haproxy_ic = [
            ic for ic in ingress_classes
            if "haproxy" in (ic.get("spec", {}).get("controller", "")).lower()
        ]

        if not haproxy_deploys and not haproxy_ic:
            return ProxyDiscoveryResult("haproxy")

        # Context check: does HAProxy actually route to this target's LLM?
        target_svc_name = _extract_svc_name(target.llm_base_url)
        target_svc_ns = target.llm_namespace or "default"

        has_route = _has_ingress_to_backend(
            api_client, target_svc_name, target_svc_ns, ingress_class_filter="haproxy",
        )
        # The Deployment that routes to this target (its own config or its Forge
        # identity) — its Service, not any "haproxy" Service, is the proxy URL.
        route_dep = next((
            d for d in haproxy_deploys
            if _has_configmap_to_backend(core, [d], target_svc_name, target_svc_ns, proxy_type="haproxy")
            or (self.db and _has_target_deployment_match(self.db, target, [d], proxy_type="haproxy"))
        ), None)
        has_route = has_route or route_dep is not None

        if not has_route:
            logger.info(
                "HAProxy discovery: infra found but no Ingress/route routes to '%s.%s' — not marking as discovered",
                target_svc_name, target_svc_ns,
            )
            return ProxyDiscoveryResult(
                "haproxy",
                found=False,
                details={
                    "ingress_classes": [ic.get("metadata", {}).get("name") for ic in haproxy_ic],
                    "deployments": [d.metadata.name for d in haproxy_deploys],
                    "reason": f"No HAProxy Ingress/route routes to {target_svc_name}.{target_svc_ns}",
                },
            )

        # Find the Service of the routing Deployment (else of the Ingress controller)
        proxy_url = None
        external_url = None
        for dep in [route_dep] if route_dep else haproxy_deploys:
            proxy_url, external_url = self._find_proxy_service_for_deployment(core, dep)
            if proxy_url:
                break

        logger.info(
            "HAProxy discovery: found routing to '%s.%s'",
            target_svc_name, target_svc_ns,
        )

        return ProxyDiscoveryResult(
            "haproxy",
            found=True,
            proxy_url=proxy_url,
            external_url=external_url,
            namespace=haproxy_ns,
            details={
                "ingress_classes": [ic.get("metadata", {}).get("name") for ic in haproxy_ic],
                "deployments": [d.metadata.name for d in haproxy_deploys],
            },
        )

    def _scan_f5_bnk(
        self,
        api_client: k8s_client.ApiClient,
        target: BenchmarkTarget,
    ) -> ProxyDiscoveryResult:
        """Detect F5 BNK by looking for F5 GatewayClass + Gateways + BNK pods.

        Uses Gateway topology data (VIP from status.addresses, port from
        spec.listeners) — the same data the BNK Topology page displays.
        Also checks HTTPRoutes to find which Gateway routes to this target's
        LLM service, so we pick the correct VIP + listener.
        """
        custom = k8s_client.CustomObjectsApi(api_client)

        # 1. Look for GatewayClass with F5 controller
        gateway_classes = _safe_list_cluster_custom(
            custom,
            group="gateway.networking.k8s.io",
            version="v1",
            plural="gatewayclasses",
        )
        f5_gc = [
            gc for gc in gateway_classes
            if any(
                marker in (gc.get("spec", {}).get("controllerName", "")).lower()
                for marker in ("f5", "bigip", "bnk", "cne")
            )
        ]

        # 2. Look for F5 BNK pods in known namespaces
        from services.bnk_pod_discovery import BNK_NAMESPACES, _fetch_pods_in_namespace
        bnk_pods = []
        for ns in BNK_NAMESPACES:
            bnk_pods.extend(_fetch_pods_in_namespace(api_client, ns))

        # 3. Look for Gateways referencing F5 GatewayClass
        gateways = _safe_list_all_custom(
            custom,
            group="gateway.networking.k8s.io",
            version="v1",
            plural="gateways",
        )
        f5_gc_names = {gc.get("metadata", {}).get("name") for gc in f5_gc}
        f5_gateways = [
            gw for gw in gateways
            if gw.get("spec", {}).get("gatewayClassName") in f5_gc_names
        ]

        if not f5_gc and not bnk_pods:
            return ProxyDiscoveryResult("f5-bnk")

        # 4. Extract Gateway VIP + listener info (same as BNK topology page)
        #    Also fetch HTTPRoutes to find which Gateway routes to this target's LLM.
        httproutes = _safe_list_all_custom(
            custom,
            group="gateway.networking.k8s.io",
            version="v1",
            plural="httproutes",
        )

        # Find the best gateway: prefer one whose HTTPRoute backends match the target LLM
        target_svc_name = _extract_svc_name(target.llm_base_url)
        target_svc_ns = target.llm_namespace or "default"
        # Full target host (before port strip, after scheme strip) — used for VIP-match below.
        # _extract_svc_name truncates at the first "." which destroys IP addresses; preserve
        # the full host so "10.0.10.101" is not silently truncated to "10".
        _target_host_raw = (target.llm_base_url or "").split("//", 1)[-1].split(":")[0]

        proxy_url = None
        external_url = None
        best_gw_name = None
        best_gw_ns = None
        best_has_routes = False  # Track whether current pick was matched via routes
        gateway_details = []

        for gw in f5_gateways:
            gw_name = gw.get("metadata", {}).get("name", "")
            gw_ns = gw.get("metadata", {}).get("namespace", "")
            gw_status = gw.get("status", {})
            listeners = gw.get("spec", {}).get("listeners", [])

            # Extract VIP: status.addresses is authoritative; fall back to spec.addresses
            # (BNK sets the VIP in spec.addresses before status is patched by the controller).
            gw_spec = gw.get("spec", {})
            status_addresses = [a.get("value", "") for a in gw_status.get("addresses", [])]
            spec_addresses = [a.get("value", "") for a in gw_spec.get("addresses", [])]
            addresses = status_addresses or spec_addresses
            vip = addresses[0] if addresses else None

            # Build listener details
            listener_details = []
            for listener in listeners:
                listener_details.append({
                    "name": listener.get("name"),
                    "port": listener.get("port"),
                    "protocol": listener.get("protocol"),
                })

            # Check if any HTTPRoute on this gateway has a backendRef to our target LLM
            routes_to_target = _find_routes_to_backend(
                httproutes, gw_name, gw_ns, target_svc_name, target_svc_ns,
            )

            gateway_details.append({
                "name": gw_name,
                "namespace": gw_ns,
                "vip": vip,
                "listeners": listener_details,
                "routes_to_target": len(routes_to_target),
                "route_names": [r.get("metadata", {}).get("name", "") for r in routes_to_target],
            })

            # Build the proxy URL from VIP + first HTTP listener port
            if vip and listeners:
                # Prefer listeners that have routes to our target; fallback to first listener
                best_port = None
                for listener in listeners:
                    l_name = listener.get("name", "")
                    l_port = listener.get("port")
                    # Check if any route on this listener points to our target
                    for route in routes_to_target:
                        for parent in route.get("spec", {}).get("parentRefs", []):
                            section = parent.get("sectionName")
                            if section is None or section == l_name:
                                best_port = l_port
                                break
                        if best_port:
                            break
                    if best_port:
                        break

                if best_port is None and listeners:
                    best_port = listeners[0].get("port", 80)

                gw_proxy_url = f"http://{vip}:{best_port}"
                gw_external_url = gw_proxy_url  # VIP is externally reachable

                # Primary match: an HTTPRoute backendRef points to our LLM service.
                # This is the highest-confidence path and takes priority.
                if routes_to_target and not best_has_routes:
                    proxy_url = gw_proxy_url
                    external_url = gw_external_url
                    best_gw_name = gw_name
                    best_gw_ns = gw_ns
                    best_has_routes = True
                    logger.info(
                        "F5 BNK discovery: selected gateway '%s/%s' (VIP %s) — "
                        "has route to target LLM '%s.%s'",
                        gw_ns, gw_name, vip, target_svc_name, target_svc_ns,
                    )
                elif not routes_to_target:
                    # VIP-match fallback: when the target's llm_base_url is an IP
                    # literal that equals this gateway's VIP, the BNK front-end IS
                    # the proxy endpoint — no HTTPRoute backendRef is expected because
                    # awsbnkctl registers the target with only llm_base_url="http://<VIP>".
                    # This path is additive and only reached for IP-literal targets.
                    # NOTE: we compare against _target_host_raw (the full host, e.g. "10.0.10.101")
                    # rather than target_svc_name which _extract_svc_name truncates at the first "."
                    # (turning "10.0.10.101" into "10" — structurally unable to match a VIP).
                    target_is_ip = False
                    try:
                        ipaddress.ip_address(_target_host_raw)
                        target_is_ip = True
                    except ValueError:
                        pass

                    if target_is_ip and vip and _target_host_raw == vip and not best_has_routes:
                        proxy_url = gw_proxy_url
                        external_url = gw_external_url
                        best_gw_name = gw_name
                        best_gw_ns = gw_ns
                        best_has_routes = True
                        logger.info(
                            "F5 BNK discovery: selected gateway '%s/%s' via VIP-match "
                            "(target host %s == gateway VIP) — no HTTPRoute backendRef expected",
                            gw_ns, gw_name, vip,
                        )
                    else:
                        logger.debug(
                            "F5 BNK discovery: skipping gateway '%s/%s' (VIP %s) — "
                            "no HTTPRoute to target LLM '%s.%s'",
                            gw_ns, gw_name, vip, target_svc_name, target_svc_ns,
                        )

        # Only mark as found if a gateway actually routes to this target's LLM.
        # F5 BNK infra may exist on the cluster, but if no gateway has an
        # HTTPRoute with a backendRef to our LLM service, it's not a valid
        # proxy for this specific target.
        if not best_has_routes:
            logger.info(
                "F5 BNK discovery: infra found (%d GatewayClasses, %d gateways, %d pods) "
                "but no gateway routes to '%s.%s' — not marking as discovered",
                len(f5_gc), len(f5_gateways), len(bnk_pods),
                target_svc_name, target_svc_ns,
            )
            return ProxyDiscoveryResult(
                "f5-bnk",
                found=False,
                details={
                    "gateway_classes": [gc.get("metadata", {}).get("name") for gc in f5_gc],
                    "gateways": gateway_details,
                    "pod_count": len(bnk_pods),
                    "reason": f"No F5 gateway has an HTTPRoute to {target_svc_name}.{target_svc_ns}",
                },
            )

        return ProxyDiscoveryResult(
            "f5-bnk",
            found=True,
            proxy_url=proxy_url,
            external_url=external_url,
            namespace=best_gw_ns or "f5-bnk",
            details={
                "gateway_classes": [gc.get("metadata", {}).get("name") for gc in f5_gc],
                "gateways": gateway_details,
                "pod_count": len(bnk_pods),
                "matched_gateway": best_gw_name,
            },
        )

    def _scan_nodeport(
        self,
        api_client: k8s_client.ApiClient,
        target: BenchmarkTarget,
    ) -> ProxyDiscoveryResult:
        """Check if the target LLM service actually has type=NodePort.

        Only marks as found when the K8s service is explicitly configured as
        NodePort — a direct-to-LLM path with no proxy layer. Resolves the
        real node IP so the user gets a routable URL, not ``<node-ip>``.
        """
        core = k8s_client.CoreV1Api(api_client)

        # Parse the service name and namespace from llm_base_url
        svc_name = _extract_svc_name(target.llm_base_url)
        svc_ns = target.llm_namespace or "default"

        try:
            svc = core.read_namespaced_service(name=svc_name, namespace=svc_ns)
        except ApiException as e:
            if e.status == 404:
                return ProxyDiscoveryResult(
                    "nodeport",
                    error=f"LLM service '{svc_name}' not found in namespace '{svc_ns}'",
                )
            raise

        svc_type = svc.spec.type or "ClusterIP"
        ports = svc.spec.ports or []

        # Only mark as discovered if the service is actually type=NodePort
        if svc_type != "NodePort":
            logger.info(
                "NodePort discovery: service '%s.%s' is type=%s, not NodePort — skipping",
                svc_name, svc_ns, svc_type,
            )
            return ProxyDiscoveryResult(
                "nodeport",
                found=False,
                details={
                    "service_name": svc_name,
                    "service_type": svc_type,
                    "reason": f"Service type is '{svc_type}', not NodePort",
                },
            )

        if not ports:
            return ProxyDiscoveryResult(
                "nodeport",
                found=False,
                details={"service_name": svc_name, "reason": "No ports defined"},
            )

        node_port = ports[0].node_port
        if not node_port:
            return ProxyDiscoveryResult(
                "nodeport",
                found=False,
                details={"service_name": svc_name, "reason": "NodePort not allocated yet"},
            )

        # Resolve a real node IP so the user gets a routable address
        node_ip = _get_node_ip(core)

        # Build URLs with the actual node IP
        proxy_url = f"http://{node_ip}:{node_port}" if node_ip else f"http://{svc_name}.{svc_ns}:{ports[0].port}"
        external_url = f"http://{node_ip}:{node_port}" if node_ip else None

        logger.info(
            "NodePort discovery: found '%s.%s' NodePort=%d node_ip=%s",
            svc_name, svc_ns, node_port, node_ip or "unknown",
        )

        return ProxyDiscoveryResult(
            "nodeport",
            found=True,
            proxy_url=proxy_url,
            external_url=external_url,
            namespace=svc_ns,
            details={
                "service_name": svc_name,
                "service_type": svc_type,
                "node_ip": node_ip,
                "ports": [
                    {"port": p.port, "target_port": str(p.target_port), "node_port": p.node_port, "name": p.name}
                    for p in ports
                ],
            },
        )

    # ------------------------------------------------------------------
    # Service URL helpers
    # ------------------------------------------------------------------

    def _find_proxy_service(
        self,
        core: k8s_client.CoreV1Api,
        namespace: str,
        *,
        label_selector: str = "",
    ) -> tuple[str | None, str | None]:
        """Find a service in a namespace and return (internal_url, external_url)."""
        try:
            svcs = core.list_namespaced_service(
                namespace=namespace,
                label_selector=label_selector,
                _request_timeout=10,
            )
        except ApiException:
            return None, None

        for svc in svcs.items:
            ports = svc.spec.ports or []
            if not ports:
                continue
            port = ports[0].port
            name = svc.metadata.name
            ns = svc.metadata.namespace

            internal = f"http://{name}.{ns}:{port}"
            external = _resolve_external_url(core, svc, ports[0])

            return internal, external

        return None, None

    def _find_service_by_name_pattern(
        self,
        core: k8s_client.CoreV1Api,
        pattern: str,
        *,
        namespace: str | None = None,
    ) -> tuple[str | None, str | None]:
        """Find a service whose name contains ``pattern``."""
        try:
            if namespace:
                svcs = core.list_namespaced_service(
                    namespace=namespace, _request_timeout=10,
                )
            else:
                svcs = core.list_service_for_all_namespaces(_request_timeout=10)
        except ApiException:
            return None, None

        for svc in svcs.items:
            name = svc.metadata.name or ""
            if pattern.lower() not in name.lower():
                continue
            ports = svc.spec.ports or []
            if not ports:
                continue

            ns = svc.metadata.namespace
            port = ports[0].port

            internal = f"http://{name}.{ns}:{port}"
            external = _resolve_external_url(core, svc, ports[0])

            return internal, external

        return None, None

    def _find_proxy_service_for_deployment(
        self,
        core: k8s_client.CoreV1Api,
        dep: Any,
    ) -> tuple[str | None, str | None]:
        """proxy_url / external_url of the Service in front of Deployment ``dep``.

        Tied to the Deployment, never a name substring: the Service named like
        the Deployment, else one whose selector matches its pod template labels,
        else one carrying its Helm instance label. An exposed (NodePort /
        LoadBalancer) Service wins among matches.
        """
        ns = dep.metadata.namespace or "default"
        try:
            svcs = core.list_namespaced_service(namespace=ns, _request_timeout=10).items or []
        except ApiException:
            return None, None
        name = dep.metadata.name or ""
        template = dep.spec.template.metadata if dep.spec and dep.spec.template else None
        pod_labels = (template.labels if template else None) or {}
        instance = (dep.metadata.labels or {}).get("app.kubernetes.io/instance")

        def _selects(svc: Any) -> bool:
            selector = svc.spec.selector or {}
            return bool(selector) and all(pod_labels.get(k) == v for k, v in selector.items())

        def _same_release(svc: Any) -> bool:
            return bool(instance) and (svc.metadata.labels or {}).get("app.kubernetes.io/instance") == instance

        for matches in (lambda s: s.metadata.name == name, _selects, _same_release):
            hits = [s for s in svcs if s.spec.ports and matches(s)]
            if hits:
                exposed = [s for s in hits if s.spec.type in ("NodePort", "LoadBalancer")]
                svc = (exposed or hits)[0]
                port = svc.spec.ports[0]
                return f"http://{svc.metadata.name}.{ns}:{port.port}", _resolve_external_url(core, svc, port)
        return None, None

    # ------------------------------------------------------------------
    # Sync discovered proxies to ProxyDeployment records
    # ------------------------------------------------------------------

    def _sync_proxy_records(
        self,
        target: BenchmarkTarget,
        results: list[ProxyDiscoveryResult],
    ) -> None:
        """Create or update ProxyDeployment rows for discovered proxies.

        - If found and no existing record → create with status='discovered'
        - If found and existing record with status 'pending' or 'failed' → update to 'discovered'
        - If NOT found and existing record with status 'discovered' → REMOVE it
          (context-aware: the proxy doesn't route to this target's LLM)
        - Leave ready/deploying/uninstalling records alone (user-managed)
        """
        existing = {
            d.proxy_type: d
            for d in self.db.query(ProxyDeployment).filter(
                ProxyDeployment.target_id == target.id,
            ).all()
        }

        now = datetime.now(UTC)

        for result in results:
            deploy = existing.get(result.proxy_type)

            if result.found:
                if deploy is None:
                    # Create new discovered proxy
                    deploy = ProxyDeployment(
                        target_id=target.id,
                        proxy_type=result.proxy_type,
                        proxy_url=result.proxy_url,
                        external_url=result.external_url,
                        status=ProxyDeploymentStatus.DISCOVERED,
                        status_message=f"Discovered on cluster in namespace '{result.namespace}'",
                    )
                    self.db.add(deploy)
                    logger.info(
                        "Discovered proxy: type=%s target=%s ns=%s",
                        result.proxy_type, target.name, result.namespace,
                    )
                elif deploy.status in (
                    ProxyDeploymentStatus.PENDING,
                    ProxyDeploymentStatus.FAILED,
                    ProxyDeploymentStatus.DISCOVERED,
                ):
                    # Update existing record with fresh discovery data
                    deploy.proxy_url = result.proxy_url or deploy.proxy_url
                    deploy.external_url = result.external_url or deploy.external_url
                    deploy.status = ProxyDeploymentStatus.DISCOVERED
                    deploy.status_message = f"Re-discovered on cluster in namespace '{result.namespace}'"
                    deploy.updated_at = now
                # else: leave ready/deploying/uninstalling records alone
            else:
                # Proxy NOT found for this target — remove stale discovered records
                if deploy and deploy.status == ProxyDeploymentStatus.DISCOVERED:
                    reason = (result.details or {}).get("reason", "no longer routes to this target")
                    logger.info(
                        "Removing stale proxy: type=%s target=%s — %s",
                        result.proxy_type, target.name, reason,
                    )
                    self.db.delete(deploy)

        self.db.flush()


# ---------------------------------------------------------------------------
# Controller overlay (D-019 display-only table)
# ---------------------------------------------------------------------------
# Maps controller substring → (proxy_type, display_name).
# Unknown controllers pass through as-is — never dropped.

_CONTROLLER_OVERLAY: list[tuple[str, str, str]] = [
    # (substring, proxy_type, display_name)
    ("ingress-nginx", "nginx", "NGINX Ingress"),
    ("nginx", "nginx", "NGINX Ingress"),
    ("haproxy", "haproxy", "HAProxy"),
    ("envoyproxy.io", "envoy", "Envoy Gateway"),
    ("envoy", "envoy", "Envoy Gateway"),
    # F5 BNK markers — GatewayClass only (IngressClass path handled separately via CIS check).
    # "f5.io" matches the BNK GatewayClass controller "f5.io/gateway-controller" and
    # "cne.f5.io/gateway-controller" without hitting the CIS domain "f5.com".
    ("f5.io", "f5-bnk", "F5 BNK"),
    ("bigip", "f5-bnk", "F5 BNK"),
    ("bnk", "f5-bnk", "F5 BNK"),
    ("cne", "f5-bnk", "F5 BNK"),
]

# CIS controller substrings — IngressClass only, must match before generic BNK markers
_CIS_CONTROLLER_SUBSTRINGS: tuple[str, ...] = ("f5.com/cntr-ingress-svcs",)

# IngressClass names that indicate CIS (legacy annotation / class name "f5")
_CIS_CLASS_NAMES: frozenset[str] = frozenset({"f5"})


def _classify_controller(
    controller: str,
    kind: str = "IngressClass",
    class_name: str = "",
) -> tuple[str, str]:
    """Return (proxy_type, display_name) for a controller string.

    Applies the overlay table (D-019); unknown controllers keep the raw
    string as both type and display_name so they are never dropped.

    ``kind`` must be "IngressClass" or "GatewayClass" so that CIS and BNK
    can be distinguished: CIS uses the Ingress API; BNK uses the Gateway API.

    ``class_name`` is the IngressClass metadata.name, used to detect the
    legacy bare "f5" IngressClass that CIS registers without a controller string.
    """
    lower = controller.lower()

    # F5 CIS detection — IngressClass only.
    # CIS registers controller "f5.com/cntr-ingress-svcs". Some older clusters
    # have an IngressClass named "f5" with no controller string. Both are CIS,
    # NOT BNK. This check must run before the generic overlay to prevent the
    # "bigip"/"f5" substring from mapping CIS → f5-bnk.
    if kind == "IngressClass":
        if any(cis_sub in lower for cis_sub in _CIS_CONTROLLER_SUBSTRINGS):
            return "f5-cis", "F5 CIS"
        if class_name.lower() in _CIS_CLASS_NAMES and not controller:
            return "f5-cis", "F5 CIS"

    for substring, proxy_type, display_name in _CONTROLLER_OVERLAY:
        if substring in lower:
            return proxy_type, display_name
    # Generic fallback — preserve unknown controllers (D-019 dynamic-by-default)
    display = controller.split("/")[-1] if "/" in controller else controller
    return controller, display


# ---------------------------------------------------------------------------
# Inventory helpers (all-namespace, fresh primitives — do not extend
# _has_ingress_to_backend which is target-scoped, risk R4)
# ---------------------------------------------------------------------------


def _safe_list_all_ingresses(
    networking: k8s_client.NetworkingV1Api,
) -> list:
    """List all Ingress objects across all namespaces. Returns [] on error."""
    try:
        resp = networking.list_ingress_for_all_namespaces(_request_timeout=10)
        return resp.items
    except ApiException as e:
        logger.debug("Failed to list all ingresses: %s", e.reason)
        return []
    except Exception as e:
        logger.debug("Failed to list all ingresses: %s", e)
        return []


def _map_ingress_backends(
    ingresses: list,
    ingress_class_name: str,
) -> list[dict[str, Any]]:
    """Return backends served by Ingress objects with *ingress_class_name*.

    Matches on spec.ingressClassName or the legacy annotation. Returns a
    deduplicated list of {service, namespace, via} dicts.
    """
    seen: set[tuple[str, str]] = set()
    backends: list[dict[str, Any]] = []

    for ing in ingresses:
        spec = ing.spec
        if spec is None:
            continue
        meta = ing.metadata or {}
        annotations = meta.annotations or {}

        # Match by class name
        class_name = spec.ingress_class_name or ""
        ann_class = annotations.get("kubernetes.io/ingress.class", "")
        if (
            class_name.lower() != ingress_class_name.lower()
            and ann_class.lower() != ingress_class_name.lower()
        ):
            continue

        ns = meta.namespace or "default"

        # Collect backends from rules
        for rule in spec.rules or []:
            http = rule.http
            if not http:
                continue
            for path in http.paths or []:
                bk = path.backend
                if bk and bk.service:
                    key = (bk.service.name, ns)
                    if key not in seen:
                        seen.add(key)
                        backends.append({"service": bk.service.name, "namespace": ns, "via": "Ingress"})

        # Default backend
        db = spec.default_backend
        if db and db.service:
            key = (db.service.name, ns)
            if key not in seen:
                seen.add(key)
                backends.append({"service": db.service.name, "namespace": ns, "via": "Ingress (default)"})

    return backends


def _map_httproute_backends(
    httproutes: list[dict],
    gateway_names: set[str],
    gateways: list[dict],
) -> list[dict[str, Any]]:
    """Return backends served by HTTPRoutes attached to any of *gateway_names*.

    Returns a deduplicated list of {service, namespace, via} dicts.
    """
    seen: set[tuple[str, str]] = set()
    backends: list[dict[str, Any]] = []

    for route in httproutes:
        spec = route.get("spec", {})
        route_ns = route.get("metadata", {}).get("namespace", "default")

        # Check if this route is attached to one of our gateways
        attached = False
        for parent in spec.get("parentRefs", []):
            if parent.get("name") in gateway_names:
                attached = True
                break
        if not attached:
            continue

        for rule in spec.get("rules", []):
            for br in rule.get("backendRefs", []):
                svc_name = br.get("name", "")
                svc_ns = br.get("namespace", route_ns)
                if svc_name:
                    key = (svc_name, svc_ns)
                    if key not in seen:
                        seen.add(key)
                        backends.append({"service": svc_name, "namespace": svc_ns, "via": "HTTPRoute"})

    return backends


# ---------------------------------------------------------------------------
# Module-level helpers (no I/O, pure logic)
# ---------------------------------------------------------------------------

def _safe_list_cluster_custom(
    custom: k8s_client.CustomObjectsApi,
    *,
    group: str,
    version: str,
    plural: str,
) -> list[dict]:
    """List cluster-scoped custom resources. Returns [] on error."""
    try:
        resp = custom.list_cluster_custom_object(
            group=group, version=version, plural=plural,
            _request_timeout=10,
        )
        return resp.get("items", [])
    except ApiException as e:
        if e.status == 404:
            return []  # CRD not installed
        logger.debug("Failed to list %s/%s %s: %s", group, version, plural, e.reason)
        return []
    except Exception as e:
        logger.debug("Failed to list %s/%s %s: %s", group, version, plural, e)
        return []


def _safe_list_all_custom(
    custom: k8s_client.CustomObjectsApi,
    *,
    group: str,
    version: str,
    plural: str,
) -> list[dict]:
    """List namespaced custom resources across all namespaces. Returns [] on error."""
    try:
        resp = custom.list_cluster_custom_object(
            group=group, version=version, plural=plural,
            _request_timeout=10,
        )
        return resp.get("items", [])
    except ApiException as e:
        if e.status == 404:
            return []
        logger.debug("Failed to list all %s/%s %s: %s", group, version, plural, e.reason)
        return []
    except Exception as e:
        logger.debug("Failed to list all %s/%s %s: %s", group, version, plural, e)
        return []


def _safe_list_namespaced_deployments(
    apps: k8s_client.AppsV1Api,
    namespace: str,
    *,
    label_selector: str = "",
) -> list:
    """List deployments in a namespace. Returns [] if namespace doesn't exist."""
    try:
        resp = apps.list_namespaced_deployment(
            namespace=namespace,
            label_selector=label_selector,
            _request_timeout=10,
        )
        return resp.items
    except ApiException as e:
        if e.status == 403:
            logger.warning("Permission denied (403) listing deployments in %s: %s", namespace, e.reason)
            return []
        if e.status == 404:
            return []
        logger.debug("Failed to list deployments in %s: %s", namespace, e.reason)
        return []
    except Exception as e:
        logger.debug("Failed to list deployments in %s: %s", namespace, e)
        return []


def _find_routes_to_backend(
    httproutes: list[dict],
    gw_name: str,
    gw_ns: str,
    target_svc_name: str,
    target_svc_ns: str,
) -> list[dict]:
    """Find HTTPRoutes that reference a gateway AND have a backendRef to the target service.

    Same matching logic as topology.py _match_routes_to_listener, but also
    checks backendRefs to find routes that actually route to our LLM service.
    """
    matched = []
    for route in httproutes:
        route_spec = route.get("spec", {})
        route_ns = route.get("metadata", {}).get("namespace", "")

        # Check parentRefs — does this route attach to our gateway?
        parents_match = False
        for parent in route_spec.get("parentRefs", []):
            parent_ns = parent.get("namespace", route_ns)
            if parent.get("name") == gw_name and parent_ns == gw_ns:
                parents_match = True
                break

        if not parents_match:
            continue

        # Check backendRefs — does any rule route to our target LLM service?
        for rule in route_spec.get("rules", []):
            for br in rule.get("backendRefs", []):
                br_name = br.get("name", "")
                br_ns = br.get("namespace", route_ns)
                if br_name == target_svc_name and br_ns == target_svc_ns:
                    matched.append(route)
                    break
            else:
                continue
            break  # Found a match in this route, move to next route

    return matched


def _resolve_external_url(
    core: k8s_client.CoreV1Api,
    svc: Any,
    port_obj: Any,
) -> str | None:
    """Build a user-friendly external URL for a K8s service.

    Resolves real node IPs for NodePort services instead of ``<node-ip>``.
    """
    if svc.spec.type == "NodePort" and port_obj.node_port:
        node_ip = _get_node_ip(core)
        if node_ip:
            return f"http://{node_ip}:{port_obj.node_port}"
        return None
    elif svc.spec.type == "LoadBalancer":
        ingress = (svc.status.load_balancer.ingress or []) if svc.status and svc.status.load_balancer else []
        if ingress:
            host = ingress[0].hostname or ingress[0].ip
            if host:
                return f"http://{host}:{port_obj.port}"
    return None


def _extract_svc_name(url: str) -> str:
    """Extract K8s service name from a URL like http://svc-name.ns:8000."""
    host = url.split("//", 1)[-1]
    host = host.split(":")[0]
    return host.split(".")[0]


def _get_node_ip(core: k8s_client.CoreV1Api) -> str | None:
    """Get a routable IP address from a cluster node.

    Prefers InternalIP, falls back to ExternalIP, then Hostname.
    Returns the IP of the first Ready node found.
    """
    try:
        nodes = core.list_node(_request_timeout=10)
    except ApiException:
        return None

    for node in nodes.items:
        addresses = node.status.addresses or []
        # Build a map of address types
        addr_map: dict[str, str] = {}
        for addr in addresses:
            addr_map[addr.type] = addr.address

        # Prefer InternalIP (routable within the network)
        ip = addr_map.get("InternalIP") or addr_map.get("ExternalIP") or addr_map.get("Hostname")
        if ip:
            return ip

    return None


def _has_ingress_to_backend(
    api_client: k8s_client.ApiClient,
    target_svc_name: str,
    target_svc_ns: str,
    *,
    ingress_class_filter: str | None = None,
) -> bool:
    """Check if any Ingress object routes to the target service.

    Scans all Ingress objects (networking.k8s.io/v1) and checks if any rule
    has a backend pointing to ``target_svc_name`` in ``target_svc_ns``.

    Args:
        ingress_class_filter: If set, only consider Ingresses whose
            ``spec.ingressClassName`` or annotation contains this string
            (case-insensitive).
    """
    networking = k8s_client.NetworkingV1Api(api_client)

    try:
        ingresses = networking.list_namespaced_ingress(
            namespace=target_svc_ns, _request_timeout=10,
        )
    except ApiException:
        return False

    for ing in ingresses.items:
        # Filter by ingress class if requested
        if ingress_class_filter:
            class_name = ing.spec.ingress_class_name or ""
            annotations = ing.metadata.annotations or {}
            ann_class = annotations.get("kubernetes.io/ingress.class", "")
            if (ingress_class_filter.lower() not in class_name.lower()
                    and ingress_class_filter.lower() not in ann_class.lower()):
                continue

        # Check each rule's backend paths
        for rule in (ing.spec.rules or []):
            http = rule.http
            if not http:
                continue
            for path in (http.paths or []):
                backend = path.backend
                if not backend or not backend.service:
                    continue
                if backend.service.name == target_svc_name:
                    return True

        # Also check the default backend
        if ing.spec.default_backend and ing.spec.default_backend.service:
            if ing.spec.default_backend.service.name == target_svc_name:
                return True

    return False


def _safe_list_all_deployments(apps: k8s_client.AppsV1Api) -> list:
    """List deployments across all namespaces. Returns [] on error."""
    try:
        resp = apps.list_deployment_for_all_namespaces(_request_timeout=10)
        return resp.items
    except ApiException as e:
        if e.status == 403:
            logger.warning("Permission denied (403) listing deployments for all namespaces: %s", e.reason)
            return []
        if e.status == 404:
            return []
        logger.debug("Failed to list deployments for all namespaces: %s", e.reason)
        return []
    except Exception as e:
        logger.debug("Failed to list deployments for all namespaces: %s", e)
        return []


def _safe_list_namespaced_configmaps(
    core: k8s_client.CoreV1Api,
    namespace: str,
) -> list:
    """List ConfigMaps in a namespace. Returns [] on error."""
    try:
        resp = core.list_namespaced_config_map(namespace=namespace, _request_timeout=10)
        return resp.items
    except ApiException as e:
        if e.status == 403:
            logger.warning("Permission denied (403) listing ConfigMaps in %s: %s", namespace, e.reason)
            return []
        if e.status == 404:
            return []
        logger.debug("Failed to list ConfigMaps in %s: %s", namespace, e.reason)
        return []
    except Exception as e:
        logger.debug("Failed to list ConfigMaps in %s: %s", namespace, e)
        return []


def _classify_deployment(
    name: str,
    labels: dict[str, str],
    images: list[str],
) -> tuple[str | None, str | None]:
    """Classify a Deployment as a proxy workload based on name, labels, and container images."""
    name_lower = name.lower()
    labels_str = " ".join(f"{k}={v}" for k, v in labels.items()).lower()
    images_str = " ".join(images).lower()
    combined = f"{name_lower} {labels_str} {images_str}"

    if "haproxy" in combined:
        return "haproxy", "HAProxy"
    if "envoy" in combined:
        return "envoy", "Envoy"
    if "nginx" in combined:
        if "backend" in name_lower and not any(k in combined for k in ("ingress", "proxy", "gateway")):
            return None, None
        return "nginx", "NGINX"
    if "traefik" in combined:
        return "traefik", "Traefik"
    if "kong" in combined:
        return "kong", "Kong"
    if "caddy" in combined:
        return "caddy", "Caddy"

    return None, None


def _deployment_configmap_names(dep: Any) -> set[str]:
    """Names of the ConfigMaps a Deployment mounts as volumes (its own config)."""
    spec = dep.spec.template.spec if dep.spec and dep.spec.template else None
    return {
        v.config_map.name
        for v in ((spec.volumes if spec else None) or [])
        if v.config_map and v.config_map.name
    }


# Server lines only (anchored): `default-server inter 2s` and friends are not backends.
_HAPROXY_SERVER_RE = re.compile(r"^\s*server\s+\S+\s+([a-zA-Z0-9_\-\.]+)(?::(\d+))?", re.MULTILINE)
_NGINX_PROXY_PASS_RE = re.compile(r"proxy_pass\s+https?://([a-zA-Z0-9_\-\.]+)(?::(\d+))?")
_ENVOY_ADDRESS_RE = re.compile(r"address:\s*[\"']?([a-zA-Z0-9_\-\.]+)[\"']?")
_ENVOY_PORT_RE = re.compile(r"port_value:\s*(\d+)")


def _service_backend(host: str, port_digits: str | None, namespace: str, via: str) -> dict[str, Any] | None:
    """Map a config host:port to a Service backend, or None if it is not one.

    IP literals (incl. 0.0.0.0 listeners and 127.0.0.1 admin) and localhost do
    not name a Service; a port must be 1..65535 (absent = 80). Digits are
    length-checked before int() so an unbounded run cannot raise ValueError.
    """
    port = 80 if port_digits is None else (int(port_digits) if len(port_digits) <= 5 else 0)
    if not 1 <= port <= 65535 or host.lower() == "localhost":
        return None
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        pass
    parts = host.split(".")
    svc_ns = parts[1] if len(parts) > 1 and parts[1] not in ("svc", "cluster", "local") else namespace
    return {"service": parts[0], "namespace": svc_ns, "port": port, "via": via}


def _parse_config_backends(
    content: str, fname: str, proxy_type: str, namespace: str, cm_name: str,
) -> list[dict[str, Any]]:
    """Parse one ConfigMap data value for Service backends."""
    found: list[dict[str, Any] | None] = []
    if proxy_type == "haproxy" or "haproxy" in fname.lower():
        via = f"HAProxy Config ({cm_name})"
        found = [
            _service_backend(m.group(1), m.group(2), namespace, via)
            for m in _HAPROXY_SERVER_RE.finditer(content)
        ]
    elif proxy_type == "nginx" or "nginx" in fname.lower() or "default.conf" in fname.lower():
        via = f"NGINX Config ({cm_name})"
        found = [
            _service_backend(m.group(1), m.group(2), namespace, via)
            for m in _NGINX_PROXY_PASS_RE.finditer(content)
        ]
    elif proxy_type == "envoy" or "envoy" in fname.lower():
        # address: <host> then port_value: <port>, line by line (linear time);
        # both may sit on one line in flow style ({address: x, port_value: n}).
        via = f"Envoy Config ({cm_name})"
        current_host: str | None = None
        for line in content.splitlines():
            addr_match = _ENVOY_ADDRESS_RE.search(line)
            if addr_match:
                current_host = addr_match.group(1)
                line = line[addr_match.end():]
            port_match = _ENVOY_PORT_RE.search(line) if current_host else None
            if current_host and port_match:
                found.append(_service_backend(current_host, port_match.group(1), namespace, via))
                current_host = None
    return [b for b in found if b]


# Controller-string substring -> workload hints (image / label) of that
# controller's own Deployments, for controllers whose image does not contain
# the controller string (Envoy Gateway, HAProxy ingress, NGINX Inc).
_CONTROLLER_WORKLOAD_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("envoyproxy.io", ("envoyproxy/gateway", "app.kubernetes.io/managed-by=envoy-gateway")),
    ("haproxy", ("haproxytech/kubernetes-ingress", "haproxy-ingress")),
    ("nginx.org", ("nginx/nginx-ingress",)),
)


def _is_covered_controller_workload(
    name: str,
    labels: dict[str, str],
    images: list[str],
    covered_controllers: set[str],
) -> bool:
    """True if a Deployment is (part of) a controller inventory already lists."""
    hay = " ".join([name, *images, *(f"{k}={v}" for k, v in labels.items())]).lower()
    for cov in covered_controllers:
        if not cov:
            continue
        if cov in hay:
            return True
        if any(sub in cov and any(h in hay for h in hints) for sub, hints in _CONTROLLER_WORKLOAD_HINTS):
            return True
    return False


def _extract_backends_from_configmaps(
    core: k8s_client.CoreV1Api,
    namespace: str,
    proxy_type: str,
    configmap_names: set[str],
) -> list[dict[str, Any]]:
    """Extract backend services from the named ConfigMaps (a proxy's own config)."""
    backends: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    if not configmap_names:
        return backends

    for cm in _safe_list_namespaced_configmaps(core, namespace):
        cm_name = cm.metadata.name or ""
        if cm_name not in configmap_names:
            continue
        try:
            for fname, content in (cm.data or {}).items():
                if not isinstance(content, str):
                    continue
                content = content[:_MAX_CONFIGMAP_PARSE_BYTES]
                for b in _parse_config_backends(content, fname, proxy_type, namespace, cm_name):
                    key = (b["service"], b["namespace"], b["port"])
                    if key not in seen:
                        seen.add(key)
                        backends.append(b)
        except Exception as e:
            logger.debug("Failed to parse ConfigMap %s/%s for backends: %s", namespace, cm_name, e)

    return backends


def _has_configmap_to_backend(
    core: k8s_client.CoreV1Api,
    deployments: list,
    target_svc_name: str,
    target_svc_ns: str,
    proxy_type: str = "haproxy",
) -> bool:
    """True if a proxy Deployment's own ConfigMap routes to the target Service."""
    for dep in deployments:
        backends = _extract_backends_from_configmaps(
            core, dep.metadata.namespace or "default", proxy_type, _deployment_configmap_names(dep),
        )
        if any(b["service"] == target_svc_name and b["namespace"] == target_svc_ns for b in backends):
            return True
    return False


def _has_target_deployment_match(
    db: Session | None,
    target: BenchmarkTarget,
    deployments: list,
    proxy_type: str = "haproxy",
) -> bool:
    """True if one of the live Deployments is this target's own Forge proxy.

    Exact identity only: the Deployment lives in the target's proxy namespace
    (where Forge installs it) and its name or Helm instance label equals the
    helm_release recorded on this target's ProxyDeployment. A row alone (without
    its live Deployment) never matches, so removed proxies drop out.
    """
    if not db or not getattr(target, "id", None):
        return False
    try:
        releases = {
            r for (r,) in db.query(ProxyDeployment.helm_release).filter(
                ProxyDeployment.target_id == target.id,
                ProxyDeployment.proxy_type == proxy_type,
                ProxyDeployment.helm_release.isnot(None),
            ).all()
        }
    except Exception as e:
        logger.debug("Failed to query ProxyDeployment for target %s: %s", target.id, e)
        return False
    proxy_ns = target.proxy_namespace or "perf-proxies"
    for d in deployments:
        if d.metadata.namespace != proxy_ns:
            continue
        labels = d.metadata.labels or {}
        if d.metadata.name in releases or labels.get("app.kubernetes.io/instance") in releases:
            return True
    return False
