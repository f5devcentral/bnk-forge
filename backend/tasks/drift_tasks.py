"""
Drift detection Celery tasks.

Handles scheduled drift detection checks:
- run_drift_check: Run OpenTofu plan to detect drift (v2: uses OpenTofuRuntime)
- schedule_drift_checks: Periodic task to schedule drift checks
"""
import logging
import os
import re
import shutil
from datetime import UTC, datetime, timedelta

from croniter import croniter

from celery_app import celery_app
from database import get_db_context
from models import DriftCheck, DriftSettings, ProjectModule
from models import Task as TaskModel
from services.credentials_service import get_cloud_credentials_env

# IMP-003: Use canonical CallbackTask with S14-022 terminal state guard
from tasks._tofu_helpers import CallbackTask  # noqa: F401 — used as base= in @celery_app.task

logger = logging.getLogger(__name__)


def parse_drift_from_plan(exit_code: int, stdout: str, stderr: str) -> dict:
    """
    Analyze terraform plan output to detect drift.

    Returns:
        dict: {
            "drift_detected": bool,
            "resource_changes": {"add": int, "change": int, "destroy": int},
            "changed_resources": [{"address": str, "action": str}],
            "summary": str
        }
    """
    result = {
        "drift_detected": False,
        "resource_changes": {"add": 0, "change": 0, "destroy": 0},
        "changed_resources": [],
        "summary": "No drift detected"
    }

    # Exit code 2 = changes detected (drift!)
    if exit_code != 2:
        if exit_code == 0:
            result["summary"] = "No drift detected - infrastructure matches code"
        else:
            result["summary"] = f"Plan failed with exit code {exit_code}"
        return result

    result["drift_detected"] = True

    # Parse plan output for "Plan: X to add, Y to change, Z to destroy"
    plan_pattern = r"Plan:\s+(\d+)\s+to\s+add,\s+(\d+)\s+to\s+change,\s+(\d+)\s+to\s+destroy"
    match = re.search(plan_pattern, stdout)
    if match:
        result["resource_changes"]["add"] = int(match.group(1))
        result["resource_changes"]["change"] = int(match.group(2))
        result["resource_changes"]["destroy"] = int(match.group(3))

        total_changes = sum(result["resource_changes"].values())
        result["summary"] = f"Drift detected: {total_changes} resource(s) will be modified ({match.group(1)} add, {match.group(2)} change, {match.group(3)} destroy)"

    # Parse individual resource changes (basic extraction)
    # Look for lines like: "  # aws_instance.example will be created"
    resource_pattern = r"#\s+([\w\.]+)\s+will\s+be\s+(created|updated|destroyed|replaced)"
    for match in re.finditer(resource_pattern, stdout):
        resource_address = match.group(1)
        action = match.group(2)
        result["changed_resources"].append({
            "address": resource_address,
            "action": action
        })

    return result


def _is_k8s_engine_module(module) -> bool:
    """Check if a module uses the K8s engine (kubernetes-direct or operator)."""
    lib_module = getattr(module, "library_module", None)
    if lib_module is None:
        return False

    execution_engine = getattr(lib_module, "execution_engine", None)
    if isinstance(execution_engine, str):
        return execution_engine.strip().lower() in {"kubernetes-direct", "operator"}

    return False


def _run_k8s_drift_check(drift_check, module, project, db) -> dict:
    """
    Run drift check for a K8s-engine module.

    Runs K8s drift check for catalog-backed modules.
    """
    from services.execution.engine_router import EngineRouter
    from services.execution.variable_assembler import assemble_variables
    from services.k8s_drift_service import check_k8s_module_drift

    # Resolve kubeconfig
    kubeconfig_path = EngineRouter.resolve_kubeconfig(project, db)
    if not kubeconfig_path:
        raise ValueError(
            f"No kubeconfig available for project '{project.name}'. "
            f"Register a Kubernetes cluster first."
        )

    # Assemble variables (same way the K8s engine does at deploy time)
    variables = assemble_variables(module, project, db)

    # Run K8s drift check
    module_path = module.path_in_project
    logger.info(f"Running K8s drift check for {project.name}/{module_path}")
    drift_info = check_k8s_module_drift(
        kubeconfig_path,
        module_path,
        variables,
        lib_module=getattr(module, "library_module", None),
    )

    return drift_info


