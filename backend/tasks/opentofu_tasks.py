"""
OpenTofu Celery Tasks for BNK-Forge v2

Handles asynchronous execution of OpenTofu operations:
- init: Initialize module workspace
- plan: Generate execution plan
- apply: Apply infrastructure changes
- destroy: Destroy infrastructure

NOTE: This replaces terraform_tasks.py from v1.
Direct OpenTofu execution with runtime variable injection (v2 architecture).
"""

import logging
from datetime import UTC, datetime

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.orm import joinedload

from celery_app import celery_app
from database import get_db_context
from models import ProjectModule
from models import Task as TaskModel
from services.credentials_service import get_cloud_credentials_env
from services.execution.opentofu_runtime import OpenTofuRuntime
from services.execution.variable_assembler import can_execute as check_dependencies
from services.infrastructure_access_service import (
    cleanup_durable_infra_private_key,
    normalize_module_outputs_in_place,
)
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
    TofuLogStreamer,
    _cleanup_stuck_finalizers,
    _create_notification,
    _is_namespace_finalizer_issue,
    _mark_task_failed,
    _notify_task_started,
    _publish_task_completion,
    _trigger_next_destroy_module,
    _update_stack_status_if_needed,
    create_deployment_record,
)

logger = logging.getLogger(__name__)


def _ts() -> str:
    """Return a compact UTC timestamp for log prefixing."""
    return datetime.now(UTC).strftime("%H:%M:%S")


def _is_saved_plan_stale_error(logs: str) -> bool:
    """Return True when OpenTofu reports a stale saved plan."""
    return "saved plan is stale" in (logs or "").lower()


