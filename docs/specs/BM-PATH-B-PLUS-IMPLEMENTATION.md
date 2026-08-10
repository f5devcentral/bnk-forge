# Path B+ Implementation Spec — SSHEngine + Bare-Metal Blueprint

> **Status:** APPROVED  
> **Date:** 2026-04-16  
> **Parent Decision:** `docs/specs/BM-EXECUTION-PATHS-DECISION.md` → Path B+  
> **Scope:** SSHEngine for Phase 1-3, existing K8s modules for Phase 4, blueprint wiring  
> **Estimated Effort:** 3 commits, 3-4 builder sessions

---

## 1. Open Question Resolutions

### OQ-1: Connectivity-Risk Steps in SSHEngine

**Question:** Is a 10-minute `apply()` call (BFB flash with wait-for-reconnect) acceptable in the engine model?

**Resolution: Yes — keep compound steps as single `apply()` calls with a phased internal pattern.**

Rationale:
- Celery tasks already support `soft_time_limit=1800` (30 min) and `time_limit=3600` (60 min) — see the `CallbackTask` base in `tasks/_tofu_helpers.py`. A 10-minute apply() is well within bounds.
- OpenTofu applies regularly run 15-25 minutes for EKS clusters. The system already handles long-running operations.
- Splitting into separate modules (flash-dpu → wait-dpu-ready) would lose atomicity — a crash between them leaves the host in a half-flashed state with no SSH.

**Implementation pattern — Pre-Stage / Execute / Wait / Validate:**

```python
def apply(self, ctx, on_output=None):
    ssh = self._build_session(ctx)
    
    # 1. Pre-stage: connectivity preservation (netplan, fallback timer)
    if ctx.variables.get("connectivity_risk"):
        self._pre_stage_connectivity(ssh, ctx, on_output)
    
    # 2. Execute: run the destructive command (background with nohup if needed)
    result = self._execute_command(ssh, ctx, on_output)
    
    # 3. Wait: if connectivity may drop, poll until SSH reconnects
    if ctx.variables.get("connectivity_risk"):
        self._wait_for_reconnect(ctx, on_output, timeout=600)
    
    # 4. Validate: verify the step succeeded
    return self._validate_result(ssh, ctx, on_output)
```

The `on_output` callback streams progress during the wait phase: `[ssh] Waiting for SSH reconnect... (elapsed: 45s / 600s)`. The existing WebSocket log streaming shows this in real time.

### OQ-2: Sequential Constraint

**Question:** How to prevent `ParallelExecutionService` from parallelizing host-scoped SSH steps?

**Resolution: Dependencies are sufficient. No explicit sequential mode needed.**

Evidence from code:
1. `ParallelExecutionService.deploy_project_parallel()` (line 179) uses `DependencyGraphService.build_layers()` which does Kahn's algorithm on the dependency graph.
2. Each SSH module declares explicit `dependencies` on the prior step — creating a linear chain.
3. The dependency graph places chained modules in separate layers, each executed after the previous completes.

Example chain for Phase 1:
```
probe-dpu → set-nic-mode → flash-dpu → wait-dpu-ready → install-dpu-prereqs
```
Each depends on the previous → each in its own layer → strictly sequential.

Independent modules (e.g., `install-k8s-prereqs` has no dep on Phase 1 steps) would land in an earlier layer. This is actually correct — K8s prereqs on the host CAN run while DPU flashing happens on the DPU. The dependency graph naturally expresses the real constraints.

**One edge case:** Phase 1 steps on the DPU (via relay) and Phase 2 steps on the host use different SSH targets. They genuinely CAN run in parallel. If we want to prevent this (to avoid SSH load on the host), add a dependency: `install-k8s-prereqs` → `probe-dpu` (or a shared sentinel module). But this is optional — the host can handle two SSH sessions.

### OQ-3: SSHCredential → ModuleContext Bridge

**Question:** How do host credentials flow into module execution?

**Resolution: New SSH fields on `ModuleContext` (option C from the decision doc), populated by a dedicated `_build_ssh_context()` function in `tasks/ssh_tasks.py`.**

The SSH tasks file will:
1. Load the `ProjectModule` from DB.
2. Look up the `BareMetalHost` linked to this module's stack instance (via a new `bare_metal_host_id` column on `StackInstance`, or by reading the `host_id` from the module's variables).
3. Use `paramiko_utils.decrypt_ssh_credential()` to get the decrypted credential dict.
4. Build `ModuleContext` with SSH fields populated.

**Why not variable injection?** Variables are visible in logs and the UI. SSH credentials must stay out of the variable dict. A dedicated context field is cleaner.

