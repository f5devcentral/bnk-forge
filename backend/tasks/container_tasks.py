"""Container Engine Celery tasks — procedural ``container_image`` artifacts.

Mirrors ``tasks/kubernetes_tasks.py`` so the UI, stack deployment, and parallel
execution systems treat the container engine identically to every other engine:
  - init:    run the artifact's init step-set (or no-op),
  - plan:    run the artifact's plan step-set (or assume changes),
  - apply:   run the artifact's apply step-set, capture its outputs file,
  - destroy: run the artifact's destroy step-set (or no-op).

On a run we:
  1. resolve the artifact manifest + substrate (DockerRunner / KubernetesRunner),
  2. resolve the image-pull credential, persist it as the project's
     ``cne_pull_secret`` ProjectSecret, and push it into the project's cluster,
  3. build a ModuleContext (cloud creds + the artifact manifest as pack_manifest)
     and drive the ContainerEngine.

The container engine itself does NO DB access; all persistence happens here.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from celery_app import celery_app
from database import get_db_context
from models import ProjectModule
from models import Task as TaskModel
from services.execution.container_engine import DEFAULT_MOUNT_PATH, ContainerEngine, select_runner
from services.execution.container_runner import DockerRunner
from services.execution.engine_interface import ModuleContext
from services.module_lock import (
    ModuleLockError,
    ModuleLockLostError,
    module_lock,
    set_locked_module_fields,
)
from services.project_service import update_project_counts
from tasks._task_lookup import fetch_task_or_raise
from tasks._tofu_helpers import (
    CallbackTask,
    _mark_task_failed,
    _notify_task_started,
    _publish_task_completion,
    _trigger_next_destroy_module,
    _update_stack_status_if_needed,
    create_deployment_record,
)

logger = logging.getLogger(__name__)


def _ts() -> str:
    return datetime.now(UTC).strftime("%H:%M:%S")


# ── Context + runner resolution ──────────────────────────────────────────────

def _artifact_manifest(module: ProjectModule) -> dict:
    """Return the artifact manifest (bnkforge.artifact.json) for the module."""
    lib = module.library_module
    manifest = getattr(lib, "pack_manifest", None) if lib else None
    if not isinstance(manifest, dict):
        raise ValueError(
            f"Module {module.id} has no artifact manifest (pack_manifest); "
            "container engine requires a bnkforge.artifact.json."
        )
    return manifest


def _registry_host(manifest: dict) -> str:
    block = manifest.get("container_image")
    if not isinstance(block, dict):
        raise ValueError("artifact manifest is missing the container_image block")
    host = (block.get("registry_host") or "").strip()
    if not host:
        raise ValueError("container_image.registry_host is required")
    return host


def _resolve_runner(db, manifest: dict, project):
    """Pick the substrate runner from config / deploy model.

    backend config precedence: execution.container_runner.backend in the
    manifest. Inference: deploy_model 'helm' ⟹ kubernetes, else docker.
    """
    execution = manifest.get("execution") if isinstance(manifest.get("execution"), dict) else {}
    runner_cfg = execution.get("container_runner") if isinstance(execution.get("container_runner"), dict) else {}
    backend = runner_cfg.get("backend")
    deploy_model = manifest.get("deploy_model")

    def _docker_factory() -> DockerRunner:
        return DockerRunner()

    def _kubernetes_factory():
        from services.execution.engine_router import EngineRouter
        from services.execution.kubernetes_runner import KubernetesRunner, RunnerKubeConfig

        kubeconfig_path = EngineRouter.resolve_kubeconfig(project, db)
        if not kubeconfig_path:
            raise ValueError(
                "container_runner.backend=kubernetes requires a registered cluster "
                f"for project '{project.name}'."
            )
        namespace = runner_cfg.get("namespace") or "bnk-forge-runner"
        return KubernetesRunner(
            RunnerKubeConfig(
                kubeconfig_path=kubeconfig_path,
                context=EngineRouter.resolve_context(project, db),
                namespace=namespace,
            )
        )

    return select_runner(
        backend=backend,
        deploy_model=deploy_model,
        runner_factory_docker=_docker_factory,
        runner_factory_kubernetes=_kubernetes_factory,
    )


def _build_engine_and_ctx(
    db, module: ProjectModule, *, operation: str = "apply",
    celery_task_id: str | None = None,
) -> tuple[ContainerEngine, ModuleContext]:
    """Resolve manifest, substrate, pull secret, and build the engine + context."""
    from services.credentials_service import get_cloud_credentials_only
    from services.execution.container_run_secrets import (
        materialize_secret_files,
        persist_cne_pull_secret,
        push_pull_secret_to_cluster,
        resolve_pull_authfile_for_module,
    )
    from services.workspace_manager import WorkspaceManager

    project = module.project
    manifest = _artifact_manifest(module)
    _registry_host(manifest)  # validate the root manifest has a registry host

    # 1. Resolve + persist the graph-wide image-pull credential (merged
    #    dockerconfigjson covering every container_image host in the references
    #    graph), enforce the host allowlist, and push it into the cluster.
    pull_authfile = resolve_pull_authfile_for_module(db, project.id, module.library_module)
    if pull_authfile:
        persist_cne_pull_secret(db, project.id, pull_authfile)
        push_pull_secret_to_cluster(db, project, pull_authfile)
        db.commit()

    # 2. Persistent workspace. state.scope="deployment" shares one workspace
    #    across a blueprint deployment's phase modules (so e.g. a bnk phase sees
    #    the cluster phase's state); default "component" isolates per module.
    #    The sibling mounts the named volume + subpath (shares storage with the
    #    worker); workspace_host is the bind-mount fallback (WORKSPACE_HOST_BASE).
    wm = WorkspaceManager(db)
    state_block = manifest.get("state") or {}
    state_scope = state_block.get("scope")
    mount_path = state_block.get("mount_path") or DEFAULT_MOUNT_PATH
    ws_key = wm.artifact_workspace_key(module, state_scope)
    workspace_local = wm.ensure_artifact_workspace(project.id, ws_key)
    workspace_host = wm.artifact_workspace_host_path(project.id, ws_key)
    workspace_volume = wm.artifact_workspace_volume()
    workspace_subpath = wm.artifact_workspace_subpath(project.id, ws_key)

    # 4. Substrate runner.
    runner = _resolve_runner(db, manifest, project)

    # 5. Cloud credentials (delivered as step env, never logged).
    #    ONLY the credential vars — never the worker's ambient environment: the
    #    step env becomes -e flags on a third-party image, and would otherwise
    #    disclose DATABASE_URL/CELERY_*/REDIS_URL and override the manifest's
    #    declared home_env (DOCKER_HOST/HOME) with worker-local values (#443).
    credentials_env = get_cloud_credentials_only(project, db)

    library_module = module.library_module
    module_path = library_module.path if library_module else module.path_in_project
    # Effective form inputs for {{inputs.*}} templating: base variables overlaid
    # with variable_overrides (where blueprint-resolved form values are stored).
    effective_variables = {**(module.variables or {}), **(module.variable_overrides or {})}
    # Inputs a pack declares `source: "module"` are resolved from the dependency's
    # outputs, the same way every other engine resolves them. Without this the
    # declaration is inert on this path — stored, never applied — and the step runs
    # with the input unset, failing from inside the image in a way that names the
    # input rather than the wiring that should have supplied it.
    #
    # Wired BEFORE the operator's own values are layered on, so an explicit form
    # value still wins: a blueprint that hard-codes a registry host should not be
    # overridden by a dependency that happens to publish one.
    if library_module is not None:
        # Imported here, not at module scope, matching how can_execute is pulled in
        # below — variable_assembler reaches back into services/ and importing it
        # eagerly from a tasks module risks a cycle.
        from services.execution.variable_assembler import apply_dependency_output_wiring

        # Not seeded with Layer 2.75's early transforms, and that is currently
        # fine rather than a known hole: MODULE_TRANSFORMS is keyed on module
        # path and every registered key is a Python-defined module
        # (bare-metal/*, bnk/*, k8s/*). Container modules are artifacts selected
        # by artifact kind, so none of them has an early transform to miss. If
        # one ever gains a transform, this is the line that has to change.
        #
        # Seeded with what is already resolved, mirroring build_variables' Layer
        # 2.6. The wiring decides whether a missing required dependency is fatal
        # by checking whether the input is ALREADY present, so handing it an empty
        # dict disables that check and makes this path stricter than every engine
        # it is meant to match: a pack that wires from infra/aws/vpc raises here on
        # bare metal, where that module does not exist, even though the operator
        # supplied the value on the form. Layer 2.6 exists precisely to prevent
        # that, and seeding is how this path inherits it.
        wired: dict = dict(effective_variables)
        apply_dependency_output_wiring(
            db, module, library_module, wired, operation=operation
        )
        resolved = {k for k in wired if k not in effective_variables}
        if resolved:
            logger.info(
                "Resolved %d input(s) from dependency wiring: %s",
                len(resolved), ", ".join(sorted(resolved)),
            )
        # A dependency output that landed on a key the operator also set is
        # discarded by the merge below. That is the intended precedence, but it
        # is invisible to someone asking why their `from_output` "didn't apply",
        # so say so once at debug level.
        overridden = sorted(
            k for k, v in wired.items()
            if k in effective_variables and effective_variables[k] != v
        )
        if overridden:
            logger.debug(
                "Dependency output(s) overridden by the module's own values: %s",
                ", ".join(overridden),
            )
        effective_variables = {**wired, **effective_variables}
    # Declared project secrets → workspace files (#442). After the form inputs
    # are known (paths may template {{inputs.*}}), before the engine runs, so a
    # missing secret fails fast naming it rather than surfacing as an opaque CLI
    # error minutes into the run.
    materialize_secret_files(db, project.id, manifest, workspace_local, effective_variables)

    ctx = ModuleContext(
        module_id=module.id,
        project_id=project.id,
        path=module_path,
        category=library_module.category if library_module else "container",
        variables=effective_variables,
        credentials_env=credentials_env,
        module_source_kind=(getattr(library_module, "module_source_kind", None) if library_module else None),
        deploy_model=(getattr(library_module, "deploy_model", None) if library_module else None),
        pack_manifest=manifest,
        workspace_path=workspace_local,
    )

    engine = ContainerEngine(
        runner,
        celery_task_id=celery_task_id,
        mount_path=mount_path,
        workspace_host_path=workspace_host,
        workspace_local_path=workspace_local,
        workspace_volume=workspace_volume,
        workspace_subpath=workspace_subpath,
        pull_authfile_json=pull_authfile,
        secret_values=list(credentials_env.values()) + ([pull_authfile] if pull_authfile else []),
    )
    return engine, ctx


def _streaming_sink(task, db, header: str, lines: list[str], *, interval: float = 2.0):
    """Build an ``on_output`` callback that appends timestamped lines AND flushes
    ``task.logs`` to the DB at most every ``interval`` seconds.

    This makes a long container step's output visible live two ways:
      * each line is pushed over the module-log WebSocket (publish_module_log →
        Redis → /ws/tasks → DeploymentLogViewer), the same channel ssh/tmos use,
        for instant no-poll tailing; and
      * ``task.logs`` is flushed to the DB at most every ``interval`` seconds, so
        the task-detail endpoint the UI polls also shows progress and the
        output-so-far survives a worker kill mid-step.
    Mirrors the OpenTofu engine's incremental ``task.logs`` writes; the final,
    complete ``_build_logs(..., result)`` is still written on completion. Lines
    arrive here already redacted by the engine's _sink, so publishing is
    secret-safe. Both the WS publish and the flush are best-effort — neither may
    fail the step.
    """
    import re as _re
    import time as _time

    module_id = getattr(task, "module_id", None)
    state: dict = {"last": 0.0, "stage": None, "stage_written": None, "stage_last": 0.0}
    # A "[N/M] <text>" step marker the artifact tool prints for each phase/step
    # (e.g. "[2/6] cluster-up", "[3/4] Waiting for License Active"). The latest
    # one is mirrored into module.stage_detail so the UI status cell shows live
    # phase progress instead of a bare spinner.
    progress_re = _re.compile(r"^\[\d+/\d+\]\s*\S")

    def _write_stage(value: str) -> None:
        # on_output runs on container_runner's output-pump thread, so we must NOT
        # touch the task's session here — a SELECT on it races the main thread and
        # trips autoflush. Use a short-lived isolated session + a direct UPDATE (no
        # object load). Best-effort UI polish.
        try:
            from database import SessionLocal
            from models import ProjectModule

            s = SessionLocal()
            try:
                s.query(ProjectModule).filter(ProjectModule.id == module_id).update(
                    {ProjectModule.stage_detail: value[:250]},
                    synchronize_session=False,
                )
                s.commit()
            finally:
                s.close()
        except Exception:  # noqa: BLE001 — stage_detail is best-effort UI polish
            pass

    def sink(line: str) -> None:
        stamped = f"[{_ts()}] {line}"
        lines.append(stamped)
        now = _time.monotonic()
        if module_id:
            try:
                from services.websocket_service import publish_module_log

                publish_module_log(module_id=module_id, line=stamped)
            except Exception:
                pass  # WebSocket publish is best-effort
            stripped = line.strip()
            # Mirror the latest changed "[N/M] <text>" phase marker into
            # module.stage_detail. Dedupe consecutive identical markers, and throttle
            # the DB write to the same `interval` the task.logs flush uses so a
            # high-frequency step (hundreds of matching lines/sec — buildah/podman
            # steps, progress suffixes) can't storm the DB with a session + commit
            # per line. The latest phase seen inside the throttle window is held in
            # state["stage"] and persisted by flush_stage() when the step ends.
            if progress_re.match(stripped) and stripped != state["stage"]:
                state["stage"] = stripped
                if now - state["stage_last"] >= interval:
                    state["stage_last"] = now
                    state["stage_written"] = stripped
                    _write_stage(stripped)
        if now - state["last"] >= interval:
            state["last"] = now
            try:
                task.logs = _build_logs(header, lines, None)
                db.commit()
            except Exception:  # noqa: BLE001 — a log flush must never fail the step
                db.rollback()

    def flush_stage() -> None:
        """Persist the final phase marker if the latest changed marker was throttled
        (arrived inside the window and never written). Call once the step completes."""
        if module_id and state["stage"] is not None and state["stage"] != state["stage_written"]:
            state["stage_written"] = state["stage"]
            _write_stage(state["stage"])

    sink.flush_stage = flush_stage  # type: ignore[attr-defined]
    return sink


def _build_logs(header: str, lines: list[str], result) -> str:
    body = (
        f"[{_ts()}] {header}\n"
        f"[{_ts()}] Engine: ContainerEngine\n"
        "---\n" + "\n".join(lines) + "\n"
    )
    if getattr(result, "error_message", None):
        body += f"\n--- ERROR ---\n{result.error_message}\n"
    if getattr(result, "error_suggestion", None):
        body += f"\n--- SUGGESTION ---\n{result.error_suggestion}\n"
    return body


def _maybe_register_container_cluster(db, module, workspace_path=None) -> None:
    """Auto-register a cluster after a successful container-engine apply.

    The opentofu/ssh engines register clusters post-apply; the container engine
    did not, so a cluster deployed by a container module never appeared on the
    Kubernetes page. Generic + cloud-agnostic: maybe_register_container_cluster
    self-gates on the module's manifest declaring how it surfaces the cluster
    config (a workspace file or output). Best-effort — a registration failure must
    never fail the deploy.
    """
    try:
        from services.cluster_auto_registration_service import maybe_register_container_cluster

        cluster = maybe_register_container_cluster(db, module, workspace_path=workspace_path)
        if cluster is not None:
            # Commit the new cluster row before enqueueing — the scan celery
            # worker opens a fresh DB session and would otherwise race the flush.
            db.commit()
            logger.info("Auto-registered cluster '%s' from module %s", cluster.name, module.id)
            # Kick off the cluster scan (BNK detection) like the opentofu/ssh
            # registration paths do — without it the cluster shows under
            # Kubernetes but never under F5 BNK until a manual scan (#452).
            from tasks.cluster_scan_task import enqueue_cluster_scan

            enqueue_cluster_scan(int(cluster.id))
    except Exception:
        logger.exception("Cluster auto-registration failed for module %s", module.id)
        db.rollback()


def _maybe_unregister_container_cluster(db, module) -> None:
    """Remove an auto-registered cluster when its source container module is destroyed."""
    try:
        from services.cluster_auto_registration_service import maybe_unregister_container_cluster

        if maybe_unregister_container_cluster(db, module):
            db.commit()
    except Exception:
        logger.exception("Cluster auto-unregistration failed for module %s", module.id)
        db.rollback()


# ── Celery tasks ─────────────────────────────────────────────────────────────

@celery_app.task(bind=True, base=CallbackTask, name="tasks.container_tasks.run_container_init")
def run_container_init(self, task_db_id: int, module_id: int, auto_apply: bool = False, **kwargs):
    """Run the artifact's init step-set (or no-op for container artifacts)."""
    task = None
    with get_db_context() as db:
        try:
            task = fetch_task_or_raise(db, task_db_id)
            task.status = "in_progress"
            task.started_at = datetime.now(UTC)
            task.command = "container-engine: init"
            db.commit()
            _notify_task_started(task)

            module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
            if not module:
                raise ValueError(f"Module {module_id} not found")

            with module_lock(db, module.id, task_id=task_db_id) as lock:
                engine, ctx = _build_engine_and_ctx(db, module, celery_task_id=self.request.id)
                lines: list[str] = []
                result = engine.init(ctx, on_output=lambda ln: lines.append(f"[{_ts()}] {ln}"))

                task.exit_code = 0 if result.success else 1
                task.logs = _build_logs(f"=== CONTAINER ENGINE INIT ===\nModule: {ctx.path}", lines, result)

                if result.success:
                    task.status = "completed"
                    fields = {"status": "initialized", "last_init_at": datetime.now(UTC)}
                else:
                    task.status = "failed"
                    task.error = result.error_message
                    fields = {"status": "init_failed", "deployment_error": result.error_message}

                task.completed_at = datetime.now(UTC)
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                set_locked_module_fields(db, module, lock, **fields)
                update_project_counts(db, module.project_id)
                db.commit()
                create_deployment_record(db, task, module, "init", task.logs)
                _update_stack_status_if_needed(module, db)

                if result.success and auto_apply:
                    from services.execution.variable_assembler import can_execute
                    can_exec, _missing = can_execute(db, module)
                    if can_exec:
                        apply_task = TaskModel(
                            task_type="apply", status="queued",
                            project_id=module.project_id, module_id=module.id,
                            created_at=datetime.now(UTC),
                        )
                        db.add(apply_task)
                        db.commit()
                        db.refresh(apply_task)
                        celery_result = run_container_apply.delay(apply_task.id, module.id)
                        apply_task.celery_task_id = celery_result.id
                        set_locked_module_fields(db, module, lock, status="applying")

            return {"success": result.success, "exit_code": task.exit_code}

        except (ModuleLockError, ModuleLockLostError) as e:
            logger.error("Container init task lock error: %s", e)
            _mark_task_failed(task, str(e), db, update_stack=False)
            raise
        except Exception as exc:
            logger.exception("Container init task failed: %s", exc)
            _mark_task_failed(task, str(exc), db, module=locals().get("module"), failed_status="init_failed")
            raise