@celery_app.task(
    bind=True,
    base=CallbackTask,
    name="tasks.opentofu_tasks.run_opentofu_init",
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    max_retries=2,
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
)
def run_opentofu_init(self, task_db_id: int, module_id: int, keep_workspace: bool = False, auto_apply: bool = False, force_reinit: bool = False):
    """
    Run tofu init for a module using persistent workspace.

    Uses persistent workspaces to enable skipping init if already initialized.
    Workspace persists at /app/workspaces/{project_id}/{module_id}/.

    Args:
        task_db_id: Database ID of the Task record
        module_id: ProjectModule ID to initialize
        keep_workspace: Keep workspace for debugging (always true with persistent workspaces)
        auto_apply: If True, automatically queue apply after successful init
                   (only if dependencies are satisfied)
        force_reinit: Force re-initialization even if already initialized

    Returns:
        dict: Task result
    """
    work_dir = None

    with get_db_context() as db:
        try:
            # Get task and module
            task = fetch_task_or_raise(db, task_db_id)

            task.status = "in_progress"
            task.started_at = datetime.now(UTC)
            db.commit()

            _notify_task_started(task)

            module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
            if not module:
                raise ValueError(f"Module {module_id} not found")

            project = module.project
            engine = OpenTofuRuntime(db)

            # Import WorkspaceManager for persistent workspace handling
            from services.workspace_manager import WorkspaceManager
            workspace = WorkspaceManager(db)

            # Note: Init does NOT require dependencies to be applied
            # Init only downloads providers and modules from the registry
            # Dependency checks are only needed for plan/apply when we need outputs

            # Acquire workspace lock to prevent concurrent operations (CQ-009)
            with module_lock(db, module.id, task_id=task_db_id) as lock:
                # Prepare persistent workspace (creates dir, clones source if needed, writes configs)
                work_dir = engine.prepare_persistent_workspace(module)
                task.working_directory = work_dir
                task.command = "tofu init"
                db.commit()

                # Preserve existing reinit safety checks (backend/version/source drift)
                # before attempting any blueprint cache shortcut.
                needs_reinit, _reinit_reason = workspace.needs_reinit(module)

                # Blueprint cache fast-path: attach pre-initialized providers/modules.
                # Safety: cache failures must never fail init task.
                if not force_reinit and not needs_reinit:
                    try:
                        if workspace.is_blueprint_eligible(module) and module.stack_instance_id:
                            if workspace.attach_cached_init(module, module.stack_instance_id):
                                logs = (
                                    f"[{_ts()}] === INIT SKIPPED (blueprint cache hit) ===\n"
                                    f"[{_ts()}] Workspace: {work_dir}\n"
                                    f"[{_ts()}] Attached cached providers/modules from shared blueprint workspace.\n"
                                    f"[{_ts()}] Skipped redundant tofu init.\n"
                                )
                                task.exit_code = 0
                                task.logs = logs
                                task.status = "completed"
                                task.completed_at = datetime.now(UTC)
                                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                                set_locked_module_fields(db, module, lock, status="initialized")
                                workspace.mark_initialized(module)
                                workspace.save_init_version(module)

                                logger.info(f"Init skipped for module {module.id} - blueprint cache attached")

                                create_deployment_record(db, task, module, "init", logs)

                                apply_queued = False
                                if auto_apply:
                                    can_exec, missing = check_dependencies(db, module)
                                    if can_exec:
                                        apply_task = TaskModel(
                                            task_type="apply",
                                            status="queued",
                                            project_id=module.project_id,
                                            module_id=module.id,
                                            created_at=datetime.now(UTC)
                                        )
                                        db.add(apply_task)
                                        db.commit()
                                        db.refresh(apply_task)

                                        celery_result = run_opentofu_apply.delay(apply_task.id, module.id)
                                        apply_task.celery_task_id = celery_result.id
                                        set_locked_module_fields(db, module, lock, status="applying")
                                        apply_queued = True

                                        logger.info(f"Auto-queued apply task {apply_task.id} for module {module.id}")
                                    else:
                                        logger.info(
                                            "Auto-apply skipped for module %s: dependencies not ready (%s)",
                                            module.id, missing,
                                        )

                                return {"success": True, "exit_code": 0, "skipped": True, "apply_queued": apply_queued}
                    except Exception as cache_error:
                        logger.warning(
                            "Blueprint cache attach failed for module %s (falling back to fresh init): %s",
                            module.id,
                            cache_error,
                        )

                # Check if already initialized (and not forcing reinit)
                if workspace.is_initialized(module) and not needs_reinit and not force_reinit:
                    # Already initialized - skip init!
                    logs = (
                        f"[{_ts()}] === INIT SKIPPED (already initialized) ===\n"
                        f"[{_ts()}] Workspace: {work_dir}\n"
                        f"[{_ts()}] Last init at: {module.last_init_at}\n"
                        f"[{_ts()}] Providers and modules are cached. Skipping redundant initialization.\n"
                    )
                    task.exit_code = 0
                    task.logs = logs
                    task.status = "completed"
                    task.completed_at = datetime.now(UTC)
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                    set_locked_module_fields(db, module, lock, status="initialized")

                    logger.info(f"Init skipped for module {module.id} - already initialized")

                    # Still create deployment record for tracking
                    create_deployment_record(db, task, module, "init", logs)

                    # Handle auto-apply if requested
                    apply_queued = False
                    if auto_apply:
                        can_exec, missing = check_dependencies(db, module)
                        if can_exec:
                            apply_task = TaskModel(
                                task_type="apply",
                                status="queued",
                                project_id=module.project_id,
                                module_id=module.id,
                                created_at=datetime.now(UTC)
                            )
                            db.add(apply_task)
                            db.commit()
                            db.refresh(apply_task)

                            celery_result = run_opentofu_apply.delay(apply_task.id, module.id)
                            apply_task.celery_task_id = celery_result.id
                            set_locked_module_fields(db, module, lock, status="applying")
                            apply_queued = True

                            logger.info(f"Auto-queued apply task {apply_task.id} for module {module.id}")
                        else:
                            logger.info(
                                "Auto-apply skipped for module %s: dependencies not ready (%s)",
                                module.id, missing,
                            )

                    return {"success": True, "exit_code": 0, "skipped": True, "apply_queued": apply_queued}

                # Get credentials
                env = get_cloud_credentials_env(project, db)

                # Run init — stream output so logs_full_size grows during the
                # run instead of only at completion (#195).
                streamer = TofuLogStreamer(task, db)
                exit_code, logs = engine.run_init(
                    work_dir, env, on_output=streamer.begin(""),
                )

                # Update task
                task.exit_code = exit_code
                task.logs = logs

                if exit_code == 0:
                    task.status = "completed"
                    module_fields = {"status": "initialized"}
                    workspace.mark_initialized(module)
                    workspace.save_init_version(module)
                    try:
                        if workspace.is_blueprint_eligible(module) and module.stack_instance_id:
                            workspace.publish_init_to_cache(module, module.stack_instance_id)
                    except Exception as cache_error:
                        logger.warning(
                            "Blueprint cache publish failed for module %s (non-fatal): %s",
                            module.id,
                            cache_error,
                        )
                else:
                    task.status = "failed"
                    task.error = "Init failed"
                    module_fields = {
                        "status": "init_failed",
                        "deployment_error": logs[-2000:] if len(logs) > 2000 else logs,
                    }

                task.completed_at = datetime.now(UTC)
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                set_locked_module_fields(db, module, lock, **module_fields)

                # Update project counts
                update_project_counts(db, project.id)
                db.commit()

                # Create deployment record
                create_deployment_record(db, task, module, "init", logs)

                # Update stack status if init failed (success path updates via apply)
                if exit_code != 0 and module.stack_instance_id:
                    _update_stack_status_if_needed(module, db)

                # Auto-apply if requested and init succeeded
                apply_queued = False
                if exit_code == 0 and auto_apply:
                    # Check if dependencies are now satisfied
                    can_exec, missing = check_dependencies(db, module)
                    if can_exec:
                        # Create apply task
                        apply_task = TaskModel(
                            task_type="apply",
                            status="queued",
                            project_id=module.project_id,
                            module_id=module.id,
                            created_at=datetime.now(UTC)
                        )
                        db.add(apply_task)
                        db.commit()
                        db.refresh(apply_task)

                        # Queue apply
                        celery_result = run_opentofu_apply.delay(apply_task.id, module.id)
                        apply_task.celery_task_id = celery_result.id
                        set_locked_module_fields(db, module, lock, status="applying")
                        apply_queued = True

                        logger.info(f"Auto-queued apply task {apply_task.id} for module {module.id}")
                    else:
                        logger.info(
                            "Auto-apply skipped for module %s: dependencies not ready (%s)",
                            module.id, missing,
                        )

            return {"success": exit_code == 0, "exit_code": exit_code, "skipped": False, "apply_queued": apply_queued}

        except ModuleLockError as e:
            logger.error(f"Init task failed - module locked: {e}")
            # Lock acquisition failed — another live worker holds the lock.
            # Do NOT touch module.status; let the live holder own state
            # transitions. Just fail this task cleanly.
            if task:
                task.status = "failed"
                task.error = f"Module locked: {str(e)}"
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()
            raise

        except ModuleLockLostError as e:
            logger.warning(f"Init task aborted - lock lost mid-operation: {e}")
            # Another worker has reclaimed the lock and now owns module state.
            # Update only the task row; leave module.status to the new holder.
            if task:
                task.status = "failed"
                task.error = f"Lock lost: {str(e)}"
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()
            raise

        except Exception as e:
            logger.exception(f"Init task failed: {e}")
            _mark_task_failed(task, e, db,
                              module=locals().get('module'),
                              failed_status="init_failed")
            raise


