"""BnkctlEngine — local-subprocess deployment engine for the *bnkctl tool family.

Implements the DeploymentEngine ABC for modules that invoke a *bnkctl binary
(awsbnkctl, kindbnkctl, dpubnkctl, …) as a local subprocess in the celery
worker container.

Design:
  - Structurally mirrors SSHEngine but substitutes subprocess.Popen for paramiko.
  - No DB access on the hot path; credentials arrive via ModuleContext.credentials_env.
  - Streaming output via on_output for real-time WebSocket log updates.
  - health_check() returns False gracefully if the binary is absent (the API
    container has no binary — must never raise or trip a circuit breaker).
  - Tool behaviour is driven by a BnkctlToolDescriptor so adding a new *bnkctl
    tool = add a descriptor; no new engine code required (two-adapters rule:
    descriptor seam is real — 5 confirmed tools in the family).
  - Use-case runner (bnkctl_action=="demo-usecases"): runs awsbnkctl demo/scenarios
    run|clean commands against an already-deployed demo cluster. Uses the existing
    workspace cluster.yaml; never renders or overwrites it.
"""

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services.execution.engine_interface import (
    DeploymentEngine,
    ModuleContext,
    OperationResult,
    PlanResult,
)

logger = logging.getLogger(__name__)

# ── Tool descriptor ──────────────────────────────────────────────────────────

@dataclass
class BnkctlToolDescriptor:
    """Per-tool configuration for a *bnkctl binary.

    Adding a new tool to the family = define one descriptor and register it
    in BNKCTL_TOOLS.  A per-tool code hook (custom get_outputs parser) is
    added ONLY when the tool's output diverges from the convention.

    Verb templates accept {cfg} (absolute path to rendered cluster.yaml) and
    {name} (cluster name derived from variables).
    """
    tool: str                        # e.g. "awsbnkctl"
    binary_path: str                 # absolute path inside the worker container
    version_args: list[str] = field(default_factory=lambda: ["version"])
    plan_args_template: list[str] = field(
        default_factory=lambda: ["up", "-f", "{cfg}", "--dry-run"],
    )
    apply_args_template: list[str] = field(
        default_factory=lambda: ["up", "-f", "{cfg}", "--auto"],
    )
    destroy_args_template: list[str] = field(
        default_factory=lambda: ["down", "-f", "{cfg}", "--yes"],
    )


# Registry of known tool descriptors.
# awsbnkctl is the first (tracer) implementation; others are stubs for later.
BNKCTL_TOOLS: dict[str, BnkctlToolDescriptor] = {
    "awsbnkctl": BnkctlToolDescriptor(
        tool="awsbnkctl",
        binary_path="/usr/local/bin/awsbnkctl",
    ),
}

# Fallback binary name when the descriptor is resolved from PATH
_DEFAULT_BINARY = "awsbnkctl"

# ── Use-case catalog ─────────────────────────────────────────────────────────

# Authoritative use-case names from awsbnkctl source (demo.go / scenarios.go).
# Demos are run via `awsbnkctl demo run <name> -f <cfg>`.
# Scenarios are run via `awsbnkctl scenarios run <name> -f <cfg>`.
_DEMO_NAMES: frozenset[str] = frozenset({
    "http2",
    "diameter",
    "ingress-migration",
    "bigip-cis",
})

# Green scenarios: safe to run on any demo cluster.
# Amber: require extra resources (egress NAT, AI model).
_SCENARIO_NAMES: frozenset[str] = frozenset({
    # Green
    "http-routing-e2e",
    "http-traffic-split",
    "proxy-protocol-l4",
    "multi-vip",
    "external-resource-pool",
    "ai-inference-e2e",
    # Amber
    "egress-snat",
    "ai-token-counting",
    "ai-semantic-cache",
})

_AMBER_SCENARIO_NAMES: frozenset[str] = frozenset({
    "egress-snat",
    "ai-token-counting",
    "ai-semantic-cache",
})


