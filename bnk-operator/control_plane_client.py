"""
Control Plane Client — WebSocket connection to BNK-Forge.

Handles:
  - Outbound WebSocket connection with TLS support (wss://) and auto-reconnect
  - Registration handshake (send cluster_name, receive operator_id)
  - Heartbeat loop with missed-heartbeat detection
  - Command dispatch (route incoming commands to handlers)
  - Streaming output back to BNK-Forge
  - Reconnect with jitter to prevent thundering herd
"""

import asyncio
import json
import logging
import random
import ssl
import time
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, Optional

import websockets
from websockets.exceptions import (
    ConnectionClosed,
    ConnectionClosedError,
    InvalidStatusCode,
)

logger = logging.getLogger("bnk-operator.client")

# Heartbeat interval (seconds)
HEARTBEAT_INTERVAL = 30

# Missed heartbeat threshold — disconnect if server doesn't respond to N heartbeats
MISSED_HEARTBEAT_THRESHOLD = 3

# Reconnect backoff: 1s, 2s, 4s, 8s, 16s, 32s, 60s (max) + jitter
MAX_BACKOFF = 60
JITTER_FACTOR = 0.3  # ±30% jitter on backoff delay


class ControlPlaneClient:
    """
    Persistent WebSocket client that connects outbound to BNK-Forge.

    Supports:
      - TLS (wss://) with optional custom CA certificate for self-signed certs
      - Token passed as Authorization header (not query param) when possible
      - Automatic reconnect with exponential backoff + jitter
      - Missed heartbeat detection (disconnects after N unanswered heartbeats)

    Usage:
        client = ControlPlaneClient(url="wss://...", token="bnk_reg_...", cluster_name="prod")
        client.on_command("apply_manifests", handle_apply)
        await client.run(shutdown_event)
    """

    def __init__(
        self,
        url: str,
        token: str,
        cluster_name: str,
        operator_version: str = "1.0.0",
        labels: Optional[Dict[str, str]] = None,
        tls_ca_cert: Optional[str] = None,
        tls_insecure: bool = False,
    ):
        self.url = url
        self.token = token
        self.cluster_name = cluster_name
        self.operator_version = operator_version
        self.labels = labels or {}
        self._tls_ca_cert = tls_ca_cert
        self._tls_insecure = tls_insecure

        # State
        self.operator_id: Optional[str] = None
        self.connected = False
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._reconnect_attempts = 0
        self._start_time = time.time()
        self._commands_executed = 0
        self._commands_failed = 0
        self._reconnect_count = 0
        self._missed_heartbeats = 0

        # Command handlers: action → async callable
        self._handlers: Dict[str, Callable] = {}

        # Metrics tracking
        self._command_durations: list = []  # Last 100 command durations
        self._last_heartbeat_ack: float = time.time()

    def on_command(self, action: str, handler: Callable):
        """Register a command handler for a specific action."""
        self._handlers[action] = handler
        logger.debug(f"Registered handler for '{action}'")

    @property
    def uptime_seconds(self) -> int:
        return int(time.time() - self._start_time)

    @property
    def reconnect_count(self) -> int:
        return self._reconnect_count

    @property
    def commands_failed(self) -> int:
        return self._commands_failed

    @property
    def avg_command_duration(self) -> float:
        if not self._command_durations:
            return 0.0
        return sum(self._command_durations) / len(self._command_durations)

    def _build_ssl_context(self) -> Optional[ssl.SSLContext]:
        """Build SSL context for wss:// connections."""
        if not self.url.startswith("wss://"):
            return None

        ctx = ssl.create_default_context()

        if self._tls_insecure:
            # Skip certificate verification (dev/testing only)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            logger.warning("TLS certificate verification DISABLED (insecure mode)")
        elif self._tls_ca_cert:
            # Custom CA certificate for self-signed certs
            ca_path = Path(self._tls_ca_cert)
            if ca_path.exists():
                ctx.load_verify_locations(str(ca_path))
                logger.info(f"Loaded custom CA certificate: {ca_path}")
            else:
                logger.warning(f"CA cert file not found: {ca_path}, using system CAs")
        # else: use system default CA bundle (certifi)

        return ctx

    async def run(self, shutdown_event: asyncio.Event):
        """
        Main loop — connect, register, handle messages, auto-reconnect.

        Runs until shutdown_event is set.
        """
        while not shutdown_event.is_set():
            try:
                await self._connect_and_run(shutdown_event)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.connected = False
                if shutdown_event.is_set():
                    break

                self._reconnect_count += 1
                delay = self._backoff_with_jitter()
                logger.warning(
                    f"Disconnected from control plane: {e}. "
                    f"Reconnecting in {delay:.1f}s (attempt {self._reconnect_attempts}, "
                    f"total reconnects: {self._reconnect_count})"
                )

                # Wait for backoff or shutdown
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=delay)
                    break  # Shutdown requested during backoff
                except asyncio.TimeoutError:
                    pass  # Backoff elapsed, reconnect

        # Clean disconnect
        self.connected = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

    async def _connect_and_run(self, shutdown_event: asyncio.Event):
        """Single connection lifecycle: connect → register → message loop."""
        # Build connection URL — pass token via query param (WS doesn't support headers reliably)
        ws_url = f"{self.url}?token={self.token}"

        logger.info(f"Connecting to {self.url}...")

        ssl_context = self._build_ssl_context()

        connect_kwargs = {
            "ping_interval": 20,
            "ping_timeout": 10,
            "close_timeout": 5,
            "max_size": 10 * 1024 * 1024,  # 10 MB max message
            "additional_headers": {
                "Authorization": f"Bearer {self.token}",
                "X-Operator-Version": self.operator_version,
                "X-Cluster-Name": self.cluster_name,
            },
        }
        if ssl_context:
            connect_kwargs["ssl"] = ssl_context

        async with websockets.connect(ws_url, **connect_kwargs) as ws:
            self._ws = ws
            self._reconnect_attempts = 0  # Reset on successful connect
            self._missed_heartbeats = 0
            self._last_heartbeat_ack = time.time()

            # --- Registration handshake ---
            await ws.send(json.dumps({
                "type": "register",
                "cluster_name": self.cluster_name,
                "operator_version": self.operator_version,
                "labels": self.labels,
            }))

            # Wait for registration confirmation
            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            reg_msg = json.loads(raw)

            if reg_msg.get("type") != "registered":
                raise ConnectionError(
                    f"Registration failed: {reg_msg.get('message', 'unknown error')}"
                )

            self.operator_id = reg_msg["operator_id"]
            self.connected = True
            logger.info(
                f"Registered with control plane: "
                f"operator_id={self.operator_id}, cluster={self.cluster_name}"
            )

            # --- Run message loop + heartbeat concurrently ---
            await asyncio.gather(
                self._message_loop(ws, shutdown_event),
                self._heartbeat_loop(ws, shutdown_event),
            )

    async def _message_loop(
        self,
        ws: websockets.WebSocketClientProtocol,
        shutdown_event: asyncio.Event,
    ):
        """Receive and dispatch messages from BNK-Forge."""
        try:
            async for raw_msg in ws:
                if shutdown_event.is_set():
                    break

                try:
                    message = json.loads(raw_msg)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON from control plane: {raw_msg[:100]}")
                    continue

                msg_type = message.get("type")

                if msg_type == "command":
                    # Dispatch command to handler in background
                    asyncio.create_task(self._handle_command(ws, message))

                elif msg_type == "ping":
                    await ws.send(json.dumps({"type": "pong"}))

                elif msg_type == "pong":
                    # Heartbeat acknowledged — reset missed counter
                    self._missed_heartbeats = 0
                    self._last_heartbeat_ack = time.time()

                else:
                    logger.debug(f"Received message type: {msg_type}")

        except ConnectionClosed:
            raise
        except asyncio.CancelledError:
            raise

    async def _heartbeat_loop(
        self,
        ws: websockets.WebSocketClientProtocol,
        shutdown_event: asyncio.Event,
    ):
        """Send periodic heartbeats to BNK-Forge with missed-heartbeat detection."""
        while not shutdown_event.is_set():
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                if shutdown_event.is_set():
                    break

                # Check for missed heartbeats
                if self._missed_heartbeats >= MISSED_HEARTBEAT_THRESHOLD:
                    logger.error(
                        f"Server unresponsive: {self._missed_heartbeats} missed heartbeat acks. "
                        f"Forcing reconnect."
                    )
                    await ws.close(code=4100, reason="Heartbeat timeout")
                    break

                self._missed_heartbeats += 1

                await ws.send(json.dumps({
                    "type": "heartbeat",
                    "operator_version": self.operator_version,
                    "uptime_seconds": self.uptime_seconds,
                    "commands_executed": self._commands_executed,
                    "commands_failed": self._commands_failed,
                    "reconnect_count": self._reconnect_count,
                }))

            except (ConnectionClosed, ConnectionClosedError):
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Heartbeat send error: {e}")

    async def _handle_command(
        self,
        ws: websockets.WebSocketClientProtocol,
        message: Dict[str, Any],
    ):
        """Execute a command and stream results back."""
        cmd_id = message.get("id", "unknown")
        action = message.get("action", "unknown")
        payload = message.get("payload", {})

        logger.info(f"Command received: {action} (id={cmd_id})")

        handler = self._handlers.get(action)
        if not handler:
            await ws.send(json.dumps({
                "type": "error",
                "command_id": cmd_id,
                "error_message": f"Unknown action: {action}",
            }))
            return

        start_time = time.time()

        # Create output callback for streaming
        async def send_output(line: str):
            try:
                await ws.send(json.dumps({
                    "type": "output",
                    "command_id": cmd_id,
                    "line": line,
                }))
            except Exception:
                pass

        try:
            # Execute the handler
            result = await handler(payload, on_output=send_output)
            duration = time.time() - start_time
            self._commands_executed += 1

            # Track duration for metrics
            self._command_durations.append(duration)
            if len(self._command_durations) > 100:
                self._command_durations.pop(0)

            # Send result
            await ws.send(json.dumps({
                "type": "result",
                "command_id": cmd_id,
                "success": result.get("success", True),
                "duration_seconds": round(duration, 2),
                **result,
            }))

            logger.info(
                f"Command {action} completed: success={result.get('success', True)}, "
                f"duration={duration:.1f}s"
            )

        except Exception as e:
            duration = time.time() - start_time
            self._commands_failed += 1
            logger.error(f"Command {action} failed: {e}", exc_info=True)

            await ws.send(json.dumps({
                "type": "error",
                "command_id": cmd_id,
                "error_message": str(e),
                "duration_seconds": round(duration, 2),
            }))

    async def send_health(self, health_report: Dict[str, Any]):
        """Send a health report to the control plane."""
        if not self.connected or not self._ws:
            return

        try:
            await self._ws.send(json.dumps({
                "type": "health",
                **health_report,
            }))
        except Exception as e:
            logger.warning(f"Failed to send health report: {e}")

    async def send_llm_metrics(
        self, analyzer_namespace: str, analyzer_name: str, data: Dict[str, Any]
    ):
        """Send in-cluster LLM backend metrics for one analyzer."""
        if not self.connected or not self._ws:
            return

        try:
            await self._ws.send(json.dumps({
                "type": "llm_metrics",
                "analyzer": {"namespace": analyzer_namespace, "name": analyzer_name},
                "data": data,
            }))
        except Exception as e:
            logger.warning(f"Failed to send llm_metrics for {analyzer_namespace}/{analyzer_name}: {e}")

    def _backoff_with_jitter(self) -> float:
        """Exponential backoff with jitter: prevents thundering herd."""
        base_delay = min(MAX_BACKOFF, 2 ** self._reconnect_attempts)
        # Add ±30% jitter
        jitter = base_delay * JITTER_FACTOR * (2 * random.random() - 1)
        delay = max(1.0, base_delay + jitter)
        self._reconnect_attempts += 1
        return delay
