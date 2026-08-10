"""
Operator Registry — tracks connected operators.

Two layers:
  1. DB layer — persistent state for registered operators, health reports
  2. In-memory layer — live WebSocket connections, command dispatch, heartbeat tracking

The in-memory layer is the source of truth for "is this operator connected right now?"
The DB layer persists state across restarts and provides the API query surface.

Note: Token CRUD UI endpoints (create, list, revoke, generate-install-command) were
removed in D3-CLEANUP. The kubeconfig-first fleet architecture (Decision D3) made
the token management UI obsolete. Token validation and operator registration are
retained for the WebSocket/polling registration flows used by existing operators.
"""

import asyncio
import hashlib
import logging
import secrets
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import WebSocket
from sqlalchemy.orm import Session

from models import ConnectedOperator, OperatorRegistrationToken
from models.enums import OperatorCommandStatus, OperatorStatus

logger = logging.getLogger(__name__)

# Token prefix for easy identification
TOKEN_PREFIX = "bnk_reg_"

# Stale connection threshold (seconds) — operators not seen for this long are marked disconnected
STALE_HEARTBEAT_THRESHOLD = 120  # 2 minutes (4 missed heartbeats at 30s interval)


def _hash_token(token: str) -> str:
    """SHA-256 hash of a registration token."""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_registration_token() -> str:
    """Generate a new registration token: bnk_reg_<32 random hex chars>."""
    return f"{TOKEN_PREFIX}{secrets.token_hex(16)}"


def create_registration_token(
    db: Session,
    name: str,
    created_by: str,
    max_uses: int | None = None,
    expires_at: datetime | None = None,
    labels: dict[str, str] | None = None,
) -> tuple:
    """
    Create a new registration token.

    Returns (OperatorRegistrationToken, plaintext_token).
    The plaintext token is returned ONCE — it is not stored.

    Note: No longer exposed via HTTP API (D3-CLEANUP). Used internally by
    WebSocket/polling registration and tests.
    """
    plaintext = generate_registration_token()
    token_hash = _hash_token(plaintext)
    token_prefix = plaintext[:12]

    token_record = OperatorRegistrationToken(
        name=name,
        token_hash=token_hash,
        token_prefix=token_prefix,
        max_uses=max_uses,
        expires_at=expires_at,
        labels=labels or {},
        created_by=created_by,
    )
    db.add(token_record)
    db.flush()
    db.refresh(token_record)

    logger.info(f"Created registration token '{name}' (prefix={token_prefix}) by {created_by}")
    return token_record, plaintext


def validate_registration_token(db: Session, plaintext_token: str) -> OperatorRegistrationToken | None:
    """
    Validate a registration token. Returns the token record if valid, None otherwise.

    Checks: exists, active, not expired, not exhausted.
    Does NOT increment use_count — caller does that on successful registration.

    Still needed by operator_ws.py and operator_polling.py for the operator
    registration handshake.
    """
    token_hash = _hash_token(plaintext_token)
    token = db.query(OperatorRegistrationToken).filter(
        OperatorRegistrationToken.token_hash == token_hash,
        OperatorRegistrationToken.is_active,
    ).first()

    if not token:
        return None

    # Check expiry — compare as naive UTC to handle SQLite (strips tzinfo)
    if token.expires_at:
        expires = token.expires_at.replace(tzinfo=None)
        now = datetime.now(UTC).replace(tzinfo=None)
        if expires < now:
            logger.warning(f"Token '{token.name}' expired at {token.expires_at}")
            return None

    # Check max uses
    if token.max_uses is not None and token.use_count >= token.max_uses:
        logger.warning(f"Token '{token.name}' exhausted ({token.use_count}/{token.max_uses} uses)")
        return None

    return token


def register_operator(
    db: Session,
    token: OperatorRegistrationToken,
    cluster_name: str,
    operator_version: str | None = None,
    extra_labels: dict[str, str] | None = None,
) -> ConnectedOperator:
    """
    Register a new operator, or re-register an existing one with the same cluster_name.

    Increments token use_count. Merges labels from token + operator.
    """
    operator_id = str(uuid.uuid4())

    # Merge labels: token labels + operator-reported labels
    merged_labels = dict(token.labels or {})
    if extra_labels:
        merged_labels.update(extra_labels)

    # Check if operator already exists for this cluster_name (reconnection)
    existing = db.query(ConnectedOperator).filter(
        ConnectedOperator.cluster_name == cluster_name,
    ).first()

    now = datetime.now(UTC)

    if existing:
        # Re-registration: update existing record
        existing.registration_token_id = token.id
        existing.labels = merged_labels
        existing.operator_version = operator_version
        existing.is_connected = True
        existing.last_connected_at = now
        existing.status = OperatorStatus.CONNECTED
        existing.disconnect_reason = None
        operator_record = existing
        logger.info(f"Re-registered operator for cluster '{cluster_name}' (operator_id={existing.operator_id})")
    else:
        # New registration
        operator_record = ConnectedOperator(
            operator_id=operator_id,
            registration_token_id=token.id,
            cluster_name=cluster_name,
            labels=merged_labels,
            is_connected=True,
            last_connected_at=now,
            operator_version=operator_version,
            status=OperatorStatus.CONNECTED,
        )
        db.add(operator_record)
        logger.info(f"Registered new operator for cluster '{cluster_name}' (operator_id={operator_id})")

    # Increment token usage
    token.use_count += 1
    token.last_used_at = now

    db.flush()
    db.refresh(operator_record)
    return operator_record