@celery_app.task(
    bind=True,
    base=CallbackTask,
    name="tasks.opentofu_tasks.run_opentofu_plan",
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    max_retries=2,
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
)
def run_opentofu_plan(self, task_db_id: int, module_id: int, keep_workspace: bool = False):
    """
    Run tofu plan for a module using persistent workspace.

    Uses persistent workspace. If module is already initialized, skips init.
    Saves plan.out for later apply and stores vars_hash for drift detection.

    Args:
        task_db_id: Database ID of the Task record
        module_id: ProjectModule ID to plan
        keep_workspace: Keep workspace for debugging (always true with persistent workspaces)

    Returns:
        dict: Task result
    """
    work_dir = None

    with get_db_context() as db:
        try:
            task = fetch_task_or_raise(db, task_db_id)

            # S14-009: Validate module BEFORE setting task to in_progress
            module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
            if not module:
                task.status = "failed"
                task.error = f"Module {module_id} not found"
                task.completed_at = datetime.now(UTC)
                db.commit()
                return {"success": False, "error": task.error}

            task.status = "in_progress"
            task.started_at = datetime.now(UTC)
            db.commit()

            _notify_task_started(task)

            project = module.project
            engine = OpenTofuRuntime(db)

            # Import WorkspaceManager for persistent workspace handling
            from services.workspace_manager import WorkspaceManager
            workspace = WorkspaceManager(db)

            # Check dependencies
            can_exec, missing = check_dependencies(db, module)
            if not can_exec:
                raise ValueError(f"Dependencies not satisfied: {', '.join(missing)}")

            env = get_cloud_credentials_env(project, db)

            # Acquire workspace lock to prevent concurrent operations (CQ-009)
            with module_lock(db, module.id, task_id=task_db_id) as lock:
                # Prepare persistent workspace (creates dir, updates configs)
                work_dir = engine.prepare_persistent_workspace(module)
                task.working_directory = work_dir
                db.commit()

                all_logs = ""
                # Stream tofu output into task.logs during the run (#195).
                streamer = TofuLogStreamer(task, db)

                # Check if workspace is initialized - run init if needed
                needs_reinit, reinit_reason = workspace.needs_reinit(module)
                if workspace.is_initialized(module) and not needs_reinit:
                    # Skip init - use cached providers
                    all_logs += f"[{_ts()}] === INIT SKIPPED (using cached providers) ===\n"
                    task.command = "tofu plan"
                    logger.info(f"Skipping init for module {module.id} - already initialized")
                else:
                    if reinit_reason:
                        all_logs += f"[{_ts()}] === INIT REQUIRED: {reinit_reason} ===\n"
                    # Need to run init first
                    task.command = "tofu init && tofu plan"
                    all_logs += f"[{_ts()}] --- INIT ---\n"
                    init_code, init_logs = engine.run_init(
                        work_dir, env, on_output=streamer.begin(all_logs),
                    )
                    all_logs += init_logs

                    if init_code != 0:
                        task.exit_code = init_code
                        task.logs = all_logs
                        task.status = "failed"
                        task.error = "Init failed during plan"
                        task.completed_at = datetime.now(UTC)
                        set_locked_module_fields(db, module, lock, status="init_failed")
                        _publish_task_completion(task)  # S14-007: Notify UI on early-return failure
                        return {"success": False, "exit_code": init_code}

                    # Mark initialized and save version
                    workspace.mark_initialized(module)
                    workspace.save_init_version(module)
                    all_logs += "\n"

                # Run plan (saves plan.out to workspace)
                all_logs += f"[{_ts()}] --- PLAN ---\n"
                exit_code, plan_logs = engine.run_plan(
                    work_dir, env, on_output=streamer.begin(all_logs),
                )
                all_logs += plan_logs

                task.exit_code = exit_code
                task.logs = all_logs

                if exit_code == 0:
                    task.status = "completed"
                    module_fields = {"status": "planned"}

                    # Save plan metadata for apply validation
                    # Compute and store vars_hash to detect if variables change before apply
                    workspace.update_vars_hash(module)

                    # Save plan output summary
                    workspace.save_plan_output(module, plan_logs)

                    logger.info(f"Plan saved for module {module.id}, serial={module.plan_serial}, vars_hash={module.vars_hash[:8] if module.vars_hash else 'N/A'}...")
                else:
                    task.status = "failed"
                    task.error = "Plan failed"
                    module_fields = {
                        "status": "plan_failed",
                        "deployment_error": plan_logs[-2000:] if len(plan_logs) > 2000 else plan_logs,
                    }

                task.completed_at = datetime.now(UTC)
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                set_locked_module_fields(db, module, lock, **module_fields)

                update_project_counts(db, project.id)
                db.commit()
                create_deployment_record(db, task, module, "plan", all_logs)

            return {"success": exit_code == 0, "exit_code": exit_code}

        except ModuleLockError as e:
            logger.error(f"Plan task failed - module locked: {e}")
            # Lock acquisition failed — another live worker holds the lock.
            # Do NOT touch module.status; let the live holder own state
            # transitions. Just fail this task cleanly.
            if task:
                task.status = "failed"
                task.error = f"Module locked: {str(e)}"
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()
            raise

        except ModuleLockLostError as e:
            logger.warning(f"Plan task aborted - lock lost mid-operation: {e}")
            if task:
                task.status = "failed"
                task.error = f"Lock lost: {str(e)}"
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()
            raise

        except Exception as e:
            # S15-001: Removed duplicate exception handler (first block had wrong "apply_failed" status)
            logger.exception(f"Plan task failed: {e}")
            _mark_task_failed(task, e, db,
                              module=locals().get('module'),
                              failed_status="plan_failed")
            raise