**Bridge implementation:**
```python
def _build_ssh_context(db, module: ProjectModule) -> ModuleContext:
    """Build ModuleContext with SSH fields from BareMetalHost."""
    host_id = (module.variable_overrides or {}).get("bare_metal_host_id")
    host = db.query(BareMetalHost).filter(BareMetalHost.id == host_id).first()
    if not host:
        raise ValueError(f"BareMetalHost {host_id} not found")
    
    # Decrypt host credential
    host_cred = decrypt_ssh_credential(host.ssh_credential)
    
    # Decrypt DPU credential if present
    dpu_cred = None
    if host.dpu_credential:
        dpu_cred = decrypt_ssh_credential(host.dpu_credential)
    
    # Resolve jumphost chain
    jumphost_chain = None
    if host.jumphost_chain:
        jumphost_chain = _resolve_jumphost_chain(db, host.jumphost_chain)
    
    variables = build_variables_for_ssh(db, module)
    
    return ModuleContext(
        module_id=module.id,
        project_id=module.project_id,
        path=module.library_module.path,
        category=module.library_module.category or "bare-metal",
        variables=variables,
        # SSH fields
        ssh_host=host.host_ip,
        ssh_port=host.ssh_port or 22,
        ssh_username=host_cred["username"],
        ssh_password=host_cred.get("password"),
        ssh_private_key_content=host_cred.get("private_key_content"),
        ssh_key_passphrase=host_cred.get("key_passphrase"),
        jumphost_chain=jumphost_chain,
        dpu_host=host.dpu_info[0].get("mgmt_ip", "192.168.100.2") if host.dpu_info else None,
        dpu_username=dpu_cred["username"] if dpu_cred else None,
        dpu_password=dpu_cred.get("password") if dpu_cred else None,
        dpu_private_key_content=dpu_cred.get("private_key_content") if dpu_cred else None,
        connectivity_risk=False,  # Set per-module by the module definition
    )
```

### OQ-4: Blueprint Per Topology

**Question:** One blueprint or many? How to handle topology-specific steps?

**Resolution: One canonical blueprint with optional modules. Topology-specific steps are `required: false` with a `topology_filter` convention in variables.**

Rationale:
- The existing blueprint system already supports `required: true/false` modules. Users toggle optional modules in the UI.
- Creating 4 separate blueprints (regular, bf3, bf3-ipmi, bmc) would cause maintenance divergence since Phase 2-4 are identical.
- The UI already renders optional modules as toggleable cards — perfect for topology-specific steps.

**How it works:**
1. The blueprint defines ALL possible Phase 1 steps as optional modules.
2. When a user selects a bare-metal host and creates a stack instance, the `StackDeploymentService` auto-enables modules matching the host's topology.
3. A small helper in `stack_deployment_service.py` reads `host.topology` and enables/disables modules:

```python
# In create_project_modules_from_template, after creating modules:
if template.category == "bare-metal":
    _auto_select_topology_modules(stack_instance, host, created_modules)
```

**Topology → module mapping:**

| Module | regular | bf3 | bf3-ipmi | bmc |
|--------|---------|-----|----------|-----|
| `bare-metal/probe-dpu` | ✅ | ✅ | ✅ | ✅ |
| `bare-metal/set-nic-mode` | ✅ | ❌ | ✅ | ❌ |
| `bare-metal/stage-vf-netplan` | ❌ | ✅ | ❌ | ✅ |
| `bare-metal/install-fallback-timer` | ❌ | ✅ | ❌ | ❌ |
| `bare-metal/flash-dpu` | ✅ | ✅ | ✅ | ✅ |
| `bare-metal/wait-connectivity` | ❌ | ✅ | ✅ | ✅ |
| `bare-metal/deposit-phase-c` | ❌ | ❌ | ✅ | ✅ |
| `bare-metal/power-cycle-bmc` | ❌ | ❌ | ✅ | ✅ |

Modules not applicable to the topology are simply disabled (not created as `ProjectModule` records).

### OQ-5: Module Variables vs Host Model

**Question:** Auto-inject from discovery or user-supplied?

**Resolution: Hybrid — auto-inject from `BareMetalHost` model with user override.**

Implementation: A new `build_variables_for_ssh()` function in `variable_assembler.py` that:
1. Starts with the module's `variable_overrides` (user-supplied via blueprint UI).
2. Auto-injects from `BareMetalHost` discovery columns for variables with `source: "host"` in the module's `InputSpec`.
3. User-supplied values take precedence (explicit override).

