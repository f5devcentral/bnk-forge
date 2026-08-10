"""
NVIDIA DPF (DOCA Platform Framework) routes.

Read-only endpoints for DPF detection, health, and resource inventory.
All DPU infrastructure data is fetched via kubeconfig (same as F5 BNK).

Frontend uses a shared React Query cache key so switching between
DPF views doesn't re-fetch from the cluster.
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from routes.auth import require_viewer
from services.dpf import analyze_dpf_health, detect_dpf, fetch_all_dpf_data
from services.kubernetes_service import KubernetesService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["k8s-dpf"])


# ============================================================================
# DPF Detection (lightweight)
# ============================================================================

@router.get(
    "/k8s/clusters/{cluster_id}/dpf/detect",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("detect DPF")
def get_dpf_detect(
    cluster_id: int,
    db: Session = Depends(get_db),
):
    """
    Lightweight DPF detection — checks if DPF is installed on this cluster.

    Only fetches DPFOperatorConfig + DPUDevice + DPUCluster CRDs.
    Much faster than the full data endpoint for the common case where
    DPF is not installed.
    """
    k8s_service = KubernetesService(db)
    return detect_dpf(k8s_service, cluster_id)


# ============================================================================
# Unified DPF Data Endpoint
# ============================================================================

@router.get(
    "/k8s/clusters/{cluster_id}/dpf/data",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("fetch DPF data")
def get_dpf_data(
    cluster_id: int,
    db: Session = Depends(get_db),
):
    """
    Unified DPF data endpoint — fetches all DPF CRD types in one parallel burst.

    Returns health analysis and full resource inventory. The frontend caches
    this under one query key so switching between DPF views is instant.
    """
    k8s_service = KubernetesService(db)
    data = fetch_all_dpf_data(k8s_service, cluster_id)
    health = analyze_dpf_health(data)

    return {
        "health": health,
        "resources": data["resources"],
        "cluster_id": cluster_id,
    }


# ============================================================================
# Individual resource endpoints (for direct access / future CRUD)
# ============================================================================

@router.get(
    "/k8s/clusters/{cluster_id}/dpf/health",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get DPF health")
def get_dpf_health(
    cluster_id: int,
    db: Session = Depends(get_db),
):
    """DPF health summary — operator, devices, clusters, services."""
    k8s_service = KubernetesService(db)
    data = fetch_all_dpf_data(k8s_service, cluster_id)
    health = analyze_dpf_health(data)
    health["cluster_id"] = cluster_id
    return health
