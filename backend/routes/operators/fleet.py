"""
Fleet health and comparison routes.

D3: Fleet health is kubeconfig-based — queries clusters directly via
bnk_data_service instead of relying on operator health reports.
Operators are optional enrichment, not the data source.
"""
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from threading import Lock

from dateutil.parser import isoparse
from fastapi import APIRouter, Depends
from kubernetes import client as k8s_client
from sqlalchemy.orm import Session

from core.k8s_types import ApiGroups
from database import get_db
from models.kubernetes import KubernetesCluster
from routes.auth import require_operator, require_viewer
from services.bnk_data_service import analyze_health, fetch_all_bnk_data
from services.connectivity_probe_service import _parse_api_server, _probe_tcp
from services.dpf.fetch import detect_dpf
from services.kubernetes_service import KubernetesService
from services.operator_registry import (
    list_operators,
    operator_connections,
)
from services.platform_context_service import PlatformContextService

from . import (
    FleetCompareClusterResponse,
    FleetCompareRequest,
    FleetCompareResponse,
    FleetHealthResponse,
    _dt_to_str,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _build_fleet_compare_platform_context(
    cluster_a: KubernetesCluster,
    cluster_b: KubernetesCluster,
) -> dict[str, object]:
    """Build shared platform-context payload for Fleet comparison truthfulness."""
    context_a = PlatformContextService.serialize_cluster_context(cluster_a)
    context_b = PlatformContextService.serialize_cluster_context(cluster_b)

    profile_a = context_a.detected_platform_profile
    profile_b = context_b.detected_platform_profile
    mixed_profiles = (
        profile_a != "unknown"
        and profile_b != "unknown"
        and profile_a != profile_b
    )

    caveats: list[str] = []
    support_semantics: list[str] = []
    if mixed_profiles:
        caveats.append(
            "Compared clusters use different detected platform profiles. "
            "Interpret differences with platform capability/constraint context."
        )
        support_semantics.append(
            "Runtime support semantics should follow each cluster's detected platform context, "
            "not only project intent or naming similarity."
        )

    return {
        "cluster_a": {
            "name": cluster_a.name,
            "detected_platform_profile": context_a.detected_platform_profile,
            "detected_platform_provider": context_a.detected_platform_provider,
            "platform_capabilities": context_a.platform_capabilities,
            "platform_constraints": context_a.platform_constraints,
        },
        "cluster_b": {
            "name": cluster_b.name,
            "detected_platform_profile": context_b.detected_platform_profile,
            "detected_platform_provider": context_b.detected_platform_provider,
            "platform_capabilities": context_b.platform_capabilities,
            "platform_constraints": context_b.platform_constraints,
        },
        "mixed_platform_profiles": mixed_profiles,
        "comparison_caveats": caveats,
        "support_semantics": support_semantics,
    }


def _build_fleet_health_platform_context(clusters: list[KubernetesCluster]) -> dict[str, object]:
    """Build fleet-level platform context summary for mixed-environment truthfulness."""
    cluster_contexts = []
    profiles: set[str] = set()
    for cluster in clusters:
        context = PlatformContextService.serialize_cluster_context(cluster)
        profile = context.detected_platform_profile
        if profile and profile != "unknown":
            profiles.add(profile)
        cluster_contexts.append({
            "cluster_id": cluster.id,
            "cluster_name": cluster.name,
            "detected_platform_profile": context.detected_platform_profile,
            "detected_platform_provider": context.detected_platform_provider,
        })

    mixed_profiles = len(profiles) > 1
    caveats: list[str] = []
    support_semantics: list[str] = []
    if mixed_profiles:
        caveats.append(
            "Fleet contains mixed detected platform profiles. "
            "Cross-cluster status and diffs should be interpreted with platform context."
        )
        support_semantics.append(
            "Support/readiness semantics are platform-aware and may differ between clusters "
            "in this fleet."
        )

    return {
        "mixed_platform_profiles": mixed_profiles,
        "detected_profiles": sorted(profiles),
        "clusters": cluster_contexts,
        "comparison_caveats": caveats,
        "support_semantics": support_semantics,
    }


def _resource_key_to_ref(resource_key: str) -> dict[str, str | None]:
    """Convert flattened resource key (Kind/ns/name) into structured fields."""
    parts = resource_key.split("/")
    if len(parts) == 3:
        kind, namespace, name = parts
        return {"kind": kind, "namespace": namespace, "name": name}
    if len(parts) == 2:
        kind, name = parts
        return {"kind": kind, "namespace": None, "name": name}
    return {"kind": "Unknown", "namespace": None, "name": resource_key}


# ---------------------------------------------------------------------------
# Fleet Dashboard Helpers (D3: kubeconfig-based)
# ---------------------------------------------------------------------------


def _derive_status_from_health(health: dict) -> str:
    """Derive fleet status (healthy/warning/critical) from analyze_health() output."""
    overall = health.get("overall", "unknown")
    # analyze_health returns: healthy, warning, critical, unknown
    if overall in ("healthy", "warning", "critical", "unknown"):
        return overall
    return "unknown"


def _extract_bnk_version(data: dict) -> str | None:
    """
    Extract BNK version from TMM pod container images.

    TMM image tags follow the pattern: `<registry>/spk-tmm:v2.5.0-0.0.5`
    We extract the semver portion (e.g., "2.5.0").
    Falls back to FLO/controller images if TMM not found.
    """
    classified = data.get("classified_pods", {})
    # Try TMM first, then FLO, then controller
    for pod_type in ("tmm", "flo", "controller"):
        for pod in classified.get(pod_type, []):
            for container in pod.get("containers", []):
                image = container.get("image", "")
                # Match version tag: :v1.2.3 or :1.2.3 (with optional build suffix)
                m = re.search(r":v?(\d+\.\d+\.\d+)", image)
                if m:
                    return m.group(1)
    return None


def _extract_uptime_seconds(data: dict) -> int:
    """
    Compute cluster uptime from the oldest running TMM or FLO pod start time.

    Returns seconds since the oldest BNK workload pod started.
    """
    classified = data.get("classified_pods", {})
    now = datetime.now(UTC)
    oldest_start = None

    for pod_type in ("tmm", "flo"):
        for pod in classified.get(pod_type, []):
            if pod.get("phase") != "Running":
                continue
            start_str = pod.get("startTime")
            if not start_str:
                continue
            try:
                start_dt = isoparse(start_str)
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=UTC)
                if oldest_start is None or start_dt < oldest_start:
                    oldest_start = start_dt
            except (ValueError, TypeError):
                continue

    if oldest_start is None:
        return 0
    return max(0, int((now - oldest_start).total_seconds()))