def mark_operator_disconnected(db: Session, operator_id: str, reason: str = "connection_closed"):
    """Mark an operator as disconnected in the DB."""
    op = db.query(ConnectedOperator).filter(
        ConnectedOperator.operator_id == operator_id,
    ).first()
    if op:
        op.is_connected = False
        op.last_disconnected_at = datetime.now(UTC)
        op.disconnect_reason = reason
        op.status = OperatorStatus.DISCONNECTED
        logger.info(f"Operator '{op.cluster_name}' (id={operator_id}) disconnected: {reason}")


def update_operator_health(
    db: Session,
    operator_id: str,
    health_report: dict[str, Any],
):
    """Persist a health report from the operator."""
    op = db.query(ConnectedOperator).filter(
        ConnectedOperator.operator_id == operator_id,
    ).first()
    if not op:
        return

    now = datetime.now(UTC)
    op.last_health_report = health_report
    op.last_health_at = now
    op.last_heartbeat_at = now

    # Extract top-level cluster info
    cluster_info = health_report.get("cluster", {})
    op.kubernetes_version = cluster_info.get("kubernetes_version")
    op.node_count = cluster_info.get("node_count")
    op.nodes_ready = cluster_info.get("nodes_ready")


def update_operator_heartbeat(
    db: Session,
    operator_id: str,
    operator_version: str | None = None,
    uptime_seconds: int | None = None,
    commands_executed: int | None = None,
):
    """Update heartbeat timestamp and stats."""
    op = db.query(ConnectedOperator).filter(
        ConnectedOperator.operator_id == operator_id,
    ).first()
    if not op:
        return

    op.last_heartbeat_at = datetime.now(UTC)
    if operator_version:
        op.operator_version = operator_version
    if uptime_seconds is not None:
        op.uptime_seconds = uptime_seconds
    if commands_executed is not None:
        op.commands_executed = commands_executed


def cleanup_stale_operators(db: Session) -> int:
    """
    Mark operators as disconnected if their heartbeat is too old.

    Called periodically by the stale-connection cleanup task.
    Returns the number of operators marked stale.
    """
    threshold = datetime.now(UTC) - timedelta(seconds=STALE_HEARTBEAT_THRESHOLD)

    stale = db.query(ConnectedOperator).filter(
        ConnectedOperator.is_connected,
        ConnectedOperator.last_heartbeat_at < threshold,
    ).all()

    count = 0
    for op in stale:
        # Skip if WS-connected (the WS layer handles its own timeouts)
        if operator_connections.is_connected(op.operator_id):
            continue

        op.is_connected = False
        op.last_disconnected_at = datetime.now(UTC)
        op.disconnect_reason = "heartbeat_timeout"
        op.status = OperatorStatus.DISCONNECTED
        count += 1
        logger.info(
            f"Stale operator detected: '{op.cluster_name}' (id={op.operator_id}), "
            f"last heartbeat: {op.last_heartbeat_at}"
        )

    if count > 0:
        logger.info(f"Cleaned up {count} stale operator(s)")

    return count


def cleanup_expired_commands(db: Session) -> int:
    """
    Mark expired queued commands as expired.

    Called periodically to clean up commands that were never picked up.
    """
    from models import OperatorCommandQueue

    now = datetime.now(UTC)
    # Find queued commands older than their timeout
    queued = db.query(OperatorCommandQueue).filter(
        OperatorCommandQueue.status == OperatorCommandStatus.QUEUED,
    ).all()

    count = 0
    for cmd in queued:
        age = (now - cmd.created_at).total_seconds()
        if age > cmd.timeout_seconds:
            cmd.status = OperatorCommandStatus.EXPIRED
            cmd.completed_at = now
            cmd.error_message = f"Command expired after {cmd.timeout_seconds}s (never picked up)"
            count += 1

    if count > 0:
        logger.info(f"Expired {count} stale queued command(s)")

    return count