```python
# In SSHModule InputSpec declarations:
inputs = {
    "host_ip": InputSpec(name="host_ip", source="host", from_output="host_ip"),
    "mst_device": InputSpec(name="mst_device", source="host", from_output="mst_device"),
    "nic_mode": InputSpec(name="nic_mode", source="host", from_output="nic_mode"),
    "bfb_url": InputSpec(name="bfb_url", source="user", required=True),
}
```

The variable assembler maps `source: "host"` to `BareMetalHost` columns. This reuses the existing `InputSpec.source` enum (currently: `"user"`, `"module"`, `"profile"`, `"auto"`, `"system"`) — we add `"host"` as a new source type.

### OQ-6: Destroy Semantics for Hardware

**Question:** What does `destroy()` do for SSH modules?

**Resolution: No-op that returns success. Hardware operations are not reversible.**

```python
def destroy(self, ctx, on_output=None):
    if on_output:
        on_output(f"[ssh] Destroy is a no-op for hardware module '{ctx.path}'. "
                  f"Hardware state cannot be automatically reversed.")
    return OperationResult(
        success=True,
        stdout="Destroy not applicable for SSH/hardware modules",
        duration_seconds=0.0,
    )
```

Exceptions:
- `bare-metal/kubeadm-init` could support `kubeadm reset` as destroy.
- `bare-metal/label-dpu-node` could remove labels.
- But these are future enhancements. v1 is all no-op destroys.

The existing engine contract says `destroy()` should "remove all resources" — for hardware, the safe behavior is no-op. The `AnsibleEngine` already has a precedent: `pack_cfg.supports_destroy = false` returns a failure. We return success instead (nothing to destroy is not an error).

---

## 2. SSHEngine Design

### File: `backend/services/execution/ssh_engine.py` (~350 lines)

