"""
Kubernetes Engine Celery Tasks for BNK-Forge v2

Handles execution of K8s-native operations via the KubernetesEngine or OperatorEngine:
  - init: validate cluster connectivity
  - plan: compare rendered manifests vs live state
  - apply: server-side apply manifests / helm install
  - destroy: delete manifests / helm uninstall

Engine resolution (inside each task):
  1. Connected operator linked to project's cluster → OperatorEngine (preferred)
  2. Kubeconfig available → KubernetesEngine (direct mode, fallback)
  3. Neither → error

These tasks mirror the OpenTofu task signatures so the UI, stack deployment,
and parallel execution systems work identically regardless of engine.

The task_dispatch.py module decides WHETHER to call these tasks (vs OpenTofu).
The engine resolution within THIS file decides HOW (operator vs direct).
"""

import logging
from datetime import UTC, datetime

from celery_app import celery_app
from database import get_db_context
from models import Deployment, ProjectModule
from models import Task as TaskModel
from services.execution.engine_interface import DeploymentEngine
from services.module_lock import (
    ModuleLockError,
    ModuleLockLostError,
    module_lock,
    set_locked_module_fields,
)
from tasks._task_lookup import fetch_task_or_raise

logger = logging.getLogger(__name__)


def _ts() -> str:
    """Return a compact UTC timestamp for log prefixing."""
    return datetime.now(UTC).strftime("%H:%M:%S")


def _get_engine(project, db) -> tuple[DeploymentEngine, str]:
    """
    Resolve the best available engine for a project's cluster.

    Priority:
      1. OperatorEngine — if a connected operator is linked to the cluster
      2. KubernetesEngine — if kubeconfig is available (direct mode)

    Returns: (engine_instance, engine_label) where engine_label is
             "operator" or "kubernetes" for logging.
    """
    from services.execution.engine_router import EngineRouter

    # Try operator first
    operator_id = EngineRouter.resolve_operator_for_project(project, db)
    if operator_id:
        from models import ConnectedOperator
        from services.execution.operator_engine import OperatorEngine

        # Look up connectivity mode for the operator
        op_record = db.query(ConnectedOperator).filter(
            ConnectedOperator.operator_id == operator_id,
        ).first()
        connectivity_mode = op_record.connectivity_mode if op_record else "direct_ws"

        logger.info(
            f"Using OperatorEngine (operator={operator_id}, mode={connectivity_mode}) "
            f"for project '{project.name}'"
        )
        return OperatorEngine(operator_id, connectivity_mode=connectivity_mode), "operator"

    # Fall back to direct K8s
    kubeconfig_path = EngineRouter.resolve_kubeconfig(project, db)
    if kubeconfig_path:
        from services.execution.kubernetes_engine import KubernetesEngine
        context = EngineRouter.resolve_context(project, db)
        return KubernetesEngine(kubeconfig_path, context=context), "kubernetes"

    raise ValueError(
        f"No operator or kubeconfig available for project '{project.name}'. "
        f"Either connect an operator or register a Kubernetes cluster."
    )


def _build_variables(db, module, *, operation: str = "apply"):
    """Build the complete variable dict using the shared variable assembler."""
    from services.execution.variable_assembler import build_variables_for_k8s
    from services.secrets_service import SecretsService

    # Prepare secrets
    secrets_service = SecretsService(db)
    secret_variables = {}
    try:
        module_path = module.library_module.path if module.library_module else module.path_in_project
        value_secrets, _ = secrets_service.prepare_secrets_for_execution(
            module.project_id, "/tmp", module_path
        )
        secret_variables = value_secrets
    except Exception as e:
        logger.warning(f"Failed to prepare secrets for K8s module {module.id}: {e}")

    return build_variables_for_k8s(db, module, secret_variables, operation=operation)


def _create_deployment_record(db, task: TaskModel, module: ProjectModule, action: str, result) -> Deployment:
    """Create a Deployment record from a K8s engine result."""
    from tasks._tofu_helpers import create_deployment_record as _base_create

    # Adapt the OperationResult to match what create_deployment_record expects
    logs = result.log_output if hasattr(result, "log_output") else str(result)
    return _base_create(db, task, module, action, logs)


def _is_catalog_k8s_module(module: ProjectModule) -> bool:
    """Return True for non-builtin catalog rows using K8s deploy models."""
    lib = module.library_module
    if lib is None:
        return False

    module_source_kind = (getattr(lib, "module_source_kind", "") or "").strip().lower()
    deploy_model = (getattr(lib, "deploy_model", "") or "").strip().lower()
    return module_source_kind != "builtin" and deploy_model in {"manifests", "helm"} and isinstance(lib.pack_manifest, dict)