def get_operator_for_cluster(
    db: Session, cluster_id: int, connected_only: bool = True
) -> ConnectedOperator | None:
    """
    Find the operator linked to a given cluster.

    Returns the ConnectedOperator record, or None if no operator is linked.
    When connected_only=True (default), only returns operators that are currently
    connected (is_connected=True in DB or live WebSocket in memory).
    """
    query = db.query(ConnectedOperator).filter(
        ConnectedOperator.cluster_id == cluster_id,
    )
    if connected_only:
        query = query.filter(ConnectedOperator.is_connected)

    # Prefer operators with active WebSocket connections
    operators = query.order_by(ConnectedOperator.last_heartbeat_at.desc()).all()

    for op in operators:
        if operator_connections.is_connected(op.operator_id):
            return op

    # Fallback: return the most recently seen connected operator (DB says connected)
    return operators[0] if operators else None


def list_operators(db: Session, connected_only: bool = False) -> list[ConnectedOperator]:
    """List all registered operators."""
    query = db.query(ConnectedOperator)
    if connected_only:
        query = query.filter(ConnectedOperator.is_connected)
    return query.order_by(ConnectedOperator.cluster_name).all()


def get_operator(db: Session, operator_id: str) -> ConnectedOperator | None:
    """Get a single operator by ID."""
    return db.query(ConnectedOperator).filter(
        ConnectedOperator.operator_id == operator_id,
    ).first()


def delete_operator(db: Session, operator_id: str) -> bool:
    """Delete an operator record."""
    op = db.query(ConnectedOperator).filter(
        ConnectedOperator.operator_id == operator_id,
    ).first()
    if not op:
        return False
    db.delete(op)
    logger.info(f"Deleted operator '{op.cluster_name}' (id={operator_id})")
    return True


# ---------------------------------------------------------------------------
# In-memory connection manager for live WebSocket connections
# ---------------------------------------------------------------------------

