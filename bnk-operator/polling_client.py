"""
Polling Client — HTTP-based alternative to WebSocket for operator-to-BNK-Forge communication.

Used when the operator can't maintain a WebSocket connection (firewalls, proxies, etc.).

Flow:
  1. Register via POST /api/operators/register-poll (token auth)
  2. Poll GET /api/operators/{operator_id}/poll for pending commands
  3. Execute commands locally (same handlers as WS mode)
  4. POST results to /api/operators/{operator_id}/results/{command_id}
  5. POST heartbeat periodically

Supports TLS with custom CA certificates.
"""

import asyncio
import logging
import ssl
import time
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, Optional

import aiohttp

logger = logging.getLogger("bnk-operator.polling")


class PollingClient:
    """
    HTTP polling client for BNK-Forge communication.

    Drop-in alternative to ControlPlaneClient for environments
    where WebSocket connections are not possible.
    """

    def __init__(
        self,
        url: str,  # Base URL: https://bnk-forge
        token: str,
        cluster_name: str,
        operator_version: str = "1.0.0",
        poll_interval: int = 5,
        labels: Optional[Dict[str, str]] = None,
        tls_ca_cert: Optional[str] = None,
        tls_insecure: bool = False,
    ):
        self.url = url.rstrip("/")
        self.token = token
        self.cluster_name = cluster_name
        self.operator_version = operator_version
        self.poll_interval = poll_interval
        self.labels = labels or {}
        self._tls_ca_cert = tls_ca_cert
        self._tls_insecure = tls_insecure

        self.operator_id: Optional[str] = None
        self.connected = False
        self._start_time = time.time()
        self._commands_executed = 0
        self._commands_failed = 0
        self._reconnect_count = 0
        self._handlers: Dict[str, Callable] = {}
        self._session: Optional[aiohttp.ClientSession] = None
        self._command_durations: list = []

    def on_command(self, action: str, handler: Callable):
        """Register a command handler."""
        self._handlers[action] = handler

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
        """Build SSL context for HTTPS connections."""
        if not self.url.startswith("https://"):
            return None

        ctx = ssl.create_default_context()

        if self._tls_insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            logger.warning("TLS certificate verification DISABLED (insecure mode)")
        elif self._tls_ca_cert:
            ca_path = Path(self._tls_ca_cert)
            if ca_path.exists():
                ctx.load_verify_locations(str(ca_path))
                logger.info(f"Loaded custom CA certificate: {ca_path}")
            else:
                logger.warning(f"CA cert file not found: {ca_path}, using system CAs")

        return ctx

    def _auth_headers(self) -> Dict[str, str]:
        """Build authentication headers with registration token."""
        return {
            "Authorization": f"Bearer {self.token}",
            "X-Operator-Version": self.operator_version,
            "X-Cluster-Name": self.cluster_name,
        }

    async def run(self, shutdown_event: asyncio.Event):
        """Main polling loop."""
        ssl_ctx = self._build_ssl_context()
        connector = aiohttp.TCPConnector(ssl=ssl_ctx) if ssl_ctx else None

        async with aiohttp.ClientSession(
            headers=self._auth_headers(),
            connector=connector,
        ) as session:
            self._session = session

            # Register first
            if not await self._register(session):
                logger.error("Registration failed — cannot start polling")
                return

            self.connected = True
            logger.info(f"Polling mode active: interval={self.poll_interval}s")

            # Poll loop
            while not shutdown_event.is_set():
                try:
                    await self._poll_and_execute(session)
                except Exception as e:
                    logger.warning(f"Poll cycle error: {e}")

                # Wait for next poll or shutdown
                try:
                    await asyncio.wait_for(
                        shutdown_event.wait(),
                        timeout=self.poll_interval,
                    )
                    break  # Shutdown requested
                except asyncio.TimeoutError:
                    pass  # Normal — poll interval elapsed

            self.connected = False
            self._session = None

    async def _register(self, session: aiohttp.ClientSession) -> bool:
        """Register with BNK-Forge via REST API."""
        try:
            async with session.post(
                f"{self.url}/api/operators/register-poll",
                json={
                    "cluster_name": self.cluster_name,
                    "operator_version": self.operator_version,
                    "labels": self.labels,
                    "connectivity_mode": "polling",
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.operator_id = data.get("operator_id")
                    logger.info(f"Registered via polling: operator_id={self.operator_id}")
                    return True
                else:
                    text = await resp.text()
                    logger.error(f"Registration failed: {resp.status} {text}")
                    return False
        except Exception as e:
            logger.error(f"Registration request failed: {e}")
            return False

    async def _poll_and_execute(self, session: aiohttp.ClientSession):
        """Poll for commands and execute them."""
        if not self.operator_id:
            return

        try:
            async with session.get(
                f"{self.url}/api/operators/{self.operator_id}/poll",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    if resp.status == 401:
                        logger.error("Authentication failed — token may be revoked")
                        self.connected = False
                    else:
                        logger.warning(f"Poll failed: {resp.status}")
                    return

                data = await resp.json()
                commands = data.get("commands", [])

                # Update poll interval from server hint
                server_interval = data.get("poll_interval_seconds")
                if server_interval and isinstance(server_interval, int):
                    self.poll_interval = max(1, min(60, server_interval))

                if commands:
                    logger.info(f"Received {len(commands)} commands")

                for cmd in commands:
                    asyncio.create_task(
                        self._execute_command(session, cmd)
                    )

        except asyncio.TimeoutError:
            logger.debug("Poll timeout (normal)")
        except Exception as e:
            logger.warning(f"Poll error: {e}")

    async def _execute_command(self, session: aiohttp.ClientSession, cmd: Dict):
        """Execute a command and POST the result."""
        cmd_id = cmd.get("id", "unknown")
        action = cmd.get("action", "unknown")
        payload = cmd.get("payload", {})

        logger.info(f"Executing command: {action} (id={cmd_id})")

        handler = self._handlers.get(action)
        if not handler:
            await self._submit_result(session, cmd_id, {
                "success": False,
                "error_message": f"Unknown action: {action}",
            })
            return

        start_time = time.time()
        output_lines = []

        async def on_output(line: str):
            output_lines.append(line)

        try:
            result = await handler(payload, on_output=on_output)
            duration = time.time() - start_time
            self._commands_executed += 1

            self._command_durations.append(duration)
            if len(self._command_durations) > 100:
                self._command_durations.pop(0)

            await self._submit_result(session, cmd_id, {
                "success": result.get("success", True),
                "result": result,
                "output_lines": output_lines,
                "duration_seconds": round(duration, 2),
            })

        except Exception as e:
            duration = time.time() - start_time
            self._commands_failed += 1
            await self._submit_result(session, cmd_id, {
                "success": False,
                "error_message": str(e),
                "output_lines": output_lines,
                "duration_seconds": round(duration, 2),
            })

    async def _submit_result(self, session: aiohttp.ClientSession, cmd_id: str, result: Dict):
        """POST command result back to BNK-Forge with retry."""
        for attempt in range(3):
            try:
                async with session.post(
                    f"{self.url}/api/operators/{self.operator_id}/results/{cmd_id}",
                    json=result,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        return
                    logger.warning(f"Result submission failed: {resp.status} (attempt {attempt + 1})")
            except Exception as e:
                logger.error(f"Failed to submit result for {cmd_id} (attempt {attempt + 1}): {e}")

            if attempt < 2:
                await asyncio.sleep(1.0 * (attempt + 1))

        logger.error(f"Exhausted retries submitting result for {cmd_id}")

    async def send_health(self, health_report: Dict[str, Any]):
        """Send health report via HTTP POST."""
        if not self._session or not self.operator_id:
            return

        try:
            async with self._session.post(
                f"{self.url}/api/operators/{self.operator_id}/health",
                json=health_report,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    logger.debug(f"Health submission: {resp.status}")
        except Exception as e:
            logger.warning(f"Health submission error: {e}")

    async def send_llm_metrics(
        self, analyzer_namespace: str, analyzer_name: str, data: Dict[str, Any]
    ):
        """Send in-cluster LLM backend metrics for one analyzer via HTTP POST."""
        if not self._session or not self.operator_id:
            return

        try:
            async with self._session.post(
                f"{self.url}/api/operators/{self.operator_id}/llm-metrics",
                json={"namespace": analyzer_namespace, "name": analyzer_name, "data": data},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    logger.debug(
                        f"LLM metrics submission: {resp.status} "
                        f"({analyzer_namespace}/{analyzer_name})"
                    )
        except Exception as e:
            logger.warning(
                f"LLM metrics submission error for {analyzer_namespace}/{analyzer_name}: {e}"
            )
