"""
Cluster scanner package — detects prerequisites and BNK components.

Re-exports the ``ClusterScanner`` class for backward compatibility with
``from services.scanner import ClusterScanner`` and also allows
``from services.cluster_scanner import ClusterScanner`` via the shim.

Modules:
    constants        — PrerequisiteStatus and expected CRD sets
    fetch            — parallel K8s API data collection (only I/O module)
    nodes            — V1Node parsing
    prereqs          — prerequisite analyzers (cert-manager, Multus, etc.)
    bnk_install      — BNK installation analysis
    recommendations  — data-driven recommendation builder
"""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from services.bnk_pod_discovery import BNK_NAMESPACES
from services.kubernetes_service import KubernetesService
from services.platform_context_service import PlatformContextService
from services.scanner.bnk_install import analyze_bnk_install
from services.scanner.constants import PrerequisiteStatus
from services.scanner.fetch import fetch_scan_data
from services.scanner.prereqs import (
    analyze_cert_manager,
    analyze_cis,
    analyze_cluster_info,
    analyze_dpf,
    analyze_gateway_api,
    analyze_hugepages,
    analyze_kamaji,
    analyze_multus,
    analyze_sriov,
    analyze_storage,
)
from services.scanner.recommendations import build_proxy_recommendations, build_recommendations