```python
"""SSH DeploymentEngine — executes bare-metal steps via paramiko."""

import logging
import time
from collections.abc import Callable
from typing import Any

from services.execution.engine_interface import (
    DeploymentEngine,
    ModuleContext,
    OperationResult,
    PlanResult,
)
from services.bare_metal.ssh_session import SSHSession, SSHResult
from services.ssh.paramiko_utils import load_private_key_from_content

logger = logging.getLogger(__name__)


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
            db_session_factory: Optional. Only needed if engine needs
                to load additional data. SSHEngine is designed to work
                entirely from ModuleContext (no DB access in hot path).
        """
        self._db_factory = db_session_factory

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
            private_key_path=None,  # We use content-based auth
            password=ctx.ssh_password,
            jumphost_chain=ctx.jumphost_chain,
            connect_timeout=15,
        )
    
    def _build_dpu_session(self, ctx: ModuleContext) -> SSHSession:
        """Build SSHSession targeting the DPU via host relay.
        
        The DPU is typically at 192.168.100.2 reachable only through
        the host's rshim/tmfifo interface. We chain: jumphost → host → DPU.
        """
        # The host itself becomes a jumphost
        host_hop = {
            "host": ctx.ssh_host,
            "port": ctx.ssh_port,
            "username": ctx.ssh_username,
            "password": ctx.ssh_password,
            "private_key_content": ctx.ssh_private_key_content,
        }
        chain = list(ctx.jumphost_chain or []) + [host_hop]
        
        return SSHSession(
            host=ctx.dpu_host or "192.168.100.2",
            username=ctx.dpu_username or "ubuntu",
            port=22,
            password=ctx.dpu_password,
            jumphost_chain=chain,
            connect_timeout=15,
        )

    def _get_session(self, ctx: ModuleContext) -> SSHSession:
        """Get the right session based on module target."""
        if ctx.variables.get("target") == "dpu":
            return self._build_dpu_session(ctx)
        return self._build_host_session(ctx)

    # ── Engine interface implementation ──────────────────────────────

    def init(self, ctx, on_output=None) -> OperationResult:
        """Validate SSH connectivity and prerequisites."""
        started = time.monotonic()
        try:
            session = self._get_session(ctx)
            if on_output:
                target = ctx.variables.get("target", "host")
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
            
            # Run module-specific prerequisite check
            module_def = self._get_module_def(ctx.path)
            if module_def and hasattr(module_def, "prereq_commands"):
                prereq_cmds = module_def.prereq_commands(ctx.variables)
                if prereq_cmds:
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

    def plan(self, ctx, on_output=None) -> PlanResult:
        """Probe current state to determine if apply is needed."""
        try:
            session = self._get_session(ctx)
            module_def = self._get_module_def(ctx.path)
            
            if not module_def:
                return PlanResult(has_changes=True, details="No module definition — will execute")
            
            if on_output:
                on_output(f"[ssh] Probing current state for {ctx.path}")
            
            plan_cmds = module_def.plan_commands(ctx.variables)
            if not plan_cmds:
                return PlanResult(has_changes=True, details="Module has no plan commands — will execute")
            
            combined_output = []
            for cmd in plan_cmds:
                result = session.execute(cmd, timeout=60)
                combined_output.append(result.stdout)
                if on_output:
                    for line in result.stdout.splitlines():
                        on_output(f"[ssh] {line}")
            
            has_changes = module_def.parse_plan_output(
                "\n".join(combined_output), ctx.variables
            )
            
            return PlanResult(
                has_changes=has_changes,
                details="\n".join(combined_output),
            )
        except Exception as e:
            return PlanResult(has_changes=True, details=f"Plan probe failed ({e}) — will execute")

    def apply(self, ctx, on_output=None) -> OperationResult:
        """Execute the SSH step."""
        started = time.monotonic()
        try:
            session = self._get_session(ctx)
            module_def = self._get_module_def(ctx.path)
            
            if not module_def:
                return OperationResult(
                    success=False,
                    duration_seconds=time.monotonic() - started,
                    error_message=f"No SSHModule definition found for {ctx.path}",
                )
            
            if on_output:
                on_output(f"[ssh] Applying {ctx.path}")
            
            # --- Pre-stage for connectivity risk ---
            if module_def.connectivity_risk:
                pre_cmds = module_def.pre_stage_commands(ctx.variables)
                for cmd in pre_cmds:
                    if on_output:
                        on_output(f"[ssh:pre-stage] {cmd[:80]}...")
                    r = session.execute(cmd, timeout=120)
                    if r.exit_code != 0:
                        return OperationResult(
                            success=False,
                            stdout=r.stdout,
                            stderr=r.stderr,
                            duration_seconds=time.monotonic() - started,
                            error_message=f"Pre-stage failed: {r.stderr}",
                        )
            
            # --- Execute main commands ---
            apply_cmds = module_def.apply_commands(ctx.variables)
            all_stdout = []
            all_stderr = []
            
            for cmd in apply_cmds:
                if on_output:
                    on_output(f"[ssh:exec] {cmd[:100]}...")
                
                # Use streaming for long commands
                if module_def.connectivity_risk or module_def.estimated_duration > 60:
                    gen = session.execute_streaming(cmd, timeout=module_def.timeout)
                    try:
                        for line in gen:
                            if on_output:
                                on_output(f"[ssh] {line}")
                            all_stdout.append(line)
                    except StopIteration as si:
                        r = si.value
                    except Exception:
                        # SSH may have dropped (expected for connectivity-risk)
                        if module_def.connectivity_risk:
                            break
                        raise
                else:
                    r = session.execute(cmd, timeout=module_def.timeout)
                    all_stdout.append(r.stdout)
                    all_stderr.append(r.stderr)
                    
                    if r.exit_code != 0 and not module_def.connectivity_risk:
                        return OperationResult(
                            success=False,
                            stdout="\n".join(all_stdout),
                            stderr="\n".join(all_stderr),
                            duration_seconds=time.monotonic() - started,
                            error_message=f"Command failed (exit {r.exit_code}): {r.stderr[:500]}",
                        )
            
            # --- Wait for reconnect if connectivity-risk ---
            if module_def.connectivity_risk:
                wait_ok = self._wait_for_reconnect(
                    ctx, on_output,
                    timeout=module_def.reconnect_timeout,
                )
                if not wait_ok:
                    return OperationResult(
                        success=False,
                        stdout="\n".join(all_stdout),
                        duration_seconds=time.monotonic() - started,
                        error_message="Host did not reconnect after connectivity-risk operation",
                        error_suggestion="Check host console access. Fallback timer may have reverted.",
                    )
            
            # --- Validate ---
            validate_cmds = module_def.validate_commands(ctx.variables)
            for cmd in validate_cmds:
                r = session.execute(cmd, timeout=60)
                if on_output:
                    on_output(f"[ssh:validate] {r.stdout.strip()}")
                if r.exit_code != 0:
                    return OperationResult(
                        success=False,
                        stdout="\n".join(all_stdout),
                        stderr=r.stderr,
                        duration_seconds=time.monotonic() - started,
                        error_message=f"Validation failed: {r.stderr}",
                    )
            
            # --- Parse outputs ---
            outputs = {}
            if hasattr(module_def, "parse_apply_output"):
                outputs = module_def.parse_apply_output(
                    "\n".join(all_stdout), ctx.variables
                )
            
            return OperationResult(
                success=True,
                outputs=outputs,
                stdout="\n".join(all_stdout),
                stderr="\n".join(all_stderr),
                duration_seconds=time.monotonic() - started,
            )
        except Exception as e:
            logger.exception("SSHEngine.apply failed for %s", ctx.path)
            return OperationResult(
                success=False,
                duration_seconds=time.monotonic() - started,
                error_message=f"SSH apply failed: {e}",
            )

    def destroy(self, ctx, on_output=None) -> OperationResult:
        """No-op for hardware modules."""
        if on_output:
            on_output(f"[ssh] Destroy is a no-op for {ctx.path}")
        return OperationResult(
            success=True,
            stdout=f"Destroy not applicable for SSH module {ctx.path}",
            duration_seconds=0.0,
        )

    def get_outputs(self, ctx) -> dict[str, Any]:
        """Read outputs from last apply (stored in ProjectModule.outputs)."""
        if self._db_factory:
            db = self._db_factory()
            try:
                from models import ProjectModule
                module = db.query(ProjectModule).filter(
                    ProjectModule.id == ctx.module_id
                ).first()
                return module.outputs or {} if module else {}
            finally:
                db.close()
        return {}

    # ── Helpers ──────────────────────────────────────────────────────

    def _get_module_def(self, module_path: str):
        """Look up SSHModule definition from registry."""
        from services.registry import ServiceRegistry
        registry = ServiceRegistry.get().module_registry
        return registry.get(module_path)

    def _wait_for_reconnect(self, ctx, on_output, timeout=600) -> bool:
        """Poll SSH until reconnection or timeout."""
        if on_output:
            on_output(f"[ssh] Waiting for SSH reconnect (timeout: {timeout}s)...")
        
        start = time.monotonic()
        interval = 10  # Start with 10s intervals
        
        while time.monotonic() - start < timeout:
            elapsed = int(time.monotonic() - start)
            if on_output:
                on_output(f"[ssh] Reconnect attempt... ({elapsed}s / {timeout}s)")
            
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
```

