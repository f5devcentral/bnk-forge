"""SSH DeploymentEngine — executes bare-metal steps via paramiko.

Implements the DeploymentEngine ABC for modules that execute shell commands
on bare-metal hosts via SSH. The engine is generic — each SSHModule subclass
defines the commands to run. The engine handles session management,
connectivity-risk flows, DPU relay, and output streaming.

Design:
  - No DB access in the hot path (all credentials resolved in Celery task).
  - Module definitions (SSHModule subclasses) drive the actual shell commands.
  - Streaming output via on_output for real-time WebSocket log updates.
  - Connectivity-risk pattern: pre-stage → execute → wait-for-reconnect → validate.
"""

import logging
import shlex
import time
from typing import Any

from services.bare_metal.ssh_session import SSHSession
from services.execution.engine_interface import (
    DeploymentEngine,
    ModuleContext,
    OperationResult,
    PlanResult,
)

logger = logging.getLogger(__name__)


class HostRelayDpuSession:
    """Relay SSH commands to a DPU via shell ssh through the host.

    Used when the DPU is reachable via IPv6 link-local address (fe80::...%IF)
    which paramiko cannot handle due to missing scope_id support.
    Commands are executed on the host as:
        ssh -6 -o StrictHostKeyChecking=no user@'fe80::...%IF' 'command'
    """

    def __init__(
        self,
        host_session: "SSHSession",
        dpu_addr: str,
        dpu_username: str = "ubuntu",
        dpu_password: str | None = None,
    ):
        self.host_session = host_session
        self.dpu_addr = dpu_addr  # fe80::...%IF
        self.dpu_username = dpu_username
        self.dpu_password = dpu_password
        # Expose these for compatibility with code that reads session attributes
        self.host = dpu_addr
        self.port = 22
        self.username = dpu_username
        self.password = dpu_password
        self.private_key_content = None
        self.jumphost_chain = None

    def execute(self, command: str, timeout: int = 30) -> Any:
        """Execute a command on the DPU via host relay.

        Wraps the command in a shell ssh call through the host session.
        Uses sshpass for password auth if available.
        """
        # Escape single quotes in the command for the outer shell
        escaped_cmd = command.replace("'", "'\\''")

        ssh_opts = (
            "-6 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
            "-o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3"
        )

        # dpu_username/dpu_addr/dpu_password come from a decrypted credential; quote
        # them so a shell metacharacter (e.g. a "'" in the password) can't break out
        # of the host-side command. escaped_cmd is already single-quote-escaped above.
        dest = shlex.quote(f"{self.dpu_username}@{self.dpu_addr}")
        if self.dpu_password:
            # SSHPASS env-var mode (sshpass -e) keeps the password out of the
            # relay host's argv (`ps`), matching RemoteSSHSession (ssh_session.py).
            relay_cmd = (
                f"SSHPASS={shlex.quote(self.dpu_password)} sshpass -e ssh {ssh_opts} "
                f"{dest} '{escaped_cmd}'"
            )
        else:
            relay_cmd = (
                f"ssh {ssh_opts} "
                f"{dest} '{escaped_cmd}'"
            )

        return self.host_session.execute(relay_cmd, timeout=timeout)

    def close(self) -> None:
        """Close is a no-op — we don't own the host session."""
        pass


