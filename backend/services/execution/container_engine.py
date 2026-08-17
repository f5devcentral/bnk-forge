"""Container Engine — procedural step runner for ``container_image`` artifacts.

This is the :class:`DeploymentEngine` adapter for artifacts whose lifecycle is a
set of *steps*, each of which invokes the artifact's OWN image with an argv
vector (the validator in ``services/module_metadata.py`` already guarantees
no shell, no command-string, no image override). This engine:

  * resolves the artifact manifest's ``execution.steps`` (or top-level ``steps``)
    for the requested lifecycle operation (init/plan/apply/destroy),
  * picks the substrate — :class:`DockerRunner` vs :class:`KubernetesRunner` —
    from the ``container_runner.backend`` config (explicit), otherwise inferred
    from the deploy model (``compose`` ⟹ docker, ``helm`` ⟹ kubernetes),
  * runs the steps in order, honoring each step's ``when`` gate and expanding
    ``{{inputs.*}}`` templating in the argv / env from ``ctx.variables``,
  * captures an ``outputs_file`` JSON written by the artifact into the workspace,
    normalizes it into the OperationResult outputs,
  * redacts known secret values from any streamed/captured log line.

The engine is synchronous and does NO database access — the task layer
(``tasks/container_tasks.py``) builds the :class:`ModuleContext`, resolves the
runner substrate config + pull credentials, and persists results.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from services.execution.container_runner import (
    ContainerRunner,
    DockerRunner,
    ResourceLimits,
    StepResult,
    StepSpec,
)
from services.execution.engine_interface import (
    DeploymentEngine,
    ModuleContext,
    OperationResult,
    PlanResult,
)

logger = logging.getLogger(__name__)

# The artifact writes its outputs here (relative to the workspace mount). The
# engine reads it back after a successful apply and normalizes it into outputs.
DEFAULT_OUTPUTS_FILENAME = "outputs.json"

# Default in-container mount path for the persistent workspace.
DEFAULT_MOUNT_PATH = "/state"

# ``{{inputs.foo}}`` (with optional surrounding whitespace) → ctx.variables["foo"].
_INPUT_TOKEN_RE = re.compile(r"\{\{\s*inputs\.([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}")


class ContainerEngine(DeploymentEngine):
    """Run an artifact's procedural step-set inside a container substrate.

    Parameters
    ----------
    runner:
        The substrate that executes one step (:class:`DockerRunner` or
        :class:`KubernetesRunner`). The task layer resolves this from config.
    workspace_host_path:
        Host-side path of the per-component persistent workspace (the
        DockerRunner bind-mounts this; the KubernetesRunner ignores it and uses
        a per-component PVC instead).
    pull_authfile_json:
        Transient ``dockerconfigjson`` string used to pull the artifact image
        (``None`` for a public image). Never logged.
    mount_path:
        Where the workspace mounts inside the step container.
    outputs_filename:
        Filename (relative to the workspace) the artifact writes outputs into.
    secret_values:
        Literal secret strings to redact from streamed/captured logs.
    """

    def __init__(
        self,
        runner: ContainerRunner,
        *,
        workspace_host_path: str,
        pull_authfile_json: str | None = None,
        mount_path: str = DEFAULT_MOUNT_PATH,
        workspace_local_path: str | None = None,
        workspace_volume: str | None = None,
        workspace_subpath: str | None = None,
        outputs_filename: str = DEFAULT_OUTPUTS_FILENAME,
        secret_values: list[str] | None = None,
        celery_task_id: str | None = None,
    ) -> None:
        self.runner = runner
        self.workspace_host_path = workspace_host_path
        # Preferred named-volume mount for the sibling (shares storage with the
        # worker; correct on Docker Desktop). None ⟹ host-path bind fallback.
        self.workspace_volume = workspace_volume
        self.workspace_subpath = workspace_subpath
        # Stamped onto each step container so the reaper can tell a live step
        # from one whose worker died. None outside a Celery context.
        self.celery_task_id = celery_task_id
        # In-container path used by the engine itself (e.g. to read outputs.json
        # back). The DockerRunner bind-mounts workspace_host_path; the engine,
        # which runs in the worker, reads via its own mount of the same volume.
        self.workspace_local_path = workspace_local_path or workspace_host_path
        self.pull_authfile_json = pull_authfile_json
        self.mount_path = mount_path
        self.outputs_filename = outputs_filename
        self._secret_values = [s for s in (secret_values or []) if s]

    # =========================================================================
    # DeploymentEngine interface
    # =========================================================================

    def init(
        self,
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None = None,
    ) -> OperationResult:
        """Run the artifact's ``init`` step-set if declared, else a no-op.

        A container artifact has no provider download / backend ceremony, so an
        absent ``init`` step-set is a successful no-op (mirrors the K8s engine).
        """
        steps = self._resolve_steps(ctx, "init")
        if not steps:
            msg = "No init steps declared for artifact; nothing to do."
            self._emit(on_output, msg)
            return OperationResult(success=True, stdout=msg)
        return self._run_phase(ctx, steps, on_output, capture_outputs=False)

    def plan(
        self,
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None = None,
    ) -> PlanResult:
        """Run the artifact's ``plan`` step-set if declared, else assume changes.

        Container artifacts are procedural and largely non-idempotent-aware, so
        with no explicit plan step-set we conservatively report "has changes".
        """
        steps = self._resolve_steps(ctx, "plan")
        if not steps:
            self._emit(on_output, "No plan steps declared for artifact; assuming changes.")
            return PlanResult(has_changes=True, details="Container artifact has no plan phase.")

        result = self._run_phase(ctx, steps, on_output, capture_outputs=False)
        return PlanResult(
            has_changes=result.success,
            details=result.stdout or result.error_message or "",
        )

    def apply(
        self,
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None = None,
    ) -> OperationResult:
        """Run the artifact's ``apply`` step-set and capture its outputs file."""
        steps = self._resolve_steps(ctx, "apply")
        if not steps:
            return OperationResult(
                success=False,
                error_message=(
                    "Artifact declares no apply step-set; a procedural artifact "
                    "requires steps.apply."
                ),
            )
        return self._run_phase(ctx, steps, on_output, capture_outputs=True)

    def destroy(
        self,
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None = None,
    ) -> OperationResult:
        """Run the artifact's ``destroy`` step-set if declared, else a no-op.

        If the artifact does not support destroy (no destroy step-set), there is
        nothing to tear down on this substrate — report success so the
        orchestrator's terminal detection advances.
        """
        steps = self._resolve_steps(ctx, "destroy")
        if not steps:
            msg = "Artifact does not declare a destroy step-set; nothing to tear down."
            self._emit(on_output, msg)
            return OperationResult(success=True, stdout=msg)
        return self._run_phase(ctx, steps, on_output, capture_outputs=False)

    def run_action(
        self,
        ctx: ModuleContext,
        action: str,
        action_inputs: dict[str, Any] | None = None,
        on_output: Callable[[str], None] | None = None,
    ) -> OperationResult:
        """Run a named action's step-set from the manifest's ``actions`` block (D-034).

        Actions reuse the exact lifecycle-step machinery (``when`` gates,
        ``{{inputs.*}}`` templating via ctx.variables, per-step timeouts, log
        streaming) and run in the module's existing workspace. Per-invocation
        ``action_inputs`` overlay ``ctx.variables`` for templating (invocation
        wins). Outputs are never captured — an action exercises the deployment,
        it does not mutate it.
        """
        manifest = ctx.pack_manifest or {}
        actions = manifest.get("actions")
        declared = actions if isinstance(actions, dict) else {}
        if action not in declared:
            names = ", ".join(sorted(declared)) if declared else "none"
            return OperationResult(
                success=False,
                error_message=(
                    f"Artifact declares no action '{action}' (declared actions: {names})."
                ),
            )

        definition = declared[action]
        steps = definition.get("steps") if isinstance(definition, dict) else None
        if not isinstance(steps, list) or not steps:
            return OperationResult(
                success=False,
                error_message=f"Action '{action}' declares no steps.",
            )

        if action_inputs:
            ctx = replace(ctx, variables={**(ctx.variables or {}), **action_inputs})
        return self._run_phase(ctx, steps, on_output, capture_outputs=False)

    def get_outputs(self, ctx: ModuleContext) -> dict[str, Any]:
        """Read the artifact's persisted outputs file from the workspace."""
        return self._read_outputs_file(self._resolve_outputs_filename(ctx))

    def health_check(self) -> bool:
        return self.runner.health_check()

    # =========================================================================
    # Step resolution + templating
    # =========================================================================

    def _resolve_steps(self, ctx: ModuleContext, operation: str) -> list[dict]:
        """Resolve the step list for a lifecycle op from the artifact manifest.

        Reads ``execution.steps.<op>`` first (the SEAMS-named location), then
        falls back to top-level ``steps.<op>`` (where the validator stores it).
        Returns ``[]`` when the phase is not declared.
        """
        manifest = ctx.pack_manifest or {}
        execution = manifest.get("execution")
        if isinstance(execution, dict) and isinstance(execution.get("steps"), dict):
            steps = execution["steps"].get(operation)
        else:
            steps_block = manifest.get("steps")
            steps = steps_block.get(operation) if isinstance(steps_block, dict) else None

        if steps is None:
            return []
        if not isinstance(steps, list):
            raise ValueError(f"steps.{operation} must be a list")
        return steps

    def _step_enabled(self, step: dict, variables: dict[str, Any]) -> bool:
        """Honor a step's ``when`` gate.

        ``when`` may be a bool, or a string that references a single
        ``{{inputs.*}}`` token resolving to a truthy value. Absent ⟹ enabled.
        """
        when = step.get("when")
        if when is None:
            return True
        if isinstance(when, bool):
            return when
        if isinstance(when, str):
            rendered = self._render_str(when, variables)
            return rendered.strip().lower() not in ("", "false", "0", "no", "none")
        return bool(when)

    def _render_str(self, value: str, variables: dict[str, Any]) -> str:
        """Expand ``{{inputs.foo}}`` tokens against the variables dict."""

        def _sub(match: re.Match[str]) -> str:
            key = match.group(1)
            return str(self._lookup_input(key, variables))

        return _INPUT_TOKEN_RE.sub(_sub, value)

    @staticmethod
    def _lookup_input(dotted_key: str, variables: dict[str, Any]) -> Any:
        """Resolve a (possibly dotted) input key against the variables dict."""
        node: Any = variables
        for part in dotted_key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return ""
        return "" if node is None else node

    def _render_args(self, args: list, variables: dict[str, Any]) -> list[str]:
        rendered: list[str] = []
        for token in args:
            if not isinstance(token, str):
                raise ValueError("step args entries must be strings")
            rendered.append(self._render_str(token, variables))
        return rendered

    def _render_env(self, env: dict | None, variables: dict[str, Any]) -> dict[str, str]:
        if not env:
            return {}
        out: dict[str, str] = {}
        for key, value in env.items():
            out[str(key)] = self._render_str(str(value), variables)
        return out

    # =========================================================================
    # Execution
    # =========================================================================

    def _run_phase(
        self,
        ctx: ModuleContext,
        steps: list[dict],
        on_output: Callable[[str], None] | None,
        *,
        capture_outputs: bool,
    ) -> OperationResult:
        start = time.monotonic()
        variables = ctx.variables or {}
        image_digest = self._resolve_image_digest(ctx)
        home_env = self._build_home_env(ctx)
        limits = self._resolve_limits(ctx)
        component_key = self._component_key(ctx)

        collected: list[str] = []

        def _sink(line: str) -> None:
            redacted = self._redact(line)
            collected.append(redacted)
            if on_output:
                on_output(redacted)

        executed = 0
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                return OperationResult(
                    success=False,
                    stdout="\n".join(collected),
                    error_message=f"step[{index}] must be an object",
                    duration_seconds=time.monotonic() - start,
                )

            step_name = str(step.get("name") or f"step-{index}")
            if not self._step_enabled(step, variables):
                _sink(f"- skipping step '{step_name}' (when gate false)")
                continue

            # run_once: non-idempotent setup steps (e.g. a procedural ``init`` that
            # aborts when its workspace already exists) must not re-run on a
            # re-apply. A marker in the persistent workspace records first success
            # so subsequent applies skip the step and proceed to the repeatable work.
            if step.get("run_once") and self._step_marker_exists(step_name):
                _sink(f"- skipping step '{step_name}' (run_once; already completed for this workspace)")
                continue

            args = step.get("args")
            if not isinstance(args, list) or not args:
                return OperationResult(
                    success=False,
                    stdout="\n".join(collected),
                    error_message=f"step '{step_name}' has no args argv list",
                    duration_seconds=time.monotonic() - start,
                )

            spec = StepSpec(
                image_digest=image_digest,
                args=self._render_args(args, variables),
                workspace_host_path=self.workspace_host_path,
                workspace_volume=self.workspace_volume,
                workspace_subpath=self.workspace_subpath,
                mount_path=self.mount_path,
                env={**(ctx.credentials_env or {}), **self._render_env(step.get("env"), variables)},
                home_env=home_env,
                limits=limits,
                timeout_seconds=int(step.get("timeout_seconds", 1800)),
                pull_authfile_json=self.pull_authfile_json,
                component_key=component_key,
                step_name=step_name,
                celery_task_id=self.celery_task_id,
            )

            # retry/backoff: a long-provisioning step (e.g. a cluster whose
            # readiness can outlast one apply attempt) may declare
            # ``retry: {max_attempts, backoff_seconds}`` so the engine re-runs it —
            # continuing from the persistent workspace state — instead of failing
            # the whole module on a transient not-ready condition. A hard timeout
            # (the step exceeding its own ``timeout_seconds``) is not retried.
            max_attempts, backoff_seconds = self._step_retry_policy(step)
            attempt = 0
            while True:
                attempt += 1
                step_result = self._run_step(spec, _sink)
                if step_result.success or step_result.timed_out or attempt >= max_attempts:
                    break
                _sink(
                    f"- step '{step_name}' failed (attempt {attempt}/{max_attempts}, "
                    f"exit {step_result.exit_code}); retrying in {backoff_seconds}s"
                )
                if backoff_seconds:
                    time.sleep(backoff_seconds)
            executed += 1

            if not step_result.success:
                return OperationResult(
                    success=False,
                    stdout="\n".join(collected),
                    error_message=self._redact(
                        f"step '{step_name}' failed (exit {step_result.exit_code}"
                        f"{', timed out' if step_result.timed_out else ''}"
                        f"{f', after {attempt} attempts' if attempt > 1 else ''})"
                    ),
                    error_suggestion=(
                        "Inspect the step logs above. The artifact image's own "
                        "stdout/stderr carries the failure detail."
                    ),
                    duration_seconds=time.monotonic() - start,
                )

            if step.get("run_once"):
                self._write_step_marker(step_name)

        outputs: dict[str, Any] = {}
        if capture_outputs:
            outputs = self._read_outputs_file(self._resolve_outputs_filename(ctx))

        return OperationResult(
            success=True,
            outputs=outputs,
            stdout="\n".join(collected),
            resources_created=executed if capture_outputs else 0,
            duration_seconds=time.monotonic() - start,
        )

    def _run_step(
        self,
        spec: StepSpec,
        sink: Callable[[str], None],
    ) -> StepResult:
        try:
            return self.runner.run_step(spec, on_output=sink)
        except Exception as exc:  # runner raised before/while launching the step
            logger.exception("Container step runner raised for %s", spec.step_name)
            return StepResult(
                success=False,
                exit_code=1,
                stderr=self._redact(str(exc)),
            )

    # =========================================================================
    # Manifest → spec helpers
    # =========================================================================

    def _resolve_image_digest(self, ctx: ModuleContext) -> str:
        """Build the digest-pinned image reference from the container_image block.

        ``registry_host`` + ``repository`` + ``digest`` (sha256:...) →
        ``registry_host/repository@sha256:...``. The runner re-validates the
        digest pin before it reaches the substrate.
        """
        manifest = ctx.pack_manifest or {}
        block = manifest.get("container_image")
        if not isinstance(block, dict):
            raise ValueError("artifact manifest is missing the container_image block")

        host = (block.get("registry_host") or "").strip().rstrip("/")
        repo = (block.get("repository") or "").strip().strip("/")
        digest = (block.get("digest") or "").strip()
        if not host or not repo or not digest:
            raise ValueError("container_image block requires registry_host, repository, and digest")
        return f"{host}/{repo}@{digest}"

    def _build_home_env(self, ctx: ModuleContext) -> dict[str, str]:
        """Resolve ``state.home_env`` declared in the manifest's state block.

        The persistent workspace is the artifact's HOME for state; the manifest
        may declare env vars (e.g. ``HOME``, ``XDG_*``) pointing into the mount.
        """
        manifest = ctx.pack_manifest or {}
        state = manifest.get("state")
        home_env: dict[str, str] = {}
        if isinstance(state, dict) and isinstance(state.get("home_env"), dict):
            for key, value in state["home_env"].items():
                home_env[str(key)] = self._render_str(str(value), ctx.variables or {})
        return home_env

    def _resolve_limits(self, ctx: ModuleContext) -> ResourceLimits:
        manifest = ctx.pack_manifest or {}
        execution = manifest.get("execution")
        limits = execution.get("limits") if isinstance(execution, dict) else None
        if not isinstance(limits, dict):
            return ResourceLimits()
        pids = limits.get("pids")
        return ResourceLimits(
            cpus=str(limits["cpus"]) if limits.get("cpus") is not None else None,
            memory=str(limits["memory"]) if limits.get("memory") is not None else None,
            pids=int(pids) if pids is not None else None,
        )

    @staticmethod
    def _component_key(ctx: ModuleContext) -> str:
        """Stable per-component identity = (project_id, module_id)."""
        return f"p{ctx.project_id}-m{ctx.module_id}"

    def _resolve_outputs_filename(self, ctx: ModuleContext) -> str:
        manifest = ctx.pack_manifest or {}
        state = manifest.get("state")
        if isinstance(state, dict) and isinstance(state.get("outputs_file"), str):
            declared = state["outputs_file"].strip()
            if not declared:
                return self.outputs_filename
            if not self._is_workspace_relative(declared):
                # The manifest value was previously passed to os.path.join with
                # only a .strip(), so an absolute path or a ../ escape read a
                # file outside the workspace and normalized it into
                # module.outputs — surfacing worker files (/app/keys, /app/secrets)
                # to the user. The validator never checked this field (#408.2).
                logger.warning(
                    "Ignoring state.outputs_file %r — it escapes the workspace; "
                    "falling back to %s",
                    declared, self.outputs_filename,
                )
                return self.outputs_filename
            return declared
        return self.outputs_filename

    def _contained_workspace_path(self, relative: str) -> str:
        """Resolve ``relative`` under the workspace, refusing to escape it.

        Lexical checks alone are NOT sufficient here, which is the lesson from
        the first version of this code. The workspace is writable by the
        artifact's own container — that is its purpose — so a step can simply
        plant a symlink at the expected name and the subsequent
        ``os.path.join`` → ``open()`` follows it as the WORKER uid:

            ln -sf /app/keys/encryption.key /state/outputs.json

        ``/app/keys/encryption.key`` is the master encryption key and
        ``/app/secrets`` is a real read-only mount, so the read direction is
        credential disclosure into ``module.outputs`` (served by the state
        viewer), and the write direction is an arbitrary-file truncate.

        Mirrors module_reports_service, which already solved this: realpath
        containment first, then O_NOFOLLOW on the open so the final component
        cannot be a symlink either.
        """
        root = os.path.realpath(self.workspace_local_path)
        target = os.path.realpath(os.path.join(root, relative))
        if target != root and not target.startswith(root + os.sep):
            raise ValueError(
                f"path {relative!r} resolves outside the module workspace"
            )
        return target

    def _open_contained(self, relative: str, mode: str):
        """Open a workspace-relative path with NO component able to be a symlink.

        O_NOFOLLOW on the final component is not containment. The path is
        resolved, then re-resolved by isfile(), then re-resolved again by open()
        — and swapping a PARENT directory between those steps escapes, because
        only the last component is checked. That is not contrived here: the
        shipped artifact declares a nested outputs_file
        (.roksbnkctl/forge/cluster-outputs.json), and `state: {scope: deployment}`
        shares one workspace across blueprint modules dispatched concurrently
        onto --concurrency=4 workers, so module A's still-running step container
        can swap a directory while module B's engine reads.

        So walk the components, opening each directory with O_NOFOLLOW |
        O_DIRECTORY relative to the previous fd, then open the leaf relative to
        the last fd. No component is ever resolved by name twice, and none of
        them may be a symlink.
        """
        import errno

        if not self._is_workspace_relative(relative):
            raise ValueError(f"path {relative!r} escapes the module workspace")

        parts = [p for p in os.path.normpath(relative).split(os.sep) if p and p != "."]
        if not parts:
            raise ValueError(f"path {relative!r} does not name a file")

        root_fd = os.open(os.path.realpath(self.workspace_local_path), os.O_RDONLY | os.O_DIRECTORY)
        open_fds = [root_fd]
        try:
            for component in parts[:-1]:
                try:
                    fd = os.open(
                        component,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=open_fds[-1],
                    )
                except OSError as exc:
                    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                        raise ValueError(
                            f"{relative!r}: component {component!r} is a symlink; "
                            "refusing to follow it out of the workspace"
                        ) from exc
                    raise
                open_fds.append(fd)

            flags = os.O_RDONLY if mode == "r" else (os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
            try:
                leaf = os.open(parts[-1], flags | os.O_NOFOLLOW, 0o600, dir_fd=open_fds[-1])
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.EMLINK):
                    raise ValueError(
                        f"{relative!r} is a symlink; refusing to follow it out of "
                        "the workspace"
                    ) from exc
                raise
            return os.fdopen(leaf, mode, encoding="utf-8")
        finally:
            for fd in open_fds:
                try:
                    os.close(fd)
                except OSError:      # pragma: no cover - best effort
                    pass

    @staticmethod
    def _is_workspace_relative(candidate: str) -> bool:
        """True when ``candidate`` stays inside the workspace when joined to it.

        Mirrors the containment the sibling ``_step_marker_path`` gets for free
        by sanitising its input. Rejects absolute paths, drive-letter/UNC forms,
        and any ``..`` that climbs out.
        """
        if not candidate or os.path.isabs(candidate) or candidate.startswith("\\"):
            return False
        if os.path.splitdrive(candidate)[0]:
            return False
        # normpath collapses ./ and ../ so a climb shows up as a leading "..".
        normalized = os.path.normpath(candidate)
        return not (normalized == ".." or normalized.startswith(".." + os.sep))

    def _read_outputs_file(self, filename: str | None = None) -> dict[str, Any]:
        """Read + normalize the artifact's outputs file from the workspace.

        ``filename`` defaults to the engine's ``outputs_filename``; callers pass
        the manifest-resolved name (``state.outputs_file``) so an artifact that
        writes somewhere other than the default is read correctly — otherwise the
        outputs come back empty and dependent modules never become ready.

        Tolerates a missing file (the artifact may not emit one) and malformed
        JSON (logged, returns empty). Always returns a flat string-keyed dict.
        """
        relative = filename or self.outputs_filename
        # No isfile() precheck: it re-resolves the path by name, which is the
        # very window a parent-directory swap exploits. Open first and let a
        # missing file surface as FileNotFoundError.
        try:
            with self._open_contained(relative, "r") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return {}
        except ValueError as exc:          # symlinked component, or escapes
            logger.warning("Refusing to read artifact outputs file: %s", exc)
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read artifact outputs file %s: %s", relative, exc)
            return {}
        return self._normalize_outputs(data)

    @staticmethod
    def _step_retry_policy(step: dict[str, Any]) -> tuple[int, int]:
        """Parse a step's optional ``retry`` block into (max_attempts, backoff_seconds).

        Defaults to a single attempt with no backoff (current behaviour) when the
        block is absent or malformed.
        """
        retry = step.get("retry")
        if not isinstance(retry, dict):
            return 1, 0
        try:
            max_attempts = max(1, int(retry.get("max_attempts", 1)))
        except (TypeError, ValueError):
            max_attempts = 1
        try:
            backoff_seconds = max(0, int(retry.get("backoff_seconds", 0)))
        except (TypeError, ValueError):
            backoff_seconds = 0
        return max_attempts, backoff_seconds

    def _step_marker_path(self, step_name: str) -> str:
        """Path of a ``run_once`` completion marker in the persistent workspace."""
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", step_name)
        return os.path.join(self.workspace_local_path, f".bnkforge_step_{safe}.done")

    def _step_marker_exists(self, step_name: str) -> bool:
        try:
            return os.path.isfile(self._step_marker_path(step_name))
        except OSError:
            return False

    def _write_step_marker(self, step_name: str) -> None:
        try:
            os.makedirs(self.workspace_local_path, exist_ok=True)
            # Same symlink exposure in the WRITE direction: a planted symlink at
            # the marker name would otherwise be an arbitrary-file truncate to
            # "done\n" as the worker uid. _step_marker_path sanitises the step
            # name (traversal), which does nothing about symlinks.
            marker = os.path.basename(self._step_marker_path(step_name))
            with self._open_contained(marker, "w") as handle:
                handle.write("done\n")
        except (OSError, ValueError) as exc:
            # Non-fatal: a missing marker only means the run_once step re-runs next
            # time (and a truly non-idempotent step would then surface its own error).
            logger.warning("Could not write run_once marker for step '%s': %s", step_name, exc)

    @staticmethod
    def _normalize_outputs(data: Any) -> dict[str, Any]:
        """Normalize an outputs JSON document into a flat string-keyed dict.

        Accepts either a plain mapping ``{"k": v}`` or a Terraform-style
        ``{"k": {"value": v}}`` document.
        """
        if not isinstance(data, dict):
            return {}
        normalized: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, dict) and "value" in value:
                normalized[str(key)] = value["value"]
            else:
                normalized[str(key)] = value
        return normalized

    # =========================================================================
    # Redaction
    # =========================================================================

    def _redact(self, line: str) -> str:
        """Replace any known literal secret value in a log line with ``***``."""
        if not line or not self._secret_values:
            return line
        out = line
        for secret in self._secret_values:
            if secret and secret in out:
                out = out.replace(secret, "***")
        return out

    @staticmethod
    def _emit(on_output: Callable[[str], None] | None, line: str) -> None:
        if on_output:
            on_output(line)