### Key Design Decisions

1. **No DB access in engine hot path** — all credential resolution happens in `_build_ssh_context()` in the Celery task. The engine gets a fully-populated `ModuleContext`.
2. **Module definitions drive commands** — the engine calls `module_def.apply_commands(variables)` to get the actual shell commands. This keeps the engine generic.
3. **Streaming output** — uses `SSHSession.execute_streaming()` for long commands, feeding `on_output` for real-time WebSocket updates.
4. **Reconnect polling** — simple retry loop with 10s intervals. Not elegant, but reliable. Matches the `wait_for_connection` pattern from Ansible.

---

## 3. SSHModule Base Class Design

### File: `backend/modules/base.py` (append to existing)

```python
class SSHModule(BaseModule):
    """
    Base for modules that execute via SSH commands on bare-metal hosts.
    
    Subclasses define shell commands instead of K8s manifests.
    The SSHEngine calls these methods to get the commands to execute.
    """
    module_type = "ssh"
    
    # SSH-specific config
    target: str = "host"            # "host" or "dpu"
    connectivity_risk: bool = False
    reconnect_timeout: int = 600    # Seconds to wait for reconnect
    estimated_duration: int = 60    # Seconds (for UI progress estimation)
    timeout: int = 300              # Per-command timeout

    def render_manifests(self, variables: dict[str, Any]) -> list[dict]:
        """SSH modules don't render K8s manifests."""
        return []

    def plan_commands(self, variables: dict[str, Any]) -> list[str]:
        """Return shell commands to probe current state.
        
        These commands should exit 0 and produce output that
        parse_plan_output() can interpret.
        """
        return []

    def parse_plan_output(self, output: str, variables: dict[str, Any]) -> bool:
        """Parse plan command output. Return True if apply is needed."""
        return True  # Default: always apply

    def apply_commands(self, variables: dict[str, Any]) -> list[str]:
        """Return shell commands to execute the step."""
        raise NotImplementedError(f"{self.__class__.__name__} must implement apply_commands()")

    def validate_commands(self, variables: dict[str, Any]) -> list[str]:
        """Return shell commands to verify apply succeeded.
        
        Each command must exit 0 for validation to pass.
        """
        return []

    def pre_stage_commands(self, variables: dict[str, Any]) -> list[str]:
        """Return commands to run BEFORE apply for connectivity preservation.
        
        Only called when connectivity_risk=True.
        """
        return []

    def parse_apply_output(self, output: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Parse apply command output into structured outputs.
        
        Returns dict of key-value pairs available to downstream modules.
        """
        return {}

    def prereq_commands(self, variables: dict[str, Any]) -> list[str]:
        """Return commands to check prerequisites during init().
        
        Each must exit 0 for init to succeed.
        """
        return []

    def get_readiness_condition(self, manifest: dict) -> str | None:
        """Not applicable for SSH modules."""
        return None
```