def _resolve_artifact_pull_secret(db, module: ProjectModule) -> None:
    """Resolve + persist the graph-wide pull secret for an artifact-kind module.

    helm_chart / manifest artifacts run through the K8s engine (helm install /
    apply); their charts/manifests reference container images the *cluster*
    pulls. Walk the references graph, enforce the host allowlist, assemble the
    merged dockerconfigjson, persist it as the project ``cne_pull_secret`` and
    push it into the cluster as a dockerconfigjson Secret. Best-effort — only
    runs for ``module_source_kind='artifact'`` rows.
    """
    lib = module.library_module
    if lib is None or (getattr(lib, "module_source_kind", "") or "").strip().lower() != "artifact":
        return
    from services.execution.container_run_secrets import (
        persist_cne_pull_secret,
        push_pull_secret_to_cluster,
        resolve_pull_authfile_for_module,
    )

    authfile = resolve_pull_authfile_for_module(db, module.project_id, lib)
    if authfile:
        persist_cne_pull_secret(db, module.project_id, authfile)
        push_pull_secret_to_cluster(db, module.project, authfile)
        db.commit()


def _prepare_catalog_workspace_if_needed(db, module: ProjectModule, *, operation: str) -> str | None:
    """Prepare persistent workspace for catalog-backed K8s modules only."""
    if not _is_catalog_k8s_module(module):
        return None

    from services.execution.opentofu_runtime import OpenTofuRuntime

    runtime = OpenTofuRuntime(db)
    return runtime.prepare_persistent_workspace(module, operation=operation)


def _notify_task_started(task: TaskModel):
    """Publish WebSocket update when task starts."""
    from services.websocket_service import publish_task_update
    publish_task_update(
        task_id=task.id,
        status=task.status,
        project_id=task.project_id,
        module_id=task.module_id,
        task_type=task.task_type,
    )


def _publish_task_completion(task: TaskModel):
    """Publish WebSocket update on task completion."""
    from services.websocket_service import publish_task_update
    publish_task_update(
        task_id=task.id,
        status=task.status,
        project_id=task.project_id,
        module_id=task.module_id,
        task_type=task.task_type,
        exit_code=task.exit_code,
        error=task.error,
        duration_seconds=task.duration_seconds,
        metadata=task.meta_data,
    )


# Import CallbackTask from shared helpers (shared base class)
from tasks._tofu_helpers import CallbackTask, _trigger_next_destroy_module, _update_stack_status_if_needed


