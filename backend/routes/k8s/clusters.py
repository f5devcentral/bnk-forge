"""
Kubernetes Cluster Management routes.

Thin HTTP handlers delegating to ClusterManagementService and KubernetesService.
"""

import logging
import time
from threading import Lock

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from core.k8s_resource_registry import list_resource_types
from database import get_db
from models import User
from routes.auth import require_cluster_owner, require_project_owner, require_viewer
from routes.k8s._shared import (
    AdaptiveModuleRequest,
    ClusterCreateRequest,
    ClusterUpdateRequest,
)
from schemas.k8s import (
    BatchConnectivityResponse,
    BnkClusterConfigCreateRequest,
    BnkClusterConfigSummary,
    BnkClusterMemberAssignRequest,
    BnkClusterMemberAssignResponse,
    ClusterConnectionTestResponse,
    ClusterConnectivityResponse,
    ClusterCreateResponse,
    ClusterDetailResponse,
    ClusterListResponse,
    ClusterOperationResponse,
    ClusterRefreshResponse,
    ClusterScanEnvelope,
    ClusterSummary,
    HugePagesDeployRequest,
    HugePagesDeployResponse,
    NamespaceListResponse,
    NodeCountResponse,
    NodeReadinessProbeRequest,
    NodeReadinessProbeResponse,
    ResourceTypeCatalogResponse,
)
from services.cluster_management_service import ClusterManagementService
from services.kubernetes_service import KubernetesService
from tasks.cluster_scan_task import enqueue_cluster_scan

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["k8s-clusters"])


# ============================================================================
# Cluster CRUD
# ============================================================================

@router.post("/projects/{project_id}/k8s/clusters/detect-eks")
@handle_route_errors("detect managed clusters")
def detect_and_register_eks_clusters(project_id: int, user: User = Depends(require_project_owner), db: Session = Depends(get_db)):
    """Detect deployed managed-cluster modules in the project and register them (owner or admin only)."""
    return ClusterManagementService(db).detect_managed_clusters(project_id)


@router.post("/projects/{project_id}/k8s/clusters", response_model=ClusterCreateResponse)
@handle_route_errors("add cluster")
def add_cluster_to_project(project_id: int, cluster_data: ClusterCreateRequest, user: User = Depends(require_project_owner), db: Session = Depends(get_db)):
    """Add a Kubernetes cluster configuration to a project (owner or admin only)."""
    result = ClusterManagementService(db).create_cluster(project_id, cluster_data)
    db.commit()
    enqueue_cluster_scan(result["id"])
    return result