### Why This Shape

- **Commands instead of manifests** — the fundamental difference from K8s modules. The engine executes shell commands via SSH rather than applying YAML via kubectl/kr8s.
- **plan_commands + parse_plan_output** — mirrors the probe-before-act pattern from the orchestrator's `StepDefinition.idempotent` field. Enables `plan()` to check if a step was already done.
- **pre_stage_commands** — explicit hook for connectivity preservation. Only used by connectivity-risk modules (BFB flash, power cycle).
- **validate_commands** — post-apply verification. Essential for hardware operations where "exit 0" doesn't guarantee success.

---

## 4. ModuleContext SSH Fields

### File: `backend/services/execution/engine_interface.py`

Add these fields to the `ModuleContext` dataclass. All default to `None`/safe values — existing engines ignore them.

```python
@dataclass
class ModuleContext:
    # ... existing fields unchanged ...
    
    # SSH-specific (ignored by OpenTofu/K8s/Ansible engines)
    ssh_host: str | None = None
    ssh_port: int = 22
    ssh_username: str | None = None
    ssh_password: str | None = None
    ssh_private_key_content: str | None = None
    ssh_key_passphrase: str | None = None
    jumphost_chain: list[dict] | None = None
    
    # DPU relay (SSH through host to DPU)
    dpu_host: str | None = None
    dpu_username: str | None = None
    dpu_password: str | None = None
    dpu_private_key_content: str | None = None
```

**Impact analysis:** All existing engines construct `ModuleContext` without these fields → they get the defaults (`None`/`22`) → no breakage. The OpenTofu, Kubernetes, Operator, and Ansible engines never read these fields.

**Sensitive data:** `ssh_password`, `ssh_private_key_content`, `ssh_key_passphrase` are decrypted in the Celery task and live only in memory for the duration of execution. They're never serialized to logs, DB, or the UI. The `ModuleContext` is not persisted.

---

## 5. Engine Router + Task Dispatch Changes

### 5a. `backend/services/execution/task_dispatch.py`

```python
# Line 32: Add "ssh" to explicit engine types
_EXPLICIT_ENGINE_TYPES = {"ansible", "kubernetes", "opentofu", "ssh"}

# In dispatch_init() — add before the K8s check:
def dispatch_init(task_id: int, module, auto_apply: bool = False, force_reinit: bool = False):
    explicit_engine = _get_explicit_engine_type(module)

    if explicit_engine == "ssh":
        from tasks.ssh_tasks import run_ssh_init
        logger.info(f"Dispatching init for module {module.id} → SSH engine (explicit metadata)")
        return run_ssh_init.delay(task_id, module.id, auto_apply=auto_apply)

    if explicit_engine == "ansible":
        # ... existing ...

# Same pattern for dispatch_apply, dispatch_destroy, dispatch_apply_signature, dispatch_destroy_signature
```

### 5b. `backend/services/execution/engine_router.py`

Minimal changes needed. The `EngineRouter` class currently only handles K8s vs OpenTofu. SSH modules use `explicit_engine_type` routing, so they bypass the router entirely (same pattern as Ansible).

```python
# In get_engine_type() — add SSH recognition:
def get_engine_type(self, module_path: str, category: str = "") -> str:
    # 0. Check registry for SSHModule instances
    svc = ServiceRegistry.get()
    registry = svc.module_registry
    if module_path in registry:
        module_def = registry[module_path]
        if getattr(module_def, 'module_type', '') == 'ssh':
            return "ssh"
        # ... existing K8s logic ...
    
    return "opentofu"

# In _get_compatible_engines() — add SSH:
def _get_compatible_engines(self, module_path: str) -> list[tuple[str, DeploymentEngine]]:
    engines = []
    registry = ServiceRegistry.get().module_registry
    if module_path in registry:
        module_def = registry[module_path]
        if getattr(module_def, 'module_type', '') == 'ssh':
            engines.append(("ssh", SSHEngine()))
            return engines  # SSH modules only run on SSH engine
        # ... existing K8s logic ...
```

### 5c. New file: `backend/tasks/ssh_tasks.py` (~250 lines)

Follows the pattern of `kubernetes_tasks.py` and `ansible_tasks.py`:

