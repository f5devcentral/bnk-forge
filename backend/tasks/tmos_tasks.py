"""TMOS Engine Celery tasks — AS3 declaration apply/plan/destroy for classic BIG-IP.

Follows the same pattern as ssh_tasks.py / kubernetes_tasks.py:
  - Load ProjectModule + F5BIGIPDevice from DB
  - Build ModuleContext with decrypted credentials (never serialized/logged)
  - Call TmosEngine methods (init/plan/apply/destroy)
  - Update task/module status in DB
  - Publish completion events for WebSocket streaming

Extended time limits: AS3 applies can take up to 30 min to poll.
  soft_time_limit=1800 / time_limit=3600 on run_tmos_apply.

Declaration rendering: tmos_tasks calls build_variables_for_tmos() then
render_as3() to produce the as3_declaration before handing context to the engine.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from celery_app import celery_app
from database import SessionLocal, get_db_context
from models import ProjectModule
from models import Task as TaskModel
from services.execution.engine_interface import ModuleContext
from services.execution.tmos_engine import TmosEngine
from services.execution.variable_assembler import build_variables_for_tmos, render_as3
from services.execution.variable_assembler import can_execute as check_dependencies
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
    _update_stack_status_if_needed,
    create_deployment_record,
)

logger = logging.getLogger(__name__)


# ── Context builder ──────────────────────────────────────────────────


def _build_tmos_context(db, module: ProjectModule) -> ModuleContext:
    """Build ModuleContext with TMOS fields from F5BIGIPDevice.

    Resolves device + credential, decrypts credential in-memory.
    Credentials live only in memory for the duration of task execution —
    never serialized to logs, DB, or UI.

    Declaration rendering is also done here via build_variables_for_tmos +
    render_as3 so the engine receives a ready-to-POST declaration.
    """
    from models.f5_credential import F5Credential
    from models.f5_device import F5BIGIPDevice

    # Resolve device id from variable overrides
    device_id = (module.variable_overrides or {}).get("f5_bigip_device_id")
    if not device_id:
        device_id = (module.variables or {}).get("f5_bigip_device_id")
    if not device_id:
        raise ValueError(
            f"Module {module.id} has no f5_bigip_device_id in variables"
        )

    device = db.query(F5BIGIPDevice).filter(F5BIGIPDevice.id == int(device_id)).first()
    if not device:
        raise ValueError(f"F5BIGIPDevice {device_id} not found")

    if not device.mgmt_credential_id:
        raise ValueError(
            f"F5BIGIPDevice {device.id} ({device.name}) has no credential set"
        )

    cred = db.query(F5Credential).filter(
        F5Credential.id == device.mgmt_credential_id
    ).first()
    if not cred:
        raise ValueError(
            f"F5Credential {device.mgmt_credential_id} not found for device {device.id}"
        )

    # Build variables using the TMOS-specific assembler
    variables = build_variables_for_tmos(db, module, device=device)

    # Resolve AS3 tenant from variables (module author specifies it)
    as3_tenant = variables.get("as3_tenant") or (module.variable_overrides or {}).get("as3_tenant")

    # Render the AS3 declaration forge-side from module template + variables
    as3_declaration: dict | None = None
    template = variables.get("as3_template") or (module.variable_overrides or {}).get("as3_template")
    if template:
        try:
            as3_declaration = render_as3(template, variables)
        except Exception as exc:
            raise ValueError(f"AS3 declaration rendering failed for module {module.id}: {exc}") from exc

    module_path = module.library_module.path if module.library_module else module.path_in_project
    category = module.library_module.category if module.library_module else "tmos"

    return ModuleContext(
        module_id=module.id,
        project_id=module.project_id,
        path=module_path,
        category=category,
        variables=variables,
        # TMOS fields — credential is an in-memory object, never serialized
        tmos_host=device.mgmt_host,
        tmos_port=device.mgmt_port or 443,
        tmos_verify_https=device.verify_https_cert,
        tmos_credential=cred,  # F5Credential ORM object (has encrypted fields; engine decrypts)
        as3_tenant=as3_tenant,
        as3_declaration=as3_declaration,
    )


# ── Engine factory ───────────────────────────────────────────────────


def _engine() -> TmosEngine:
    return TmosEngine(SessionLocal)


# ── Log formatting ───────────────────────────────────────────────────


def _ts() -> str:
    return datetime.now(UTC).strftime("%H:%M:%S")


def _format_logs(
    header: str,
    output_lines: list[str],
    error_message: str | None = None,
    suggestion: str | None = None,
) -> str:
    logs = [header, "---"]
    logs.extend(output_lines)
    if error_message:
        logs.extend(["", "--- ERROR ---", error_message])
    if suggestion:
        logs.extend(["", "--- SUGGESTION ---", suggestion])
    return "\n".join(logs) + "\n"


# ── Celery tasks ─────────────────────────────────────────────────────


@celery_app.task(bind=True, base=CallbackTask, name="tasks.tmos_tasks.run_tmos_init")
def run_tmos_init(self, task_db_id: int, module_id: int, auto_apply: bool = False, **kwargs):
    """Init TMOS module — authenticate to BIG-IP and verify AS3 version."""
    task = None
    with get_db_context() as db:
        try:
            task = fetch_task_or_raise(db, task_db_id)

            task.status = "in_progress"
            task.started_at = datetime.now(UTC)
            task.command = "tmos-engine: init"
            db.commit()
            _notify_task_started(task)

            module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
            if not module:
                raise ValueError(f"Module {module_id} not found")

            with module_lock(db, module.id, task_id=task_db_id) as lock:
                ctx = _build_tmos_context(db, module)
                output_lines: list[str] = []

                def _on_output(line: str) -> None:
                    stamped = f"[{_ts()}] {line}"
                    output_lines.append(stamped)
                    try:
                        from services.websocket_service import publish_module_log
                        publish_module_log(module_id=module.id, line=stamped)
                    except Exception:
                        pass

                result = _engine().init(ctx, on_output=_on_output)

                task.exit_code = 0 if result.success else 1
                task.logs = _format_logs(
                    header=f"=== TMOS ENGINE INIT ===\nModule: {ctx.path}",
                    output_lines=output_lines,
                    error_message=result.error_message,
                    suggestion=result.error_suggestion,
                )

                if result.success:
                    task.status = "completed"
                    module_fields = {"status": "initialized", "last_init_at": datetime.now(UTC)}
                else:
                    task.status = "failed"
                    task.error = result.error_message or "TMOS init failed"
                    module_fields = {"status": "init_failed", "deployment_error": task.error}

                task.completed_at = datetime.now(UTC)
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                set_locked_module_fields(db, module, lock, **module_fields)

                update_project_counts(db, module.project_id)
                db.commit()
                create_deployment_record(db, task, module, "init", task.logs)

                if module.stack_instance_id:
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
                        logger.info("TMOS auto-apply skipped for module %s: %s", module.id, missing)

            return {"success": result.success, "exit_code": task.exit_code}

        except ModuleLockError as e:
            logger.error("TMOS init task failed - module locked: %s", e)
            _mark_task_failed(task, f"Module locked: {e}", db, update_stack=False)
            mod = locals().get("module")
            if mod and mod.stack_instance_id:
                _update_stack_status_if_needed(mod, db)
            raise

        except ModuleLockLostError as e:
            logger.warning("TMOS init aborted - lock lost mid-operation: %s", e)
            _mark_task_failed(task, f"Lock lost: {e}", db, update_stack=False)
            mod = locals().get("module")
            if mod and mod.stack_instance_id:
                _update_stack_status_if_needed(mod, db)
            raise

        except Exception as exc:
            logger.exception("TMOS init task failed: %s", exc)
            mod = locals().get("module")
            _mark_task_failed(task, str(exc), db, module=mod, failed_status="init_failed")
            raise


@celery_app.task(bind=True, base=CallbackTask, name="tasks.tmos_tasks.run_tmos_plan")
def run_tmos_plan(self, task_db_id: int, module_id: int, **kwargs):
    """Plan TMOS module — AS3 dry-run to show what would change."""
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
            task.command = "tmos-engine: plan"
            db.commit()
            _notify_task_started(task)

            ctx = _build_tmos_context(db, module)
            output_lines: list[str] = []

            def _on_output(line: str) -> None:
                stamped = f"[{_ts()}] {line}"
                output_lines.append(stamped)
                try:
                    from services.websocket_service import publish_module_log
                    publish_module_log(module_id=module.id, line=stamped)
                except Exception:
                    pass

            result = _engine().plan(ctx, on_output=_on_output)

            task.exit_code = 0
            task.logs = _format_logs(
                header=f"=== TMOS ENGINE PLAN ===\nModule: {ctx.path}",
                output_lines=output_lines,
            )
            task.status = "completed"
            task.completed_at = datetime.now(UTC)
            if task.started_at:
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
            db.commit()
            create_deployment_record(db, task, module, "plan", task.logs)

            return {
                "success": True,
                "has_changes": result.has_changes,
                "adds": result.adds,
                "changes": result.changes,
                "destroys": result.destroys,
            }

        except Exception as exc:
            logger.exception("TMOS plan task failed: %s", exc)
            mod = locals().get("module")
            _mark_task_failed(task, str(exc), db, module=mod)
            raise


@celery_app.task(
    bind=True,
    base=CallbackTask,
    name="tasks.tmos_tasks.run_tmos_apply",
    soft_time_limit=1800,
    time_limit=3600,
)
def run_tmos_apply(self, task_db_id: int, module_id: int, **kwargs):
    """Apply TMOS module — POST AS3 declaration and poll task to completion.

    Extended time limits because AS3 applies can be long-running (poll timeout 30 min).
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
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()
                _publish_task_completion(task)
                return {"success": False, "error": task.error}

            task.status = "in_progress"
            task.started_at = datetime.now(UTC)
            task.command = "tmos-engine: apply"
            db.commit()
            _notify_task_started(task)

            can_exec, missing = check_dependencies(db, module)
            if not can_exec:
                raise ValueError(f"Dependencies not satisfied: {', '.join(missing)}")

            with module_lock(db, module.id, task_id=task_db_id) as lock:
                ctx = _build_tmos_context(db, module)
                output_lines: list[str] = []

                def _on_output(line: str) -> None:
                    stamped = f"[{_ts()}] {line}"
                    output_lines.append(stamped)
                    try:
                        from services.websocket_service import publish_module_log
                        publish_module_log(module_id=module.id, line=stamped)
                    except Exception:
                        pass

                result = _engine().apply(ctx, on_output=_on_output)

                task.exit_code = 0 if result.success else 1
                task.logs = _format_logs(
                    header=f"=== TMOS ENGINE APPLY ===\nModule: {ctx.path}",
                    output_lines=output_lines,
                    error_message=result.error_message,
                    suggestion=result.error_suggestion,
                )

                if result.success:
                    task.status = "completed"
                    module_fields = {
                        "status": "applied",
                        "outputs": result.outputs or {},
                        "last_deployed_at": datetime.now(UTC),
                        "deployment_error": None,
                    }
                else:
                    task.status = "failed"
                    task.error = result.error_message or "TMOS apply failed"
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
            logger.error("TMOS apply task failed - module locked: %s", e)
            _mark_task_failed(task, f"Module locked: {e}", db, update_stack=False)
            mod = locals().get("module")
            if mod and mod.stack_instance_id:
                _update_stack_status_if_needed(mod, db)
            raise

        except ModuleLockLostError as e:
            logger.warning("TMOS apply aborted - lock lost mid-operation: %s", e)
            _mark_task_failed(task, f"Lock lost: {e}", db, update_stack=False)
            mod = locals().get("module")
            if mod and mod.stack_instance_id:
                _update_stack_status_if_needed(mod, db)
            raise

        except Exception as exc:
            logger.exception("TMOS apply task failed: %s", exc)
            mod = locals().get("module")
            _mark_task_failed(task, str(exc), db, module=mod, failed_status="apply_failed")
            raise