@celery_app.task(bind=True, base=CallbackTask, name="tasks.container_tasks.run_container_plan")
def run_container_plan(self, task_db_id: int, module_id: int, **kwargs):
    """Run the artifact's plan step-set (or assume changes)."""
    task = None
    with get_db_context() as db:
        try:
            task = fetch_task_or_raise(db, task_db_id)

            module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
            if not module:
                task.status = "failed"
                task.error = f"Module {module_id} not found"
                task.completed_at = datetime.now(UTC)
                db.commit()
                return {"success": False, "error": task.error}

            task.status = "in_progress"
            task.started_at = datetime.now(UTC)
            task.command = "container-engine: plan"
            db.commit()
            _notify_task_started(task)

            with module_lock(db, module.id, task_id=task_db_id) as lock:
                engine, ctx = _build_engine_and_ctx(db, module, celery_task_id=self.request.id)
                lines: list[str] = []
                result = engine.plan(ctx, on_output=lambda ln: lines.append(f"[{_ts()}] {ln}"))

                task.exit_code = 0
                task.logs = _build_logs(
                    f"=== CONTAINER ENGINE PLAN ===\nModule: {ctx.path}\n"
                    f"[{_ts()}] Has Changes: {result.has_changes}",
                    lines,
                    result,
                )
                task.status = "completed"
                task.completed_at = datetime.now(UTC)
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                set_locked_module_fields(db, module, lock, status="planned", deployment_error=None)
                update_project_counts(db, module.project_id)
                db.commit()
                create_deployment_record(db, task, module, "plan", task.logs)
                _update_stack_status_if_needed(module, db)

            return {"success": True, "exit_code": 0, "has_changes": result.has_changes}

        except (ModuleLockError, ModuleLockLostError) as e:
            logger.error("Container plan task lock error: %s", e)
            _mark_task_failed(task, str(e), db, update_stack=False)
            raise
        except Exception as exc:
            logger.exception("Container plan task failed: %s", exc)
            _mark_task_failed(task, str(exc), db, module=locals().get("module"), failed_status="plan_failed")
            raise


