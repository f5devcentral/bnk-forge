"""
Operator Polling API — HTTP-based command dispatch for operators in polling mode.

When an operator can't maintain a WebSocket connection (firewall, proxy, etc.),
it polls these endpoints instead:

  POST /api/operators/register-poll              — Register a polling-mode operator
  GET  /api/operators/{operator_id}/poll          — Pick up pending commands
  POST /api/operators/{operator_id}/results/{id}  — Submit command results
  POST /api/operators/{operator_id}/heartbeat     — Send heartbeat
  POST /api/operators/{operator_id}/health        — Submit health report

Authentication: All endpoints use the registration token passed via
Authorization: Bearer header. JWT auth middleware is bypassed for these
paths (they use operator registration tokens, not user JWTs).
"""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.errors import BadRequestError, NotFoundError, UnauthorizedError
from database import get_db
from models import ConnectedOperator, OperatorCommandQueue
from models.enums import OperatorCommandStatus, OperatorStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/operators", tags=["operator-polling"])


# ---------------------------------------------------------------------------
# Token validation helper
# ---------------------------------------------------------------------------

def _validate_operator_token(request: Request, db: Session) -> str:
    """
    Validate operator registration token from Authorization header.

    Returns the token string if valid, raises UnauthorizedError otherwise.
    Operators use registration tokens (bnk_reg_*), not user JWTs.
    """
    auth_header = request.headers.get("authorization", "")
    token = None

    if auth_header.startswith("Bearer "):
        token = auth_header[7:]

    if not token:
        # Fallback: query parameter (legacy support)
        token = request.query_params.get("token", "")

    if not token:
        raise UnauthorizedError("Missing registration token. Provide via Authorization: Bearer header.")

    from services.operator_registry import validate_registration_token
    token_record = validate_registration_token(db, token)
    if not token_record:
        raise UnauthorizedError("Invalid or expired registration token.")

    return token


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class PollResponse(BaseModel):
    commands: list  # List of pending command dicts
    poll_interval_seconds: int = 5


class CommandResultSubmission(BaseModel):
    success: bool
    result: dict | None = None
    output_lines: list | None = None
    error_message: str | None = None
    duration_seconds: float | None = None


class HeartbeatSubmission(BaseModel):
    operator_version: str | None = None
    uptime_seconds: int | None = None
    commands_executed: int | None = None


class HealthSubmission(BaseModel):
    cluster: dict | None = None
    bnk: dict | None = None


class LlmMetricsSubmission(BaseModel):
    """Polling-path equivalent of the WebSocket ``llm_metrics`` message."""
    namespace: str
    name: str
    data: dict


class RegisterPollRequest(BaseModel):
    cluster_name: str
    operator_version: str | None = None
    labels: dict | None = None
    connectivity_mode: str | None = "polling"


# ---------------------------------------------------------------------------
# Register polling operator
# ---------------------------------------------------------------------------

