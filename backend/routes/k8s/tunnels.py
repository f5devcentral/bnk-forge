"""
SSH Tunnel Management routes.

Endpoints for managing SSH tunnels to Kubernetes clusters:
  - List all active tunnels
  - Get tunnel status for a cluster
  - Open / close tunnels
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload

from core.errors import BadRequestError, NotFoundError, handle_route_errors
from database import get_db
from models import KubernetesCluster, User
from routes.auth import require_admin, require_cluster_owner, require_viewer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["k8s-tunnels"])


@router.get("/k8s/tunnels", dependencies=[Depends(require_viewer)])
def list_all_tunnels():
    """
    List all active SSH tunnels with health status.

    Returns tunnel info for each active tunnel: cluster_id, local_port,
    ssh_host, health status, idle time.
    """
    from services.ssh_tunnel_manager import get_tunnel_manager
    mgr = get_tunnel_manager()
    return {"tunnels": mgr.get_all_tunnel_statuses()}


@router.get("/k8s/clusters/{cluster_id}/tunnel", dependencies=[Depends(require_viewer)])
def get_tunnel_status(
    cluster_id: int,
    db: Session = Depends(get_db)
):
    """
    Get SSH tunnel status for a specific cluster.

    Returns tunnel details if active, or indicates tunnel is not active.
    Also returns cluster SSH config so the UI can show tunnel settings.
    """
    from services.ssh_tunnel_manager import get_tunnel_manager

    cluster = db.query(KubernetesCluster).filter(
        KubernetesCluster.id == cluster_id
    ).first()
    if not cluster:
        raise NotFoundError("cluster", cluster_id)

    mgr = get_tunnel_manager()
    tunnel = mgr.get_tunnel_status(cluster_id)

    return {
        "cluster_id": cluster_id,
        "cluster_name": cluster.name,
        "ssh_tunnel_enabled": cluster.ssh_tunnel_enabled,
        "ssh_remote_k8s_host": cluster.ssh_remote_k8s_host or "localhost",
        "ssh_remote_k8s_port": cluster.ssh_remote_k8s_port or 6443,
        "tunnel_active": tunnel is not None,
        "tunnel": tunnel,
    }


@router.post("/k8s/clusters/{cluster_id}/tunnel/open")
@handle_route_errors("open SSH tunnel")
def open_tunnel(
    cluster_id: int,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """
    Explicitly open an SSH tunnel for a cluster.

    The tunnel is normally opened on-demand, but this endpoint allows
    the user to pre-open it and verify connectivity.
    """
    from services.cluster_utils import _maybe_open_ssh_tunnel

    cluster = db.query(KubernetesCluster).options(
        joinedload(KubernetesCluster.project),
        joinedload(KubernetesCluster.ssh_credential),
    ).filter(KubernetesCluster.id == cluster_id).first()
    if not cluster:
        raise NotFoundError("cluster", cluster_id)

    if not cluster.ssh_tunnel_enabled:
        raise BadRequestError("SSH tunnel is not enabled for this cluster")

    local_port = _maybe_open_ssh_tunnel(cluster)
    if local_port:
        from services.ssh_tunnel_manager import get_tunnel_manager
        mgr = get_tunnel_manager()
        tunnel = mgr.get_tunnel_status(cluster_id)
        return {
            "success": True,
            "message": f"SSH tunnel opened on port {local_port}",
            "local_port": local_port,
            "tunnel": tunnel,
        }
    else:
        raise BadRequestError("Failed to open tunnel -- check SSH credentials and cluster config")


@router.post("/k8s/clusters/{cluster_id}/tunnel/close")
def close_tunnel(
    cluster_id: int,
    user: User = Depends(require_cluster_owner),
):
    """
    Close the SSH tunnel for a cluster.

    The tunnel will be re-opened on-demand when the next K8s operation
    is requested for this cluster.
    """
    from services.ssh_tunnel_manager import get_tunnel_manager
    mgr = get_tunnel_manager()
    tunnel = mgr.get_tunnel_status(cluster_id)
    if not tunnel:
        return {"success": True, "message": "No active tunnel to close"}

    mgr.close_tunnel(cluster_id)
    return {"success": True, "message": f"Tunnel closed (was on port {tunnel['local_port']})"}


@router.post("/k8s/tunnels/close-all", dependencies=[Depends(require_admin)])
def close_all_tunnels():
    """Close all active SSH tunnels."""
    from services.ssh_tunnel_manager import get_tunnel_manager
    mgr = get_tunnel_manager()
    tunnels = mgr.get_all_tunnel_statuses()
    mgr.close_all()
    return {"success": True, "message": f"Closed {len(tunnels)} tunnel(s)"}