def resolve_usecase_commands(usecases: str | None) -> list[tuple[str, str]]:
    """Resolve a use-case selector into an ordered list of (verb, target) pairs.

    The returned list defines the order that plan/apply will execute commands
    (forward) and destroy will execute them (reversed).

    Presets:
      "none" / "" / None → [] (skip; cluster-only deploy)
      "all-green"  → [("demo","--all")]
                     awsbnkctl `demo run --all` runs 4 demos + 6 green scenarios (10).
      "all-demos"  → [("demo","http2"),("demo","diameter"),
                      ("demo","ingress-migration"),("demo","bigip-cis")]
      "all"        → [("demo","--all"),("scenarios","egress-snat"),
                      ("scenarios","ai-token-counting"),("scenarios","ai-semantic-cache")]
                     "demo --all" covers the 10 green; then the 3 amber are appended.
      <demo name>  → [("demo", name)]
      <scenario name> → [("scenarios", name)]
      unknown → raises ValueError

    Args:
        usecases: A preset name or individual use-case name.
            Pass "none", "", or None to skip all use-cases (returns []).

    Returns:
        Ordered list of (verb, target) tuples.  Empty list means skip.

    Raises:
        ValueError: When the name is not a known preset or use-case name.
    """
    if not usecases or usecases == "none":
        return []

    if usecases == "all-green":
        return [("demo", "--all")]

    if usecases == "all-demos":
        return [
            ("demo", "http2"),
            ("demo", "diameter"),
            ("demo", "ingress-migration"),
            ("demo", "bigip-cis"),
        ]

    if usecases == "all":
        return [
            ("demo", "--all"),
            ("scenarios", "egress-snat"),
            ("scenarios", "ai-token-counting"),
            ("scenarios", "ai-semantic-cache"),
        ]

    if usecases in _DEMO_NAMES:
        return [("demo", usecases)]

    if usecases in _SCENARIO_NAMES:
        return [("scenarios", usecases)]

    raise ValueError(
        f"Unknown use-case selector: {usecases!r}. "
        f"Valid presets: all-green, all-demos, all. "
        f"Valid demos: {sorted(_DEMO_NAMES)}. "
        f"Valid scenarios: {sorted(_SCENARIO_NAMES)}."
    )


# ── Engine ───────────────────────────────────────────────────────────────────