def _extract_health_metrics(health: dict, data: dict) -> dict:
    """Extract fleet-relevant metrics from analyze_health() output + raw data."""
    counts = health.get("counts", {})

    # BNK version from pod images
    bnk_version = _extract_bnk_version(data)

    # Uptime from oldest running BNK pod
    uptime_secs = _extract_uptime_seconds(data)

    # Count component health from all sections + per-section detail
    health_summary = {"healthy": 0, "warning": 0, "critical": 0}
    section_labels = {
        "platform": "Platform",
        "dataPlane": "Data Plane",
        "networking": "Networking",
        "security": "Security",
    }
    # Sub-component labels for human-friendly messages
    _sub_labels = {
        "flo": "FLO operator",
        "controller": "Controller",
        "crdInstaller": "CRD Installer",
        "analyzer": "Analyzer",
        "tmm": "TMM pods",
        "cneInstance": "CNE Instance",
    }
    health_issues: list[dict[str, str]] = []
    for section_key in ("platform", "dataPlane", "networking", "security"):
        section = health.get(section_key, {})
        severity = section.get("severity", "unknown")
        if severity in health_summary:
            health_summary[severity] += 1
        if severity in ("warning", "critical"):
            # Find which sub-components are unhealthy for a specific message
            bad_subs = []
            for sub_key, sub_val in section.items():
                if isinstance(sub_val, dict) and sub_val.get("severity") in ("warning", "critical"):
                    label = _sub_labels.get(sub_key, sub_key)
                    total = sub_val.get("total", 0)
                    running = sub_val.get("running", sub_val.get("completed", 0))
                    if total > 0:
                        bad_subs.append(f"{label} ({running}/{total})")
                    else:
                        bad_subs.append(label)
            section_label = section_labels.get(section_key, section_key)
            if bad_subs:
                message = f"{section_label}: {', '.join(bad_subs)}"
            else:
                message = f"{section_label} is {severity}"
            health_issues.append({
                "component": section_label,
                "severity": severity,
                "message": message,
            })

    # If the cluster is reachable but no BNK signals were detected at all
    # (fetch_all_bnk_data returned empty without raising — e.g. plain OCP with
    # no BNK CRDs installed), every section comes back "unknown" and the loop
    # above produces no health_issues. Surface an explicit reason so the UI
    # isn't a warning badge with nothing to say.
    if not health_issues and health.get("overall") == "unknown":
        health_issues.append({
            "component": "BNK",
            "severity": "warning",
            "message": "BNK not installed or no BNK resources detected",
        })

    return {
        "bnk_version": bnk_version,
        "route_count": counts.get("httpRoutes", 0),
        "tmm_count": counts.get("tmm_pods", 0),
        "gateway_count": counts.get("gateways", 0),
        "uptime_seconds": uptime_secs,
        "health_summary": health_summary,
        "health_issues": health_issues,
    }


