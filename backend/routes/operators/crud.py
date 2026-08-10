"""
Operator CRUD routes: list, detail, delete, cleanup, connectivity modes.
"""
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.errors import NotFoundError
from database import get_db
from routes.auth import require_admin, require_operator, require_viewer
from services.operator_registry import (
    delete_operator,
    get_operator,
    list_operators,
    operator_connections,
)

from . import (
    _operator_to_response,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Operator Endpoints
# ---------------------------------------------------------------------------

@router.get("/", dependencies=[Depends(require_viewer)])
def list_all_operators(
    connected_only: bool = False,
    db: Session = Depends(get_db),
):
    """
    List all registered operators.

    The `is_connected` field reflects real-time WebSocket state.
    """
    operators = list_operators(db, connected_only=connected_only)
    return [_operator_to_response(op) for op in operators]


# NOTE: Static-path GET routes MUST be defined before the /{operator_id}
# wildcard, otherwise FastAPI will match "connectivity-modes" as an operator_id.

@router.get("/connectivity-modes", dependencies=[Depends(require_viewer)])
def list_connectivity_modes(
    db: Session = Depends(get_db),
):
    """List all available operator connectivity modes with current status."""
    from services.operator_connectivity import get_available_modes
    return get_available_modes(db)


@router.get("/{operator_id}", dependencies=[Depends(require_viewer)])
def get_operator_detail(
    operator_id: str,
    db: Session = Depends(get_db),
):
    """Get detailed operator information including last health report."""
    op = get_operator(db, operator_id)
    if not op:
        raise NotFoundError("operator", operator_id)

    resp = _operator_to_response(op)
    resp["last_health_report"] = op.last_health_report
    resp["registration_token_name"] = (
        op.registration_token.name if op.registration_token else None
    )
    return resp


@router.delete("/{operator_id}", dependencies=[Depends(require_operator)])
def remove_operator(
    operator_id: str,
    db: Session = Depends(get_db),
):
    """
    Delete an operator record.

    If the operator is currently connected, it will be disconnected.
    """
    # Disconnect if connected
    if operator_connections.is_connected(operator_id):
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're in an async context (FastAPI) — schedule the coroutine
                asyncio.ensure_future(
                    operator_connections.unregister(operator_id, reason="deleted_by_admin")
                )
            else:
                loop.run_until_complete(
                    operator_connections.unregister(operator_id, reason="deleted_by_admin")
                )
        except Exception as e:
            logger.warning(f"Error disconnecting operator {operator_id}: {e}")

    success = delete_operator(db, operator_id)
    if not success:
        raise NotFoundError("operator", operator_id)
    db.commit()
    return {"success": True, "message": f"Operator {operator_id} deleted"}


@router.post("/cleanup-stale", dependencies=[Depends(require_admin)])
def cleanup_stale(db: Session = Depends(get_db)):
    """
    Manually trigger stale operator cleanup.

    Marks operators as disconnected if their heartbeat is older than
    the threshold (2 minutes). Also expires queued commands that were
    never picked up.
    """
    from services.operator_registry import cleanup_expired_commands, cleanup_stale_operators

    stale_count = cleanup_stale_operators(db)
    expired_count = cleanup_expired_commands(db)
    db.commit()

    return {
        "success": True,
        "stale_operators_cleaned": stale_count,
        "expired_commands_cleaned": expired_count,
    }