class OperatorConnectionManager:
    """
    Manages live WebSocket connections from operators.

    This is the in-memory layer that tracks which operators are connected
    right now, routes commands to them, and collects responses.

    Thread-safe via asyncio (single event loop).
    """

    def __init__(self):
        # operator_id → WebSocket
        self._connections: dict[str, WebSocket] = {}
        # operator_id → ConnectedOperator DB record (cached for quick access)
        self._operator_info: dict[str, dict[str, Any]] = {}
        # command_id → asyncio.Future for response
        self._pending_commands: dict[str, asyncio.Future] = {}
        # command_id → list of output lines (streaming)
        self._command_output: dict[str, list] = {}
        # command_id → output callback
        self._output_callbacks: dict[str, Callable] = {}
        # Metrics
        self._total_commands_sent: int = 0
        self._total_commands_failed: int = 0

    @property
    def connected_count(self) -> int:
        return len(self._connections)

    @property
    def connected_operators(self) -> dict[str, dict[str, Any]]:
        return dict(self._operator_info)

    def is_connected(self, operator_id: str) -> bool:
        return operator_id in self._connections

    def get_connected_operator_ids(self) -> list[str]:
        """Return list of all connected operator IDs."""
        return list(self._connections.keys())

    async def register(self, operator_id: str, websocket: WebSocket, info: dict[str, Any]):
        """Register a new operator connection."""
        # Close existing connection if any (operator reconnected)
        if operator_id in self._connections:
            old_ws = self._connections[operator_id]
            try:
                await old_ws.close(code=1000, reason="Replaced by new connection")
            except Exception:
                pass
            logger.info(f"Replaced existing connection for operator {operator_id}")

        self._connections[operator_id] = websocket
        self._operator_info[operator_id] = info
        logger.info(f"Operator {operator_id} ({info.get('cluster_name', '?')}) connected. Total: {self.connected_count}")

    async def unregister(self, operator_id: str, reason: str = "disconnected"):
        """Remove an operator connection."""
        self._connections.pop(operator_id, None)
        self._operator_info.pop(operator_id, None)

        # Cancel any pending commands for this operator
        cancelled_cmds = []
        for cmd_id, future in list(self._pending_commands.items()):
            if cmd_id.startswith(operator_id):
                if not future.done():
                    future.set_exception(ConnectionError(f"Operator disconnected: {reason}"))
                cancelled_cmds.append(cmd_id)

        for cmd_id in cancelled_cmds:
            self._pending_commands.pop(cmd_id, None)
            self._command_output.pop(cmd_id, None)
            self._output_callbacks.pop(cmd_id, None)

        logger.info(f"Operator {operator_id} disconnected ({reason}). Total: {self.connected_count}")

    async def send_command(
        self,
        operator_id: str,
        command: dict[str, Any],
        timeout: float = 300.0,
        on_output: Callable[[str], Coroutine] | None = None,
    ) -> dict[str, Any]:
        """
        Send a command to an operator and wait for the result.

        Args:
            operator_id: Target operator
            command: Command dict with 'action' and 'payload'
            timeout: Max seconds to wait for result
            on_output: Async callback for streaming output lines

        Returns:
            Result dict from the operator

        Raises:
            ConnectionError: If operator is not connected
            TimeoutError: If command times out
        """
        ws = self._connections.get(operator_id)
        if not ws:
            raise ConnectionError(f"Operator {operator_id} is not connected")

        # Generate unique command ID
        cmd_id = f"{operator_id}:{uuid.uuid4().hex[:12]}"
        command["id"] = cmd_id

        # Set up future for the result
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_commands[cmd_id] = future
        self._command_output[cmd_id] = []
        if on_output:
            self._output_callbacks[cmd_id] = on_output

        try:
            # Send command to operator
            await ws.send_json({"type": "command", **command})
            self._total_commands_sent += 1
            logger.debug(f"Sent command {cmd_id}: action={command.get('action')}")

            # Wait for result with timeout
            result = await asyncio.wait_for(future, timeout=timeout)
            return result

        except TimeoutError:
            self._total_commands_failed += 1
            logger.error(f"Command {cmd_id} timed out after {timeout}s")
            raise TimeoutError(f"Command timed out after {timeout}s")
        finally:
            self._pending_commands.pop(cmd_id, None)
            self._command_output.pop(cmd_id, None)
            self._output_callbacks.pop(cmd_id, None)

    async def send_command_to_multiple(
        self,
        operator_ids: list[str],
        command: dict[str, Any],
        timeout: float = 300.0,
    ) -> dict[str, dict[str, Any]]:
        """
        Fan-out: send a command to multiple operators concurrently.

        Returns a dict mapping operator_id → result (or error dict).
        All commands run concurrently with individual timeouts.
        """
        async def _send_one(op_id: str) -> tuple:
            try:
                result = await self.send_command(op_id, dict(command), timeout=timeout)
                return op_id, result
            except Exception as e:
                return op_id, {
                    "success": False,
                    "error_message": str(e),
                    "operator_id": op_id,
                }

        tasks = [_send_one(op_id) for op_id in operator_ids if self.is_connected(op_id)]

        if not tasks:
            return {}

        results = await asyncio.gather(*tasks, return_exceptions=True)

        output = {}
        for item in results:
            if isinstance(item, tuple):
                op_id, result = item
                output[op_id] = result
            elif isinstance(item, Exception):
                logger.error(f"Fan-out command error: {item}")

        return output

    async def handle_message(self, operator_id: str, message: dict[str, Any]):
        """
        Handle an incoming message from an operator.

        Message types:
          - heartbeat: Update last_heartbeat timestamp
          - health: Full health report
          - output: Streaming output for a command
          - result: Final result for a command
          - error: Error report
        """
        msg_type = message.get("type")

        if msg_type == "output":
            cmd_id = message.get("command_id")
            line = message.get("line", "")
            if cmd_id and cmd_id in self._command_output:
                self._command_output[cmd_id].append(line)
                # Fire output callback if registered
                callback = self._output_callbacks.get(cmd_id)
                if callback:
                    try:
                        await callback(line)
                    except Exception as e:
                        logger.warning(f"Output callback error: {e}")

        elif msg_type == "result":
            cmd_id = message.get("command_id")
            if cmd_id and cmd_id in self._pending_commands:
                future = self._pending_commands[cmd_id]
                if not future.done():
                    # Attach collected output lines
                    message["output_lines"] = self._command_output.get(cmd_id, [])
                    future.set_result(message)

        elif msg_type == "error":
            cmd_id = message.get("command_id")
            if cmd_id and cmd_id in self._pending_commands:
                future = self._pending_commands[cmd_id]
                if not future.done():
                    future.set_exception(
                        RuntimeError(message.get("error_message", "Unknown operator error"))
                    )

        # heartbeat and health are handled at the WS endpoint level
        # (they trigger DB updates via operator_registry functions)

    async def broadcast(self, message: dict[str, Any]):
        """Broadcast a message to all connected operators."""
        disconnected = []
        for op_id, ws in self._connections.items():
            try:
                await ws.send_json(message)
            except Exception:
                disconnected.append(op_id)

        for op_id in disconnected:
            await self.unregister(op_id, reason="broadcast_send_failed")

    def get_operator_ws(self, operator_id: str) -> WebSocket | None:
        """Get the WebSocket for an operator (for direct communication)."""
        return self._connections.get(operator_id)


# Global singleton
operator_connections = OperatorConnectionManager()
