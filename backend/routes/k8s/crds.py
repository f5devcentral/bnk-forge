"""
CRD discovery route (D-018, P1).

GET /api/k8s/clusters/{cluster_id}/crds
  — lists CRDs installed in the cluster, merged with the static registry.
"""
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from routes.auth import require_viewer
from schemas.k8s import CrdListEnvelope
from services.crd_discovery_service import CrdDiscoveryService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["k8s-crds"])


@router.get(
    "/k8s/clusters/{cluster_id}/crds",
    response_model=CrdListEnvelope,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("discover CRDs")
def get_cluster_crds(
    cluster_id: int,
    group: list[str] = Query(default=[]),
    db: Session = Depends(get_db),
):
    """Return CRDs installed in the cluster.

    Optional ``?group=`` query parameter (repeatable) filters results to one
    or more API groups.  Filter is applied in Python after the per-cluster
    cache read.

    - Reachable cluster, CRDs found → 200 with populated ``crds``.
    - Reachable cluster, no CRDs match filter → 200 with empty ``crds`` and
      non-null ``info`` describing why (not an error).
    - Cluster unreachable / apiextensions API failure → 503
      ``code=NETWORK_UNREACHABLE`` (never a 200 with empty list).
    """
    svc = CrdDiscoveryService(db)
    return svc.list_crds(cluster_id, group_filter=group if group else None)