@celery_app.task(bind=True, base=CallbackTask, name="tasks.container_tasks.run_container_apply")
def run_container_apply(self, task_db_id: int, module_id: int, **kwargs):
    """Run the artifact's apply step-set and capture its outputs file."""
    task = None
    with get_db_context() as db:
        try:
            task = fetch_task_or_raise(db, task_db_id)

            module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
            if not module:
                task.status = "failed"
                task.error = f"Module {module_id} not found"
                task.completed_at = datetime.now(UTC)
                db.commit()
                return {"success": False, "error": task.error}

            task.status = "in_progress"
            task.started_at = datetime.now(UTC)
            task.command = "container-engine: apply"
            db.commit()
            _notify_task_started(task)

            from services.execution.variable_assembler import can_execute
            can_exec, missing = can_execute(db, module)
            if not can_exec:
                raise ValueError(f"Dependencies not satisfied: {', '.join(missing)}")

            with module_lock(db, module.id, task_id=task_db_id) as lock:
                engine, ctx = _build_engine_and_ctx(db, module, celery_task_id=self.request.id)
                lines: list[str] = []
                header = f"=== CONTAINER ENGINE APPLY ===\nModule: {ctx.path}"
                sink = _streaming_sink(task, db, header, lines)
                result = engine.apply(ctx, on_output=sink)
                sink.flush_stage()  # persist final phase if it was throttled

                task.exit_code = 0 if result.success else 1
                task.logs = _build_logs(header, lines, result)

                if result.success:
                    task.status = "completed"
                    fields = {
                        "status": "applied",
                        "outputs": result.outputs,
                        "last_deployed_at": datetime.now(UTC),
                        "deployment_error": None,
                    }
                else:
                    task.status = "failed"
                    task.error = result.error_message or "Apply failed"
                    fields = {"status": "apply_failed", "deployment_error": (result.error_message or "")[:2000]}

                task.completed_at = datetime.now(UTC)
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                set_locked_module_fields(db, module, lock, **fields)
                update_project_counts(db, module.project_id)
                db.commit()
                create_deployment_record(db, task, module, "apply", task.logs)
                _update_stack_status_if_needed(module, db)

            if result.success:
                _maybe_register_container_cluster(db, module, ctx.workspace_path)

            return {
                "success": result.success,
                "exit_code": task.exit_code,
                "outputs": result.outputs,
                "duration_seconds": result.duration_seconds,
            }

        except (ModuleLockError, ModuleLockLostError) as e:
            logger.error("Container apply task lock error: %s", e)
            _mark_task_failed(task, str(e), db, update_stack=False)
            raise
        except Exception as exc:
            logger.exception("Container apply task failed: %s", exc)
            _mark_task_failed(task, str(exc), db, module=locals().get("module"), failed_status="apply_failed")
            raise


