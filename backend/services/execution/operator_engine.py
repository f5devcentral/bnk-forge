"""
Operator Engine — executes K8s operations via a remote BNK Operator agent.

This is one of three DeploymentEngine implementations:
  1. OpenTofuEngine   — local subprocess, for infrastructure (VPC, EKS, etc.)
  2. KubernetesEngine — direct kr8s, for clusters with kubeconfig access
  3. OperatorEngine   — WebSocket to remote operator, for clusters with agent installed

The OperatorEngine sends commands over WebSocket to the operator running inside
the target cluster. The operator executes kr8s/helm locally and streams results
back. This eliminates the need for kubeconfig access from BNK-Forge.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import UTC
from typing import Any

from services.execution.engine_interface import (
    DeploymentEngine,
    ModuleContext,
    OperationResult,
    PlanResult,
)
from services.execution.k8s_catalog_payload import (
    build_helm_payload,
    build_manifest_payload,
    collect_outputs_from_pack,
    is_catalog_k8s_context,
)
from services.operator_registry import operator_connections

logger = logging.getLogger(__name__)


class OperatorEngine(DeploymentEngine):
    """
    Executes K8s operations via a remote BNK Operator agent.

    The operator_id identifies which connected operator to send commands to.
    Commands are dispatched via:
      - WebSocket (OperatorConnectionManager) for WS-connected operators
      - DB command queue (operator_polling) for polling-mode operators

    The engine auto-detects the mode and routes accordingly.
    """

    def __init__(self, operator_id: str, connectivity_mode: str = "direct_ws"):
        self.operator_id = operator_id
        self.connectivity_mode = connectivity_mode

    def health_check(self) -> bool:
        """
        Check that the operator is connected and heartbeat is recent.

        For WS-mode: verify WebSocket is connected.
        For polling-mode: verify last heartbeat < 60s ago (via DB).
        """
        if self.connectivity_mode == "polling":
            # For polling operators, check heartbeat recency via DB
            try:
                from datetime import datetime

                from database import SessionLocal
                from models import ConnectedOperator

                db = SessionLocal()
                try:
                    operator = (
                        db.query(ConnectedOperator)
                        .filter(ConnectedOperator.operator_id == self.operator_id)
                        .first()
                    )
                    if not operator or not operator.is_connected:
                        return False
                    if operator.last_heartbeat_at:
                        age = (datetime.now(UTC) - operator.last_heartbeat_at).total_seconds()
                        return age < 60
                    return False
                finally:
                    db.close()
            except Exception:
                return False
        else:
            # For WS-mode: check live WebSocket connection
            return operator_connections.is_connected(self.operator_id)

    def _run_async(self, coro):
        """Run an async coroutine from sync context (Celery workers are sync)."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Already in an async context — use a new thread
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, coro)
                    return future.result(timeout=600)
            else:
                return loop.run_until_complete(coro)
        except RuntimeError:
            # No event loop — create one
            return asyncio.run(coro)

    def _is_polling_mode(self) -> bool:
        """Check if this operator uses polling (not WebSocket)."""
        if self.connectivity_mode == "polling":
            return True
        # Also check: if WS mode but not actually connected via WS,
        # the operator might have reconnected in polling mode
        if not operator_connections.is_connected(self.operator_id):
            return True  # Fall through to polling if WS is down
        return False

    def _send_command(
        self,
        action: str,
        payload: dict[str, Any],
        timeout: float = 300.0,
        on_output: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """
        Send a command to the operator and wait for a result.

        Routes to WebSocket or DB polling queue based on operator's connectivity mode.
        """
        # If operator is connected via WebSocket, use the fast WS path
        if operator_connections.is_connected(self.operator_id) and not self.connectivity_mode == "polling":
            return self._send_command_ws(action, payload, timeout, on_output)

        # Otherwise, use the DB polling queue
        return self._send_command_polling(action, payload, timeout, on_output)

    def _send_command_ws(
        self,
        action: str,
        payload: dict[str, Any],
        timeout: float = 300.0,
        on_output: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Send command via WebSocket (low latency, streaming output)."""

        async def _do():
            async_callback = None
            if on_output:
                async def _on_output(line: str):
                    on_output(line)
                async_callback = _on_output

            return await operator_connections.send_command(
                operator_id=self.operator_id,
                command={"action": action, "payload": payload},
                timeout=timeout,
                on_output=async_callback,
            )

        return self._run_async(_do())

    def _send_command_polling(
        self,
        action: str,
        payload: dict[str, Any],
        timeout: float = 300.0,
        on_output: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """
        Send command via DB polling queue (for polling-mode operators).

        Enqueues the command, then polls the DB for the result.
        Higher latency than WS but works through any firewall.
        """
        from database import SessionLocal
        from routes.operator_polling import enqueue_command, wait_for_result

        if on_output:
            on_output(f"[operator:polling] Enqueuing {action} command...")

        db = SessionLocal()
        try:
            command_id = enqueue_command(
                db=db,
                operator_id=self.operator_id,
                action=action,
                payload=payload,
                timeout_seconds=int(timeout),
            )

            if on_output:
                on_output(f"[operator:polling] Command {command_id} queued, waiting for operator to pick up...")

            # Poll DB for result (blocking, with timeout)
            result = wait_for_result(
                db=db,
                command_id=command_id,
                timeout=timeout,
                poll_interval=1.0,
            )

            if result is None:
                return {
                    "success": False,
                    "error_message": f"No result received for command {command_id}",
                }

            if on_output:
                for line in result.get("output_lines", []):
                    on_output(line)

            return result

        finally:
            db.close()

    def init(
        self,
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None = None,
    ) -> OperationResult:
        """
        Initialize module — for K8s engine this is a connectivity check.
        Sends a quick scan_cluster command to verify the operator is reachable.
        """
        start = time.time()
        if on_output:
            on_output(f"[operator] Verifying connectivity to operator {self.operator_id}...")

        # Keep explicit WS connectivity guard for direct mode, but do not block
        # polling-mode operators (they are expected to execute through queueing).
        if self.connectivity_mode != "polling" and not operator_connections.is_connected(self.operator_id):
            return OperationResult(
                success=False,
                error_message=f"Operator {self.operator_id} is not connected",
                duration_seconds=time.time() - start,
            )

        try:
            result = self._send_command("get_health", {}, timeout=30.0, on_output=on_output)
            success = result.get("success", False)
            if on_output:
                if success:
                    cluster = result.get("cluster", {})
                    on_output(
                        f"[operator] Connected — K8s {cluster.get('kubernetes_version', '?')}, "
                        f"{cluster.get('nodes_ready', '?')}/{cluster.get('node_count', '?')} nodes ready"
                    )
                else:
                    on_output(f"[operator] Health check failed: {result.get('error_message', 'unknown')}")

            return OperationResult(
                success=success,
                stdout=str(result),
                duration_seconds=time.time() - start,
                error_message=result.get("error_message") if not success else None,
            )

        except Exception as e:
            return OperationResult(
                success=False,
                error_message=f"Operator connectivity check failed: {e}",
                duration_seconds=time.time() - start,
            )

    def plan(
        self,
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None = None,
    ) -> PlanResult:
        """Quick plan — operator doesn't do full diff, just validates what would be sent."""
        try:
            if not is_catalog_k8s_context(ctx):
                raise ValueError(f"Module {ctx.path} is not a catalog K8s module — cannot plan via operator")

            deploy_model = (ctx.deploy_model or "").strip().lower()
            if deploy_model == "helm":
                payload = build_helm_payload(ctx, ctx.variables)
                if on_output:
                    on_output(f"[operator] Plan: Helm chart {payload['chart_ref']} would be installed")
                return PlanResult(
                    has_changes=True,
                    adds=1,
                    details=f"Helm chart {payload['chart_ref']} to install via operator",
                )

            payload = build_manifest_payload(ctx, ctx.variables)
            count = len(payload["manifests"])
            if on_output:
                on_output(f"[operator] Plan: {count} manifests would be applied for {ctx.path}")
            return PlanResult(
                has_changes=count > 0,
                adds=count,
                details=f"{count} K8s resources to apply via operator",
            )
        except Exception as e:
            return PlanResult(has_changes=False, details=f"Plan failed: {e}")

        return PlanResult(has_changes=False)

    def apply(
        self,
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None = None,
    ) -> OperationResult:
        """
        Deploy the module via the operator.

        Renders manifests/values locally, sends them to the operator,
        operator applies them locally via kr8s/helm.
        """
        start = time.time()

        try:
            if not is_catalog_k8s_context(ctx):
                raise ValueError(f"Module {ctx.path} is not a catalog K8s module — cannot apply via operator")

            deploy_model = (ctx.deploy_model or "").strip().lower()
            if deploy_model == "helm":
                payload_def = build_helm_payload(ctx, ctx.variables)
                if on_output:
                    on_output(f"[operator] Installing Helm chart {payload_def['chart_ref']}...")

                payload = {
                    "chart_ref": payload_def["chart_ref"],
                    "release_name": payload_def["release_name"],
                    "namespace": payload_def["namespace"],
                    "values": payload_def["values"],
                    "timeout": f"{int(payload_def['timeout'])}s",
                    "create_namespace": payload_def["create_namespace"],
                }
                if payload_def.get("chart_version"):
                    payload["version"] = payload_def["chart_version"]

                if ctx.credentials_env:
                    registry_auth = self._build_registry_auth(ctx.credentials_env)
                    if registry_auth:
                        payload["registry_auth"] = registry_auth

                result = self._send_command(
                    action="install_helm",
                    payload=payload,
                    timeout=int(payload_def["timeout"]) + 60,
                    on_output=on_output,
                )
            else:
                payload_def = build_manifest_payload(ctx, ctx.variables)
                manifests = payload_def["manifests"]
                if on_output:
                    on_output(f"[operator] Sending {len(manifests)} manifests to operator...")

                result = self._send_command(
                    action="apply_manifests",
                    payload={
                        "manifests": manifests,
                        "namespace": payload_def["namespace"],
                        "wait_for_ready": False,
                        "timeout": payload_def["timeout"],
                    },
                    timeout=int(payload_def["timeout"]) + 30,
                    on_output=on_output,
                )

            success = result.get("success", False)
            outputs: dict[str, Any] = result.get("outputs", {}) if success else {}
            if success and is_catalog_k8s_context(ctx):
                pack_outputs = collect_outputs_from_pack(ctx, ctx.variables)
                if pack_outputs:
                    outputs = pack_outputs

            return OperationResult(
                success=success,
                stdout="\n".join(result.get("output_lines", [])),
                resources_created=result.get("resources_created", 0),
                duration_seconds=time.time() - start,
                error_message=result.get("error_message") if not success else None,
                outputs=outputs,
            )

        except (ConnectionError, TimeoutError, RuntimeError) as e:
            return OperationResult(
                success=False,
                error_message=str(e),
                duration_seconds=time.time() - start,
            )

    def destroy(
        self,
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None = None,
    ) -> OperationResult:
        """Destroy module resources via the operator."""
        start = time.time()

        try:
            if not is_catalog_k8s_context(ctx):
                raise ValueError(f"Module {ctx.path} is not a catalog K8s module — cannot destroy via operator")

            deploy_model = (ctx.deploy_model or "").strip().lower()
            if deploy_model == "helm":
                payload_def = build_helm_payload(ctx, ctx.variables)
                if on_output:
                    on_output(f"[operator] Uninstalling Helm release {payload_def['release_name']}...")

                result = self._send_command(
                    action="uninstall_helm",
                    payload={
                        "release_name": payload_def["release_name"],
                        "namespace": payload_def["namespace"],
                    },
                    timeout=300,
                    on_output=on_output,
                )
            else:
                payload_def = build_manifest_payload(ctx, ctx.variables)
                manifests = payload_def["manifests"]
                if on_output:
                    on_output(f"[operator] Destroying {len(manifests)} resources via operator...")

                result = self._send_command(
                    action="destroy_manifests",
                    payload={
                        "manifests": manifests,
                        "namespace": payload_def["namespace"],
                        "ignore_not_found": True,
                    },
                    timeout=300,
                    on_output=on_output,
                )

            success = result.get("success", False)
            return OperationResult(
                success=success,
                stdout="\n".join(result.get("output_lines", [])),
                resources_destroyed=result.get("resources_destroyed", 0),
                duration_seconds=time.time() - start,
                error_message=result.get("error_message") if not success else None,
            )

        except (ConnectionError, TimeoutError, RuntimeError) as e:
            return OperationResult(
                success=False,
                error_message=str(e),
                duration_seconds=time.time() - start,
            )

    def get_outputs(self, ctx: ModuleContext) -> dict[str, Any]:
        """Read outputs from pack manifest declarations for catalog K8s modules."""
        if is_catalog_k8s_context(ctx):
            return collect_outputs_from_pack(ctx, ctx.variables)
        return {}

    def _build_registry_auth(self, credentials_env: dict[str, str]) -> dict[str, str] | None:
        """Build OCI registry auth from credentials env."""
        # Look for F5 FAR pull secret in the credentials
        # The cne_pull_secret contains base64-encoded Docker config JSON
        import base64
        import json

        for key, value in credentials_env.items():
            if "pull_secret" in key.lower() or "docker" in key.lower():
                try:
                    config = json.loads(base64.b64decode(value))
                    auths = config.get("auths", {})
                    for registry_url, auth_data in auths.items():
                        username = auth_data.get("username", "")
                        password = auth_data.get("password", "")
                        if username and password:
                            return {
                                "url": registry_url,
                                "username": username,
                                "password": password,
                            }
                except Exception:
                    continue
        return None