@celery_app.task(
    bind=True,
    base=CallbackTask,
    name="tasks.kubernetes_tasks.run_k8s_init",
)
def run_k8s_init(self, task_db_id: int, module_id: int, auto_apply: bool = False, **kwargs):
    """
    Validate K8s connectivity for a module.

    For the K8s engine, "init" means:
      - Resolve kubeconfig for the project
      - Validate we can reach the API server
      - If auto_apply, chain into run_k8s_apply

    This is near-instant compared to OpenTofu's init (no provider downloads).
    """
    task = None

    with get_db_context() as db:
        try:
            task = fetch_task_or_raise(db, task_db_id)

            task.status = "in_progress"
            task.started_at = datetime.now(UTC)
            task.command = "k8s-engine: validate connectivity"
            db.commit()
            _notify_task_started(task)

            module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
            if not module:
                raise ValueError(f"Module {module_id} not found")

            project = module.project
            engine, engine_label = _get_engine(project, db)

            # Load cloud credentials for EKS auth / Helm OCI registry
            from services.credentials_service import get_cloud_credentials_env
            credentials_env = get_cloud_credentials_env(project, db)

            # Build minimal context for init
            from services.execution.engine_interface import ModuleContext
            library_module = module.library_module
            ctx = ModuleContext(
                module_id=module.id,
                project_id=project.id,
                path=library_module.path if library_module else module.path_in_project,
                category=library_module.category if library_module else "k8s",
                credentials_env=credentials_env,
                module_source_kind=(getattr(library_module, "module_source_kind", None) if library_module else None),
                deploy_model=(getattr(library_module, "deploy_model", None) if library_module else None),
                pack_manifest=(getattr(library_module, "pack_manifest", None) if library_module else None),
            )

            with module_lock(db, module.id, task_id=task_db_id) as lock:
                # Init = connectivity check
                result = engine.init(ctx)

                engine_desc = (
                    "OperatorEngine (remote agent)" if engine_label == "operator"
                    else "KubernetesEngine (kr8s + helm)"
                )
                task.exit_code = 0 if result.success else 1
                task.logs = (
                    f"[{_ts()}] === K8S ENGINE INIT ===\n"
                    f"[{_ts()}] Module: {ctx.path}\n"
                    f"[{_ts()}] Engine: {engine_desc}\n"
                    f"{result.stdout}\n"
                )

                # Record success/failure in circuit breaker
                from services.execution.engine_router import record_engine_failure, record_engine_success
                if result.success:
                    record_engine_success(engine_label)
                    task.status = "completed"
                    module_fields = {"status": "initialized", "last_init_at": datetime.now(UTC)}
                else:
                    record_engine_failure(engine_label)
                    task.status = "failed"
                    task.error = result.error_message
                    module_fields = {"status": "init_failed", "deployment_error": result.error_message}

                task.completed_at = datetime.now(UTC)
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                set_locked_module_fields(db, module, lock, **module_fields)

                from services.project_service import update_project_counts
                update_project_counts(db, project.id)
                db.commit()

                _create_deployment_record(db, task, module, "init", result)

                if not result.success:
                    _update_stack_status_if_needed(module, db)

                # Auto-apply if requested
                if result.success and auto_apply:
                    from services.execution.variable_assembler import can_execute
                    can_exec, missing = can_execute(db, module)
                    if can_exec:
                        apply_task = TaskModel(
                            task_type="apply",
                            status="queued",
                            project_id=module.project_id,
                            module_id=module.id,
                            created_at=datetime.now(UTC),
                        )
                        db.add(apply_task)
                        db.commit()
                        db.refresh(apply_task)

                        celery_result = run_k8s_apply.delay(apply_task.id, module.id)
                        apply_task.celery_task_id = celery_result.id
                        set_locked_module_fields(db, module, lock, status="applying")

                        logger.info(f"K8s auto-queued apply task {apply_task.id} for module {module.id}")

            return {"success": result.success, "exit_code": task.exit_code}

        except ModuleLockError as e:
            logger.error("K8s init task failed - module locked: %s", e)
            if task:
                task.status = "failed"
                task.error = f"Module locked: {str(e)}"
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()
            raise

        except ModuleLockLostError as e:
            logger.warning("K8s init aborted - lock lost mid-operation: %s", e)
            if task:
                task.status = "failed"
                task.error = f"Lock lost: {str(e)}"
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()
            raise

        except Exception as e:
            logger.exception(f"K8s init task failed: {e}")
            # Do NOT clobber module.status from this handler — the lock has
            # been released and another worker may already own state.
            if task:
                task.status = "failed"
                task.error = str(e)
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()
            raise