@celery_app.task(bind=True, base=CallbackTask, name="tasks.container_tasks.run_container_action")
def run_container_action(self, task_db_id: int, module_id: int, action: str, action_inputs: dict | None = None, **kwargs):
    """Run a manifest-declared action's step-set on a post-apply module (D-034).

    Actions never mutate ``module.status`` — the Task + deployment record carry
    the run status, driven by step exit codes. No outputs capture, no cluster
    registration: an action exercises the deployment, it does not change it.
    """
    task = None
    with get_db_context() as db:
        try:
            task = fetch_task_or_raise(db, task_db_id)

            module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
            if not module:
                task.status = "failed"
                task.error = f"Module {module_id} not found"
                task.completed_at = datetime.now(UTC)
                db.commit()
                return {"success": False, "error": task.error}

            # Status gate: actions exercise a deployed module — a test against an
            # absent cluster fails fast with an actionable error.
            #
            # This gate is also why _build_engine_and_ctx below takes the default
            # operation="apply" rather than something destroy-flavoured: D-034's
            # contract is that an action exercises a deployed module WITHOUT
            # changing it, and this check means its dependencies are up by
            # definition. There is no action that can reach the dependency wiring
            # with its deps already torn down, so the strict (non-destroy) branch
            # is correct here, not just consistent with kubernetes_tasks.
            if module.status != "applied":
                task.status = "failed"
                task.error = (
                    f"Cannot run action '{action}': module must be in a post-apply "
                    f"state (applied), current status is '{module.status}'."
                )
                task.completed_at = datetime.now(UTC)
                db.commit()
                _publish_task_completion(task)
                return {"success": False, "error": task.error}

            task.status = "in_progress"
            task.started_at = datetime.now(UTC)
            task.command = f"container-engine: action {action}"
            task.meta_data = {**(task.meta_data or {}), "action": action}
            db.commit()
            _notify_task_started(task)

            with module_lock(db, module.id, task_id=task_db_id):
                # Re-assert the applied gate inside the lock (D-034 F3): the
                # pre-lock check at :518 is fail-fast, but a concurrent destroy
                # could win the lock, tear the deployment down, and release it
                # before this action acquires it. Re-read status now and refuse
                # to exercise a cluster that changed under us.
                db.refresh(module)
                if module.status != "applied":
                    task.status = "failed"
                    task.error = (
                        f"Cannot run action '{action}': cluster state changed under the action "
                        f"(module status is now '{module.status}', expected 'applied')."
                    )
                    task.completed_at = datetime.now(UTC)
                    db.commit()
                    _publish_task_completion(task)
                    return {"success": False, "error": task.error}

                # N1 (#457 re-review, defense-in-depth): re-validate the inputs
                # here, not only at submit_action. submit_action is the sole
                # caller today, but this task is the dispatch entry point — a
                # future direct caller (the planned PR-3 MCP tool) would
                # otherwise reach run_action with an unvalidated payload and
                # bypass the undeclared-key / enum / flag-injection filter.
                from utils.security import validate_action_inputs
                action_def = (_artifact_manifest(module).get("actions") or {}).get(action) or {}
                try:
                    action_inputs = validate_action_inputs(action_def.get("inputs") or [], action_inputs or {})
                except ValueError as exc:
                    task.status = "failed"
                    task.error = f"Invalid inputs for action '{action}': {exc}"
                    task.completed_at = datetime.now(UTC)
                    db.commit()
                    _publish_task_completion(task)
                    return {"success": False, "error": task.error}

                engine, ctx = _build_engine_and_ctx(db, module, celery_task_id=self.request.id)
                lines: list[str] = []
                header = f"=== CONTAINER ENGINE ACTION '{action}' ===\nModule: {ctx.path}"
                result = engine.run_action(
                    ctx,
                    action,
                    action_inputs=action_inputs,
                    on_output=_streaming_sink(task, db, header, lines),
                )

                task.exit_code = 0 if result.success else 1
                task.logs = _build_logs(header, lines, result)

                if result.success:
                    task.status = "completed"
                else:
                    task.status = "failed"
                    task.error = result.error_message or f"Action '{action}' failed"

                task.completed_at = datetime.now(UTC)
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                # Deliberately NO module.status change: the module stays 'applied'.
                db.commit()
                create_deployment_record(db, task, module, f"action:{action}"[:50], task.logs)
                _publish_task_completion(task)

            return {"success": result.success, "exit_code": task.exit_code}

        except (ModuleLockError, ModuleLockLostError) as e:
            logger.error("Container action task lock error: %s", e)
            _mark_task_failed(task, str(e), db, update_stack=False)
            raise
        except Exception as exc:
            logger.exception("Container action task failed: %s", exc)
            # No module/failed_status: an action failure must not change module.status.
            _mark_task_failed(task, str(exc), db, update_stack=False)
            raise