@celery_app.task(
    bind=True,
    base=CallbackTask,
    name="tasks.opentofu_tasks.run_opentofu_apply",
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    max_retries=1,
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
)
def run_opentofu_apply(self, task_db_id: int, module_id: int, keep_workspace: bool = False, force_new_plan: bool = False):
    """
    Run tofu apply for a module using saved plan from persistent workspace.

    With persistent workspaces:
    - If a saved plan exists and is valid, applies it directly (no init/plan)
    - If no saved plan, falls back to init + plan + apply
    - Validates that variables haven't changed since plan was created

    Args:
        task_db_id: Database ID of the Task record
        module_id: ProjectModule ID to apply
        keep_workspace: Keep workspace for debugging (always true with persistent workspaces)
        force_new_plan: Force running a new plan even if saved plan exists

    Returns:
        dict: Task result with outputs
    """
    work_dir = None
    apply_code = None

    with get_db_context() as db:
        try:
            task = fetch_task_or_raise(db, task_db_id)

            # S14-009: Validate module BEFORE setting task to in_progress
            # If module doesn't exist, fail the task cleanly instead of leaving it stuck
            module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
            if not module:
                task.status = "failed"
                task.error = f"Module {module_id} not found"
                task.completed_at = datetime.now(UTC)
                db.commit()
                return {"success": False, "error": task.error}

            task.status = "in_progress"
            task.started_at = datetime.now(UTC)
            db.commit()

            _notify_task_started(task)

            project = module.project
            engine = OpenTofuRuntime(db)

            # Import WorkspaceManager for persistent workspace handling
            from services.workspace_manager import WorkspaceManager
            workspace = WorkspaceManager(db)

            # Check dependencies
            can_exec, missing = check_dependencies(db, module)
            if not can_exec:
                raise ValueError(f"Dependencies not satisfied: {', '.join(missing)}")

            env = get_cloud_credentials_env(project, db)

            # Acquire workspace lock to prevent concurrent operations (CQ-009)
            # Apply uses longer timeout since it can run for extended periods
            with module_lock(db, module.id, task_id=task_db_id) as lock:
                # Prepare persistent workspace (creates dir, updates configs)
                work_dir = engine.prepare_persistent_workspace(module)
                task.working_directory = work_dir
                db.commit()

                all_logs = ""
                used_saved_plan = False
                # Stream tofu output into task.logs during the run (#195).
                streamer = TofuLogStreamer(task, db)

                def _ensure_workspace_initialized() -> bool:
                    """Ensure providers/modules are installed before reconcile/plan/apply."""
                    nonlocal all_logs
                    needs_reinit, reinit_reason = workspace.needs_reinit(module)
                    if workspace.is_initialized(module) and not needs_reinit:
                        all_logs += f"[{_ts()}] === INIT SKIPPED (using cached providers) ===\n"
                        return True

                    if reinit_reason:
                        all_logs += f"[{_ts()}] === INIT REQUIRED: {reinit_reason} ===\n"

                    all_logs += f"[{_ts()}] --- INIT ---\n"
                    init_code, init_logs = engine.run_init(
                        work_dir, env, on_output=streamer.begin(all_logs),
                    )
                    all_logs += init_logs
                    if init_code != 0:
                        task.exit_code = init_code
                        task.logs = all_logs
                        task.status = "failed"
                        task.error = "Init failed"
                        task.completed_at = datetime.now(UTC)
                        set_locked_module_fields(db, module, lock, status="init_failed")
                        _publish_task_completion(task)  # S14-007: Notify UI on early-return failure
                        return False

                    workspace.mark_initialized(module)
                    workspace.save_init_version(module)
                    all_logs += "\n"
                    return True

                # Check for saved plan
                has_plan = workspace.has_saved_plan(module)
                plan_valid, invalid_reason = workspace.plan_is_valid(module) if has_plan else (False, "No plan")

                run_new_plan = True

                if has_plan and plan_valid and not force_new_plan:
                    # Even with saved plan, init must be ready before reconcile/import operations.
                    if not _ensure_workspace_initialized():
                        return {"success": False, "exit_code": task.exit_code}

                    reconciled_imports = engine.reconcile_known_existing_resources(work_dir, module, env)
                    all_logs += (
                        f"[{_ts()}] === PRE-APPLY STATE RECONCILIATION ===\n"
                        f"[{_ts()}] Imported {reconciled_imports} existing resource(s) into state for idempotent rerun.\n"
                    )

                    if reconciled_imports > 0:
                        all_logs += (
                            f"[{_ts()}] Saved plan is now stale — creating new plan.\n"
                            f"[{_ts()}] === RECONCILIATION INVALIDATED SAVED PLAN ===\n"
                            f"[{_ts()}] Discarding saved plan because state imports changed managed resources.\n\n"
                        )
                        workspace.clear_plan(module)
                        has_plan = False
                        plan_valid = False
                    else:
                        all_logs += "\n"
                        # Use saved plan! (The idiomatic workflow)
                        task.command = "tofu apply plan.out"
                        all_logs += (
                            f"[{_ts()}] === USING SAVED PLAN ===\n"
                            f"[{_ts()}] Plan serial: {module.plan_serial}\n"
                            f"[{_ts()}] Vars hash: {module.vars_hash[:16] if module.vars_hash else 'N/A'}...\n"
                            f"[{_ts()}] Applying previously reviewed plan.\n\n"
                        )
                        used_saved_plan = True
                        run_new_plan = False
                        logger.info(f"Applying saved plan for module {module.id} (serial={module.plan_serial})")

                if run_new_plan:
                    # Need to create a new plan
                    if has_plan and not plan_valid:
                        all_logs += f"[{_ts()}] === SAVED PLAN INVALID: {invalid_reason} ===\n"
                        all_logs += f"[{_ts()}] Creating new plan...\n\n"
                        workspace.clear_plan(module)

                    if force_new_plan and has_plan:
                        all_logs += f"[{_ts()}] === FORCE NEW PLAN REQUESTED ===\n"
                        all_logs += f"[{_ts()}] Discarding existing saved plan before replanning.\n\n"
                        workspace.clear_plan(module)

                    task.command = "tofu init && tofu plan && tofu apply"

                    # Ensure init is complete before reconciliation and plan
                    if not _ensure_workspace_initialized():
                        return {"success": False, "exit_code": task.exit_code}

                    # Reconcile known existing K8s resources AFTER init (providers must be available)
                    reconciled_imports = engine.reconcile_known_existing_resources(work_dir, module, env)
                    all_logs += (
                        f"[{_ts()}] === PRE-PLAN STATE RECONCILIATION ===\n"
                        f"[{_ts()}] Imported {reconciled_imports} existing resource(s) into state for idempotent rerun.\n\n"
                    )
                    if reconciled_imports > 0:
                        # Clear any stale saved plan since state changed
                        if workspace.has_saved_plan(module):
                            all_logs += f"[{_ts()}] Discarding saved plan because state imports changed managed resources.\n\n"
                            workspace.clear_plan(module)

                    # Run plan
                    all_logs += f"[{_ts()}] --- PLAN ---\n"
                    plan_code, plan_logs = engine.run_plan(
                        work_dir, env, on_output=streamer.begin(all_logs),
                    )
                    all_logs += plan_logs
                    if plan_code != 0:
                        task.exit_code = plan_code
                        task.logs = all_logs
                        task.status = "failed"
                        task.error = "Plan failed"
                        task.completed_at = datetime.now(UTC)
                        set_locked_module_fields(
                            db, module, lock,
                            status="plan_failed",
                            deployment_error=plan_logs[-2000:] if len(plan_logs) > 2000 else plan_logs,
                        )
                        _publish_task_completion(task)  # S14-007: Notify UI on early-return failure
                        return {"success": False, "exit_code": plan_code}

                    all_logs += "\n"

                # Apply (uses plan.out which exists either from saved plan or just-created plan)
                all_logs += f"[{_ts()}] --- APPLY ---\n"
                apply_code, apply_logs, outputs = engine.run_apply(
                    work_dir, env, module=module, on_output=streamer.begin(all_logs),
                )

                # Bounded stale-plan recovery: clear stale plan, re-plan once, then retry apply.
                # This prevents repeated failures when remote state changed after plan creation.
                if apply_code != 0 and _is_saved_plan_stale_error(apply_logs):
                    logger.warning(
                        "Detected stale saved plan for module %s; clearing plan and retrying once",
                        module.id,
                    )
                    all_logs += (
                        f"\n[{_ts()}] === SAVED PLAN STALE: AUTO-RECOVER ONCE ===\n"
                        f"[{_ts()}] Discarding stale plan, generating a fresh plan, then retrying apply.\n\n"
                    )
                    workspace.clear_plan(module)

                    needs_reinit, reinit_reason = workspace.needs_reinit(module)
                    if workspace.is_initialized(module) and not needs_reinit:
                        all_logs += f"[{_ts()}] === INIT SKIPPED (using cached providers) ===\n"
                    else:
                        if reinit_reason:
                            all_logs += f"[{_ts()}] === INIT REQUIRED: {reinit_reason} ===\n"
                        init_code, init_logs = engine.run_init(
                            work_dir, env, on_output=streamer.begin(all_logs),
                        )
                        all_logs += init_logs
                        if init_code != 0:
                            task.exit_code = init_code
                            task.logs = all_logs
                            task.status = "failed"
                            task.error = "Init failed"
                            task.completed_at = datetime.now(UTC)
                            set_locked_module_fields(db, module, lock, status="init_failed")
                            _publish_task_completion(task)
                            return {"success": False, "exit_code": init_code}

                        workspace.mark_initialized(module)
                        workspace.save_init_version(module)
                        all_logs += "\n"

                    all_logs += f"[{_ts()}] --- PLAN (RETRY AFTER STALE PLAN) ---\n"
                    plan_code, plan_logs = engine.run_plan(
                        work_dir, env, on_output=streamer.begin(all_logs),
                    )
                    all_logs += plan_logs
                    if plan_code != 0:
                        task.exit_code = plan_code
                        task.logs = all_logs
                        task.status = "failed"
                        task.error = "Plan failed"
                        task.completed_at = datetime.now(UTC)
                        set_locked_module_fields(
                            db, module, lock,
                            status="plan_failed",
                            deployment_error=plan_logs[-2000:] if len(plan_logs) > 2000 else plan_logs,
                        )
                        _publish_task_completion(task)
                        return {"success": False, "exit_code": plan_code}

                    all_logs += "\n"
                    all_logs += f"[{_ts()}] --- APPLY (RETRY) ---\n"
                    apply_code, apply_logs, outputs = engine.run_apply(
                        work_dir, env, module=module, on_output=streamer.begin(all_logs),
                    )

                all_logs += apply_logs

                task.exit_code = apply_code
                task.logs = all_logs

                if apply_code == 0:
                    task.status = "completed"
                    # Normalize outputs before persisting (mutates module.outputs in place;
                    # we capture the result and clear ORM dirty state so the fence-protected
                    # UPDATE below is the sole writer.)
                    module.outputs = outputs
                    normalize_module_outputs_in_place(module)
                    final_outputs = module.outputs
                    db.expire(module, ["outputs"])
                    set_locked_module_fields(
                        db, module, lock,
                        status="applied",
                        outputs=final_outputs,
                        last_deployed_at=datetime.now(UTC),
                        deployment_error=None,
                    )
                    logger.info(f"Stored {len(final_outputs or {})} outputs for module {module.id}")

                    # Clear the consumed plan
                    workspace.clear_plan(module)

                    # PERF-011/PERF-017: Invalidate all state caches after apply
                    from services.state_decryption_service import invalidate_state_cache
                    invalidate_state_cache(module_id, project.id)

                    # Auto-register managed cluster (EKS or ROKS) to Kubernetes page
                    from services.cluster_management_service import ClusterManagementService
                    from services.eks_service import matches_eks_output_contract
                    from tasks.cluster_scan_task import enqueue_cluster_scan
                    managed_type = ClusterManagementService._classify_managed_cluster_module(module.library_module)
                    if managed_type == "eks" or matches_eks_output_contract(final_outputs):
                        try:
                            from services.eks_service import register_eks_cluster
                            cluster = register_eks_cluster(db, module)
                            logger.info(f"Auto-registered EKS cluster {cluster.name} to Kubernetes page")

                            # Add metadata for frontend notification
                            task.meta_data = task.meta_data or {}
                            task.meta_data["eks_cluster_registered"] = {
                                "cluster_id": cluster.id,
                                "cluster_name": cluster.name,
                                "message": f"EKS cluster '{cluster.name}' registered to Kubernetes page"
                            }
                            db.commit()
                            enqueue_cluster_scan(int(cluster.id))
                        except Exception as e:
                            logger.warning(f"Failed to auto-register EKS cluster: {e}")
                            # Don't fail the apply if registration fails - it's a convenience feature
                    elif managed_type == "roks":
                        try:
                            from services.roks_service import register_roks_cluster
                            cluster = register_roks_cluster(db, module)
                            logger.info(f"Auto-registered ROKS cluster {cluster.name} to Kubernetes page")

                            task.meta_data = task.meta_data or {}
                            task.meta_data["roks_cluster_registered"] = {
                                "cluster_id": cluster.id,
                                "cluster_name": cluster.name,
                                "message": f"ROKS cluster '{cluster.name}' registered to Kubernetes page"
                            }
                            db.commit()
                            enqueue_cluster_scan(int(cluster.id))
                        except ValueError as e:
                            logger.warning(f"ROKS auto-registration skipped (missing outputs): {e}")
                        except Exception as e:
                            logger.warning(f"Failed to auto-register ROKS cluster: {e}")
                            # Don't fail the apply if registration fails - it's a convenience feature

                    # Output-contract-driven cluster auto-registration
                    # Any module outputting cluster_name + remote_kubeconfig_path + remote_host
                    # gets its cluster auto-registered with proper SSH tunnel settings
                    try:
                        from services.cluster_auto_registration_service import (
                            maybe_auto_register_cluster,
                        )
                        reg_result = maybe_auto_register_cluster(db, module, outputs)
                        if reg_result and reg_result.get("success"):
                            logger.info(
                                "Auto-registered cluster '%s' (id=%s) from module %s outputs",
                                reg_result.get("cluster_name"),
                                reg_result.get("cluster_id"),
                                module.id,
                            )
                            task.meta_data = task.meta_data or {}
                            task.meta_data["cluster_auto_registered"] = {
                                "cluster_id": reg_result.get("cluster_id"),
                                "cluster_name": reg_result.get("cluster_name"),
                                "action": reg_result.get("action"),
                                "message": f"Cluster '{reg_result.get('cluster_name')}' auto-registered",
                            }
                            db.commit()
                        elif reg_result and not reg_result.get("success"):
                            logger.warning(
                                "Cluster auto-registration failed for module %s: %s",
                                module.id,
                                reg_result.get("error"),
                            )
                    except Exception as e:
                        logger.warning(f"Failed to auto-register cluster from module outputs: {e}")
                        # Don't fail the apply if registration fails - it's a convenience feature
                else:
                    task.status = "failed"
                    task.error = "Apply failed"
                    set_locked_module_fields(
                        db, module, lock,
                        status="apply_failed",
                        deployment_error=apply_logs[-2000:] if len(apply_logs) > 2000 else apply_logs,
                    )

                task.completed_at = datetime.now(UTC)
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()

                update_project_counts(db, project.id)
                db.commit()
                create_deployment_record(db, task, module, "apply", all_logs)

                # Update stack status if module belongs to a stack
                _update_stack_status_if_needed(module, db)

            return {
                "success": apply_code == 0,
                "exit_code": apply_code,
                "outputs": outputs,
                "used_saved_plan": used_saved_plan
            }

        except SoftTimeLimitExceeded:
            # S14-034: Handle Celery soft time limit gracefully
            logger.error(f"Apply task hit soft time limit for module {module_id}")
            try:
                task_obj = locals().get('task')
                module_obj = locals().get('module')
                if task_obj:
                    task_obj.status = "failed"
                    task_obj.error = "Task exceeded time limit (soft timeout). The operation may still be running in AWS."
                    task_obj.completed_at = datetime.now(UTC)
                    if task_obj.started_at:
                        task_obj.duration_seconds = (task_obj.completed_at - task_obj.started_at).total_seconds()
                if module_obj:
                    module_obj.status = "apply_failed"
                db.commit()
                if task_obj:
                    _publish_task_completion(task_obj)
            except Exception as cleanup_err:
                logger.warning(f"Failed to update task status on apply soft timeout: {cleanup_err}")
            raise

        except ModuleLockError as e:
            logger.error(f"Apply task failed - module locked: {e}")
            # Lock acquisition failed — another live worker holds the lock.
            # Do NOT touch module.status; let the live holder own state
            # transitions. Just fail this task cleanly.
            if task:
                task.status = "failed"
                task.error = f"Module locked: {str(e)}"
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()
            raise

        except ModuleLockLostError as e:
            logger.warning(f"Apply task aborted - lock lost mid-operation: {e}")
            if task:
                task.status = "failed"
                task.error = f"Lock lost: {str(e)}"
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()
            raise

        except Exception as e:
            logger.exception(f"Apply task failed: {e}")
            _mark_task_failed(task, e, db,
                              module=locals().get('module'),
                              failed_status="apply_failed")
            raise