def _query_dpf_summary(k8s_service, cluster_id: int) -> dict:
    """Lightweight DPF detection for fleet view — no heavy fetch."""
    try:
        dpf = detect_dpf(k8s_service, cluster_id)
        if not dpf.get("detected"):
            return {
                "dpf_detected": False,
                "dpf_version": None,
                "dpf_status": None,
                "dpu_count": 0,
                "dpu_cluster_count": 0,
            }
        op = dpf.get("operator", {})
        status = "ready" if op.get("ready") else ("partial" if op.get("configured") else "not_installed")
        return {
            "dpf_detected": True,
            "dpf_version": dpf.get("version"),
            "dpf_status": status,
            "dpu_count": dpf.get("devices", {}).get("total", 0),
            "dpu_cluster_count": dpf.get("dpuclusters", 0),
        }
    except Exception:
        return {
            "dpf_detected": False,
            "dpf_version": None,
            "dpf_status": None,
            "dpu_count": 0,
            "dpu_cluster_count": 0,
        }


# BNK API groups — presence of any of these indicates BNK CRDs are installed.
# Gateway API alone is not a BNK signal (it exists independently, e.g. Istio).
_BNK_API_GROUPS: frozenset[str] = frozenset({
    ApiGroups.F5_NET,
    ApiGroups.F5_K8S,
    ApiGroups.F5_GATEWAY_NET,
})

# DPF API groups — presence of any of these indicates NVIDIA DOCA Platform
# Framework CRDs are installed. Used to gate the DPF discovery probe so we
# don't hit /apis/provisioning.dpu.nvidia.com on every poll for clusters
# that have no DPUs.
_DPF_API_GROUPS: frozenset[str] = frozenset({
    ApiGroups.DPF_PROVISIONING,
    ApiGroups.DPF_SERVICE,
    ApiGroups.DPF_OPERATOR,
})

# Per-cluster cache of the full set of registered API groups. One discovery
# call answers multiple presence questions (BNK, DPF, ...), cached for a
# minute so the cheap /apis call doesn't repeat on every 30s poll.
_API_GROUPS_TTL_SEC = 60.0
_api_groups_cache: dict[int, tuple[float, frozenset[str]]] = {}
_api_groups_lock = Lock()


def _cluster_api_groups(cluster: KubernetesCluster, db: Session) -> frozenset[str] | None:
    """
    Return the set of API group names registered on the cluster.

    Cached per cluster for _API_GROUPS_TTL_SEC. Returns None on discovery
    failure so callers can decide whether to fall through (run the full
    fetch) or short-circuit.
    """
    now = time.monotonic()
    cluster_id = int(cluster.id)

    with _api_groups_lock:
        cached = _api_groups_cache.get(cluster_id)
        if cached and (now - cached[0]) < _API_GROUPS_TTL_SEC:
            return cached[1]

    try:
        k8s_service = KubernetesService(db)
        api_client = k8s_service.load_kubeconfig(cluster)
        apis = k8s_client.ApisApi(api_client).get_api_versions()
        groups = frozenset(g.name for g in (apis.groups or []))
    except Exception as e:
        logger.warning(
            f"Fleet: API group discovery failed for cluster {cluster.name} "
            f"(id={cluster_id}): {e}"
        )
        return None

    with _api_groups_lock:
        _api_groups_cache[cluster_id] = (now, groups)
    return groups