class SSHEngine(DeploymentEngine):
    """
    Engine for executing bare-metal operations via SSH.

    Each module's Python class (SSHModule subclass) defines:
      - plan_commands: shell commands that probe current state
      - apply_commands: shell commands that execute the step
      - validate_commands: shell commands that verify success
      - parse_plan_output: interprets plan_commands output
      - parse_apply_output: interprets apply result for outputs

    The engine handles:
      - Building SSHSession from ModuleContext SSH fields
      - Connectivity-risk pre-stage / wait / validate cycle
      - DPU relay (SSH to host, then SSH through host to DPU)
      - Streaming output to on_output callback
      - OperationResult construction
    """

    def __init__(self, db_session_factory=None):
        """
        Args:
            db_session_factory: Optional callable returning a DB session.
                Only needed for get_outputs() which reads ProjectModule.outputs.
                The engine hot path (init/plan/apply/destroy) never touches DB.
        """
        self._db_factory = db_session_factory

    def _update_stage(self, ctx: ModuleContext, detail: str) -> None:
        """Write stage_detail to ProjectModule for poll-based UI progress.

        Best-effort: failures are logged at debug level and never block
        module execution. Mirrors the pattern from dpu_flash_service._mark_stage.
        """
        if not self._db_factory or not getattr(ctx, "module_id", None):
            return
        try:
            db = self._db_factory()
            from models.project import ProjectModule
            db.query(ProjectModule).filter(
                ProjectModule.id == ctx.module_id,
            ).update({"stage_detail": detail})
            db.commit()
        except Exception:
            logger.debug("stage_detail write failed for module %s", getattr(ctx, "module_id", "?"))
        finally:
            try:
                db.close()
            except Exception:
                pass

    def health_check(self) -> bool:
        """SSH engine is always available (paramiko is a library, not a service)."""
        return True

    # ── Session builders ─────────────────────────────────────────────

    def _build_host_session(self, ctx: ModuleContext) -> SSHSession:
        """Build SSHSession targeting the bare-metal host."""
        return SSHSession(
            host=ctx.ssh_host,
            username=ctx.ssh_username,
            port=ctx.ssh_port,
            private_key_path=None,
            private_key_content=ctx.ssh_private_key_content,
            password=ctx.ssh_password,
            jumphost_chain=ctx.jumphost_chain,
            connect_timeout=15,
        )

    def _build_dpu_session(self, ctx: ModuleContext) -> Any:
        """Build SSHSession targeting the DPU via host relay.

        The DPU is typically at 192.168.100.2, reachable only through
        the host's rshim/tmfifo interface. We chain: jumphost → host → DPU.

        For dual_dpu_obmc (IPv6 link-local DPU address containing '%'),
        paramiko cannot handle scope IDs, so we use a host-relay approach
        where the host session runs 'ssh -6' commands to reach the DPU.
        """
        dpu_host = ctx.dpu_host or "192.168.100.2"

        # IPv6 link-local with scope ID — paramiko can't handle this
        if "%" in dpu_host:
            host_session = self._build_host_session(ctx)
            return HostRelayDpuSession(
                host_session=host_session,
                dpu_addr=dpu_host,
                dpu_username=ctx.dpu_username or "ubuntu",
                dpu_password=ctx.dpu_password,
            )

        host_hop = {
            "host": ctx.ssh_host,
            "port": ctx.ssh_port,
            "username": ctx.ssh_username,
            "password": ctx.ssh_password,
            "private_key_content": ctx.ssh_private_key_content,
            "passphrase": ctx.ssh_key_passphrase,
        }
        chain = list(ctx.jumphost_chain or []) + [host_hop]

        return SSHSession(
            host=dpu_host,
            username=ctx.dpu_username or "ubuntu",
            port=22,
            private_key_content=ctx.dpu_private_key_content,
            password=ctx.dpu_password,
            jumphost_chain=chain,
            connect_timeout=15,
        )

    def _get_session(self, ctx: ModuleContext) -> Any:
        """Get the right session based on module target variable."""
        if ctx.variables.get("target") == "dpu":
            return self._build_dpu_session(ctx)
        return self._build_host_session(ctx)

    # ── Engine interface implementation ──────────────────────────────

    def init(self, ctx: ModuleContext, on_output=None) -> OperationResult:
        """Validate SSH connectivity and module prerequisites."""
        started = time.monotonic()
        try:
            session = self._get_session(ctx)
            target = ctx.variables.get("target", "host")
            if on_output:
                on_output(f"[ssh] Validating connectivity to {target} ({session.host})")

            result = session.execute("echo 'SSH_OK' && hostname && uname -r", timeout=15)

            if result.exit_code != 0:
                return OperationResult(
                    success=False,
                    duration_seconds=time.monotonic() - started,
                    error_message=f"SSH connectivity check failed: {result.stderr}",
                    error_suggestion="Verify host IP, credentials, and network access",
                )

            if on_output:
                on_output(f"[ssh] Connected: {result.stdout.strip()}")

            # Validate required inputs before proceeding
            module_def = self._get_module_def(ctx.path)
            if module_def and hasattr(module_def, "validate_inputs"):
                input_errors = module_def.validate_inputs(ctx.variables)
                if input_errors:
                    error_list = "; ".join(input_errors)
                    return OperationResult(
                        success=False,
                        duration_seconds=time.monotonic() - started,
                        error_message=f"Missing required inputs: {error_list}",
                        error_suggestion="Set required variables via Edit Variables before running this module",
                    )

            # Run module-specific prerequisite check
            if module_def and hasattr(module_def, "prereq_commands"):
                prereq_cmds = module_def.prereq_commands(ctx.variables)
                for cmd in prereq_cmds:
                    pr = session.execute(cmd, timeout=30)
                    if pr.exit_code != 0:
                        return OperationResult(
                            success=False,
                            duration_seconds=time.monotonic() - started,
                            error_message=f"Prerequisite check failed: {pr.stderr}",
                        )

            return OperationResult(
                success=True,
                stdout=result.stdout,
                duration_seconds=time.monotonic() - started,
            )
        except Exception as e:
            return OperationResult(
                success=False,
                duration_seconds=time.monotonic() - started,
                error_message=f"SSH init failed: {e}",
            )

    def plan(self, ctx: ModuleContext, on_output=None) -> PlanResult:
        """Probe current state to determine if apply is needed."""
        try:
            module_def = self._get_module_def(ctx.path)

            if not module_def:
                return PlanResult(has_changes=True, details="No module definition — will execute")

            # Validate required inputs before probing
            if hasattr(module_def, "validate_inputs"):
                input_errors = module_def.validate_inputs(ctx.variables)
                if input_errors:
                    error_list = "; ".join(input_errors)
                    return PlanResult(
                        has_changes=False,
                        details=f"Missing required inputs: {error_list}",
                    )

            plan_cmds = module_def.plan_commands(ctx.variables)
            if not plan_cmds:
                return PlanResult(has_changes=True, details="Module has no plan commands — will execute")

            if on_output:
                on_output(f"[ssh] Probing current state for {ctx.path}")

            session = self._get_session(ctx)
            combined_output: list[str] = []

            for cmd in plan_cmds:
                result = session.execute(cmd, timeout=60)
                combined_output.append(result.stdout)
                if on_output:
                    for line in result.stdout.splitlines():
                        on_output(f"[ssh] {line}")

            has_changes = module_def.parse_plan_output(
                "\n".join(combined_output), ctx.variables,
            )

            return PlanResult(
                has_changes=has_changes,
                details="\n".join(combined_output),
            )
        except Exception as e:
            return PlanResult(has_changes=True, details=f"Plan probe failed ({e}) — will execute")

    def apply(self, ctx: ModuleContext, on_output=None) -> OperationResult:
        """Execute the SSH step with pre-stage/execute/wait/validate pattern."""
        started = time.monotonic()
        try:
            module_def = self._get_module_def(ctx.path)

            if not module_def:
                return OperationResult(
                    success=False,
                    duration_seconds=time.monotonic() - started,
                    error_message=f"No SSHModule definition found for {ctx.path}",
                )

            # Validate required inputs before running any commands
            if hasattr(module_def, "validate_inputs"):
                input_errors = module_def.validate_inputs(ctx.variables)
                if input_errors:
                    error_list = "; ".join(input_errors)
                    self._update_stage(ctx, f"Failed: {error_list[:100]}")
                    return OperationResult(
                        success=False,
                        duration_seconds=time.monotonic() - started,
                        error_message=f"Missing required inputs: {error_list}",
                        error_suggestion="Set required variables via Edit Variables before running this module",
                    )

            if on_output:
                on_output(f"[ssh] Applying {ctx.path}")
            self._update_stage(ctx, f"Connecting to {ctx.variables.get('target', 'host')}...")

            session = self._get_session(ctx)

            # --- Pre-stage for connectivity risk ---
            if module_def.connectivity_risk:
                self._update_stage(ctx, "Pre-staging: connectivity preservation...")
                pre_cmds = module_def.pre_stage_commands(ctx.variables)
                for cmd in pre_cmds:
                    if on_output:
                        on_output(f"[ssh:pre-stage] {cmd[:80]}...")
                    r = session.execute(cmd, timeout=120)
                    if r.exit_code != 0:
                        self._update_stage(ctx, f"Failed: {r.stderr[:100]}")
                        return OperationResult(
                            success=False,
                            stdout=r.stdout,
                            stderr=r.stderr,
                            duration_seconds=time.monotonic() - started,
                            error_message=f"Pre-stage failed: {r.stderr}",
                        )

            # --- Try Python execution path ---
            # Modules that override execute() get a live session and do
            # their own SSH calls + parsing in Python. This replaces
            # apply_commands() + parse_apply_output() for complex modules.
            from modules.base import SSHModule

            use_python_path = type(module_def).execute is not SSHModule.execute

            if use_python_path:
                self._update_stage(ctx, "Executing...")
                if on_output:
                    on_output(f"[ssh] Executing {ctx.path} (Python path)")

                # Wrap on_output to also update stage_detail with each line,
                # so the UI card shows live progress (not just "Executing...")
                _raw_on_output = on_output or (lambda _: None)

                def _tracked_output(line: str) -> None:
                    _raw_on_output(line)
                    # Use the line content (trimmed) as the stage detail
                    short = line.strip()[:120]
                    if short:
                        self._update_stage(ctx, short)

                outputs = module_def.execute(
                    session, ctx.variables, _tracked_output,
                )
                all_stdout: list[str] = []  # Not applicable for Python path
                all_stderr: list[str] = []
            else:
                # --- Existing shell-string path (unchanged) ---
                apply_cmds = module_def.apply_commands(ctx.variables)
                all_stdout: list[str] = []
                all_stderr: list[str] = []

                for cmd_idx, cmd in enumerate(apply_cmds, 1):
                    self._update_stage(ctx, f"Executing command {cmd_idx}/{len(apply_cmds)}...")
                    if on_output:
                        on_output(f"[ssh:exec:{cmd_idx}/{len(apply_cmds)}] {cmd[:100]}...")

                    # Use streaming for long-running or connectivity-risk commands
                    if module_def.connectivity_risk or module_def.estimated_duration > 60:
                        r = self._execute_streaming(session, cmd, module_def, all_stdout, on_output)
                        if r is None:
                            # SSH dropped during connectivity-risk op — expected
                            break
                    else:
                        r = session.execute(cmd, timeout=module_def.timeout)
                        all_stdout.append(r.stdout)
                        all_stderr.append(r.stderr)

                        if on_output and r.stdout.strip():
                            for stdout_line in r.stdout.strip().splitlines():
                                on_output(f"  {stdout_line}")

                        if r.exit_code != 0 and not module_def.connectivity_risk:
                            err_msg = f"Command failed (exit {r.exit_code}): {r.stderr[:500]}"
                            self._update_stage(ctx, f"Failed: {err_msg[:100]}")
                            return OperationResult(
                                success=False,
                                stdout="\n".join(all_stdout),
                                stderr="\n".join(all_stderr),
                                duration_seconds=time.monotonic() - started,
                                error_message=err_msg,
                            )

                # --- Parse outputs (shell path only) ---
                outputs: dict[str, Any] = {}
                if hasattr(module_def, "parse_apply_output"):
                    outputs = module_def.parse_apply_output(
                        "\n".join(all_stdout), ctx.variables,
                    )

            # --- Wait for reconnect if connectivity-risk ---
            if module_def.connectivity_risk:
                self._update_stage(ctx, "Waiting for SSH reconnect...")
                wait_ok = self._wait_for_reconnect(
                    ctx, on_output, timeout=module_def.reconnect_timeout,
                )
                if not wait_ok:
                    self._update_stage(ctx, "Failed: Host did not reconnect")
                    return OperationResult(
                        success=False,
                        stdout="\n".join(all_stdout),
                        duration_seconds=time.monotonic() - started,
                        error_message="Host did not reconnect after connectivity-risk operation",
                        error_suggestion="Check host console access. Fallback timer may have reverted.",
                    )

            # --- Validate ---
            # Re-create session after potential reconnect
            if module_def.connectivity_risk:
                session = self._get_session(ctx)

            self._update_stage(ctx, "Validating...")
            validate_cmds = module_def.validate_commands(ctx.variables)
            for cmd in validate_cmds:
                r = session.execute(cmd, timeout=60)
                if on_output:
                    on_output(f"[ssh:validate] {r.stdout.strip()}")
                if r.exit_code != 0:
                    self._update_stage(ctx, f"Failed: {r.stderr[:100]}")
                    return OperationResult(
                        success=False,
                        stdout="\n".join(all_stdout),
                        stderr=r.stderr,
                        duration_seconds=time.monotonic() - started,
                        error_message=f"Validation failed: {r.stderr}",
                    )

            self._update_stage(ctx, "Complete")
            return OperationResult(
                success=True,
                outputs=outputs,
                stdout="\n".join(all_stdout),
                stderr="\n".join(all_stderr),
                duration_seconds=time.monotonic() - started,
            )
        except Exception as e:
            logger.exception("SSHEngine.apply failed for %s", ctx.path)
            self._update_stage(ctx, f"Failed: {str(e)[:100]}")
            return OperationResult(
                success=False,
                duration_seconds=time.monotonic() - started,
                error_message=f"SSH apply failed: {e}",
            )

    def destroy(self, ctx: ModuleContext, on_output=None) -> OperationResult:
        """Reverse the module's apply where it defines a destroy() (ADR-204 BNK ports).

        Hardware modules (flash, networking, kubeadm) are not reversible and keep
        the historical no-op. BNK-layer SSH ports (BnkSSHModule) port the
        poc-deployer revert scripts: helm uninstall / kubectl delete in reverse.
        """
        started = time.monotonic()
        from modules.bare_metal.bnk_ssh_base import BnkSSHModule

        module_def = self._get_module_def(ctx.path)
        if isinstance(module_def, BnkSSHModule):
            try:
                session = self._get_session(ctx)
                if on_output:
                    on_output(f"[ssh] Destroying {ctx.path}")
                outputs = module_def.destroy(session, ctx.variables, on_output or (lambda _: None))
                return OperationResult(
                    success=True,
                    outputs=outputs or {},
                    duration_seconds=time.monotonic() - started,
                )
            except Exception as e:
                logger.exception("SSHEngine.destroy failed for %s", ctx.path)
                return OperationResult(
                    success=False,
                    duration_seconds=time.monotonic() - started,
                    error_message=f"SSH destroy failed: {e}",
                )

        if on_output:
            on_output(
                f"[ssh] Destroy is a no-op for {ctx.path}. "
                "Hardware state cannot be automatically reversed.",
            )
        return OperationResult(
            success=True,
            stdout=f"Destroy not applicable for SSH module {ctx.path}",
            duration_seconds=0.0,
        )

    def get_outputs(self, ctx: ModuleContext) -> dict[str, Any]:
        """Read outputs from last apply (stored in ProjectModule.outputs)."""
        if self._db_factory:
            db = self._db_factory()
            try:
                from models import ProjectModule

                module = db.query(ProjectModule).filter(
                    ProjectModule.id == ctx.module_id,
                ).first()
                return module.outputs or {} if module else {}
            finally:
                db.close()
        return {}

    # ── Helpers ──────────────────────────────────────────────────────

    def _get_module_def(self, module_path: str):
        """Look up SSHModule definition from the module registry."""
        from services.registry import ServiceRegistry

        registry = ServiceRegistry.get().module_registry
        return registry.get(module_path)

    def _execute_streaming(self, session, cmd, module_def, all_stdout, on_output):
        """Execute a command with streaming output. Returns SSHResult or None on expected drop."""
        gen = session.execute_streaming(cmd, timeout=module_def.timeout)
        try:
            for line in gen:
                if on_output:
                    on_output(f"[ssh] {line}")
                all_stdout.append(line)
            # Generator exhausted normally — get the SSHResult
            return getattr(gen, "value", None)
        except StopIteration as si:
            return si.value
        except Exception:
            if module_def.connectivity_risk:
                # SSH drop during connectivity-risk op is expected
                return None
            raise

    def _wait_for_reconnect(self, ctx: ModuleContext, on_output, timeout: int = 600) -> bool:
        """Poll SSH until reconnection or timeout."""
        if on_output:
            on_output(f"[ssh] Waiting for SSH reconnect (timeout: {timeout}s)...")

        start = time.monotonic()
        interval = 10

        while time.monotonic() - start < timeout:
            elapsed = int(time.monotonic() - start)
            if on_output:
                on_output(f"[ssh] Reconnect attempt... ({elapsed}s / {timeout}s)")
            self._update_stage(ctx, f"Waiting for SSH reconnect ({elapsed}s / {timeout}s)...")

            try:
                session = self._get_session(ctx)
                result = session.execute("echo 'RECONNECTED'", timeout=10)
                if result.exit_code == 0:
                    if on_output:
                        on_output(f"[ssh] Reconnected after {elapsed}s")
                    return True
            except Exception:
                pass  # Expected — host is still rebooting/reconfiguring

            time.sleep(interval)

        return False