@celery_app.task(bind=True, base=CallbackTask, name="tasks.container_tasks.run_container_destroy")
def run_container_destroy(self, task_db_id: int, module_id: int, **kwargs):
    """Run the artifact's destroy step-set (or no-op)."""
    task = None
    with get_db_context() as db:
        try:
            task = fetch_task_or_raise(db, task_db_id)

            module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
            if not module:
                task.status = "failed"
                task.error = f"Module {module_id} not found"
                task.completed_at = datetime.now(UTC)
                db.commit()
                return {"success": False, "error": task.error}

            # Skip destroy for modules that were never applied.
            if module.status in ("not_initialized", "initialized", "planned", "init_failed", "plan_failed"):
                task.status = "completed"
                task.started_at = datetime.now(UTC)
                task.completed_at = datetime.now(UTC)
                task.error = f"Skipped: no deployed resources (status: {module.status})"
                module.status = "destroyed"
                db.commit()
                _publish_task_completion(task)
                _update_stack_status_if_needed(module, db)
                _trigger_next_destroy_module(module, db, task.id)
                return {"status": "skipped", "module_id": module.id}

            task.status = "in_progress"
            task.started_at = datetime.now(UTC)
            task.command = "container-engine: destroy"
            db.commit()
            _notify_task_started(task)

            with module_lock(db, module.id, task_id=task_db_id) as lock:
                engine, ctx = _build_engine_and_ctx(
                    db, module, operation="destroy", celery_task_id=self.request.id
                )
                lines: list[str] = []
                header = f"=== CONTAINER ENGINE DESTROY ===\nModule: {ctx.path}"
                sink = _streaming_sink(task, db, header, lines)
                result = engine.destroy(ctx, on_output=sink)
                sink.flush_stage()  # persist final phase if it was throttled

                task.exit_code = 0 if result.success else 1
                task.logs = _build_logs(header, lines, result)

                if result.success:
                    task.status = "completed"
                    fields = {"status": "destroyed", "outputs": None, "deployment_error": None}
                else:
                    task.status = "failed"
                    task.error = result.error_message or "Destroy failed"
                    fields = {"status": "destroy_failed", "deployment_error": (result.error_message or "")[:2000]}

                task.completed_at = datetime.now(UTC)
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                set_locked_module_fields(db, module, lock, **fields)
                update_project_counts(db, module.project_id)
                db.commit()
                create_deployment_record(db, task, module, "destroy", task.logs)
                _update_stack_status_if_needed(module, db)
                _trigger_next_destroy_module(module, db, task.id)

            if result.success:
                _maybe_unregister_container_cluster(db, module)

            return {"success": result.success, "exit_code": task.exit_code}

        except (ModuleLockError, ModuleLockLostError) as e:
            logger.error("Container destroy task lock error: %s", e)
            _exc_module = locals().get("module")
            _mark_task_failed(task, str(e), db, update_stack=False)
            if _exc_module:
                _exc_module.status = "destroy_failed"
                try:
                    db.commit()
                except Exception:
                    db.rollback()
            try:
                if _exc_module:
                    db.refresh(_exc_module)
                    _trigger_next_destroy_module(_exc_module, db, task.id)
            except Exception as trigger_err:
                logger.warning("destroy trigger failed after container lock error: %s", trigger_err)
            raise
        except Exception as exc:
            logger.exception("Container destroy task failed: %s", exc)
            _exc_module = locals().get("module")
            _mark_task_failed(task, str(exc), db, module=_exc_module, failed_status="destroy_failed")
            try:
                if _exc_module is not None:
                    db.refresh(_exc_module)
                    _trigger_next_destroy_module(_exc_module, db, task.id)
            except Exception as trigger_err:
                logger.warning("destroy trigger failed after container exception: %s", trigger_err)
            raise