@router.get("/k8s/clusters", response_model=ClusterListResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("list all clusters")
def list_all_clusters(db: Session = Depends(get_db)):
    """List all Kubernetes clusters (global)."""
    return ClusterManagementService(db).list_all_clusters()


@router.get("/k8s/clusters/connectivity", response_model=BatchConnectivityResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("batch connectivity check")
def batch_connectivity_check(db: Session = Depends(get_db)):
    """Probe connectivity for all clusters in parallel. Fast, lightweight network check."""
    from services.connectivity_probe_service import ConnectivityProbeService
    return ConnectivityProbeService(db).probe_all_clusters()


@router.get("/projects/{project_id}/k8s/clusters", response_model=ClusterListResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("list project clusters")
def list_project_clusters(project_id: int, db: Session = Depends(get_db)):
    """List all Kubernetes clusters for a project."""
    return ClusterManagementService(db).list_project_clusters(project_id)


@router.get("/k8s/clusters/{cluster_id}", response_model=ClusterDetailResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("get cluster details")
def get_cluster_details(cluster_id: int, db: Session = Depends(get_db)):
    """Get cluster details."""
    return ClusterManagementService(db).get_cluster_details(cluster_id)


@router.put("/k8s/clusters/{cluster_id}", response_model=ClusterSummary)
@handle_route_errors("update cluster")
def update_cluster(cluster_id: int, cluster_data: ClusterUpdateRequest, user: User = Depends(require_cluster_owner), db: Session = Depends(get_db)):
    """Update cluster configuration (owner or admin only)."""
    result = ClusterManagementService(db).update_cluster(cluster_id, cluster_data)
    db.commit()
    enqueue_cluster_scan(cluster_id)
    return result


@router.post("/k8s/clusters/{cluster_id}/resync", response_model=ClusterOperationResponse)
@handle_route_errors("resync cluster")
def resync_cluster(cluster_id: int, user: User = Depends(require_cluster_owner), db: Session = Depends(get_db)):
    """Force a fresh inventory sync for a cluster (owner or admin only).

    Issue #194: an explicit, documented rescan trigger. Operators previously
    relied on a no-op ``PUT`` to force a refresh; this endpoint makes that
    intent first-class and reliable. It enqueues a background scan (the same
    task registration/PUT use) and returns immediately — the scan stamps
    ``last_synced_at`` on completion. 404s if the cluster does not exist.
    """
    # Validate existence so a resync of an unknown cluster is a clean 404
    # rather than a silently-swallowed background no-op.
    ClusterManagementService(db).get_cluster_details(cluster_id)
    enqueue_cluster_scan(cluster_id)
    return {
        "success": True,
        "message": "Inventory sync enqueued",
        "cluster_id": cluster_id,
    }


@router.delete("/k8s/clusters/{cluster_id}", response_model=ClusterOperationResponse)
@handle_route_errors("delete cluster")
def delete_cluster(cluster_id: int, user: User = Depends(require_cluster_owner), db: Session = Depends(get_db)):
    """Delete cluster configuration (owner or admin only)."""
    result = ClusterManagementService(db).delete_cluster(cluster_id)
    db.commit()
    return result


# ============================================================================
# Cluster Operations (delegated to KubernetesService)
# ============================================================================

@router.post("/k8s/clusters/{cluster_id}/refresh-kubeconfig", response_model=ClusterRefreshResponse)
@handle_route_errors("refresh kubeconfig")
def refresh_cluster_kubeconfig(cluster_id: int, user: User = Depends(require_cluster_owner), db: Session = Depends(get_db)):
    """Refresh kubeconfig — EKS via AWS CLI or on-prem via SSH probe (owner or admin only)."""
    result = ClusterManagementService(db).refresh_kubeconfig(cluster_id)
    db.commit()
    enqueue_cluster_scan(cluster_id)
    return result


@router.post("/k8s/clusters/{cluster_id}/test", response_model=ClusterConnectionTestResponse)
@handle_route_errors("test cluster connection")
def test_cluster_connection(cluster_id: int, user: User = Depends(require_cluster_owner), db: Session = Depends(get_db)):
    """Test connection to Kubernetes cluster (owner or admin only)."""
    return KubernetesService(db).test_connection(cluster_id)


@router.get("/k8s/clusters/{cluster_id}/namespaces", response_model=NamespaceListResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("list cluster namespaces")
def list_cluster_namespaces(cluster_id: int, db: Session = Depends(get_db)):
    """List all namespaces in a cluster."""
    k8s_service = KubernetesService(db)
    namespaces = k8s_service.list_namespaces(cluster_id)
    namespace_objects = [{"name": ns, "status": "Active", "created_at": None} for ns in namespaces]
    return {"namespaces": namespace_objects, "count": len(namespace_objects), "cluster_id": cluster_id}


@router.get("/k8s/clusters/{cluster_id}/nodes/count", response_model=NodeCountResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("get cluster node count")
def get_cluster_node_count(cluster_id: int, db: Session = Depends(get_db)):
    """Get the total number of nodes in a cluster."""
    return {"cluster_id": cluster_id, "node_count": KubernetesService(db).get_node_count(cluster_id)}


@router.get("/k8s/resource-types", response_model=ResourceTypeCatalogResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("list resource types")
def list_supported_resource_types():
    """List all supported Kubernetes resource types."""
    resource_types = list_resource_types()
    result = []
    for key, rt in resource_types.items():
        result.append({
            "key": key, "kind": rt.kind, "api_group": rt.api_group,
            "api_version": rt.api_version, "plural": rt.plural,
            "namespaced": rt.namespaced, "display_name": rt.display_name,
            "description": rt.description, "category": rt.category
        })
    result.sort(key=lambda x: (x["category"], x["display_name"]))
    return {"resource_types": result, "count": len(result)}


# ============================================================================
# Cluster Connectivity Probes
# ============================================================================

@router.get("/k8s/clusters/{cluster_id}/connectivity", response_model=ClusterConnectivityResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("cluster connectivity check")
def check_cluster_connectivity(cluster_id: int, db: Session = Depends(get_db)):
    """Probe connectivity to a single cluster. Tests ICMP, TCP port, and K8s API."""
    from services.connectivity_probe_service import ConnectivityProbeService
    return ConnectivityProbeService(db).probe_cluster(cluster_id)


# ============================================================================
# Cluster Scanner & Adaptive Module Planning
# ============================================================================

# Per-cluster cache of scan results. The scanner does ~25 K8s API calls and
# transfers the full CRD list (~15 MB on mature clusters), so a single scan
# can take 30–60s on real fleets. Cache per cluster for ten minutes — scan
# results (installed prerequisites, BNK install state, platform profile)
# don't change on a minute scale, and the user-initiated "Rescan" action
# always bypasses the cache by passing force=true.
_SCAN_CACHE_TTL_SEC = 600.0
_scan_cache: dict[int, tuple[float, dict]] = {}
_scan_cache_lock = Lock()


@router.post("/k8s/clusters/{cluster_id}/scan", response_model=ClusterScanEnvelope)
@handle_route_errors("scan cluster")
def scan_cluster(
    cluster_id: int,
    force: bool = False,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """Scan a cluster to detect installed prerequisites and BNK components (owner or admin only).

    Results are cached per cluster for ~2 minutes. Pass `?force=true` to bypass
    the cache and run a fresh scan (used by the "Rescan" action in the UI).
    """
    from services.cluster_scanner import ClusterScanner

    now = time.monotonic()
    if not force:
        with _scan_cache_lock:
            cached = _scan_cache.get(cluster_id)
            if cached and (now - cached[0]) < _SCAN_CACHE_TTL_SEC:
                return cached[1]

    result = ClusterScanner(db).scan(cluster_id)
    db.commit()

    write_mono = time.monotonic()
    with _scan_cache_lock:
        # Opportunistic eviction of stale entries so the cache can't grow
        # unbounded across cluster churn.
        stale_cutoff = write_mono - _SCAN_CACHE_TTL_SEC
        for key in [k for k, (t, _) in _scan_cache.items() if t < stale_cutoff]:
            _scan_cache.pop(key, None)
        _scan_cache[cluster_id] = (write_mono, result)
    return result


@router.post("/k8s/clusters/{cluster_id}/adaptive-modules")
@handle_route_errors("get adaptive module plan")
def get_adaptive_module_plan(cluster_id: int, request: AdaptiveModuleRequest, user: User = Depends(require_cluster_owner), db: Session = Depends(get_db)):
    """Scan a cluster and produce an adaptive deployment plan (owner or admin only)."""
    from services.adaptive_module_selector import AdaptiveModuleSelector
    from services.cluster_scanner import ClusterScanner

    scan_data = ClusterScanner(db).scan(cluster_id)
    selector = AdaptiveModuleSelector(scan_data)

    if request.template_slug:
        plan = selector.plan_for_template(request.template_slug, sizing_profile=request.sizing_profile)
    elif request.module_paths:
        plan = selector.plan_for_modules(request.module_paths)
    else:
        plan = selector.plan_for_template("f5-bnk-2.2", sizing_profile=request.sizing_profile)
    return plan.to_dict()


@router.post(
    "/k8s/clusters/{cluster_id}/recommendations/hugepages/deploy",
    response_model=HugePagesDeployResponse,
)
@handle_route_errors("deploy HugePages")
def deploy_hugepages(
    cluster_id: int,
    request: HugePagesDeployRequest,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """Reserve 2Mi HugePages on TMM nodes (owner or admin only).

    Drives the "Deploy" action on the HugePages recommendation. Dispatches a
    one-shot Kubernetes Job (one pod per TMM node) that sets
    ``vm.nr_hugepages`` at runtime and persists it to ``/etc/sysctl.d/`` for
    reboot survival where the host filesystem allows.
    """
    from services.hugepages_deploy_service import HugePagesDeployService

    # Invalidate the scan cache so the next scan reflects the new HugePages.
    _scan_cache.pop(cluster_id, None)

    return HugePagesDeployService(db).deploy(
        cluster_id=cluster_id,
        size=request.size,
        namespace=request.namespace,
        image=request.image,
    )


@router.post(
    "/k8s/clusters/{cluster_id}/node-readiness/probe",
    response_model=NodeReadinessProbeResponse,
)
@handle_route_errors("probe node readiness")
def probe_node_readiness(
    cluster_id: int,
    request: NodeReadinessProbeRequest,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """Probe every node for CNI delegate plugins + core_pattern (owner or admin only).

    On-demand, privileged detection (issue #387 part A) — NOT part of the
    normal cluster scan. Dispatches a one-shot Kubernetes Job (one pod per
    node, all nodes) and waits for it to complete before reading pod logs.
    """
    from services.node_readiness_service import NodeReadinessService

    return NodeReadinessService(db).probe(
        cluster_id=cluster_id,
        namespace=request.namespace,
        image=request.image,
    )


@router.post("/k8s/clusters/{cluster_id}/adaptive-modules/from-scan")
@handle_route_errors("get adaptive module plan from scan")
def get_adaptive_module_plan_from_scan(cluster_id: int, request: AdaptiveModuleRequest, user: User = Depends(require_cluster_owner), db: Session = Depends(get_db)):
    """Produce an adaptive deployment plan from cached scan results (owner or admin only)."""
    from services.adaptive_module_selector import AdaptiveModuleSelector
    from services.cluster_scanner import ClusterScanner

    scan_data = ClusterScanner(db).scan(cluster_id)
    selector = AdaptiveModuleSelector(scan_data)

    if request.template_slug:
        plan = selector.plan_for_template(request.template_slug, sizing_profile=request.sizing_profile)
    elif request.module_paths:
        plan = selector.plan_for_modules(request.module_paths)
    else:
        plan = selector.plan_for_template("f5-bnk-2.2", sizing_profile=request.sizing_profile)
    return plan.to_dict()


@router.post(
    "/k8s/clusters/{cluster_id}/bnk-config",
    response_model=BnkClusterConfigSummary,
)
@handle_route_errors("configure BNK cluster settings")
def configure_bnk_cluster(
    cluster_id: int,
    request: BnkClusterConfigCreateRequest,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """Configure BNK cluster side-table options (tmfifo CIDR pool, join transport, CP host)."""
    from services.bnk_cluster_service import BnkClusterService

    service = BnkClusterService(db)
    cfg = service.get_or_create_config(
        cluster_id=cluster_id,
        tmfifo_pool_cidr=request.tmfifo_pool_cidr,
        join_transport=request.join_transport,
        control_plane_host_id=request.control_plane_host_id,
    )
    db.commit()
    db.refresh(cfg)
    host_ids, dpu_ids = service.cluster_membership(cluster_id)
    return BnkClusterConfigSummary(
        id=cfg.id,
        cluster_id=cfg.cluster_id,
        tmfifo_pool_cidr=cfg.tmfifo_pool_cidr,
        join_transport=cfg.join_transport,
        control_plane_host_id=cfg.control_plane_host_id,
        host_ids=host_ids,
        dpu_ids=dpu_ids,
    )


@router.post(
    "/k8s/clusters/{cluster_id}/bnk-members",
    response_model=BnkClusterMemberAssignResponse,
)
@handle_route_errors("assign BNK cluster members")
def assign_bnk_cluster_members(
    cluster_id: int,
    request: BnkClusterMemberAssignRequest,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """Assign bare-metal hosts and DPUs to a BNK cluster and perform tmfifo IP allocations."""
    from services.bnk_cluster_service import BnkClusterService

    result = BnkClusterService(db).assign_members(
        cluster_id=cluster_id,
        control_plane_host_id=request.control_plane_host_id,
        host_ids=request.host_ids,
        dpu_ids=request.dpu_ids,
        tmfifo_pool_cidr=request.tmfifo_pool_cidr,
    )
    db.commit()
    return BnkClusterMemberAssignResponse(**result)