@celery_app.task(bind=True, base=CallbackTask, name="tasks.tmos_tasks.run_tmos_destroy")
def run_tmos_destroy(self, task_db_id: int, module_id: int, **kwargs):
    """Destroy TMOS module — tenant-scoped DELETE /declare/{tenant}."""
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
            task.command = "tmos-engine: destroy"
            db.commit()
            _notify_task_started(task)

            with module_lock(db, module.id, task_id=task_db_id) as lock:
                ctx = _build_tmos_context(db, module)
                output_lines: list[str] = []

                def _on_output(line: str) -> None:
                    output_lines.append(f"[{_ts()}] {line}")

                result = _engine().destroy(ctx, on_output=_on_output)

                task.exit_code = 0 if result.success else 1
                task.logs = _format_logs(
                    header=f"=== TMOS ENGINE DESTROY ===\nModule: {ctx.path}",
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
                else:
                    task.status = "failed"
                    task.error = result.error_message or "TMOS destroy failed"
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

            return {"success": result.success, "exit_code": task.exit_code}

        except ModuleLockError as e:
            logger.error("TMOS destroy task failed - module locked: %s", e)
            _mark_task_failed(task, f"Module locked: {e}", db, update_stack=False)
            mod = locals().get("module")
            if mod and mod.stack_instance_id:
                _update_stack_status_if_needed(mod, db)
            raise

        except ModuleLockLostError as e:
            logger.warning("TMOS destroy aborted - lock lost mid-operation: %s", e)
            _mark_task_failed(task, f"Lock lost: {e}", db, update_stack=False)
            mod = locals().get("module")
            if mod and mod.stack_instance_id:
                _update_stack_status_if_needed(mod, db)
            raise

        except Exception as exc:
            logger.exception("TMOS destroy task failed: %s", exc)
            mod = locals().get("module")
            _mark_task_failed(task, str(exc), db, module=mod, failed_status="destroy_failed")
            raise