```python
"""SSH Engine Celery tasks for bare-metal deployment steps."""

from celery_app import celery_app
from database import get_db_context
from models import ProjectModule
from models import Task as TaskModel
from models.bare_metal import BareMetalHost
from services.execution.ssh_engine import SSHEngine
from services.execution.engine_interface import ModuleContext
from services.ssh.paramiko_utils import decrypt_ssh_credential
from tasks._tofu_helpers import CallbackTask, _update_stack_status_if_needed

# ... _build_ssh_context() as described in OQ-3 ...

@celery_app.task(bind=True, base=CallbackTask, name="tasks.ssh_tasks.run_ssh_init")
def run_ssh_init(self, task_db_id: int, module_id: int, auto_apply: bool = False):
    # Same pattern as run_k8s_init: load module, build context, call engine.init()
    # If auto_apply and init succeeds, chain into run_ssh_apply
    ...

@celery_app.task(bind=True, base=CallbackTask, name="tasks.ssh_tasks.run_ssh_apply",
                 soft_time_limit=1800, time_limit=3600)
def run_ssh_apply(self, task_db_id: int, module_id: int):
    # Extended time limits for connectivity-risk operations
    # Calls engine.apply(), stores outputs, triggers auto-registration check
    ...

@celery_app.task(bind=True, base=CallbackTask, name="tasks.ssh_tasks.run_ssh_destroy")
def run_ssh_destroy(self, task_db_id: int, module_id: int):
    # Calls engine.destroy() — always succeeds (no-op)
    ...
```

---

## 6. Task Breakdown

### Commit 1: SSHEngine Infrastructure

**Task B+-001: SSHModule base class + ModuleContext SSH fields**
- Files to create: (none — modifications only)
- Files to modify:
  - `backend/modules/base.py` — add `SSHModule` class (§3)
  - `backend/services/execution/engine_interface.py` — add SSH fields to `ModuleContext` (§4)
- Dependencies: None
- Estimated time: 30 minutes
- Acceptance: `SSHModule` can be imported and subclassed; `ModuleContext` accepts SSH kwargs without breaking existing engine tests

**Task B+-002: SSHEngine implementation**
- Files to create:
  - `backend/services/execution/ssh_engine.py` (~350 lines, §2)
- Files to modify: None
- Dependencies: B+-001
- Estimated time: 2 hours
- Acceptance: `SSHEngine().init()` / `plan()` / `apply()` / `destroy()` return correct `OperationResult`/`PlanResult` types; `health_check()` returns True

**Task B+-003: SSH Celery tasks + task dispatch wiring**
- Files to create:
  - `backend/tasks/ssh_tasks.py` (~250 lines, §5c)
- Files to modify:
  - `backend/services/execution/task_dispatch.py` — add `"ssh"` routing (§5a)
  - `backend/services/execution/engine_router.py` — add SSH recognition (§5b)
- Dependencies: B+-002
- Estimated time: 1.5 hours
- Acceptance: `dispatch_init(task_id, module)` routes to `run_ssh_init.delay()` when `module.library_module.engine_type == "ssh"`

**Commit message:** `feat: SSHEngine infrastructure — engine, tasks, dispatch routing`

---

### Commit 2: SSH Module Definitions

**Task B+-004: Create module directory and Phase 1 modules**
- Files to create:
  - `backend/modules/bare_metal/__init__.py`
  - `backend/modules/bare_metal/probe_dpu.py`
  - `backend/modules/bare_metal/set_nic_mode.py`
  - `backend/modules/bare_metal/flash_dpu.py`
  - `backend/modules/bare_metal/wait_dpu_ready.py`
  - `backend/modules/bare_metal/install_dpu_prereqs.py`
- Dependencies: B+-001
- Estimated time: 2 hours
- Acceptance: Each module class instantiates without error; `apply_commands()` returns non-empty list

**Task B+-005: Phase 2-3 modules**
- Files to create:
  - `backend/modules/bare_metal/install_k8s_prereqs.py`
  - `backend/modules/bare_metal/kubeadm_init.py`
  - `backend/modules/bare_metal/install_cni.py`
  - `backend/modules/bare_metal/install_sriov.py`
  - `backend/modules/bare_metal/install_storage.py`
  - `backend/modules/bare_metal/kubeadm_join.py`
  - `backend/modules/bare_metal/label_dpu_node.py`
  - `backend/modules/bare_metal/taint_dpu_node.py`
- Dependencies: B+-001
- Estimated time: 2 hours
- Acceptance: Each module class instantiates; dependency chains form a valid DAG; `kubeadm_init` outputs match the cluster auto-registration contract

**Task B+-006: Register modules + seeder updates**
- Files to modify:
  - `backend/modules/__init__.py` — register all SSH modules in `_register_all()` (§9)
  - `backend/modules/metadata_export.py` — add `bare-metal` provider inference
  - `backend/services/builtin_module_seeder.py` — set `engine_type="ssh"` for SSHModule instances