def _cluster_has_bnk_api_groups(cluster: KubernetesCluster, db: Session) -> bool:
    """
    Check whether the cluster has any BNK-specific API groups registered.

    Used as a fast short-circuit for fleet health: a cluster without any BNK
    API groups can't have BNK resources, so we skip the ~20-call CRD fetch
    burst and return "BNK not installed" directly.

    On discovery failure we return True (fall through to the full fetch) so
    a broken discovery call never silently hides BNK that's actually present.
    """
    groups = _cluster_api_groups(cluster, db)
    if groups is None:
        return True
    return bool(_BNK_API_GROUPS & groups)


def _cluster_has_dpf_api_groups(cluster: KubernetesCluster, db: Session) -> bool:
    """
    Check whether the cluster has any DPF (NVIDIA DOCA Platform Framework)
    API groups registered.

    Used to gate the DPF discovery probe — clusters without DPUs shouldn't
    be calling /apis/provisioning.dpu.nvidia.com on every 30s poll. On
    discovery failure we return False so we DON'T probe — erring on the
    side of skipping expensive calls rather than repeating 404s.
    """
    groups = _cluster_api_groups(cluster, db)
    if groups is None:
        return False
    return bool(_DPF_API_GROUPS & groups)


# Fleet-health response cache — avoids re-running the full multi-cluster
# fetch burst when multiple tabs/users poll the endpoint within a short window.
# Keyed by (sorted cluster IDs tuple, max updated_at) so create/update/delete
# of a cluster naturally invalidates the cache without needing explicit hooks.
_FLEET_HEALTH_TTL_SEC = 15.0
_fleet_health_cache: dict[tuple, tuple[float, dict]] = {}
_fleet_health_lock = Lock()


_BNK_NOT_INSTALLED_RESULT = {
    "status": "warning",
    "bnk_severity": "warning",
    "effective_connectivity_status": "connected",
    "reachable": True,
    "bnk_version": None,
    "route_count": 0,
    "tmm_count": 0,
    "gateway_count": 0,
    "uptime_seconds": 0,
    "health_summary": {"healthy": 0, "warning": 1, "critical": 0},
    "health_issues": [{
        "component": "BNK",
        "severity": "warning",
        "message": "BNK not installed or no BNK resources detected",
    }],
    "dpf_detected": False,
    "dpf_version": None,
    "dpf_status": None,
    "dpu_count": 0,
    "dpu_cluster_count": 0,
}

# Empty DPF summary — returned when the cluster has no DPF API groups so
# we skip the detect_dpf probe entirely. Keeps the fleet row shape stable.
_DPF_NOT_INSTALLED_SUMMARY = {
    "dpf_detected": False,
    "dpf_version": None,
    "dpf_status": None,
    "dpu_count": 0,
    "dpu_cluster_count": 0,
}

_OFFLINE_RESULT = {
    "status": "offline",
    "bnk_severity": "unknown",
    "effective_connectivity_status": "unreachable",
    "reachable": False,
    "bnk_version": None,
    "route_count": 0,
    "tmm_count": 0,
    "gateway_count": 0,
    "uptime_seconds": 0,
    "health_summary": {"healthy": 0, "warning": 0, "critical": 0},
    "health_issues": [],
    "dpf_detected": False,
    "dpf_version": None,
    "dpf_status": None,
    "dpu_count": 0,
    "dpu_cluster_count": 0,
}

# TCP pre-check timeout — fast fail for port-blocked clusters.
# 10s accommodates slower on-prem/tunneled clusters while still
# short-circuiting truly unreachable hosts before the 60s K8s timeout.
_TCP_PRECHECK_TIMEOUT = 10