@router.post("/register-poll")
def register_poll_operator(
    body: RegisterPollRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Register a polling-mode operator.

    This is the equivalent of the WebSocket registration handshake but via HTTP.
    The operator calls this on startup to get an operator_id.
    """
    _validate_operator_token(request, db)

    from services.operator_registry import register_operator, validate_registration_token

    # Re-validate to get the token record
    auth_header = request.headers.get("authorization", "")
    token_str = auth_header[7:] if auth_header.startswith("Bearer ") else request.query_params.get("token", "")
    token_record = validate_registration_token(db, token_str)

    if not token_record:
        raise UnauthorizedError("Invalid registration token")

    operator_record = register_operator(
        db=db,
        token=token_record,
        cluster_name=body.cluster_name,
        operator_version=body.operator_version,
        extra_labels=body.labels,
    )

    # Set connectivity mode
    operator_record.connectivity_mode = body.connectivity_mode or "polling"
    db.commit()
    db.refresh(operator_record)

    logger.info(
        f"Polling operator registered: cluster={body.cluster_name}, "
        f"operator_id={operator_record.operator_id}"
    )

    return {
        "operator_id": operator_record.operator_id,
        "cluster_name": operator_record.cluster_name,
        "message": f"Successfully registered as '{body.cluster_name}'",
    }


# ---------------------------------------------------------------------------
# Poll for commands
# ---------------------------------------------------------------------------

@router.get("/{operator_id}/poll")
def poll_commands(
    operator_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Poll for pending commands.

    The operator calls this every N seconds. Returns any queued commands
    and marks them as 'picked_up'. If no commands are pending, returns
    an empty list.

    Also acts as a heartbeat — we update last_heartbeat_at.
    """
    # Validate token
    _validate_operator_token(request, db)

    # Validate operator exists
    op = db.query(ConnectedOperator).filter(
        ConnectedOperator.operator_id == operator_id,
    ).first()
    if not op:
        raise NotFoundError("operator", operator_id)

    # Update heartbeat (polling IS the heartbeat)
    op.last_heartbeat_at = datetime.now(UTC)
    if not op.is_connected:
        op.is_connected = True
        op.last_connected_at = datetime.now(UTC)
        op.status = OperatorStatus.CONNECTED
    db.commit()

    # Pick up pending commands
    pending = (
        db.query(OperatorCommandQueue)
        .filter(
            OperatorCommandQueue.operator_id == operator_id,
            OperatorCommandQueue.status == OperatorCommandStatus.QUEUED,
        )
        .order_by(OperatorCommandQueue.created_at.asc())
        .limit(5)  # Max 5 at a time
        .all()
    )

    commands = []
    now = datetime.now(UTC)
    for cmd in pending:
        # Check if expired
        age = (now - cmd.created_at).total_seconds()
        if age > cmd.timeout_seconds:
            cmd.status = OperatorCommandStatus.EXPIRED
            cmd.completed_at = now
            cmd.error_message = f"Command expired after {cmd.timeout_seconds}s"
            continue

        cmd.status = OperatorCommandStatus.PICKED_UP
        cmd.picked_up_at = now
        commands.append({
            "id": cmd.command_id,
            "action": cmd.action,
            "payload": cmd.payload or {},
            "timeout_seconds": cmd.timeout_seconds,
        })

    db.commit()

    poll_interval = 5
    if op.connectivity_config:
        poll_interval = op.connectivity_config.get("poll_interval_seconds", 5)

    return {
        "commands": commands,
        "poll_interval_seconds": poll_interval,
    }


# ---------------------------------------------------------------------------
# Submit command results
# ---------------------------------------------------------------------------

@router.post("/{operator_id}/results/{command_id}")
def submit_result(
    operator_id: str,
    command_id: str,
    body: CommandResultSubmission,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Submit the result of a completed command.

    The operator executes the command locally and POSTs the result here.
    """
    _validate_operator_token(request, db)

    cmd = db.query(OperatorCommandQueue).filter(
        OperatorCommandQueue.command_id == command_id,
        OperatorCommandQueue.operator_id == operator_id,
    ).first()

    if not cmd:
        raise NotFoundError("command", command_id)

    if cmd.status not in (OperatorCommandStatus.PICKED_UP, OperatorCommandStatus.QUEUED):
        raise BadRequestError(f"Command {command_id} is in status '{cmd.status}' — cannot submit result")

    cmd.status = OperatorCommandStatus.COMPLETED if body.success else OperatorCommandStatus.FAILED
    cmd.result = body.result or {}
    cmd.output_lines = body.output_lines or []
    cmd.error_message = body.error_message
    cmd.completed_at = datetime.now(UTC)

    # Update operator stats
    op = db.query(ConnectedOperator).filter(
        ConnectedOperator.operator_id == operator_id,
    ).first()
    if op:
        op.commands_executed = (op.commands_executed or 0) + 1
        if not body.success:
            op.commands_failed = (op.commands_failed or 0) + 1

    db.commit()

    # Notify any waiting futures (for compatibility with WS-mode OperatorEngine)
    _notify_command_result(command_id, {
        "success": body.success,
        "command_id": command_id,
        "result": body.result or {},
        "output_lines": body.output_lines or [],
        "error_message": body.error_message,
        "duration_seconds": body.duration_seconds,
    })

    return {"success": True, "command_id": command_id}


# ---------------------------------------------------------------------------
# Heartbeat and health
# ---------------------------------------------------------------------------

@router.post("/{operator_id}/heartbeat")
def submit_heartbeat(
    operator_id: str,
    body: HeartbeatSubmission,
    request: Request,
    db: Session = Depends(get_db),
):
    """Submit a heartbeat from a polling-mode operator."""
    _validate_operator_token(request, db)

    from services.operator_registry import update_operator_heartbeat

    update_operator_heartbeat(
        db=db,
        operator_id=operator_id,
        operator_version=body.operator_version,
        uptime_seconds=body.uptime_seconds,
        commands_executed=body.commands_executed,
    )
    db.commit()

    return {"success": True}


@router.post("/{operator_id}/health")
def submit_health(
    operator_id: str,
    body: HealthSubmission,
    request: Request,
    db: Session = Depends(get_db),
):
    """Submit a health report from a polling-mode operator."""
    _validate_operator_token(request, db)

    from services.operator_registry import update_operator_health

    health_report = {}
    if body.cluster:
        health_report["cluster"] = body.cluster
    if body.bnk:
        health_report["bnk"] = body.bnk

    update_operator_health(
        db=db,
        operator_id=operator_id,
        health_report=health_report,
    )
    db.commit()

    return {"success": True}


@router.post("/{operator_id}/llm-metrics")
def submit_llm_metrics(
    operator_id: str,
    body: LlmMetricsSubmission,
    request: Request,
    db: Session = Depends(get_db),
):
    """Submit in-cluster LLM backend metrics from a polling-mode operator."""
    _validate_operator_token(request, db)

    from services.llm_metrics_service import store_llm_metrics

    store_llm_metrics(db, operator_id, body.namespace, body.name, body.data)

    return {"success": True}


# ---------------------------------------------------------------------------
# Command queue management (called by OperatorConnectionManager)
# ---------------------------------------------------------------------------

def enqueue_command(
    db: Session,
    operator_id: str,
    action: str,
    payload: dict,
    timeout_seconds: int = 300,
) -> str:
    """
    Enqueue a command for a polling-mode operator.

    Returns the command_id for tracking.
    """
    command_id = f"{operator_id}:{uuid.uuid4().hex[:12]}"

    cmd = OperatorCommandQueue(
        command_id=command_id,
        operator_id=operator_id,
        action=action,
        payload=payload,
        timeout_seconds=timeout_seconds,
        status=OperatorCommandStatus.QUEUED,
    )
    db.add(cmd)
    db.commit()

    logger.info(f"Enqueued command {command_id}: {action} for operator {operator_id}")
    return command_id


def wait_for_result(
    db: Session,
    command_id: str,
    timeout: float = 300.0,
    poll_interval: float = 1.0,
) -> dict | None:
    """
    Wait for a polling-mode command to complete.

    Polls the DB for the result. Used by OperatorEngine when the operator
    is in polling mode — the engine enqueues, then waits here.
    """
    import time
    start = time.time()

    while time.time() - start < timeout:
        cmd = db.query(OperatorCommandQueue).filter(
            OperatorCommandQueue.command_id == command_id,
        ).first()

        if not cmd:
            return None

        if cmd.status in (OperatorCommandStatus.COMPLETED, OperatorCommandStatus.FAILED):
            return {
                "success": cmd.status == OperatorCommandStatus.COMPLETED,
                "command_id": command_id,
                "result": cmd.result or {},
                "output_lines": cmd.output_lines or [],
                "error_message": cmd.error_message,
            }

        if cmd.status == OperatorCommandStatus.EXPIRED:
            return {
                "success": False,
                "command_id": command_id,
                "error_message": "Command expired",
            }

        time.sleep(poll_interval)
        db.expire(cmd)  # Force re-read from DB

    return {
        "success": False,
        "command_id": command_id,
        "error_message": f"Timed out waiting for result after {timeout}s",
    }


# In-memory notification bridge (for future: connect polling results to WS futures)
_result_callbacks: dict[str, callable] = {}


def _notify_command_result(command_id: str, result: dict):
    """Notify any registered callback that a command result is available."""
    callback = _result_callbacks.pop(command_id, None)
    if callback:
        try:
            callback(result)
        except Exception as e:
            logger.warning(f"Result callback error for {command_id}: {e}")
