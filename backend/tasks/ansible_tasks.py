"""Ansible Engine Celery tasks for manifest-backed deployment packs."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from celery_app import celery_app
from database import SessionLocal, get_db_context
from models import ProjectModule
from models import Task as TaskModel
from services.credentials_service import get_cloud_credentials_env
from services.execution.ansible_engine import AnsibleEngine
from services.execution.engine_interface import ModuleContext
from services.execution.variable_assembler import build_variables
from services.execution.variable_assembler import (
    can_execute as check_dependencies,
)
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
    _notify_task_started,
    _publish_task_completion,
    _trigger_next_destroy_module,
    _update_stack_status_if_needed,
    create_deployment_record,
)

logger = logging.getLogger(__name__)


def _build_context(
    db,
    module: ProjectModule,
    *,
    operation: str = "apply",
    include_dependency_wiring: bool = True,
) -> ModuleContext:
    project = module.project
    module_path = module.library_module.path if module.library_module else module.path_in_project
    category = module.library_module.category if module.library_module else "infra"
    if include_dependency_wiring:
        variables = build_variables(db, module, operation=operation)
    else:
        variables = module.variable_overrides or module.variables or {}
    credentials_env = get_cloud_credentials_env(project, db)

    return ModuleContext(
        module_id=module.id,
        project_id=project.id,
        path=module_path,
        category=category,
        variables=variables,
        credentials_env=credentials_env,
    )


def _engine() -> AnsibleEngine:
    return AnsibleEngine(SessionLocal)


def _format_logs(header: str, output_lines: list[str], error_message: str | None = None, suggestion: str | None = None) -> str:
    logs = [header, "---"]
    logs.extend(output_lines)
    if error_message:
        logs.extend(["", "--- ERROR ---", error_message])
    if suggestion:
        logs.extend(["", "--- SUGGESTION ---", suggestion])
    return "\n".join(logs) + "\n"


def _plan_failed(result) -> bool:
    """Detect ansible plan execution failures from PlanResult semantics."""
    details = (result.details or "").strip().lower()
    return details.startswith("ansible check-mode failed") or details.startswith("plan error:")


def _handle_lock_error(task, db, exc: Exception, message: str) -> None:
    """Common cleanup for ModuleLockError/ModuleLockLostError handlers — only
    touches the task row, never module.status (the new lock holder owns it).
    """
    logger.warning(message)
    if task:
        task.status = "failed"
        task.error = message
        task.completed_at = datetime.now(UTC)
        if task.started_at:
            task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
        db.commit()


@celery_app.task(bind=True, base=CallbackTask, name="tasks.ansible_tasks.run_ansible_init")
def run_ansible_init(self, task_db_id: int, module_id: int, auto_apply: bool = False, **kwargs):
    task = None
    with get_db_context() as db:
        try:
            task = fetch_task_or_raise(db, task_db_id)

            task.status = "in_progress"
            task.started_at = datetime.now(UTC)
            task.command = "ansible-engine: init"
            db.commit()
            _notify_task_started(task)

            module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
            if not module:
                raise ValueError(f"Module {module_id} not found")

            with module_lock(db, module.id, task_id=task_db_id) as lock:
                ctx = _build_context(db, module, operation="init", include_dependency_wiring=False)
                output_lines: list[str] = []
                result = _engine().init(ctx, on_output=output_lines.append)

                task.exit_code = 0 if result.success else 1
                task.logs = _format_logs(
                    header=f"=== ANSIBLE ENGINE INIT ===\nModule: {ctx.path}",
                    output_lines=output_lines,
                    error_message=result.error_message,
                    suggestion=result.error_suggestion,
                )

                if result.success:
                    task.status = "completed"
                    module_fields = {"status": "initialized", "last_init_at": datetime.now(UTC)}
                else:
                    task.status = "failed"
                    task.error = result.error_message or "Ansible init failed"
                    module_fields = {"status": "init_failed", "deployment_error": task.error}

                task.completed_at = datetime.now(UTC)
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                set_locked_module_fields(db, module, lock, **module_fields)

                update_project_counts(db, module.project_id)
                db.commit()
                create_deployment_record(db, task, module, "init", task.logs)

                if not result.success and module.stack_instance_id:
                    _update_stack_status_if_needed(module, db)

                if result.success and auto_apply:
                    can_exec, missing = check_dependencies(db, module)
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

                        from services.execution.task_dispatch import dispatch_apply

                        celery_result = dispatch_apply(apply_task.id, module)
                        apply_task.celery_task_id = celery_result.id
                        set_locked_module_fields(db, module, lock, status="applying")
                    else:
                        logger.info("Ansible auto-apply skipped for module %s: %s", module.id, missing)

            return {"success": result.success, "exit_code": task.exit_code}

        except ModuleLockError as e:
            _handle_lock_error(task, db, e, f"Module locked: {str(e)}")
            raise
        except ModuleLockLostError as e:
            _handle_lock_error(task, db, e, f"Lock lost: {str(e)}")
            raise
        except Exception as exc:
            logger.exception("Ansible init task failed: %s", exc)
            if task:
                task.status = "failed"
                task.error = str(exc)
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()
            raise


@celery_app.task(bind=True, base=CallbackTask, name="tasks.ansible_tasks.run_ansible_plan")
def run_ansible_plan(self, task_db_id: int, module_id: int, **kwargs):
    task = None
    with get_db_context() as db:
        try:
            task = fetch_task_or_raise(db, task_db_id)

            module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
            if not module:
                task.status = "failed"
                task.error = f"Module {module_id} not found"
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()
                _publish_task_completion(task)
                return {"success": False, "error": task.error}

            task.status = "in_progress"
            task.started_at = datetime.now(UTC)
            task.command = "ansible-engine: plan"
            db.commit()
            _notify_task_started(task)

            can_exec, missing = check_dependencies(db, module)
            if not can_exec:
                raise ValueError(f"Dependencies not satisfied: {', '.join(missing)}")

            with module_lock(db, module.id, task_id=task_db_id) as lock:
                ctx = _build_context(db, module, operation="plan")
                output_lines: list[str] = []
                result = _engine().plan(ctx, on_output=output_lines.append)

                success = not _plan_failed(result)
                task.exit_code = 0 if success else 1
                task.logs = _format_logs(
                    header=f"=== ANSIBLE ENGINE PLAN ===\nModule: {ctx.path}",
                    output_lines=output_lines + ([result.details] if result.details else []),
                    error_message=None if success else result.details,
                )

                if success:
                    task.status = "completed"
                    module_fields = {
                        "status": "planned",
                        "plan_output": result.details,
                        "deployment_error": None,
                    }
                else:
                    task.status = "failed"
                    task.error = "Ansible plan failed"
                    module_fields = {
                        "status": "plan_failed",
                        "deployment_error": (result.details or "Ansible plan failed")[-2000:],
                    }

                task.completed_at = datetime.now(UTC)
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                set_locked_module_fields(db, module, lock, **module_fields)

                update_project_counts(db, module.project_id)
                db.commit()
                create_deployment_record(db, task, module, "plan", task.logs)

                if module.stack_instance_id:
                    _update_stack_status_if_needed(module, db)

            return {"success": success, "exit_code": task.exit_code, "has_changes": result.has_changes}

        except ModuleLockError as e:
            _handle_lock_error(task, db, e, f"Module locked: {str(e)}")
            raise
        except ModuleLockLostError as e:
            _handle_lock_error(task, db, e, f"Lock lost: {str(e)}")
            raise
        except Exception as exc:
            logger.exception("Ansible plan task failed: %s", exc)
            if task:
                task.status = "failed"
                task.error = str(exc)
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()
            raise


@celery_app.task(bind=True, base=CallbackTask, name="tasks.ansible_tasks.run_ansible_apply")
def run_ansible_apply(self, task_db_id: int, module_id: int, **kwargs):
    task = None
    with get_db_context() as db:
        try:
            task = fetch_task_or_raise(db, task_db_id)

            module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
            if not module:
                task.status = "failed"
                task.error = f"Module {module_id} not found"
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()
                _publish_task_completion(task)
                return {"success": False, "error": task.error}

            task.status = "in_progress"
            task.started_at = datetime.now(UTC)
            task.command = "ansible-engine: apply"
            db.commit()
            _notify_task_started(task)

            can_exec, missing = check_dependencies(db, module)
            if not can_exec:
                raise ValueError(f"Dependencies not satisfied: {', '.join(missing)}")

            with module_lock(db, module.id, task_id=task_db_id) as lock:
                ctx = _build_context(db, module, operation="apply")
                output_lines: list[str] = []
                result = _engine().apply(ctx, on_output=output_lines.append)

                task.exit_code = 0 if result.success else 1
                task.logs = _format_logs(
                    header=f"=== ANSIBLE ENGINE APPLY ===\nModule: {ctx.path}",
                    output_lines=output_lines,
                    error_message=result.error_message,
                    suggestion=result.error_suggestion,
                )

                if result.success:
                    task.status = "completed"
                    # Stage outputs on the ORM so normalize can mutate them,
                    # then capture + expire so the fence-protected UPDATE is
                    # the sole writer (avoids ORM flushing an unprotected dup).
                    module.outputs = result.outputs or {}
                    normalize_module_outputs_in_place(module)
                    final_outputs = module.outputs
                    db.expire(module, ["outputs"])
                    module_fields = {
                        "status": "applied",
                        "outputs": final_outputs,
                        "last_deployed_at": datetime.now(UTC),
                        "deployment_error": None,
                    }
                else:
                    task.status = "failed"
                    task.error = result.error_message or "Ansible apply failed"
                    module_fields = {
                        "status": "apply_failed",
                        "deployment_error": task.error[-2000:],
                    }

                task.completed_at = datetime.now(UTC)
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                set_locked_module_fields(db, module, lock, **module_fields)

                update_project_counts(db, module.project_id)
                db.commit()
                create_deployment_record(db, task, module, "apply", task.logs)
                _update_stack_status_if_needed(module, db)

            return {
                "success": result.success,
                "exit_code": task.exit_code,
                "outputs": result.outputs or {},
            }

        except ModuleLockError as e:
            _handle_lock_error(task, db, e, f"Module locked: {str(e)}")
            raise
        except ModuleLockLostError as e:
            _handle_lock_error(task, db, e, f"Lock lost: {str(e)}")
            raise
        except Exception as exc:
            logger.exception("Ansible apply task failed: %s", exc)
            if task:
                task.status = "failed"
                task.error = str(exc)
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()
            raise


@celery_app.task(bind=True, base=CallbackTask, name="tasks.ansible_tasks.run_ansible_destroy")
def run_ansible_destroy(self, task_db_id: int, module_id: int, **kwargs):
    task = None
    with get_db_context() as db:
        try:
            task = fetch_task_or_raise(db, task_db_id)

            module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
            if not module:
                task.status = "failed"
                task.error = f"Module {module_id} not found"
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()
                _publish_task_completion(task)
                return {"success": False, "error": task.error}

            task.status = "in_progress"
            task.started_at = datetime.now(UTC)
            task.command = "ansible-engine: destroy"
            db.commit()
            _notify_task_started(task)

            with module_lock(db, module.id, task_id=task_db_id) as lock:
                ctx = _build_context(db, module, operation="destroy")
                output_lines: list[str] = []
                result = _engine().destroy(ctx, on_output=output_lines.append)

                task.exit_code = 0 if result.success else 1
                task.logs = _format_logs(
                    header=f"=== ANSIBLE ENGINE DESTROY ===\nModule: {ctx.path}",
                    output_lines=output_lines,
                    error_message=result.error_message,
                    suggestion=result.error_suggestion,
                )

                if result.success:
                    task.status = "completed"
                    module_fields = {
                        "status": "destroyed",
                        "outputs": None,
                        "last_deployed_at": None,
                        "last_init_at": None,
                        "plan_output": None,
                        "plan_serial": 0,
                        "vars_hash": None,
                        "deployment_error": None,
                    }
                    cleanup_durable_infra_private_key(module.project_id, module.id)
                else:
                    task.status = "failed"
                    task.error = result.error_message or "Ansible destroy failed"
                    module_fields = {
                        "status": "destroy_failed",
                        "deployment_error": task.error[-2000:],
                    }

                task.completed_at = datetime.now(UTC)
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                set_locked_module_fields(db, module, lock, **module_fields)

                update_project_counts(db, module.project_id)
                db.commit()
                create_deployment_record(db, task, module, "destroy", task.logs)
                _update_stack_status_if_needed(module, db)
                # D-001 Phase 3: event-chain destroy trigger hook
                _trigger_next_destroy_module(module, db, task.id)

            return {"success": result.success, "exit_code": task.exit_code}

        except ModuleLockError as e:
            # C1: set destroy_failed and fire trigger so terminal detection runs.
            _exc_module = locals().get("module")
            _handle_lock_error(task, db, e, f"Module locked: {str(e)}")
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
                logger.warning("_trigger_next_destroy_module failed after Ansible ModuleLockError: %s", trigger_err)
            raise
        except ModuleLockLostError as e:
            # C1: set destroy_failed and fire trigger so terminal detection runs.
            _exc_module = locals().get("module")
            _handle_lock_error(task, db, e, f"Lock lost: {str(e)}")
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
                logger.warning("_trigger_next_destroy_module failed after Ansible ModuleLockLostError: %s", trigger_err)
            raise
        except Exception as exc:
            logger.exception("Ansible destroy task failed: %s", exc)
            _exc_module = locals().get("module")
            if _exc_module:
                _exc_module.status = "destroy_failed"
            if task:
                task.status = "failed"
                task.error = str(exc)
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                try:
                    db.commit()
                except Exception:
                    db.rollback()
                _publish_task_completion(task)
            # C1: fire event-chain trigger so terminal detection runs even on exception
            try:
                if _exc_module is not None:
                    db.refresh(_exc_module)
                    _trigger_next_destroy_module(_exc_module, db, task.id)
            except Exception as trigger_err:
                logger.warning("_trigger_next_destroy_module failed after Ansible generic exception: %s", trigger_err)
            raise