def _query_cluster_health(
    cluster: KubernetesCluster,
    db: Session,
) -> dict:
    """
    Query a single cluster's BNK + DPF health via kubeconfig.

    Returns a dict with status, health metrics, DPF summary, and cluster metadata.
    Handles errors gracefully — returns "offline" status on failure.

    Uses a fast TCP pre-check to skip clusters where the API port is blocked
    (avoids 60s+ K8s client timeout for unreachable clusters).
    SSH-tunneled and cloud-managed (EKS/AKS/GKE) clusters skip the pre-check
    — SSH routes through the tunnel, and cloud API servers behind LBs may
    block raw TCP probes but are reachable via HTTPS with kubeconfig auth.

    The detailed connectivity reason (port_blocked vs unreachable) is provided
    by the separate /api/k8s/clusters/connectivity endpoint — Fleet fetches
    that in parallel on the frontend for tooltip details.
    """
    base_metadata = {
        "cloud_provider": getattr(cluster, "cloud_provider", None),
        "region": getattr(cluster, "region", None),
        "account_id": getattr(cluster, "account_id", None),
        "discovery_status": getattr(cluster, "discovery_status", None),
    }

    # Fast TCP pre-check: if port is blocked, skip the expensive K8s fetch.
    # Skip for:
    #   - SSH-tunneled clusters (api_server only reachable via the tunnel)
    #   - Cloud-managed clusters (EKS/AKS/GKE) where the API server is behind
    #     a cloud LB that may block raw TCP probes from outside the VPC, but
    #     the K8s API is reachable via HTTPS with kubeconfig auth (STS tokens, etc.)
    _cloud_providers = {"aws", "azure", "gcp"}
    _skip_tcp = (
        cluster.ssh_tunnel_enabled
        or (cluster.cloud_provider or "").lower() in _cloud_providers
    )
    if not _skip_tcp:
        host, port = _parse_api_server(cluster.api_server)
        if host:
            tcp = _probe_tcp(host, port, timeout=_TCP_PRECHECK_TIMEOUT)
            if not tcp.get("open"):
                logger.info(
                    f"Fleet: cluster {cluster.name} (id={cluster.id}) TCP port {port} "
                    f"unreachable — marking offline (skipped K8s fetch)"
                )
                return {**_OFFLINE_RESULT, **base_metadata}

    try:
        k8s_service = KubernetesService(db)
        # Quick reachability check via K8s API before expensive BNK fetch
        try:
            k8s_service.test_connection(cluster.id)
        except Exception as e:
            logger.warning(f"Fleet: cluster {cluster.name} (id={cluster.id}) K8s API unreachable: {e}")
            return {**_OFFLINE_RESULT, **base_metadata}

        # Cheap short-circuits: one discovery call (cached) answers both
        # "does this cluster have BNK?" and "does it have DPF?". Skip the
        # heavy probes for absent frameworks.
        has_bnk = _cluster_has_bnk_api_groups(cluster, db)
        has_dpf = _cluster_has_dpf_api_groups(cluster, db)

        dpf_summary = (
            _query_dpf_summary(k8s_service, cluster.id)
            if has_dpf
            else dict(_DPF_NOT_INSTALLED_SUMMARY)
        )

        if not has_bnk:
            return {**_BNK_NOT_INSTALLED_RESULT, **dpf_summary, **base_metadata}

        try:
            data = fetch_all_bnk_data(k8s_service, cluster.id)
            health = analyze_health(data)
            status = _derive_status_from_health(health)
            metrics = _extract_health_metrics(health, data)
        except Exception as e:
            # Cluster is reachable but BNK data fetch failed unexpectedly.
            # (The no-BNK-installed case is now handled by the short-circuit above.)
            logger.info(f"Fleet: cluster {cluster.name} (id={cluster.id}) BNK fetch failed: {e}")
            return {**_BNK_NOT_INSTALLED_RESULT, **dpf_summary, **base_metadata}

        return {
            "status": status,
            "bnk_severity": health.get("overall", status),
            "effective_connectivity_status": "connected",
            "reachable": True,
            **metrics,
            **dpf_summary,
            **base_metadata,
        }
    except Exception as e:
        logger.warning(f"Fleet health query failed for cluster {cluster.name} (id={cluster.id}): {e}")
        return {**_OFFLINE_RESULT, **base_metadata}