def _run_opentofu_drift_check(self_task, drift_check, module, project, db) -> dict:
    """
    Run drift check for an OpenTofu module via `tofu plan -detailed-exitcode`.
    """
    from services.execution.opentofu_runtime import OpenTofuRuntime
    engine = OpenTofuRuntime(db)

    # Prepare workspace
    logger.info(f"Preparing workspace for drift check on module {module.id}")
    workspace = engine.prepare_workspace(module, keep_workspace=False)

    try:
        # Get cloud credentials
        env = get_cloud_credentials_env(project, db)

        # Run init
        logger.info(f"Running tofu init for drift check in {workspace}")
        init_exit_code, init_output = engine.run_init(workspace, env)
        if init_exit_code != 0:
            raise RuntimeError(f"Init failed with exit code {init_exit_code}: {init_output}")

        # Run plan with detailed exit code
        logger.info(f"Running tofu plan (detailed) for drift check in {workspace}")
        exit_code, output = engine.run_plan_detailed(workspace, env)

        # Parse drift
        drift_info = parse_drift_from_plan(exit_code, output, "")

        # Check for plan failure (not drift, but an error)
        if exit_code not in [0, 2]:
            drift_info["plan_failed"] = True
            drift_info["error"] = f"Plan failed with exit code {exit_code}"

        return drift_info

    finally:
        # Cleanup workspace
        if workspace and os.path.exists(workspace):
            shutil.rmtree(workspace, ignore_errors=True)
            logger.info(f"Cleaned up workspace: {workspace}")


def _fire_drift_alert(db, drift_info: dict, module, project):
    """Fire alert via alert service when drift is detected."""
    try:
        from services.alert_service import fire_alert
        changes = drift_info.get("resource_changes", {})
        change_parts = []
        if changes.get("add"):
            change_parts.append(f"+{changes['add']} add")
        if changes.get("change"):
            change_parts.append(f"~{changes['change']} change")
        if changes.get("destroy"):
            change_parts.append(f"-{changes['destroy']} destroy")
        change_summary = ", ".join(change_parts) if change_parts else "changes detected"

        module_name = module.library_module.name if module.library_module else module.path_in_project
        fire_alert(
            db=db,
            event_type="drift_detected",
            severity="warning",
            title=f"Drift detected: {module_name}",
            message=f"Infrastructure drift detected in project '{project.name}', module '{module_name}': {change_summary}",
            project_id=project.id,
            extra={
                "module_id": module.id,
                "module_path": module.path_in_project,
                "module_name": module_name,
                "resource_changes": changes,
                "changed_resources": drift_info.get("changed_resources", [])[:10],
            },
        )
        logger.info(f"Drift alert fired for {project.name}/{module_name}")
    except Exception as e:
        logger.error(f"Failed to fire drift alert: {e}")


@celery_app.task(
    bind=True,
    base=CallbackTask,
    name="tasks.drift_tasks.run_drift_check",
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    max_retries=2,
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
)
def run_drift_check(self, drift_check_id: int, module_id: int):
    """
    Run a drift detection check for a module.

    Dispatches to K8s-engine drift (manifest/helm comparison) or OpenTofu
    drift (tofu plan -detailed-exitcode) based on module type.

    Args:
        drift_check_id: Database ID of the DriftCheck record
        module_id: ProjectModule ID to check for drift

    Returns:
        dict: Drift check result
    """
    drift_check = None
    task = None

    with get_db_context() as db:
        try:
            # Get drift check record
            drift_check = db.query(DriftCheck).filter(DriftCheck.id == drift_check_id).first()
            if not drift_check:
                raise ValueError(f"DriftCheck {drift_check_id} not found")

            # Update drift check status
            drift_check.status = "checking"
            drift_check.last_check_at = datetime.now(UTC)
            db.commit()

            # Get module and project
            module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
            if not module:
                raise ValueError(f"Module {module_id} not found")

            project = module.project
            is_k8s = _is_k8s_engine_module(module)
            engine_label = "k8s" if is_k8s else "opentofu"

            # Create task record
            task = TaskModel(
                celery_task_id=self.request.id,
                task_type="drift_check",
                status="in_progress",
                project_id=project.id,
                module_id=module.id,
                command=f"drift_check ({engine_label})",
                triggered_by="drift_scheduler",
                started_at=datetime.now(UTC)
            )
            db.add(task)
            db.commit()

            drift_check.task_id = task.id
            db.commit()

            # Dispatch to correct engine
            if is_k8s:
                drift_info = _run_k8s_drift_check(drift_check, module, project, db)
            else:
                drift_info = _run_opentofu_drift_check(self, drift_check, module, project, db)

            # Update task with results
            task.logs = drift_info.get("summary", "")
            task.completed_at = datetime.now(UTC)
            if task.started_at:
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()

            # Update drift check record
            drift_check.drift_detected = drift_info["drift_detected"]
            drift_check.drift_summary = drift_info["summary"]
            drift_check.drift_details = drift_info
            drift_check.status = "completed"

            # Handle plan failure (OpenTofu specific)
            if drift_info.get("plan_failed"):
                drift_check.status = "failed"
                drift_check.error_message = drift_info.get("error", "Plan failed")
                task.error = drift_check.error_message
                task.status = "failed"
            else:
                task.status = "completed"

            # Calculate next check time
            settings = db.query(DriftSettings).filter(DriftSettings.project_id == project.id).first()
            if settings and settings.enabled:
                if settings.schedule_type == "cron":
                    cron = croniter(settings.schedule_value, datetime.now(UTC))
                    drift_check.next_check_at = cron.get_next(datetime)
                elif settings.schedule_type == "interval":
                    interval_seconds = int(settings.schedule_value)
                    drift_check.next_check_at = datetime.now(UTC) + timedelta(seconds=interval_seconds)

            db.commit()

            # Fire alert if drift detected
            if drift_info["drift_detected"]:
                _fire_drift_alert(db, drift_info, module, project)
                db.commit()

            return {
                "success": drift_check.status == "completed",
                "drift_detected": drift_check.drift_detected,
                "drift_summary": drift_check.drift_summary,
                "drift_details": drift_info,
                "engine": engine_label,
            }

        except Exception as e:
            logger.error(f"Error in run_drift_check: {e}", exc_info=True)
            if drift_check:
                drift_check.status = "failed"
                drift_check.error_message = str(e)
            if task:
                task.error = str(e)
                task.status = "failed"
            if drift_check or task:
                db.commit()
            raise


