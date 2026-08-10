"""
Command dispatch routes: send command, fan-out, connectivity mode, reverse tunnel, link/unlink cluster.
"""
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.errors import BadRequestError, NotFoundError
from database import get_db
from routes.auth import require_operator
from services.operator_registry import (
    get_operator,
    list_operators,
    operator_connections,
)

from . import (
    FanOutCommandRequest,
    LinkClusterRequest,
    SendCommandRequest,
    SetConnectivityModeRequest,
    _operator_to_response,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Send Command to Operator
# ---------------------------------------------------------------------------

@router.post("/{operator_id}/command", dependencies=[Depends(require_operator)])
async def send_operator_command(
    operator_id: str,
    body: SendCommandRequest,
    db: Session = Depends(get_db),
):
    """
    Send a command to a connected operator.

    The operator must be connected via WebSocket for this to work.
    Commands are sent and we wait for a result (up to timeout seconds).
    """
    if not operator_connections.is_connected(operator_id):
        raise BadRequestError(f"Operator {operator_id} is not connected")

    try:
        result = await operator_connections.send_command(
            operator_id=operator_id,
            command={
                "action": body.action,
                "payload": body.payload or {},
            },
            timeout=body.timeout,
        )
        return {
            "success": result.get("success", False),
            "command_id": result.get("command_id", ""),
            "result": result,
        }
    except ConnectionError as e:
        raise BadRequestError(str(e))
    except TimeoutError as e:
        raise BadRequestError(str(e))
    except RuntimeError as e:
        raise BadRequestError(f"Operator error: {e}")


# ---------------------------------------------------------------------------
# Multi-cluster command fan-out
# ---------------------------------------------------------------------------

@router.post("/fan-out", dependencies=[Depends(require_operator)])
async def fan_out_command(
    body: FanOutCommandRequest,
    db: Session = Depends(get_db),
):
    """
    Send a command to multiple operators concurrently.

    Target operators can be specified by:
      - operator_ids: explicit list of operator IDs
      - labels: filter connected operators by labels (all must match)
      - Neither: sends to ALL connected operators

    Returns a dict mapping each operator_id to its result (or error).
    """
    # Determine target operators
    target_ids = []

    if body.operator_ids:
        # Explicit list
        target_ids = body.operator_ids
    elif body.labels:
        # Filter by labels
        all_ops = list_operators(db, connected_only=True)
        for op in all_ops:
            op_labels = op.labels or {}
            if all(op_labels.get(k) == v for k, v in body.labels.items()):
                target_ids.append(op.operator_id)
    else:
        # All connected
        target_ids = operator_connections.get_connected_operator_ids()

    if not target_ids:
        return {
            "success": True,
            "results": {},
            "total_operators": 0,
            "message": "No matching operators found",
        }

    # Fan out
    results = await operator_connections.send_command_to_multiple(
        operator_ids=target_ids,
        command={
            "action": body.action,
            "payload": body.payload or {},
        },
        timeout=body.timeout,
    )

    success_count = sum(1 for r in results.values() if r.get("success", False))
    fail_count = len(results) - success_count

    return {
        "success": fail_count == 0,
        "results": results,
        "total_operators": len(results),
        "succeeded": success_count,
        "failed": fail_count,
    }


# ---------------------------------------------------------------------------
# Connectivity Mode
# ---------------------------------------------------------------------------

@router.post("/{operator_id}/connectivity", dependencies=[Depends(require_operator)])
def set_connectivity_mode(
    operator_id: str,
    body: SetConnectivityModeRequest,
    db: Session = Depends(get_db),
):
    """
    Set the connectivity mode for an operator.

    Returns the resolved connectivity setup including the correct
    install command, environment variables, and notes.
    """
    from services.operator_connectivity import CONNECTIVITY_MODES, resolve_connectivity

    if body.mode not in CONNECTIVITY_MODES:
        raise BadRequestError(f"Unknown connectivity mode: {body.mode}. Valid: {list(CONNECTIVITY_MODES.keys())}")

    op = get_operator(db, operator_id)
    if not op:
        raise NotFoundError("operator", operator_id)

    # Merge token into config for install command generation
    config = dict(body.config or {})
    # Find the operator's registration token
    if op.registration_token:
        # We can't recover the plaintext token, but we use the prefix for display
        config.setdefault("token", "<your-registration-token>")

    setup = resolve_connectivity(db, body.mode, operator_id, config)

    # Persist the mode on the operator record
    op.connectivity_mode = body.mode
    op.connectivity_config = body.config or {}
    db.commit()
    db.refresh(op)

    return {
        "operator": _operator_to_response(op),
        "setup": {
            "mode": setup.mode,
            "control_plane_url": setup.control_plane_url,
            "env_vars": setup.env_vars,
            "helm_install_command": setup.helm_install_command,
            "kubectl_command": setup.kubectl_command,
            "notes": setup.notes,
            "setup_status": setup.setup_status,
            "error": setup.error,
        },
    }


@router.post("/{operator_id}/connectivity/preview", dependencies=[Depends(require_operator)])
def preview_connectivity_setup(
    operator_id: str,
    body: SetConnectivityModeRequest,
    db: Session = Depends(get_db),
):
    """
    Preview what a connectivity mode setup would look like without persisting.

    Used by the UI to show install commands before the user commits.
    """
    from services.operator_connectivity import CONNECTIVITY_MODES, resolve_connectivity

    if body.mode not in CONNECTIVITY_MODES:
        raise BadRequestError(f"Unknown mode: {body.mode}")

    config = dict(body.config or {})
    setup = resolve_connectivity(db, body.mode, operator_id or "preview", config)

    return {
        "mode": setup.mode,
        "control_plane_url": setup.control_plane_url,
        "env_vars": setup.env_vars,
        "helm_install_command": setup.helm_install_command,
        "kubectl_command": setup.kubectl_command,
        "notes": setup.notes,
        "setup_status": setup.setup_status,
        "error": setup.error,
    }


# ---------------------------------------------------------------------------
# Reverse Tunnel
# ---------------------------------------------------------------------------

@router.post("/{operator_id}/reverse-tunnel/open", dependencies=[Depends(require_operator)])
def open_reverse_tunnel(
    operator_id: str,
    db: Session = Depends(get_db),
):
    """
    Open a reverse SSH tunnel for an operator in reverse_ssh mode.

    Uses the SSH credential from the operator's connectivity_config.
    """
    from services.operator_connectivity import open_reverse_ssh_tunnel

    op = get_operator(db, operator_id)
    if not op:
        raise NotFoundError("operator", operator_id)

    if op.connectivity_mode != "reverse_ssh":
        raise BadRequestError(f"Operator is in '{op.connectivity_mode}' mode, not 'reverse_ssh'")

    config = op.connectivity_config or {}
    ssh_cred_id = config.get("ssh_credential_id")
    if not ssh_cred_id:
        raise BadRequestError("No SSH credential configured. Set connectivity config first.")

    result = open_reverse_ssh_tunnel(
        db=db,
        ssh_credential_id=ssh_cred_id,
        remote_port=config.get("reverse_port", 443),
    )

    return result


# ---------------------------------------------------------------------------
# Link / Unlink Cluster
# ---------------------------------------------------------------------------

@router.post("/{operator_id}/link-cluster", dependencies=[Depends(require_operator)])
def link_operator_to_cluster(
    operator_id: str,
    body: LinkClusterRequest,
    db: Session = Depends(get_db),
):
    """
    Link an operator to an existing KubernetesCluster record.

    This enables the engine router to prefer the operator for deployments
    to that cluster's project. Only one operator can be linked to a cluster.
    """
    from models import ConnectedOperator, KubernetesCluster

    op = get_operator(db, operator_id)
    if not op:
        raise NotFoundError("operator", operator_id)

    cluster = db.query(KubernetesCluster).filter(
        KubernetesCluster.id == body.cluster_id,
    ).first()
    if not cluster:
        raise NotFoundError("cluster", body.cluster_id)

    # Check if another operator is already linked to this cluster
    existing = db.query(ConnectedOperator).filter(
        ConnectedOperator.cluster_id == body.cluster_id,
        ConnectedOperator.operator_id != operator_id,
    ).first()
    if existing:
        raise BadRequestError(
            f"Cluster '{cluster.name}' already has operator '{existing.cluster_name}' "
            f"(id={existing.operator_id}) linked. Unlink it first."
        )

    op.cluster_id = body.cluster_id
    db.commit()
    db.refresh(op)

    logger.info(
        f"Linked operator '{op.cluster_name}' (id={operator_id}) to cluster "
        f"'{cluster.name}' (id={cluster.id}, project_id={cluster.project_id})"
    )

    return {
        "success": True,
        "message": f"Operator linked to cluster '{cluster.name}'",
        "operator": _operator_to_response(op),
    }


@router.post("/{operator_id}/unlink-cluster", dependencies=[Depends(require_operator)])
def unlink_operator_from_cluster(
    operator_id: str,
    db: Session = Depends(get_db),
):
    """Unlink an operator from its cluster."""
    op = get_operator(db, operator_id)
    if not op:
        raise NotFoundError("operator", operator_id)

    old_cluster_id = op.cluster_id
    op.cluster_id = None
    db.commit()
    db.refresh(op)

    logger.info(f"Unlinked operator '{op.cluster_name}' (id={operator_id}) from cluster {old_cluster_id}")

    return {
        "success": True,
        "message": "Operator unlinked from cluster",
        "operator": _operator_to_response(op),
    }