@celery_app.task(bind=True, base=CallbackTask, name="tasks.opentofu_tasks.run_opentofu_destroy")
def run_opentofu_destroy(self, task_db_id: int, module_id: int, keep_workspace: bool = False):
    """
    Run tofu destroy for a module using persistent workspace.

    Uses persistent workspace. Skips init if already initialized.

    Args:
        task_db_id: Database ID of the Task record
        module_id: ProjectModule ID to destroy
        keep_workspace: Keep workspace for debugging (always true with persistent workspaces)

    Returns:
        dict: Task result
    """
    work_dir = None

    with get_db_context() as db:
        try:
            task = fetch_task_or_raise(db, task_db_id)

            # S14-009: Validate module BEFORE setting task to in_progress
            module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
            if not module:
                task.status = "failed"
                task.error = f"Module {module_id} not found"
                task.completed_at = datetime.now(UTC)
                db.commit()
                return {"success": False, "error": task.error}

            task.status = "in_progress"
            task.started_at = datetime.now(UTC)
            db.commit()

            _notify_task_started(task)

            # CP-008: Skip destroy for modules that were never applied (no infrastructure to destroy)
            if module.status in ["not_initialized", "initialized", "planned", "init_failed", "plan_failed"]:
                logger.info(f"Skipping destroy for module {module_id} - no deployed infrastructure (status: {module.status})")
                task.status = "completed"
                task.completed_at = datetime.now(UTC)
                task.error = f"Skipped: no deployed infrastructure (status: {module.status})"
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                # Mark module as destroyed since there's nothing to destroy
                module.status = "destroyed"
                db.commit()
                _publish_task_completion(task)
                # S14-039: Update stack status if module belongs to a stack
                _update_stack_status_if_needed(module, db)
                # D-001 Phase 3: fire destroy trigger even for skipped modules
                _trigger_next_destroy_module(module, db, task.id)
                return {"status": "skipped", "module_id": module.id, "reason": f"No infrastructure (status was {module.status})"}

            project = module.project
            engine = OpenTofuRuntime(db)

            # Import WorkspaceManager for persistent workspace handling
            from services.workspace_manager import WorkspaceManager
            workspace = WorkspaceManager(db)

            env = get_cloud_credentials_env(project, db)

            # Acquire workspace lock to prevent concurrent operations (CQ-009)
            # Destroy uses longer timeout since cleanup can take time
            with module_lock(db, module.id, task_id=task_db_id) as lock:
                # Prepare persistent workspace (lenient about missing dep outputs)
                work_dir = engine.prepare_persistent_workspace(module, operation="destroy")
                task.working_directory = work_dir
                db.commit()

                all_logs = ""
                # Stream tofu output into task.logs during the run (#195).
                streamer = TofuLogStreamer(task, db)

                # Check if workspace is initialized
                needs_reinit, reinit_reason = workspace.needs_reinit(module)
                if workspace.is_initialized(module) and not needs_reinit:
                    all_logs += f"[{_ts()}] === INIT SKIPPED (using cached providers) ===\n"
                    task.command = "tofu destroy"
                else:
                    if reinit_reason:
                        all_logs += f"[{_ts()}] === INIT REQUIRED: {reinit_reason} ===\n"
                    task.command = "tofu init && tofu destroy"
                    # Init
                    all_logs += f"[{_ts()}] --- INIT ---\n"
                    init_code, init_logs = engine.run_init(
                        work_dir, env, on_output=streamer.begin(all_logs),
                    )
                    all_logs += init_logs
                    if init_code != 0:
                        task.exit_code = init_code
                        task.logs = all_logs
                        task.status = "failed"
                        task.error = "Init failed"
                        task.completed_at = datetime.now(UTC)
                        set_locked_module_fields(db, module, lock, status="destroy_failed")
                        _publish_task_completion(task)  # S14-007: Notify UI on early-return failure
                        return {"success": False, "exit_code": init_code}

                    workspace.mark_initialized(module)
                    workspace.save_init_version(module)
                    all_logs += "\n"

                # Destroy (with retry logic for dependency violations and module-specific timeout)
                all_logs += f"[{_ts()}] --- DESTROY ---\n"
                timeout = engine.get_destroy_timeout(module)
                destroy_code, destroy_logs = engine.run_destroy_with_retry(
                    work_dir, env, module=module, timeout=timeout,
                    on_output=streamer.begin(all_logs),
                )
                all_logs += destroy_logs

                # Check if destroy failed due to namespace finalizer issues
                # This is common with F5 BNK and other CRD-heavy deployments
                if destroy_code != 0 and _is_namespace_finalizer_issue(destroy_logs):
                    logger.info(f"Detected namespace finalizer issue for module {module_id}, attempting cleanup")
                    cleanup_result = _cleanup_stuck_finalizers(db, project, module, destroy_logs)

                    if cleanup_result["cleaned"]:
                        all_logs += f"\n[{_ts()}] --- FINALIZER CLEANUP ---\n{cleanup_result['message']}\n"

                        # Retry destroy after cleanup
                        logger.info("Retrying destroy after finalizer cleanup")
                        all_logs += f"\n[{_ts()}] --- DESTROY RETRY ---\n"
                        retry_code, retry_logs = engine.run_destroy_with_retry(
                            work_dir, env, module=module, timeout=300,  # Shorter timeout for retry
                            on_output=streamer.begin(all_logs),
                        )
                        all_logs += f"{retry_logs}\n"

                        if retry_code == 0:
                            destroy_code = 0
                            destroy_logs = retry_logs
                            all_logs += "Destroy succeeded after finalizer cleanup!\n"

                            # Notify user of successful recovery
                            _create_notification(
                                db,
                                notification_type="success",
                                title="Destroy Succeeded After Auto-Cleanup",
                                message=f"Module '{module.library_module.name}' was successfully destroyed after "
                                       f"automatic finalizer cleanup. No manual intervention required.",
                                resource_type="module",
                                resource_id=module.id
                            )
                        else:
                            # Notify user that retry also failed
                            _create_notification(
                                db,
                                notification_type="error",
                                title="Destroy Failed After Auto-Cleanup",
                                message=f"Module '{module.library_module.name}' destroy still failed after finalizer cleanup. "
                                       f"Manual cleanup may be required. Check task logs for details.",
                                resource_type="module",
                                resource_id=module.id
                            )

                task.exit_code = destroy_code
                task.logs = all_logs

                if destroy_code == 0:
                    task.status = "completed"
                    set_locked_module_fields(
                        db, module, lock,
                        status="destroyed",
                        outputs=None,
                        last_deployed_at=None,
                    )
                    cleanup_durable_infra_private_key(module.project_id, module.id)

                    # PERF-011/PERF-017: Invalidate all state caches after destroy
                    from services.state_decryption_service import invalidate_state_cache
                    invalidate_state_cache(module_id, project.id)

                    # Auto-unregister EKS cluster from Kubernetes page
                    if module.library_module.name == "eks":
                        try:
                            from services.eks_service import unregister_eks_cluster
                            unregistered = unregister_eks_cluster(db, module)
                            if unregistered:
                                logger.info("Auto-unregistered EKS cluster from Kubernetes page")
                        except Exception as e:
                            logger.warning(f"Failed to auto-unregister EKS cluster: {e}")
                            # Don't fail the destroy if unregistration fails
                else:
                    task.status = "failed"
                    task.error = "Destroy failed"
                    set_locked_module_fields(
                        db, module, lock,
                        status="destroy_failed",
                        deployment_error=destroy_logs[-2000:] if len(destroy_logs) > 2000 else destroy_logs,
                    )

                task.completed_at = datetime.now(UTC)
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()

                update_project_counts(db, project.id)
                db.commit()
                create_deployment_record(db, task, module, "destroy", all_logs)

                # Update stack status if module belongs to a stack
                _update_stack_status_if_needed(module, db)

                # D-001 Phase 3: event-chain destroy trigger hook
                # Fire after module reaches destroyed/destroy_failed so the next
                # dependency in the reverse-DAG chain is queued (or finalize runs).
                _trigger_next_destroy_module(module, db, task.id)

                # If destroy succeeded, clear plan metadata (infrastructure no longer exists)
                if destroy_code == 0:
                    workspace.clear_plan(module)
                    # Reset init timestamp since infrastructure is destroyed
                    module.last_init_at = None
                    db.commit()

            return {"success": destroy_code == 0, "exit_code": destroy_code}

        except SoftTimeLimitExceeded:
            # S15-015: Handle Celery soft time limit gracefully (mirrors apply task pattern at S14-034)
            logger.error(f"Destroy task hit soft time limit for module {module_id}")
            _exc_module = None
            try:
                task_obj = locals().get('task')
                module_obj = locals().get('module')
                if task_obj:
                    task_obj.status = "failed"
                    task_obj.error = "Task exceeded time limit (soft timeout). The destroy operation may still be running in AWS."
                    task_obj.completed_at = datetime.now(UTC)
                    if task_obj.started_at:
                        task_obj.duration_seconds = (task_obj.completed_at - task_obj.started_at).total_seconds()
                if module_obj:
                    module_obj.status = "destroy_failed"
                    _exc_module = module_obj
                db.commit()
                if task_obj:
                    _publish_task_completion(task_obj)
            except Exception as cleanup_err:
                logger.warning(f"Failed to update task status on destroy soft timeout: {cleanup_err}")
            # C1: fire event-chain trigger so terminal detection can run even on timeout
            if _exc_module is not None:
                try:
                    _trigger_next_destroy_module(_exc_module, db, task.id)
                except Exception as trigger_err:
                    logger.warning(f"_trigger_next_destroy_module failed after soft timeout: {trigger_err}")
            raise

        except ModuleLockError as e:
            logger.error(f"Destroy task failed - module locked: {e}")
            # Lock acquisition failed — another live worker holds the lock.
            # Do NOT touch module.status; let the live holder own state
            # transitions. Just fail this task cleanly.
            # C1: set destroy_failed so this module is terminal and trigger detection.
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
                logger.warning(f"_trigger_next_destroy_module failed after ModuleLockError: {trigger_err}")
            raise

        except ModuleLockLostError as e:
            logger.warning(f"Destroy task aborted - lock lost mid-operation: {e}")
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
                logger.warning(f"_trigger_next_destroy_module failed after ModuleLockLostError: {trigger_err}")
            raise

        except Exception as e:
            logger.exception(f"Destroy task failed: {e}")
            _exc_module = locals().get('module')
            _mark_task_failed(task, e, db,
                              module=_exc_module,
                              failed_status="destroy_failed")
            # C1: fire event-chain trigger so terminal detection runs even on exception
            try:
                if _exc_module is not None:
                    db.refresh(_exc_module)
                    _trigger_next_destroy_module(_exc_module, db, task.id)
            except Exception as trigger_err:
                logger.warning(f"_trigger_next_destroy_module failed after generic exception: {trigger_err}")
            raise