@celery_app.task(name="tasks.drift_tasks.schedule_drift_checks")
def schedule_drift_checks():
    """
    Periodic task that schedules drift checks for enabled projects.

    This task runs every 15 minutes via Celery Beat and:
    1. Queries all projects with drift detection enabled
    2. Checks if it's time for a drift check (based on schedule)
    3. Creates drift_check records and queues run_drift_check tasks
    """
    with get_db_context() as db:
        try:
            logger.info("Running scheduled drift check scheduler")

            # Get all projects with drift detection enabled
            settings_list = db.query(DriftSettings).filter(DriftSettings.enabled).all()

            if not settings_list:
                logger.info("No projects with drift detection enabled")
                return {"scheduled": 0}

            scheduled_count = 0
            now = datetime.now(UTC)

            for settings in settings_list:
                project = settings.project
                if not project:
                    continue

                logger.info(f"Checking drift schedule for project {project.name}")

                # Get modules to check
                if settings.check_all_modules:
                    modules = db.query(ProjectModule).filter(
                        ProjectModule.project_id == project.id,
                        ProjectModule.status.in_(["initialized", "planned", "applied"])
                    ).all()
                else:
                    if not settings.module_ids:
                        continue
                    modules = db.query(ProjectModule).filter(
                        ProjectModule.id.in_(settings.module_ids),
                        ProjectModule.status.in_(["initialized", "planned", "applied"])
                    ).all()

                for module in modules:
                    # Check if drift check is due
                    last_check = db.query(DriftCheck).filter(
                        DriftCheck.project_id == project.id,
                        DriftCheck.module_id == module.id
                    ).order_by(DriftCheck.created_at.desc()).first()

                    should_check = False

                    if not last_check:
                        # No previous check, schedule first check
                        should_check = True
                    elif last_check.next_check_at and now >= last_check.next_check_at:
                        # Next check time has passed
                        should_check = True
                    elif not last_check.next_check_at:
                        # No next check time set, calculate based on schedule
                        if settings.schedule_type == "cron":
                            try:
                                cron = croniter(settings.schedule_value, last_check.last_check_at or now)
                                next_check = cron.get_next(datetime)
                                should_check = now >= next_check
                            except Exception as e:
                                logger.error(f"Invalid cron expression: {settings.schedule_value}: {e}")
                                continue
                        elif settings.schedule_type == "interval":
                            interval_seconds = int(settings.schedule_value)
                            next_check = (last_check.last_check_at or now) + timedelta(seconds=interval_seconds)
                            should_check = now >= next_check

                    if should_check:
                        # Create drift check record
                        drift_check = DriftCheck(
                            project_id=project.id,
                            module_id=module.id,
                            schedule_enabled=True,
                            schedule_cron=settings.schedule_value if settings.schedule_type == "cron" else None,
                            status="scheduled"
                        )
                        db.add(drift_check)
                        db.commit()

                        # Queue drift check task
                        try:
                            run_drift_check.delay(drift_check.id, module.id)
                            scheduled_count += 1
                            logger.info(f"Scheduled drift check for {project.name}/{module.library_module.name}")
                        except Exception as e:
                            logger.error(f"Failed to queue drift check: {e}")
                            drift_check.status = "failed"
                            drift_check.error_message = f"Failed to queue task: {str(e)}"
                            db.commit()

            logger.info(f"Scheduled {scheduled_count} drift checks")
            return {"scheduled": scheduled_count}

        except Exception as e:
            logger.error(f"Error in schedule_drift_checks: {e}", exc_info=True)
            raise