def select_runner(
    *,
    backend: str | None,
    deploy_model: str | None,
    runner_factory_docker: Callable[[], ContainerRunner] | None = None,
    runner_factory_kubernetes: Callable[[], ContainerRunner] | None = None,
) -> ContainerRunner:
    """Pick the substrate runner.

    Precedence:
      1. explicit ``backend`` config (``docker`` | ``kubernetes``),
      2. inferred from deploy model (``compose`` ⟹ docker, ``helm`` ⟹ kubernetes),
      3. default docker.

    ``runner_factory_*`` let the task layer inject configured runner instances
    (e.g. a KubernetesRunner bound to a resolved kubeconfig). When omitted, a
    default DockerRunner is built for the docker path; the kubernetes path
    requires its factory (a runner needs a kube config).
    """
    chosen = (backend or "").strip().lower()
    if not chosen:
        model = (deploy_model or "").strip().lower()
        if model == "helm":
            chosen = "kubernetes"
        else:  # compose / unset / anything else → docker
            chosen = "docker"

    if chosen == "kubernetes":
        if runner_factory_kubernetes is None:
            raise ValueError(
                "container_runner.backend=kubernetes requires a resolved runner "
                "kube config (no runner_factory_kubernetes provided)"
            )
        return runner_factory_kubernetes()

    if chosen == "docker":
        if runner_factory_docker is not None:
            return runner_factory_docker()
        return DockerRunner()

    raise ValueError(f"Unknown container_runner.backend '{backend}' (expected docker|kubernetes)")