@celery_app.task(
    bind=True,
    base=CallbackTask,
    name="tasks.opentofu_tasks.run_opentofu_refresh",
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    max_retries=2,
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
)
def run_opentofu_refresh(self, task_id: int, module_id: int):
    """
    Recover state file by running tofu apply with existing infrastructure.

    IMPORTANT: 'tofu refresh' only updates tracked resources. To recover a lost/corrupted
    state file, we need to run a full 'tofu apply' which will:
    1. Detect existing infrastructure
    2. Update state to match reality
    3. Avoid making changes if infrastructure matches desired state

    This is the correct way to recover state when infrastructure exists but state is lost.
    """
    task = None
    work_dir = None

    with get_db_context() as db:
        try:
            task = fetch_task_or_raise(db, task_id)
            module = db.query(ProjectModule).options(
                joinedload(ProjectModule.project),
                joinedload(ProjectModule.library_module)
            ).filter(ProjectModule.id == module_id).first()

            if not module:
                raise Exception(f"Module {module_id} not found")

            project = module.project

            task.status = "in_progress"
            task.started_at = datetime.now(UTC)
            # S15-024: Guard against None logs
            if task.logs is None:
                task.logs = ""
            task.logs += f"[{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}] Starting state recovery...\n"
            task.logs += f"[{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}] Note: Running 'tofu apply' to reconcile state with existing AWS infrastructure\n"
            db.commit()
            _notify_task_started(task)

            # Initialize OpenTofu runtime
            engine = OpenTofuRuntime(db)

            # Check if module can be executed
            deps_ok, missing_deps = check_dependencies(db, module)
            if not deps_ok:
                raise Exception(f"Dependencies not met: {missing_deps}")

            env = get_cloud_credentials_env(module.project, db)

            # S14-013: Acquire workspace lock to prevent concurrent operations
            with module_lock(db, module.id, task_id=task_id) as lock:
                # Prepare workspace
                task.logs += f"[{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}] Preparing workspace...\n"
                db.commit()

                work_dir = engine.prepare_persistent_workspace(module, operation="apply")
                task.logs += f"[{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}] Workspace: {work_dir}\n"
                db.commit()

                # Run init
                task.logs += f"\n[{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}] Running tofu init...\n"
                task.logs += "=" * 80 + "\n"
                db.commit()

                init_exit_code, init_logs = engine.run_init(work_dir, env)
                task.logs += init_logs + "\n"
                db.commit()

                if init_exit_code != 0:
                    task.status = "failed"
                    task.error = f"Init failed with exit code {init_exit_code}"
                    task.logs += f"\n[{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}] Init failed with exit code {init_exit_code}\n"
                    task.completed_at = datetime.now(UTC)
                    if task.started_at:
                        task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                    set_locked_module_fields(db, module, lock, status="init_failed")
                    _publish_task_completion(task)
                    return

                # Run plan to see what would change
                task.logs += f"\n[{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}] Running tofu plan to check infrastructure state...\n"
                task.logs += "=" * 80 + "\n"
                db.commit()

                plan_exit_code, plan_logs = engine.run_plan(work_dir, env)
                task.logs += plan_logs + "\n"
                db.commit()

                if plan_exit_code != 0:
                    task.status = "failed"
                    task.error = f"Plan failed with exit code {plan_exit_code}"
                    task.logs += f"\n[{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}] Plan failed with exit code {plan_exit_code}\n"
                    task.logs += "\nState recovery failed. Check if AWS infrastructure still exists.\n"
                    task.completed_at = datetime.now(UTC)
                    if task.started_at:
                        task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                    db.commit()
                    _publish_task_completion(task)
                    return

                # Run apply to reconcile state
                task.logs += f"\n[{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}] Running tofu apply to reconcile state...\n"
                task.logs += "=" * 80 + "\n"
                db.commit()

                apply_exit_code, apply_logs, outputs_data = engine.run_apply(work_dir, env, module=module)
                task.logs += apply_logs + "\n"
                db.commit()

                if apply_exit_code != 0:
                    task.status = "failed"
                    task.error = f"Apply failed with exit code {apply_exit_code}"
                    task.logs += f"\n[{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}] Apply failed with exit code {apply_exit_code}\n"
                    task.logs += "\nState recovery failed.\n"
                    task.completed_at = datetime.now(UTC)
                    if task.started_at:
                        task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                    set_locked_module_fields(db, module, lock, status="apply_failed")
                    _publish_task_completion(task)
                    return

                # Update module with recovered outputs
                final_outputs = None
                if outputs_data:
                    module.outputs = outputs_data
                    normalize_module_outputs_in_place(module)
                    final_outputs = module.outputs
                    db.expire(module, ["outputs"])
                    task.logs += f"\n[{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}] Captured {len(outputs_data)} outputs\n"
                    task.logs += f"Outputs: {list(outputs_data.keys())}\n"

                # Success!
                task.status = "completed"
                task.logs += f"\n[{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}] State successfully recovered!\n"
                task.logs += f"State file location: /app/state/{module.project_id}/{module.id}/terraform.tfstate\n"
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                success_fields: dict = {
                    "status": "applied",
                    "last_deployed_at": datetime.now(UTC),
                }
                if final_outputs is not None:
                    success_fields["outputs"] = final_outputs
                set_locked_module_fields(db, module, lock, **success_fields)

                # S15-024: Create deployment record and publish completion
                update_project_counts(db, project.id)
                db.commit()
                _publish_task_completion(task)

            # S15-024: Guard against outputs_data being undefined if apply wasn't reached
            output_count = len(outputs_data) if 'outputs_data' in dir() and outputs_data else 0
            logger.info(f"State recovery completed for module {module_id} - captured {output_count} outputs")

        except Exception as e:
            logger.error(f"State recovery failed for module {module_id}: {str(e)}")
            if task:
                task.status = "failed"
                task.error = str(e)
                if task.logs is None:
                    task.logs = ""
                task.logs += f"\n[{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}] ERROR: {str(e)}\n"
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()
                _publish_task_completion(task)
            raise

        finally:
            # Persistent workspace is intentionally retained for recovery parity
            pass