class ClusterScanner:
    """
    Scans a Kubernetes cluster for installed prerequisites and BNK components.

    Orchestrates parallel data fetching and analysis via the scanner sub-modules.
    """

    def __init__(self, db: Session):
        self.db = db
        self.k8s_service = KubernetesService(db)

    def scan(self, cluster_id: int) -> dict[str, Any]:
        """
        Perform a full cluster scan.

        Returns a structured result with:
          - cluster_info: version, distribution, node count
          - prerequisites: cert-manager, multus, sriov, hugepages, storage, gateway_api
          - bnk_install: existing BNK installation status
          - recommendations: what needs to be deployed
          - scan_metadata: timing info
        """
        start_time = datetime.now(UTC)

        cluster = self.k8s_service.get_cluster(cluster_id)
        api_client = self.k8s_service.load_kubeconfig(cluster)

        # Seed BNK pod discovery from previously persisted namespaces (if any).
        # NULL / empty list → first run: static BNK_NAMESPACES seed is used.
        # Use getattr for forward-compat with test stubs that may not have this column.
        persisted_ns: list[str] = list(getattr(cluster, "discovered_namespaces", None) or [])

        # Parallel data collection (~1-2s)
        data = fetch_scan_data(
            api_client, self.k8s_service, cluster_id, extra_namespaces=persisted_ns
        )

        # Persist the namespaces where F5 components were found so subsequent
        # scans can use them as the fast-path seed (Issue #139).
        all_f5_pods = list(data.get("f5_tenant_pods") or []) + list(data.get("f5_utils_pods") or [])
        newly_discovered: list[str] = sorted({
            pod.get("namespace") for pod in all_f5_pods if pod.get("namespace")
        } | set(persisted_ns) | set(BNK_NAMESPACES))
        existing_ns = getattr(cluster, "discovered_namespaces", None) or []
        if newly_discovered != sorted(existing_ns):
            try:
                cluster.discovered_namespaces = newly_discovered
                self.db.flush()
            except AttributeError:
                pass  # test stub without this column — non-fatal

        # Analysis (pure functions — no I/O)
        cluster_info = analyze_cluster_info(
            cluster, data["version_info"], data["nodes"], data["namespaces"]
        )
        cert_manager = analyze_cert_manager(
            data["crds"], data["cert_manager_pods"], data["helm_releases"]
        )
        multus = analyze_multus(
            data["crds"], data["crd_names"], data["kube_system_pods"], data["daemonsets"]
        )
        sriov = analyze_sriov(data["nodes"], data["daemonsets"], data["kube_system_pods"])
        hugepages = analyze_hugepages(data["nodes"])
        storage = analyze_storage(data["storage_classes"])
        gateway_api = analyze_gateway_api(
            data["crds"], data["crd_names"], data["gateways"], data["gatewayclasses"]
        )
        dpf = analyze_dpf(
            data["crds"],
            data["crd_names"],
            data["dpf_operator_configs"],
            data["dpudevices"],
            data["dpusets"],
            data["dpuclusters"],
            data["dpuservices"],
            data["bfbs"],
            data["helm_releases"],
        )
        kamaji = analyze_kamaji(
            data["crds"],
            data["crd_names"],
            data["kamaji_pods"],
            data["kamaji_tcps"],
            data["helm_releases"],
        )
        cis = analyze_cis(
            data["cis_controllers"],
            data["cis_virtualservers"],
            data["cis_transportservers"],
            data["cis_ingresslinks"],
            data.get("cis_as3_configmaps") or [],
            data.get("cis_f5_ingresses") or [],
            data.get("openshift_routes") or [],
        )
        bnk_install = analyze_bnk_install(
            data["crds"],
            data["crd_names"],
            data["crd_groups"],
            data["f5_tenant_pods"],
            data["f5_utils_pods"],
            data["helm_releases"],
            data["cneinstances"],
            data["vlans"],
            data["namespaces"],
        )

        # ADR-494 Phase B: persist the running BNK release line identified by this scan.
        # FLO chart version (e.g. "2.21.13-0.0.28") resolves to a release-line registry
        # row (version-line granularity, not exact build).  Unrecognised versions upsert
        # an observed row so the information is preserved for the drift signal.
        try:
            from services.bnk_upgrade_service import detect_current_bnk_version
            from services.release_registry_service import ReleaseRegistryService

            running_flo = detect_current_bnk_version(bnk_install)
            if running_flo is not None:
                registry = ReleaseRegistryService(self.db)
                ga = registry.resolve_ga(flo_version=running_flo)
                # SAVEPOINT: if get_or_create_observed's internal flush raises a
                # DB-level error, only this savepoint is rolled back, leaving the
                # outer session intact for the subsequent platform-context flush.
                with self.db.begin_nested():
                    cluster.running_release_id = (
                        ga.release_id if ga is not None else registry.get_or_create_observed(running_flo)
                    )
        except Exception as exc:
            # Broad except is deliberate: discovery write-back must never fail the scan
            # (mirrors the adjacent proxy-inventory pattern above).
            logger.warning("running_release_id write-back failed (non-fatal): %s", exc)

        # Proxy inventory (deliberate exception to "fetch is the only I/O module":
        # reuse outranks one-I/O-module here; proxy I/O lives in ProxyDiscoveryService)
        existing_proxies: dict[str, Any] = {"status": "none", "proxies": [], "discovered_count": 0, "total_scanned": 0}
        try:
            from services.proxy_discovery_service import ProxyDiscoveryService
            proxy_svc = ProxyDiscoveryService(self.db)
            proxy_items = proxy_svc.discover_inventory(api_client)
            existing_proxies = {
                "status": "detected" if proxy_items else "none",
                "proxies": proxy_items,
                "discovered_count": len(proxy_items),
                "total_scanned": len(proxy_items),
            }
        except Exception as exc:
            logger.warning("Proxy inventory failed (non-fatal): %s", exc)

        platform_context = PlatformContextService.apply_cluster_context(
            cluster,
            {
                "cluster_info": cluster_info,
                "crd_groups": data["crd_groups"],
                "prerequisites": {
                    "multus": multus,
                    "sriov": sriov,
                    "hugepages": hugepages,
                    "gateway_api": gateway_api,
                },
            },
        )
        self.db.flush()

        from services.scanner.recommendations import resolve_enabled_prereqs

        enabled_prereq_set = resolve_enabled_prereqs(cluster.enabled_prerequisites)

        recommendations = build_recommendations(
            cert_manager,
            multus,
            sriov,
            hugepages,
            storage,
            gateway_api,
            bnk_install,
            dpf,
            kamaji,
            platform_context.detected_platform_profile,
            platform_context.to_dict(),
            enabled_prereq_set,
        )
        recommendations.extend(build_proxy_recommendations(existing_proxies))

        end_time = datetime.now(UTC)
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        # Issue #194: record when this cluster was last successfully scanned so
        # "never scanned" (last_synced_at IS NULL) is distinguishable from
        # "scanned and genuinely empty". Set only after all analysis has
        # completed — a scan that raises earlier must NOT stamp a sync time.
        # Every scan path (registration/PUT async task, the /scan endpoint,
        # upgrade pre-checks) flows through here, so this is the single place
        # that keeps last_synced_at honest. Flushed here; the caller commits.
        cluster.last_synced_at = end_time
        self.db.flush()

        return {
            "cluster_id": cluster_id,
            "cluster_name": cluster.name,
            "cluster_info": cluster_info,
            "prerequisites": {
                "cert_manager": cert_manager,
                "multus": multus,
                "sriov": sriov,
                "hugepages": hugepages,
                "storage": storage,
                "gateway_api": gateway_api,
                "dpf": dpf,
                "kamaji": kamaji,
                "existing_proxies": existing_proxies,
                "cis": cis,
            },
            "bnk_install": bnk_install,
            "recommendations": recommendations,
            # Effective enabled-prereq set for this cluster, so the UI can
            # render only the prerequisite status cards the user opted into.
            "enabled_prerequisites": sorted(enabled_prereq_set),
            "scan_metadata": {
                "scanned_at": start_time.isoformat(),
                "duration_ms": duration_ms,
                "api_calls": 22,
            },
            "platform_context": platform_context.to_dict(),
        }


__all__ = [
    "ClusterScanner",
    "PrerequisiteStatus",
    # Analysis functions (for direct use / testing)
    "analyze_cluster_info",
    "analyze_cert_manager",
    "analyze_multus",
    "analyze_sriov",
    "analyze_hugepages",
    "analyze_storage",
    "analyze_gateway_api",
    "analyze_dpf",
    "analyze_kamaji",
    "analyze_cis",
    "analyze_bnk_install",
    "build_recommendations",
    "build_proxy_recommendations",
    "fetch_scan_data",
]