class BnkctlEngine(DeploymentEngine):
    """
    Engine for *bnkctl tools executed as local subprocesses in the celery worker.

    Each operation renders form variables → cluster.yaml in a per-project
    persistent workspace directory, then shells out to the *bnkctl binary.
    Output is streamed line-by-line via the on_output callback.

    The engine is tool-agnostic: which binary to run is resolved from
    ctx.variables["bnkctl_tool"] (default: "awsbnkctl").  The descriptor
    carries the flag templates so adding a new tool is data-only.
    """

    # Persistent workspace root inside the worker container.
    # Mounted from bnk-forge-data:/app/projects in docker-compose.
    _WORKSPACE_ROOT = "/app/projects"

    def __init__(self, db_session_factory=None):
        """
        Args:
            db_session_factory: Optional callable returning a DB session.
                Used only by _update_stage() for poll-based UI progress.
                The engine hot path never touches DB otherwise.
        """
        self._db_factory = db_session_factory

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _update_stage(self, ctx: ModuleContext, detail: str) -> None:
        """Write stage_detail to ProjectModule for poll-based UI progress.

        Best-effort: failures are logged at debug level and never block
        module execution.  Mirrors SSHEngine._update_stage exactly.

        NOTE: Direct DB access here mirrors SSHEngine (ssh_engine.py:109,127) and
        is intentional pre-existing precedent; the DeploymentEngine ABC discourages
        it (engine_interface.py:123) — tracked as hardening backlog item.
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
            logger.debug(
                "stage_detail write failed for module %s",
                getattr(ctx, "module_id", "?"),
            )
        finally:
            try:
                db.close()
            except Exception:
                pass

    def _get_descriptor(self, ctx: ModuleContext) -> BnkctlToolDescriptor:
        """Resolve the tool descriptor from ctx.variables or use the default."""
        tool_name = ctx.variables.get("bnkctl_tool", _DEFAULT_BINARY)
        descriptor = BNKCTL_TOOLS.get(tool_name)
        if descriptor is None:
            logger.warning(
                "Unknown bnkctl_tool '%s' in ctx.variables for module %s; "
                "falling back to awsbnkctl descriptor",
                tool_name,
                ctx.module_id,
            )
            descriptor = BNKCTL_TOOLS[_DEFAULT_BINARY]
        return descriptor

    def _workspace_dir(self, ctx: ModuleContext) -> Path:
        """Return (and create) the per-project persistent workspace dir.

        Layout: /app/projects/<project_id>/awsbnkctl/
        awsbnkctl writes .awsbnkctl/<name>/ state + kubeconfig relative to
        its cwd, so keeping cwd stable across runs preserves durable state.
        """
        tool = ctx.variables.get("bnkctl_tool", _DEFAULT_BINARY)
        workspace = Path(self._WORKSPACE_ROOT) / str(ctx.project_id) / tool
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    def _render_cluster_yaml(self, ctx: ModuleContext, workspace: Path) -> Path:
        """Render form variables → cluster.yaml in the workspace dir.

        For the tracer bullet the variables dict carries the cluster.yaml
        content verbatim under key "cluster_yaml" (set by cli_tasks.py after
        rendering the template).  If that key is absent we fall back to
        serialising the full variables dict as YAML (future: Jinja template).
        """
        import yaml  # PyYAML is already a forge dep

        cluster_yaml_path = workspace / "cluster.yaml"
        raw = ctx.variables.get("cluster_yaml")
        if raw:
            # Pre-rendered content from the task layer
            cluster_yaml_path.write_text(raw)
        else:
            # Fallback: serialise variables (useful for integration tests)
            cluster_yaml_path.write_text(yaml.safe_dump(ctx.variables, default_flow_style=False))
        return cluster_yaml_path

    @staticmethod
    def _applied_cluster_name(cfg_path: Path) -> str | None:
        """Read metadata.name from an applied cluster.yaml.

        Used on the destroy path so log lines (and any {name} placeholder a tool
        descriptor uses) describe the cluster actually being torn down, rather
        than whatever the project form currently says. Best-effort: a malformed
        file must not block a destroy, since the config reaches the tool by path
        regardless.
        """
        import yaml

        try:
            doc = yaml.safe_load(cfg_path.read_text()) or {}
        except Exception:
            logger.warning("Could not parse applied cluster.yaml at %s", cfg_path)
            return None
        if not isinstance(doc, dict):
            return None
        metadata = doc.get("metadata")
        if isinstance(metadata, dict) and metadata.get("name"):
            return str(metadata["name"])
        return None

    def _build_env(self, ctx: ModuleContext) -> dict[str, str]:
        """Merge process env with per-project cloud credentials.

        Pattern from engine_router.py:811: env={**os.environ, **creds_env}.
        """
        return {**os.environ, **ctx.credentials_env}

    def _fmt_args(self, template: list[str], cfg: str, name: str) -> list[str]:
        """Expand {cfg} and {name} slots in a verb template."""
        return [arg.format(cfg=cfg, name=name) for arg in template]

    def _check_demo_mode(self, workspace: Path, ctx: ModuleContext) -> str | None:
        """Check that the cluster was deployed with DEMO_MODE=true.

        Reads .awsbnkctl/<cluster_name>/state.env (mirroring get_outputs' discovery).
        Returns None if the check passes (or if the state dir does not exist at all,
        which is acceptable for a plan-before-first-apply — the binary will then error
        on state load, which is the correct fail-fast behaviour).
        Returns a non-empty error string if DEMO_MODE != "true" in a state dir that
        exists, so the caller can surface a clear "not a demo cluster" message.

        Design choice (documented in work/v1.md): a missing .awsbnkctl directory is
        treated as fail-soft (guard skipped) rather than a hard error, because the
        first thing an operator does is run plan before apply.  The binary itself
        enforces the DEMO_MODE requirement on apply; we simply catch it earlier when
        state is already present.
        """
        cluster_name = ctx.variables.get("name") or ctx.variables.get("cluster_name", "")
        bnkctl_dir = workspace / ".awsbnkctl"

        # Attempt to locate state dir (mirrors get_outputs discovery)
        state_dir: Path | None = None
        if cluster_name:
            candidate = bnkctl_dir / cluster_name
            if (candidate / "state.env").exists():
                state_dir = candidate
        if state_dir is None:
            try:
                for subdir in bnkctl_dir.iterdir():
                    if subdir.is_dir() and (subdir / "state.env").exists():
                        state_dir = subdir
                        break
            except (FileNotFoundError, PermissionError):
                pass

        if state_dir is None:
            # No state yet — fail-soft, skip the guard.
            logger.debug(
                "_check_demo_mode: no state.env found under %s — pre-apply, skipping guard",
                bnkctl_dir,
            )
            return None

        # Parse state.env for DEMO_MODE
        try:
            env_vars: dict[str, str] = {}
            for raw_line in (state_dir / "state.env").read_text().splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                env_vars[key.strip()] = val.strip()
        except OSError as exc:
            logger.debug("_check_demo_mode: could not read state.env: %s", exc)
            return None

        demo_mode = env_vars.get("DEMO_MODE", "")
        if demo_mode.lower() != "true":
            return (
                "Not a demo cluster — DEMO_MODE is not 'true' in state.env. "
                "Re-deploy the cluster module with demo_enabled=true to enable demo use-cases."
            )
        return None

    def _run_usecase_commands(
        self,
        ctx: ModuleContext,
        resolved: str,
        cfg: str,
        workspace: Path,
        env: dict[str, str],
        op: str,
        commands: list[tuple[str, str]],
        dry_run: bool,
        on_output,
    ) -> tuple[bool, str]:
        """Run an ordered list of (verb, target) use-case commands sequentially.

        Fail-fast on first non-zero exit (matches binary fail-fast semantics).
        Returns (success, aggregated_stdout).
        """
        all_stdout: list[str] = []
        for verb, target in commands:
            if target == "--all":
                args = [resolved, verb, op, "--all", "-f", cfg]
            else:
                args = [resolved, verb, op, target, "-f", cfg]
            if dry_run and op == "run":
                args.append("--dry-run")

            if on_output:
                on_output(f"[bnkctl] {verb} {op}: {' '.join(args[1:])}")

            rc, stdout = self._run_streaming_with_ctx(
                ctx,
                args=args,
                env=env,
                cwd=workspace,
                on_output=on_output,
            )
            all_stdout.append(stdout)
            if rc != 0:
                return False, "\n".join(all_stdout)

        return True, "\n".join(all_stdout)

    def _run_streaming_with_ctx(
        self,
        ctx: ModuleContext,
        args: list[str],
        env: dict[str, str],
        cwd: Path,
        on_output,
    ) -> tuple[int, str]:
        """Run a subprocess with streaming stdout, updating stage_detail per line.

        Uses Popen with stdout=PIPE, stderr=STDOUT, text=True, bufsize=1
        so lines are emitted in real time — same contract as
        SSHEngine._execute_streaming.

        Returns (returncode, full_stdout).
        """
        all_lines: list[str] = []
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            cwd=str(cwd),
        )
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                stripped = line.rstrip("\n")
                all_lines.append(stripped)
                if on_output:
                    on_output(stripped)
                short = stripped.strip()[:120]
                if short:
                    self._update_stage(ctx, short)
        finally:
            proc.stdout.close()  # type: ignore[union-attr]
            if proc.poll() is None:
                # Reader loop exited abnormally (e.g. SoftTimeLimitExceeded raised into the
                # loop) while the child is still running — kill it rather than blocking
                # proc.wait() until the hard time limit SIGKILLs the worker and orphans a
                # half-finished bring-up.
                proc.kill()
            proc.wait()

        return proc.returncode, "\n".join(all_lines)

    def _run_captured(
        self,
        args: list[str],
        env: dict[str, str] | None = None,
        timeout: int = 30,
        cwd: str | None = None,
    ) -> tuple[int, str, str]:
        """Run a subprocess, capture stdout+stderr, return (rc, stdout, stderr)."""
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                cwd=cwd,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return 1, "", f"Command timed out after {timeout}s: {args}"
        except FileNotFoundError:
            return 127, "", f"Binary not found: {args[0]}"
        except Exception as e:
            return 1, "", str(e)

    # ── DeploymentEngine ABC ─────────────────────────────────────────────────

    def health_check(self) -> bool:
        """Check that the *bnkctl binary is present and executable.

        Must return False gracefully when the binary is absent (the API
        container has no awsbnkctl — must never raise or trip a circuit
        breaker that blocks other engines).
        """
        descriptor = BNKCTL_TOOLS.get(_DEFAULT_BINARY)
        binary = descriptor.binary_path if descriptor else _DEFAULT_BINARY

        # shutil.which respects PATH; also handles the absolute path case
        resolved = shutil.which(binary) or (binary if os.path.isfile(binary) else None)
        if not resolved:
            logger.debug("BnkctlEngine.health_check: binary not found at %s", binary)
            return False

        rc, _, _ = self._run_captured([resolved] + (descriptor.version_args if descriptor else ["version"]))
        healthy = rc == 0
        if not healthy:
            logger.debug("BnkctlEngine.health_check: 'version' exited %d", rc)
        return healthy

    def init(self, ctx: ModuleContext, on_output=None) -> OperationResult:
        """Validate that the binary is present and the workspace is writable.

        For the bnkctl engine 'init' is lightweight: verify binary + mkdir
        workspace.  No cluster state is touched.
        """
        started = time.monotonic()
        try:
            descriptor = self._get_descriptor(ctx)
            resolved = shutil.which(descriptor.binary_path) or descriptor.binary_path

            if not os.path.isfile(resolved) and not shutil.which(descriptor.binary_path):
                return OperationResult(
                    success=False,
                    duration_seconds=time.monotonic() - started,
                    error_message=f"Binary not found: {descriptor.binary_path}",
                    error_suggestion=(
                        "Mount the linux/amd64 binary into the worker container: "
                        "add '- ./bin/awsbnkctl:/usr/local/bin/awsbnkctl:ro' to x-worker-volumes"
                    ),
                )

            workspace = self._workspace_dir(ctx)
            if on_output:
                on_output(f"[bnkctl] Workspace: {workspace}")

            rc, stdout, stderr = self._run_captured(
                [resolved] + descriptor.version_args,
                env=self._build_env(ctx),
            )
            if on_output:
                on_output(f"[bnkctl] {stdout.strip() or stderr.strip()}")

            return OperationResult(
                success=rc == 0,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=time.monotonic() - started,
                error_message=stderr if rc != 0 else None,
            )
        except Exception as e:
            logger.exception("BnkctlEngine.init failed for module %s", ctx.module_id)
            return OperationResult(
                success=False,
                duration_seconds=time.monotonic() - started,
                error_message=f"bnkctl init failed: {e}",
            )

    def plan(self, ctx: ModuleContext, on_output=None) -> PlanResult:
        """Run `awsbnkctl up -f <rendered.yaml> --dry-run`.

        Dry-run makes ZERO AWS API calls and exits 0 (lifecycle.go:297-303).
        Returns PlanResult(has_changes=True, details=stdout) on success —
        the plan always signals changes because dry-run doesn't diff live state.

        For bnkctl_action=="demo-usecases": uses the existing workspace cluster.yaml
        (never renders/overwrites it) and runs use-case commands with --dry-run.
        """
        if ctx.variables.get("bnkctl_action") == "demo-usecases":
            return self._plan_usecases(ctx, on_output)

        try:
            descriptor = self._get_descriptor(ctx)
            resolved = shutil.which(descriptor.binary_path) or descriptor.binary_path
            workspace = self._workspace_dir(ctx)
            cfg_path = self._render_cluster_yaml(ctx, workspace)
            cluster_name = ctx.variables.get("name", str(ctx.project_id))

            args = [resolved] + self._fmt_args(
                descriptor.plan_args_template,
                cfg=str(cfg_path),
                name=cluster_name,
            )
            if on_output:
                on_output(f"[bnkctl] plan: {' '.join(args)}")

            rc, stdout, stderr = self._run_captured(
                args,
                env=self._build_env(ctx),
                timeout=120,
                cwd=str(workspace),
            )
            details = stdout or stderr
            if on_output:
                for line in details.splitlines():
                    on_output(f"[bnkctl] {line}")

            if rc != 0:
                return PlanResult(
                    has_changes=False,
                    details=f"dry-run failed (exit {rc}):\n{details}",
                )
            return PlanResult(has_changes=True, details=details)
        except Exception as e:
            return PlanResult(
                has_changes=True,
                details=f"Plan probe failed ({e}) — will execute",
            )

    def _plan_usecases(self, ctx: ModuleContext, on_output=None) -> PlanResult:
        """Plan path for bnkctl_action=="demo-usecases".

        Uses the existing cluster.yaml from the workspace; never renders it.
        Runs resolved use-case commands with --dry-run.
        Short-circuits immediately when usecases is "none"/empty — no cluster.yaml
        required, no DEMO_MODE check, just returns success with a skip message.
        """
        usecases_val = ctx.variables.get("usecases", "none")
        commands = resolve_usecase_commands(usecases_val)
        if not commands:
            if on_output:
                on_output("[bnkctl] no use-cases selected — skipped")
            return PlanResult(
                has_changes=False,
                details="no use-cases selected — skipped",
                skipped=True,
            )

        try:
            descriptor = self._get_descriptor(ctx)
            resolved = shutil.which(descriptor.binary_path) or descriptor.binary_path
            workspace = self._workspace_dir(ctx)

            cfg_path = workspace / "cluster.yaml"
            if not cfg_path.exists():
                return PlanResult(
                    has_changes=False,
                    details=(
                        "cluster.yaml not found in workspace — the cluster module must run "
                        "successfully before use-cases can be planned. Run the cluster module first."
                    ),
                )

            guard_error = self._check_demo_mode(workspace, ctx)
            if guard_error:
                return PlanResult(has_changes=False, details=guard_error)

            success, stdout = self._run_usecase_commands(
                ctx=ctx,
                resolved=resolved,
                cfg=str(cfg_path),
                workspace=workspace,
                env=self._build_env(ctx),
                op="run",
                commands=commands,
                dry_run=True,
                on_output=on_output,
            )
            if not success:
                return PlanResult(
                    has_changes=False,
                    details=f"use-case dry-run failed:\n{stdout}",
                )
            return PlanResult(has_changes=True, details=stdout)
        except Exception as e:
            return PlanResult(
                has_changes=True,
                details=f"Use-case plan probe failed ({e}) — will execute",
            )

    def apply(self, ctx: ModuleContext, on_output=None) -> OperationResult:
        """Run `awsbnkctl up -f <rendered.yaml> --auto`, streaming stdout.

        Streams each output line through on_output (→ WebSocket) and also
        writes trimmed lines to stage_detail for poll-based UI progress.

        For bnkctl_action=="demo-usecases": uses the existing workspace cluster.yaml
        and runs use-case commands in forward order, fail-fast on first non-zero exit.
        """
        if ctx.variables.get("bnkctl_action") == "demo-usecases":
            return self._apply_usecases(ctx, on_output)

        started = time.monotonic()
        try:
            descriptor = self._get_descriptor(ctx)
            resolved = shutil.which(descriptor.binary_path) or descriptor.binary_path
            workspace = self._workspace_dir(ctx)
            cfg_path = self._render_cluster_yaml(ctx, workspace)
            cluster_name = ctx.variables.get("name", str(ctx.project_id))

            args = [resolved] + self._fmt_args(
                descriptor.apply_args_template,
                cfg=str(cfg_path),
                name=cluster_name,
            )

            if on_output:
                on_output(f"[bnkctl] apply: {' '.join(args)}")
            self._update_stage(ctx, "Starting awsbnkctl up...")

            rc, stdout = self._run_streaming_with_ctx(
                ctx,
                args=args,
                env=self._build_env(ctx),
                cwd=workspace,
                on_output=on_output,
            )

            success = rc == 0
            self._update_stage(ctx, "Complete" if success else f"Failed (exit {rc})")

            return OperationResult(
                success=success,
                stdout=stdout,
                duration_seconds=time.monotonic() - started,
                error_message=None if success else f"awsbnkctl up exited {rc}",
            )
        except Exception as e:
            logger.exception("BnkctlEngine.apply failed for module %s", ctx.module_id)
            self._update_stage(ctx, f"Failed: {str(e)[:100]}")
            return OperationResult(
                success=False,
                duration_seconds=time.monotonic() - started,
                error_message=f"bnkctl apply failed: {e}",
            )

    def _apply_usecases(self, ctx: ModuleContext, on_output=None) -> OperationResult:
        """Apply path for bnkctl_action=="demo-usecases".

        Uses the existing cluster.yaml; runs resolved use-case commands in forward
        order with op="run". Fail-fast on first non-zero exit.
        Short-circuits immediately when usecases is "none"/empty — no cluster.yaml
        required, no DEMO_MODE check, just returns success with a skip message.
        """
        started = time.monotonic()

        usecases_val = ctx.variables.get("usecases", "none")
        try:
            commands = resolve_usecase_commands(usecases_val)
        except ValueError as exc:
            return OperationResult(
                success=False,
                duration_seconds=time.monotonic() - started,
                error_message=str(exc),
            )
        if not commands:
            if on_output:
                on_output("[bnkctl] no use-cases selected — skipped")
            return OperationResult(
                success=True,
                stdout="no use-cases selected — skipped",
                duration_seconds=time.monotonic() - started,
            )

        try:
            descriptor = self._get_descriptor(ctx)
            resolved = shutil.which(descriptor.binary_path) or descriptor.binary_path
            workspace = self._workspace_dir(ctx)

            cfg_path = workspace / "cluster.yaml"
            if not cfg_path.exists():
                return OperationResult(
                    success=False,
                    duration_seconds=time.monotonic() - started,
                    error_message=(
                        "cluster.yaml not found in workspace — the cluster module must run "
                        "successfully before use-cases can be applied. Run the cluster module first."
                    ),
                )

            guard_error = self._check_demo_mode(workspace, ctx)
            if guard_error:
                return OperationResult(
                    success=False,
                    duration_seconds=time.monotonic() - started,
                    error_message=guard_error,
                )

            self._update_stage(ctx, "Starting use-case runner...")
            success, stdout = self._run_usecase_commands(
                ctx=ctx,
                resolved=resolved,
                cfg=str(cfg_path),
                workspace=workspace,
                env=self._build_env(ctx),
                op="run",
                commands=commands,
                dry_run=False,
                on_output=on_output,
            )
            self._update_stage(ctx, "Use-cases complete" if success else "Use-case runner failed")

            return OperationResult(
                success=success,
                stdout=stdout,
                duration_seconds=time.monotonic() - started,
                error_message=None if success else "use-case runner exited non-zero",
            )
        except Exception as e:
            logger.exception("BnkctlEngine._apply_usecases failed for module %s", ctx.module_id)
            self._update_stage(ctx, f"Failed: {str(e)[:100]}")
            return OperationResult(
                success=False,
                duration_seconds=time.monotonic() - started,
                error_message=f"bnkctl use-case apply failed: {e}",
            )

    def destroy(self, ctx: ModuleContext, on_output=None) -> OperationResult:
        """Run `awsbnkctl down -f <rendered.yaml> --yes`, streaming stdout.

        `down` requires both --config/-f AND --yes (lifecycle.go:124-125).

        For bnkctl_action=="demo-usecases": uses the existing workspace cluster.yaml
        and runs use-case clean commands in REVERSE order. Fail-fast on first non-zero
        exit (symmetric with apply semantics; documented in work/v1.md).
        """
        if ctx.variables.get("bnkctl_action") == "demo-usecases":
            return self._destroy_usecases(ctx, on_output)

        started = time.monotonic()
        try:
            descriptor = self._get_descriptor(ctx)
            resolved = shutil.which(descriptor.binary_path) or descriptor.binary_path
            workspace = self._workspace_dir(ctx)
            # Destroy from the APPLIED config, never a re-render of current form
            # variables. The workspace cluster.yaml was written at apply time and
            # sits beside the tool's own .awsbnkctl/<name>/state.env, so it is the
            # config the live cluster was actually built from. Re-rendering here
            # (the old behaviour) meant that editing cluster_name after apply made
            # `down` target a cluster that never existed -- reporting success while
            # the real EKS cluster stayed up and unmanaged. Refusing is strictly
            # safer than guessing; same stance as _destroy_usecases below.
            cfg_path = workspace / "cluster.yaml"
            if not cfg_path.exists():
                return OperationResult(
                    success=False,
                    duration_seconds=time.monotonic() - started,
                    error_message=(
                        f"cluster.yaml not found in workspace {workspace} — refusing to "
                        "destroy. The applied configuration is what identifies the live "
                        "cluster; re-rendering it from the current project form could "
                        "target the wrong cluster and orphan the real one. Restore the "
                        "workspace (or tear the cluster down with awsbnkctl directly) "
                        "and retry."
                    ),
                )
            cluster_name = self._applied_cluster_name(cfg_path) or ctx.variables.get(
                "name", str(ctx.project_id)
            )

            args = [resolved] + self._fmt_args(
                descriptor.destroy_args_template,
                cfg=str(cfg_path),
                name=cluster_name,
            )

            if on_output:
                on_output(f"[bnkctl] destroy: {' '.join(args)}")
            self._update_stage(ctx, "Starting awsbnkctl down...")

            rc, stdout = self._run_streaming_with_ctx(
                ctx,
                args=args,
                env=self._build_env(ctx),
                cwd=workspace,
                on_output=on_output,
            )

            success = rc == 0
            self._update_stage(ctx, "Destroyed" if success else f"Destroy failed (exit {rc})")

            return OperationResult(
                success=success,
                stdout=stdout,
                duration_seconds=time.monotonic() - started,
                error_message=None if success else f"awsbnkctl down exited {rc}",
            )
        except Exception as e:
            logger.exception("BnkctlEngine.destroy failed for module %s", ctx.module_id)
            self._update_stage(ctx, f"Failed: {str(e)[:100]}")
            return OperationResult(
                success=False,
                duration_seconds=time.monotonic() - started,
                error_message=f"bnkctl destroy failed: {e}",
            )

    def _destroy_usecases(self, ctx: ModuleContext, on_output=None) -> OperationResult:
        """Destroy path for bnkctl_action=="demo-usecases".

        Uses the existing cluster.yaml; runs resolved use-case commands in REVERSE
        order with op="clean". Fail-fast on first non-zero exit.

        Note: `scenarios clean` requires ExactArgs(1) — no --all support. The
        resolve_usecase_commands function never produces ("scenarios","--all"), so
        this constraint is never violated.
        Short-circuits immediately when usecases is "none"/empty — no-op success.
        """
        started = time.monotonic()

        usecases_val = ctx.variables.get("usecases", "none")
        try:
            commands = resolve_usecase_commands(usecases_val)
        except ValueError as exc:
            return OperationResult(
                success=False,
                duration_seconds=time.monotonic() - started,
                error_message=str(exc),
            )
        if not commands:
            if on_output:
                on_output("[bnkctl] no use-cases selected — destroy skipped")
            return OperationResult(
                success=True,
                stdout="no use-cases selected — destroy skipped",
                duration_seconds=time.monotonic() - started,
            )

        try:
            descriptor = self._get_descriptor(ctx)
            resolved = shutil.which(descriptor.binary_path) or descriptor.binary_path
            workspace = self._workspace_dir(ctx)

            cfg_path = workspace / "cluster.yaml"
            if not cfg_path.exists():
                return OperationResult(
                    success=False,
                    duration_seconds=time.monotonic() - started,
                    error_message=(
                        "cluster.yaml not found in workspace — cannot clean use-cases "
                        "without the original cluster configuration."
                    ),
                )

            # Reverse order for destroy
            reversed_commands = list(reversed(commands))

            self._update_stage(ctx, "Cleaning use-cases...")
            success, stdout = self._run_usecase_commands(
                ctx=ctx,
                resolved=resolved,
                cfg=str(cfg_path),
                workspace=workspace,
                env=self._build_env(ctx),
                op="clean",
                commands=reversed_commands,
                dry_run=False,
                on_output=on_output,
            )
            self._update_stage(ctx, "Use-cases cleaned" if success else "Use-case clean failed")

            return OperationResult(
                success=success,
                stdout=stdout,
                duration_seconds=time.monotonic() - started,
                error_message=None if success else "use-case clean exited non-zero",
            )
        except Exception as e:
            logger.exception("BnkctlEngine._destroy_usecases failed for module %s", ctx.module_id)
            self._update_stage(ctx, f"Failed: {str(e)[:100]}")
            return OperationResult(
                success=False,
                duration_seconds=time.monotonic() - started,
                error_message=f"bnkctl use-case destroy failed: {e}",
            )

    def get_outputs(self, ctx: ModuleContext) -> dict[str, Any]:
        """Return cluster outputs parsed from state.env + kubeconfig left by awsbnkctl.

        For a real apply, awsbnkctl writes .awsbnkctl/<cluster_name>/state.env and
        .awsbnkctl/<cluster_name>/kubeconfig into the workspace cwd.  Parse those to
        emit the EKS-contract keys so cli_tasks can call register_eks_cluster.

        Fail-soft: if state.env or the CA data is missing (dry-run, or apply not yet
        run) return the minimal variable-derived dict without raising.
        """
        cluster_name = ctx.variables.get("name", "")
        region = ctx.variables.get("region", "")
        workspace = self._workspace_dir(ctx)
        kubeconfig_path = str(workspace / ".awsbnkctl" / cluster_name / "kubeconfig") if cluster_name else ""
        vip = ctx.variables.get("vip", "")

        base = {
            "cluster_name": cluster_name,
            "region": region,
            "kubeconfig_path": kubeconfig_path,
            "vip": vip,
            "workspace": str(workspace),
        }

        # Attempt to resolve state dir — prefer variable-derived name, fall back to
        # the single subdir under .awsbnkctl/ that contains a state.env.
        bnkctl_dir = workspace / ".awsbnkctl"
        state_dir: Path | None = None
        if cluster_name:
            candidate = bnkctl_dir / cluster_name
            if (candidate / "state.env").exists():
                state_dir = candidate
        if state_dir is None:
            # auto-discover: find the first subdir with state.env
            try:
                for subdir in bnkctl_dir.iterdir():
                    if subdir.is_dir() and (subdir / "state.env").exists():
                        state_dir = subdir
                        break
            except (FileNotFoundError, PermissionError):
                pass

        if state_dir is None:
            logger.debug(
                "get_outputs: no state.env found under %s for module %s — dry-run or pre-apply",
                bnkctl_dir,
                ctx.module_id,
            )
            return base

        # Parse state.env (KEY=VALUE lines, # comments ignored)
        env_vars: dict[str, str] = {}
        try:
            for raw_line in (state_dir / "state.env").read_text().splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                env_vars[key.strip()] = val.strip()
        except OSError as exc:
            logger.debug("get_outputs: could not read state.env: %s", exc)
            return base

        eks_cluster_name = env_vars.get("EKS_CLUSTER_NAME", "")
        eks_endpoint = env_vars.get("EKS_ENDPOINT", "")
        aws_region = env_vars.get("AWS_REGION", region)
        eks_version = env_vars.get("EKS_VERSION", "")
        eks_arn = env_vars.get("EKS_CLUSTER_ARN", "")
        eks_oidc = env_vars.get("EKS_OIDC_URL", "")

        # Parse certificate-authority-data from kubeconfig YAML
        ca_data = ""
        kubeconfig_file = state_dir / "kubeconfig"
        if kubeconfig_file.exists():
            try:
                import yaml as _yaml
                kube_doc = _yaml.safe_load(kubeconfig_file.read_text())
                clusters = (kube_doc or {}).get("clusters", [])
                if clusters:
                    ca_data = clusters[0].get("cluster", {}).get("certificate-authority-data", "")
            except Exception as exc:
                logger.debug("get_outputs: could not parse kubeconfig CA: %s", exc)

        if not (eks_cluster_name and eks_endpoint and ca_data and aws_region):
            logger.debug(
                "get_outputs: EKS contract incomplete for module %s "
                "(cluster_name=%r endpoint=%r ca=%r region=%r) — skipping EKS keys",
                ctx.module_id,
                eks_cluster_name,
                eks_endpoint,
                bool(ca_data),
                aws_region,
            )
            return base

        return {
            **base,
            # EKS contract keys
            "cluster_name": eks_cluster_name or cluster_name,
            "cluster_endpoint": eks_endpoint,
            "cluster_certificate_authority_data": ca_data,
            "region": aws_region,
            "cluster_version": eks_version,
            "cluster_arn": eks_arn,
            "oidc_provider_url": eks_oidc,
        }
