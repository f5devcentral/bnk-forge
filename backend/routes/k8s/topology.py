"""
Namespace topology route (D-018, P4).

GET /api/k8s/clusters/{cluster_id}/topology?namespace=<ns>
  — returns a TopologyGraphResponse (nodes + edges) for the given namespace.
"""
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from routes.auth import require_viewer
from schemas.k8s import TopologyGraphResponse
from services.kubernetes import KubernetesService
from services.topology_builder_service import build_namespace_topology

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["k8s-topology"])


@router.get(
    "/k8s/clusters/{cluster_id}/topology",
    response_model=TopologyGraphResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("build namespace topology")
def get_cluster_topology(
    cluster_id: int,
    namespace: str = "default",
    db: Session = Depends(get_db),
):
    """Return a topology graph (nodes + edges) for a cluster namespace.

    - Nodes: Services, Pods, and the owning Deployment/StatefulSet/DaemonSet.
    - Edges: Service→Pod (kind='selects') and Workload→Pod (kind='owns').
    - Cluster unreachable → 503 code=NETWORK_UNREACHABLE (via @with_breaker).
    - Namespace with >300 pods → truncated graph with non-null ``info``.
    """
    k8s = KubernetesService(db)
    return build_namespace_topology(k8s, cluster_id, namespace)