@celery_app.task(
    bind=True,
    base=CallbackTask,
    name="tasks.kubernetes_tasks.run_k8s_plan",
)
def run_k8s_plan(self, task_db_id: int, module_id: int, **kwargs):
    """Plan a K8s-engine module via OperatorEngine/KubernetesEngine."""
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
            task.command = "k8s-engine: plan"
            db.commit()
            _notify_task_started(task)

            project = module.project
            variables = _build_variables(db, module)
            workspace_path = _prepare_catalog_workspace_if_needed(db, module, operation="plan")
            engine, engine_label = _get_engine(project, db)
            module_path = module.library_module.path if module.library_module else module.path_in_project

            from services.credentials_service import get_cloud_credentials_env

            credentials_env = get_cloud_credentials_env(project, db)
            from services.execution.engine_interface import ModuleContext

            library_module = module.library_module
            ctx = ModuleContext(
                module_id=module.id,
                project_id=project.id,
                path=module_path,
                category=library_module.category if library_module else "k8s",
                variables=variables,
                credentials_env=credentials_env,
                module_source_kind=(getattr(library_module, "module_source_kind", None) if library_module else None),
                deploy_model=(getattr(library_module, "deploy_model", None) if library_module else None),
                pack_manifest=(getattr(library_module, "pack_manifest", None) if library_module else None),
                workspace_path=workspace_path,
            )

            output_lines = []

            def on_output(line: str):
                stamped = f"[{_ts()}] {line}"
                output_lines.append(stamped)
                logger.info(f"[{engine_label}:{module_path}] {line}")

            with module_lock(db, module.id, task_id=task_db_id) as lock:
                result = engine.plan(ctx, on_output=on_output)

                task.exit_code = 0
                task.logs = (
                    f"[{_ts()}] === K8S ENGINE PLAN ===\n"
                    f"[{_ts()}] Module: {module_path}\n"
                    f"[{_ts()}] Engine: {'OperatorEngine (remote agent)' if engine_label == 'operator' else 'KubernetesEngine (kr8s + helm)'}\n"
                    f"[{_ts()}] Has Changes: {result.has_changes}\n"
                    "---\n"
                    + "\n".join(output_lines)
                    + ("\n" + result.details if result.details else "")
                    + "\n"
                )

                task.status = "completed"
                task.completed_at = datetime.now(UTC)
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                set_locked_module_fields(db, module, lock, status="planned", deployment_error=None)

                from services.project_service import update_project_counts

                update_project_counts(db, project.id)
                db.commit()

                _create_deployment_record(db, task, module, "plan", result)
                _update_stack_status_if_needed(module, db)

            return {
                "success": True,
                "exit_code": 0,
                "has_changes": result.has_changes,
                "adds": result.adds,
                "changes": result.changes,
                "destroys": result.destroys,
                "engine": engine_label,
            }

        except ModuleLockError as e:
            logger.error("K8s plan task failed - module locked: %s", e)
            if task:
                task.status = "failed"
                task.error = f"Module locked: {str(e)}"
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()
            raise

        except ModuleLockLostError as e:
            logger.warning("K8s plan aborted - lock lost mid-operation: %s", e)
            if task:
                task.status = "failed"
                task.error = f"Lock lost: {str(e)}"
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()
            raise

        except Exception as e:
            logger.exception(f"K8s plan task failed: {e}")
            # Do NOT clobber module.status from this handler — the lock has
            # been released and another worker may already own state.
            if task:
                task.status = "failed"
                task.error = str(e)
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()
            raise