# ---------------------------------------------------------------------------
# Fleet Endpoints
# ---------------------------------------------------------------------------

@router.get("/fleet-health", response_model=FleetHealthResponse, dependencies=[Depends(require_viewer)])
def get_fleet_health(db: Session = Depends(get_db)):
    """
    Aggregate fleet health for all clusters via kubeconfig (D3).

    Queries every KubernetesCluster record in parallel using
    bnk_data_service.analyze_health() — no operator required.
    If an operator is linked to a cluster, its metadata is included.

    Returns status, BNK version, route/TMM/gateway counts, and health
    summary for each cluster. Used by the Fleet Dashboard (UX-012).

    Cached per-process for ~15s; the cache key includes the sorted cluster
    ID set + max updated_at so cluster create/update/delete invalidate
    naturally without needing explicit cache-busting hooks.
    """
    clusters = db.query(KubernetesCluster).all()

    # Cache lookup — key on (cluster IDs, max updated_at). A changed cluster
    # set or any cluster row update produces a new key, bypassing stale cache.
    max_updated = max(
        (c.updated_at for c in clusters if c.updated_at is not None),
        default=None,
    )
    cache_key = (
        tuple(sorted(int(c.id) for c in clusters)),
        max_updated.isoformat() if max_updated else None,
    )
    now_mono = time.monotonic()
    with _fleet_health_lock:
        cached = _fleet_health_cache.get(cache_key)
        if cached and (now_mono - cached[0]) < _FLEET_HEALTH_TTL_SEC:
            return cached[1]

    if not clusters:
        empty_response = {
            "total_clusters": 0,
            "healthy": 0,
            "warning": 0,
            "critical": 0,
            "offline": 0,
            "unknown": 0,
            "operators": [],
            "platform_context": {
                "mixed_platform_profiles": False,
                "detected_profiles": [],
                "clusters": [],
                "comparison_caveats": [],
                "support_semantics": [],
            },
        }
        with _fleet_health_lock:
            _fleet_health_cache[cache_key] = (now_mono, empty_response)
        return empty_response

    # Build operator lookup: cluster_id → operator record
    all_ops = list_operators(db)
    op_by_cluster: dict[int, object] = {}
    for op in all_ops:
        if op.cluster_id:
            op_by_cluster[op.cluster_id] = op

    # Query all clusters in parallel (30s per-cluster, 60s overall safety timeout)
    cluster_results: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=min(10, len(clusters))) as executor:
        futures = {
            executor.submit(_query_cluster_health, cluster, db): cluster
            for cluster in clusters
        }
        try:
            for future in as_completed(futures, timeout=60):
                cluster = futures[future]
                try:
                    cluster_results[cluster.id] = future.result(timeout=30)
                except Exception as e:
                    logger.error(f"Unexpected error querying cluster {cluster.name}: {e}")
                    cluster_results[cluster.id] = {**_OFFLINE_RESULT}
        except TimeoutError:
            # Overall timeout — mark remaining clusters as offline
            for future, cluster in futures.items():
                if cluster.id not in cluster_results:
                    logger.warning(f"Fleet: cluster {cluster.name} timed out — marking offline")
                    cluster_results[cluster.id] = {**_OFFLINE_RESULT}

    # Build response
    operators_out = []
    counts = {"healthy": 0, "warning": 0, "critical": 0, "offline": 0, "unknown": 0}

    for cluster in clusters:
        result = cluster_results.get(cluster.id, {})
        status = result.get("status", "offline")
        counts[status] = counts.get(status, 0) + 1
        platform_context = PlatformContextService.serialize_cluster_context(cluster)

        # Uptime from BNK pod start times (kubeconfig-derived)
        uptime_secs = result.get("uptime_seconds", 0)

        # Enrich with operator metadata if linked
        linked_op = op_by_cluster.get(cluster.id)
        if linked_op:
            is_connected_ws = operator_connections.is_connected(linked_op.operator_id)
            is_connected_polling = False
            if linked_op.connectivity_mode == "polling" and linked_op.last_heartbeat_at:
                heartbeat_age = (datetime.now(UTC) - linked_op.last_heartbeat_at).total_seconds()
                is_connected_polling = heartbeat_age < 60
            _is_connected = is_connected_ws or is_connected_polling

            operator_version = linked_op.operator_version
            connectivity_mode = linked_op.connectivity_mode or "direct_ws"
            last_seen = _dt_to_str(linked_op.last_heartbeat_at or linked_op.last_connected_at)
            operator_id = linked_op.id
            operator_uuid = linked_op.operator_id
            k8s_version = linked_op.kubernetes_version or cluster.version
            node_count = linked_op.node_count
        else:
            operator_version = None
            connectivity_mode = "kubeconfig"
            last_seen = _dt_to_str(cluster.last_synced_at)
            operator_id = cluster.id  # use cluster ID as stand-in
            operator_uuid = f"cluster-{cluster.id}"
            k8s_version = cluster.version
            node_count = None

        # Format uptime string
        if uptime_secs < 60:
            uptime_str = f"{uptime_secs}s" if uptime_secs > 0 else "--"
        elif uptime_secs < 3600:
            uptime_str = f"{uptime_secs // 60}m"
        elif uptime_secs < 86400:
            uptime_str = f"{uptime_secs // 3600}h {(uptime_secs % 3600) // 60}m"
        else:
            uptime_str = f"{uptime_secs // 86400}d {(uptime_secs % 86400) // 3600}h"

        operators_out.append({
            "operator_id": operator_id,
            "operator_uuid": operator_uuid,
            "cluster_id": cluster.id,
            "cluster_name": cluster.name,
            "status": status,
            "bnk_severity": result.get("bnk_severity", status),
            "effective_connectivity_status": result.get(
                "effective_connectivity_status",
                "connected" if result.get("reachable") else "unreachable",
            ),
            "last_seen": last_seen,
            "bnk_version": result.get("bnk_version"),
            "route_count": result.get("route_count", 0),
            "tmm_count": result.get("tmm_count", 0),
            "gateway_count": result.get("gateway_count", 0),
            "uptime": uptime_str,
            "health_summary": result.get("health_summary", {"healthy": 0, "warning": 0, "critical": 0}),
            "health_issues": result.get("health_issues", []),
            "kubernetes_version": k8s_version,
            "node_count": node_count,
            "operator_version": operator_version,
            "connectivity_mode": connectivity_mode,
            "dpf_detected": result.get("dpf_detected", False),
            "dpf_version": result.get("dpf_version"),
            "dpf_status": result.get("dpf_status"),
            "dpu_count": result.get("dpu_count", 0),
            "dpu_cluster_count": result.get("dpu_cluster_count", 0),
            "cloud_provider": getattr(cluster, "cloud_provider", None),
            "region": getattr(cluster, "region", None),
            "account_id": getattr(cluster, "account_id", None),
            "discovery_status": getattr(cluster, "discovery_status", None),
            "detected_platform_profile": platform_context.detected_platform_profile,
            "detected_platform_provider": platform_context.detected_platform_provider,
        })

    response = {
        "total_clusters": len(clusters),
        "healthy": counts["healthy"],
        "warning": counts["warning"],
        "critical": counts["critical"],
        "offline": counts["offline"],
        "unknown": counts["unknown"],
        "operators": operators_out,
        "platform_context": _build_fleet_health_platform_context(clusters),
    }
    with _fleet_health_lock:
        # Opportunistically trim stale entries so the cache can't grow unbounded
        # if cluster sets churn a lot (each distinct key would accumulate).
        stale_cutoff = now_mono - _FLEET_HEALTH_TTL_SEC
        for key in [k for k, (t, _) in _fleet_health_cache.items() if t < stale_cutoff]:
            _fleet_health_cache.pop(key, None)
        _fleet_health_cache[cache_key] = (now_mono, response)
    return response