@celery_app.task(name="tasks.container_tasks.kill_module_containers")
def kill_module_containers(celery_task_ids: list[str]) -> dict:
    """Kill the step containers owned by ``celery_task_ids``. Runs on the WORKER.

    This exists because cancel is invoked from a FastAPI route — i.e. the
    ``backend`` service, built from the ``api`` Dockerfile stage, which does NOT
    copy the docker CLI (only the ``worker`` stage does) and is not given
    DOCKER_HOST by compose. Calling the runner inline there raised
    FileNotFoundError, which the old code swallowed into "0 containers killed",
    so the user was told the operation stopped while the detached container kept
    driving the vendor CLI against live infrastructure.

    Same constraint the reaper already documents: only the celery services can
    reach the docker endpoint.

    Returns ``{"killed": [...], "reachable": bool, "error": str | None}`` so the
    caller can tell "nothing was running" from "I could not look" — the module
    lock must only be released on the former.
    """
    from services.execution.container_runner import (
        ContainerKillUnavailableError,
        DockerRunner,
    )

    runner = DockerRunner()
    killed: list[str] = []
    for task_id in celery_task_ids or []:
        if not task_id:
            continue
        try:
            killed.extend(runner.kill_task_containers(task_id))
        except ContainerKillUnavailableError as exc:
            logger.warning("kill_module_containers: %s", exc)
            return {"killed": killed, "reachable": False, "error": str(exc)}
    return {"killed": killed, "reachable": True, "error": None}