@celery_app.task(
    bind=True,
    base=CallbackTask,
    name="tasks.kubernetes_tasks.run_k8s_apply",
)
def run_k8s_apply(self, task_db_id: int, module_id: int, **kwargs):
    """
    Apply a K8s-engine module: render manifests → apply → wait → collect outputs.

    This combines init+plan+apply into a single fast operation because
    K8s manifest apply is idempotent (server-side apply) and near-instant.
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

            task.status = "in_progress"
            task.started_at = datetime.now(UTC)
            task.command = "k8s-engine: apply"
            db.commit()
            _notify_task_started(task)

            project = module.project

            # Check dependencies (same logic as OpenTofu path)
            from services.execution.variable_assembler import can_execute
            can_exec, missing = can_execute(db, module)
            if not can_exec:
                raise ValueError(f"Dependencies not satisfied: {', '.join(missing)}")

            # Build variables using the shared 7-layer chain
            variables = _build_variables(db, module)

            # Prepare workspace only when catalog metadata/artifacts are authoritative.
            workspace_path = _prepare_catalog_workspace_if_needed(db, module, operation="apply")

            # Get engine (operator or direct K8s)
            engine, engine_label = _get_engine(project, db)
            module_path = module.library_module.path if module.library_module else module.path_in_project

            # Load cloud credentials for Helm OCI registry auth
            from services.credentials_service import get_cloud_credentials_env
            credentials_env = get_cloud_credentials_env(project, db)

            from services.execution.engine_interface import ModuleContext
            library_module = module.library_module
            ctx = ModuleContext(
                module_id=module.id,
                project_id=project.id,
                path=module_path,
                category=library_module.category if library_module else "k8s",
                variables=variables,
                credentials_env=credentials_env,
                module_source_kind=(getattr(library_module, "module_source_kind", None) if library_module else None),
                deploy_model=(getattr(library_module, "deploy_model", None) if library_module else None),
                pack_manifest=(getattr(library_module, "pack_manifest", None) if library_module else None),
                workspace_path=workspace_path,
            )

            # Collect output lines for WebSocket streaming
            output_lines = []

            def on_output(line: str):
                stamped = f"[{_ts()}] {line}"
                output_lines.append(stamped)
                logger.info(f"[{engine_label}:{module_path}] {line}")

            # Supply-chain: resolve + push the graph-wide pull secret for
            # artifact-kind (helm_chart / manifest) modules before applying.
            _resolve_artifact_pull_secret(db, module)

            with module_lock(db, module.id, task_id=task_db_id) as lock:
                # Apply!
                result = engine.apply(ctx, on_output=on_output)

                # Build logs
                engine_desc = (
                    "OperatorEngine (remote agent)" if engine_label == "operator"
                    else "KubernetesEngine (kr8s + helm)"
                )
                all_logs = (
                    f"[{_ts()}] === K8S ENGINE APPLY ===\n"
                    f"[{_ts()}] Module: {module_path}\n"
                    f"[{_ts()}] Engine: {engine_desc}\n"
                    f"[{_ts()}] Duration: {result.duration_seconds:.1f}s\n"
                    f"---\n"
                    + "\n".join(output_lines)
                    + "\n"
                )
                if result.error_message:
                    all_logs += f"\n--- ERROR ---\n{result.error_message}\n"
                if result.error_suggestion:
                    all_logs += f"\n--- SUGGESTION ---\n{result.error_suggestion}\n"

                task.exit_code = 0 if result.success else 1
                task.logs = all_logs

                # Record success/failure in circuit breaker
                from services.execution.engine_router import record_engine_failure, record_engine_success
                if result.success:
                    record_engine_success(engine_label)
                    task.status = "completed"
                    module_fields = {
                        "status": "applied",
                        "outputs": result.outputs,
                        "last_deployed_at": datetime.now(UTC),
                        "deployment_error": None,
                    }
                    logger.info(
                        f"K8s apply succeeded for {module_path}: "
                        f"{result.resources_created} created, {result.resources_modified} modified, "
                        f"{len(result.outputs)} outputs in {result.duration_seconds:.1f}s"
                    )
                else:
                    record_engine_failure(engine_label)
                    task.status = "failed"
                    task.error = result.error_message or "Apply failed"
                    module_fields = {
                        "status": "apply_failed",
                        "deployment_error": (result.error_message or "")[:2000],
                    }

                task.completed_at = datetime.now(UTC)
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                set_locked_module_fields(db, module, lock, **module_fields)

                from services.project_service import update_project_counts
                update_project_counts(db, project.id)
                db.commit()

                _create_deployment_record(db, task, module, "apply", result)

                # Update stack status if module belongs to a stack
                _update_stack_status_if_needed(module, db)

            return {
                "success": result.success,
                "exit_code": task.exit_code,
                "outputs": result.outputs,
                "engine": engine_label,
                "duration_seconds": result.duration_seconds,
            }

        except ModuleLockError as e:
            logger.error("K8s apply task failed - module locked: %s", e)
            if task:
                task.status = "failed"
                task.error = f"Module locked: {str(e)}"
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()
            raise

        except ModuleLockLostError as e:
            logger.warning("K8s apply aborted - lock lost mid-operation: %s", e)
            if task:
                task.status = "failed"
                task.error = f"Lock lost: {str(e)}"
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()
            raise

        except Exception as e:
            logger.exception(f"K8s apply task failed: {e}")
            # Do NOT clobber module.status from this handler — the lock has
            # been released and another worker may already own state.
            if task:
                task.status = "failed"
                task.error = str(e)
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()
            raise


@celery_app.task(
    bind=True,
    base=CallbackTask,
    name="tasks.kubernetes_tasks.run_k8s_destroy",
)
def run_k8s_destroy(self, task_db_id: int, module_id: int, **kwargs):
    """
    Destroy a K8s-engine module: delete manifests / helm uninstall.
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

            # Skip destroy for modules that were never applied
            if module.status in ["not_initialized", "initialized", "planned", "init_failed", "plan_failed"]:
                task.status = "completed"
                task.started_at = datetime.now(UTC)
                task.completed_at = datetime.now(UTC)
                task.error = f"Skipped: no deployed resources (status: {module.status})"
                module.status = "destroyed"
                db.commit()
                _publish_task_completion(task)
                _update_stack_status_if_needed(module, db)
                # D-001 Phase 3: fire destroy trigger for skipped modules too
                _trigger_next_destroy_module(module, db, task.id)
                return {"status": "skipped", "module_id": module.id}

            task.status = "in_progress"
            task.started_at = datetime.now(UTC)
            task.command = "k8s-engine: destroy"
            db.commit()
            _notify_task_started(task)

            project = module.project
            variables = _build_variables(db, module, operation="destroy")
            workspace_path = _prepare_catalog_workspace_if_needed(db, module, operation="destroy")
            engine, engine_label = _get_engine(project, db)
            module_path = module.library_module.path if module.library_module else module.path_in_project

            # Load cloud credentials for Helm OCI registry auth
            from services.credentials_service import get_cloud_credentials_env
            credentials_env = get_cloud_credentials_env(project, db)

            from services.execution.engine_interface import ModuleContext
            library_module = module.library_module
            ctx = ModuleContext(
                module_id=module.id,
                project_id=project.id,
                path=module_path,
                category=library_module.category if library_module else "k8s",
                variables=variables,
                credentials_env=credentials_env,
                module_source_kind=(getattr(library_module, "module_source_kind", None) if library_module else None),
                deploy_model=(getattr(library_module, "deploy_model", None) if library_module else None),
                pack_manifest=(getattr(library_module, "pack_manifest", None) if library_module else None),
                workspace_path=workspace_path,
            )

            output_lines = []

            def on_output(line: str):
                stamped = f"[{_ts()}] {line}"
                output_lines.append(stamped)
                logger.info(f"[{engine_label}:{module_path}] {line}")

            with module_lock(db, module.id, task_id=task_db_id) as lock:
                result = engine.destroy(ctx, on_output=on_output)

                engine_desc = (
                    "OperatorEngine (remote agent)" if engine_label == "operator"
                    else "KubernetesEngine (kr8s + helm)"
                )
                all_logs = (
                    f"[{_ts()}] === K8S ENGINE DESTROY ===\n"
                    f"[{_ts()}] Module: {module_path}\n"
                    f"[{_ts()}] Engine: {engine_desc}\n"
                    f"[{_ts()}] Duration: {result.duration_seconds:.1f}s\n"
                    f"---\n"
                    + "\n".join(output_lines) + "\n"
                )

                task.exit_code = 0 if result.success else 1
                task.logs = all_logs

                # Record success/failure in circuit breaker
                from services.execution.engine_router import record_engine_failure, record_engine_success
                if result.success:
                    record_engine_success(engine_label)
                    task.status = "completed"
                    module_fields = {
                        "status": "destroyed",
                        "outputs": None,
                        "deployment_error": None,
                    }
                else:
                    record_engine_failure(engine_label)
                    task.status = "failed"
                    task.error = result.error_message or "Destroy failed"
                    module_fields = {
                        "status": "destroy_failed",
                        "deployment_error": (result.error_message or "")[:2000],
                    }

                task.completed_at = datetime.now(UTC)
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                set_locked_module_fields(db, module, lock, **module_fields)

                from services.project_service import update_project_counts
                update_project_counts(db, project.id)
                db.commit()

                _create_deployment_record(db, task, module, "destroy", result)

                _update_stack_status_if_needed(module, db)
                # D-001 Phase 3: event-chain destroy trigger hook
                _trigger_next_destroy_module(module, db, task.id)

            return {
                "success": result.success,
                "exit_code": task.exit_code,
                "engine": engine_label,
            }

        except ModuleLockError as e:
            logger.error("K8s destroy task failed - module locked: %s", e)
            # C1: set destroy_failed and fire trigger so terminal detection runs.
            _exc_module = locals().get('module')
            if task:
                task.status = "failed"
                task.error = f"Module locked: {str(e)}"
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
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
                logger.warning("_trigger_next_destroy_module failed after K8s ModuleLockError: %s", trigger_err)
            raise

        except ModuleLockLostError as e:
            logger.warning("K8s destroy aborted - lock lost mid-operation: %s", e)
            # C1: set destroy_failed and fire trigger so terminal detection runs.
            _exc_module = locals().get('module')
            if task:
                task.status = "failed"
                task.error = f"Lock lost: {str(e)}"
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
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
                logger.warning("_trigger_next_destroy_module failed after K8s ModuleLockLostError: %s", trigger_err)
            raise

        except Exception as e:
            logger.exception(f"K8s destroy task failed: {e}")
            _exc_module = locals().get('module')
            if _exc_module:
                _exc_module.status = "destroy_failed"
            if task:
                task.status = "failed"
                task.error = str(e)
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
            try:
                db.commit()
            except Exception:
                db.rollback()
            # C1: fire event-chain trigger so terminal detection runs even on exception
            try:
                if _exc_module is not None:
                    db.refresh(_exc_module)
                    _trigger_next_destroy_module(_exc_module, db, task.id)
            except Exception as trigger_err:
                logger.warning("_trigger_next_destroy_module failed after K8s generic exception: %s", trigger_err)
            raise