@router.post(
    "/fleet/compare",
    response_model=FleetCompareClusterResponse | FleetCompareResponse,
    dependencies=[Depends(require_operator)],
)
def fleet_compare_configs(
    body: FleetCompareRequest,
    db: Session = Depends(get_db),
):
    """
    Compare BNK configuration between two clusters.

    D3: Uses cluster IDs directly (from the fleet health response's
    operator_id field, which maps to cluster.id for kubeconfig clusters).
    Falls back to operator health report diff if clusters aren't accessible.
    """
    # Try direct cluster-based diff first
    cluster_a = db.query(KubernetesCluster).filter(KubernetesCluster.id == body.operator_a_id).first()
    cluster_b = db.query(KubernetesCluster).filter(KubernetesCluster.id == body.operator_b_id).first()

    if cluster_a and cluster_b:
        try:
            from services.config_export_service import diff_configs, export_cluster_config
            config_a = export_cluster_config(cluster_a.id, db)
            config_b = export_cluster_config(cluster_b.id, db)
            diff_result = diff_configs(config_a, config_b)
            diff_result["comparison_mode"] = "cluster_config"
            diff_result["resources"] = {
                "only_in_a": [_resource_key_to_ref(key) for key in diff_result["resources"]["only_in_a"]],
                "only_in_b": [_resource_key_to_ref(key) for key in diff_result["resources"]["only_in_b"]],
                "changed": diff_result["resources"]["changed"],
            }
            diff_result["operator_a"] = cluster_a.name
            diff_result["operator_b"] = cluster_b.name
            diff_result["platform_context"] = _build_fleet_compare_platform_context(cluster_a, cluster_b)
            return diff_result
        except Exception as e:
            logger.warning("Cluster config diff failed; falling back to health report: %s", e)

    # Fallback: try operator-based comparison
    from models import ConnectedOperator

    op_a = db.query(ConnectedOperator).filter(ConnectedOperator.id == body.operator_a_id).first()
    op_b = db.query(ConnectedOperator).filter(ConnectedOperator.id == body.operator_b_id).first()

    if not op_a and not op_b:
        # Neither cluster nor operator found
        name_a = cluster_a.name if cluster_a else f"ID {body.operator_a_id}"
        name_b = cluster_b.name if cluster_b else f"ID {body.operator_b_id}"
        return {
            "operator_a": name_a,
            "operator_b": name_b,
            "comparison_mode": "health_report",
            "total_diffs": 0,
            "summary": "Unable to compare — no health data available",
            "differences": [],
            "platform_context": None,
        }

    # Operator health report fallback
    report_a = (op_a.last_health_report if op_a else {}) or {}
    report_b = (op_b.last_health_report if op_b else {}) or {}

    bnk_a = report_a.get("bnk", {})
    bnk_b = report_b.get("bnk", {})

    differences = []
    compare_keys = [
        ("bnk_version", "BNK Version"),
        ("tmm_total", "TMM Replicas"),
        ("tmm_ready", "TMM Ready"),
        ("routes_count", "Route Count"),
        ("gateway_count", "Gateway Count"),
        ("flo_ready", "FLO Ready"),
        ("installed", "BNK Installed"),
    ]

    for key, label in compare_keys:
        val_a = bnk_a.get(key)
        val_b = bnk_b.get(key)
        if val_a != val_b:
            differences.append({
                "field": label,
                "key": key,
                "value_a": val_a,
                "value_b": val_b,
            })

    name_a = (op_a.cluster_name if op_a else cluster_a.name) if (op_a or cluster_a) else f"ID {body.operator_a_id}"
    name_b = (op_b.cluster_name if op_b else cluster_b.name) if (op_b or cluster_b) else f"ID {body.operator_b_id}"

    compare_response = {
        "operator_a": name_a,
        "operator_b": name_b,
        "comparison_mode": "health_report",
        "total_diffs": len(differences),
        "summary": f"{len(differences)} difference(s) between {name_a} and {name_b}",
        "differences": differences,
    }

    if cluster_a and cluster_b:
        compare_response["platform_context"] = _build_fleet_compare_platform_context(cluster_a, cluster_b)
    else:
        compare_response["platform_context"] = None

    return compare_response