- Dependencies: B+-004, B+-005
- Estimated time: 30 minutes
- Acceptance: `get_module_registry()` returns all SSH modules; `seed_builtin_modules()` creates/updates their `ModuleLibrary` rows

**Commit message:** `feat: SSH module definitions for bare-metal DPU deployment (Phase 1-3)`

---

### Commit 3: Blueprint + Cluster Registration Bridge

**Task B+-007: Blueprint JSON + topology auto-selection**
- Files to modify:
  - `backend/data/stack_templates.json` — add BNK bare-metal blueprint (§8)
  - `backend/services/stack_deployment_service.py` — add topology-aware module selection for `category=="bare-metal"` blueprints
- Dependencies: B+-006
- Estimated time: 1.5 hours
- Acceptance: Blueprint appears in stack template list; `create_project_modules_from_template()` creates `ProjectModule` records for all modules; topology filtering disables irrelevant modules

**Task B+-008: Cluster auto-registration bridge**
- Files to modify:
  - `backend/services/cluster_auto_registration_service.py` — accept `ssh_credential_id` in outputs for credential resolution (§7)
  - `backend/tasks/ssh_tasks.py` — call `maybe_auto_register_cluster()` after successful kubeadm-init apply
  - `backend/models/bare_metal.py` — update `BareMetalHost.kubernetes_cluster_id` after registration
- Dependencies: B+-003, B+-005
- Estimated time: 1 hour
- Acceptance: After `kubeadm-init` module apply, a `KubernetesCluster` record exists with encrypted kubeconfig; `BareMetalHost.kubernetes_cluster_id` is set; Phase 4 K8s modules resolve the kubeconfig

**Task B+-009: Variable assembler `source="host"` support**
- Files to modify:
  - `backend/services/execution/variable_assembler.py` — add `build_variables_for_ssh()` function that injects `BareMetalHost` columns for `source="host"` inputs
- Dependencies: B+-003
- Estimated time: 45 minutes
- Acceptance: `build_variables_for_ssh()` returns dict with `host_ip`, `mst_device`, `nic_mode` etc. populated from `BareMetalHost` columns when module inputs declare `source="host"`

**Commit message:** `feat: bare-metal blueprint, cluster registration bridge, variable assembly`

---

### Summary

| Commit | Tasks | Files Changed | New Files | Est. Time |
|--------|-------|---------------|-----------|-----------|
| 1. SSHEngine infrastructure | B+-001..003 | 3 modified | 2 created | ~4 hours |
| 2. Module definitions | B+-004..006 | 3 modified | 14 created | ~4.5 hours |
| 3. Blueprint + wiring | B+-007..009 | 4 modified | 0 created | ~3.25 hours |
| **Total** | **9 tasks** | **10 modified** | **16 created** | **~12 hours (3-4 sessions)** |

---

## UI Story (Brief)

### What works as-is (zero changes needed)

| UI Component | Why It Works |
|-------------|-------------|
| Stack template selector | Blueprint appears in the list, user picks it |
| Module toggle grid | Each SSH module renders as a card with enable/disable |
| Variable editor per module | SSH module `InputSpec` declarations surface in the form |
| Deployment progress grid | `ProjectModule` status (pending → deploying → deployed) displays per module |
| Live log streaming | Celery task → WebSocket → `on_output` callback — already wired |
| Retry/cancel | Module action buttons trigger existing task dispatch |
| Deployment history | Stack instance history shows all deployments |
| Cluster management | After auto-registration, the cluster appears in the clusters list |

### What needs minor adaptation (1-2 hours frontend)

1. **Bare-metal host selector widget** — when creating a stack from the bare-metal blueprint, the user needs to pick which `BareMetalHost` to deploy on. This could be a dropdown populated from `/api/bare-metal/hosts?project_id=X`. The selected host ID flows into `variable_templates.bare_metal_host_id`.

2. **Topology auto-detection hint** — after host selection, show which modules will be auto-enabled/disabled based on the host's discovered topology. This is a UI nicety, not a blocker.

3. **Discovery panel link** — add a "Deploy" button on the existing `BareMetalPanel.tsx` discovery UI that navigates to the stack creation page with the blueprint pre-selected and host pre-filled. One `<Link>` component.

### What explicitly does NOT change

- The existing `BareMetalPanel.tsx` discovery + assessment UI stays as-is.
- The existing bare-metal orchestrator (`orchestrator.py`) stays as-is — it can coexist during transition.
- All existing K8s/OpenTofu/Ansible deployment paths are untouched.
