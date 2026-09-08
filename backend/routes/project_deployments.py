"""
Project Module Deployment History & State Management routes.

Endpoints for:
  - Deployment logs
  - Deployment history (per-module and per-project)
  - State file info and resource listing
  - State recovery

Split from monolithic project_modules.py (2,487 lines).
"""

import json
import logging
import os
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session, joinedload

from core.errors import BadRequestError, NotFoundError, handle_route_errors
from database import get_db
from models import ModuleLibrary, Project, ProjectModule, User
from models.enums import ModuleStatus, TaskStatus
from routes.auth import get_username_from_request, require_module_owner, require_viewer
from schemas.projects import DeploymentOutputResponse
from services.infrastructure_access_service import normalize_module_outputs_in_place

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/project-modules", tags=["project-deployments"])





# ============================================================================
# Deployment Logs
# ============================================================================

@router.get("/{module_id}/logs", dependencies=[Depends(require_viewer)])
def get_deployment_logs(
    module_id: int,
    limit: int = 1000,
    level: str | None = None,
    db: Session = Depends(get_db)
):
    """
    Get historical deployment logs for a module.

    Entries are returned NEWEST FIRST regardless of source. Sources, in
    preference order:
      - "deployment_log": DeploymentLog rows (written by the retry path)
      - "task": the module's newest Task.logs -- where every engine actually
        streams its step output; `task_id` names it (GET /api/tasks/{task_id})
      - "none": nothing recorded yet; `hint` says where output will appear

    Args:
        module_id: Module ID
        limit: Maximum number of logs to return (1-10000, default 1000);
            on the "task" source this is a tail of the most recent lines
        level: Filter by log level (all, info, error, warning, success);
            best-effort on the "task" source (matched on engine markers)
    """
    from models import DeploymentLog

    # Validate limit
    if limit < 1 or limit > 10000:
        raise BadRequestError("Limit must be between 1 and 10000")

    # Validate level
    valid_levels = ["all", "info", "error", "warning", "success", None]
    if level and level not in valid_levels:
        raise BadRequestError(f"Invalid level. Must be one of: {', '.join([level_name for level_name in valid_levels if level_name])}")

    # Verify module exists
    module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
    if not module:
        raise NotFoundError("module", module_id)

    # Build query
    query = db.query(DeploymentLog).filter(
        DeploymentLog.module_id == module_id
    )

    # Filter by level if specified
    if level and level != "all":
        query = query.filter(DeploymentLog.level == level)

    # Apply ordering and limit
    query = query.order_by(DeploymentLog.timestamp.desc()).limit(limit)

    # Execute query
    logs = query.all()

    if logs:
        logger.info(f"Retrieved {len(logs)} logs for module {module_id} (level={level}, limit={limit})")
        return {
            "module_id": module_id,
            "module_name": module.library_module.name,
            "total_logs": len(logs),
            "source": "deployment_log",
            "task_id": None,
            "logs": [
                {
                    "timestamp": log.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "level": log.level,
                    "message": log.message
                }
                for log in logs
            ]
        }

    # No DeploymentLog rows. That is the NORMAL case, not an empty history:
    # every engine (opentofu, container, ansible, ssh, cli, kubernetes, tmos)
    # streams its step output into Task.logs, and only the retry path ever
    # writes DeploymentLog. Returning 200 {"logs": []} here read as "this
    # step produced no output" and sent operators to `docker logs` on the
    # host (#154). Fall back to the module's most recent task and say so.
    from models import Task as TaskModel

    task = (
        db.query(TaskModel)
        .filter(TaskModel.module_id == module_id)
        .order_by(TaskModel.id.desc())
        .first()
    )
    if task is None or not task.logs:
        return {
            "module_id": module_id,
            "module_name": module.library_module.name,
            "total_logs": 0,
            "source": "none",
            "task_id": task.id if task else None,
            "logs": [],
            "hint": (
                "No output recorded for this module yet. Step output is stored per "
                "task: GET /api/tasks?module_id=<id> lists them, GET /api/tasks/{id} "
                "returns the full log."
            ),
        }

    lines = task.logs.splitlines()
    if level and level != "all":
        # Task logs are free text; apply a best-effort level filter on the
        # engine's own markers so the parameter keeps meaning on this path.
        markers = {
            "error": ("ERROR", "✗", "error:", "--- ERROR ---"),
            "warning": ("WARN", "WARNING", "⚠"),
            "success": ("✓", "SUCCESS", "Complete"),
            "info": (),
        }
        wanted = markers.get(level, ())
        if wanted:
            lines = [ln for ln in lines if any(m in ln for m in wanted)]
    # Tail, then NEWEST FIRST -- the same order the DeploymentLog branch has
    # always returned (timestamp.desc()). A caller treating logs[0] as "most
    # recent" must get the same answer from either source.
    lines = lines[-limit:]
    lines.reverse()

    logger.info(
        f"No DeploymentLog rows for module {module_id}; served {len(lines)} lines "
        f"from task {task.id}"
    )
    return {
        "module_id": module_id,
        "module_name": module.library_module.name,
        "total_logs": len(lines),
        "source": "task",
        "task_id": task.id,
        "logs": [{"timestamp": None, "level": "info", "message": ln} for ln in lines],
    }


# ============================================================================
# Deployment History
# ============================================================================

@router.get("/{module_id}/deployments", dependencies=[Depends(require_viewer)])
def get_deployment_history(
    module_id: int,
    action: str | None = Query(None, description="Filter by action: plan, apply, destroy"),
    status: str | None = Query(None, description="Filter by status: success, failed, cancelled"),
    limit: int = Query(50, ge=1, le=200, description="Max deployments to return"),
    db: Session = Depends(get_db)
):
    """
    Get deployment history for a module.

    Returns a paginated list of deployments with action type, status,
    timestamps, duration, resource change counts, and execution details.
    """
    from models import Deployment

    # Check if module exists
    module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
    if not module:
        raise NotFoundError("module", module_id)

    # Build query
    query = db.query(Deployment).filter(Deployment.module_id == module_id)

    # Apply filters
    if action and action != "all":
        query = query.filter(Deployment.action == action)

    if status and status != "all":
        query = query.filter(Deployment.status == status)

    # Apply ordering and limit
    query = query.order_by(Deployment.started_at.desc()).limit(limit)

    # Execute query
    deployments = query.all()

    logger.info(f"Retrieved {len(deployments)} deployments for module {module_id} (action={action}, status={status})")

    # Format response
    return {
        "module_id": module_id,
        "module_name": module.library_module.name if module.library_module else "Unknown",
        "total_deployments": len(deployments),
        "deployments": [
            {
                "id": dep.id,
                # The handle for this run's output: GET /api/tasks/{task_id}.
                # `id` is the deployment row, NOT the task -- an easy thing to
                # mistake for the log handle (#154). Older rows predate the
                # meta_data backfill and report null.
                "task_id": (dep.meta_data or {}).get("task_id"),
                "action": dep.action,
                "status": dep.status,
                "triggered_by": dep.triggered_by,
                "started_at": dep.started_at.isoformat() if dep.started_at else None,
                "completed_at": dep.completed_at.isoformat() if dep.completed_at else None,
                "duration_seconds": dep.duration_seconds,
                "resources_to_add": dep.resources_to_add or 0,
                "resources_to_change": dep.resources_to_change or 0,
                "resources_to_destroy": dep.resources_to_destroy or 0,
                "exit_code": dep.exit_code,
                "environment": dep.environment,
                "git_commit": dep.git_commit,
                "git_branch": dep.git_branch,
            }
            for dep in deployments
        ]
    }


@router.get(
    "/{module_id}/deployments/{deployment_id}/output",
    response_model=DeploymentOutputResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get deployment output")
def get_deployment_output(
    module_id: int,
    deployment_id: int,
    max_bytes: int = Query(
        2_000_000,
        ge=1024,
        le=20_000_000,
        description="Cap on returned stdout size; the TAIL is kept when it exceeds this",
    ),
    db: Session = Depends(get_db),
):
    """
    Get the captured output of a single deployment run.

    The deployment list endpoint reports status, timing and resource counts but
    carries no log, so a failed module could only be diagnosed by opening the UI.
    This returns the run's stdout/stderr so a headless or CI-driven deploy can
    find out what actually failed (issue #526).

    Output is kept from the END when it exceeds ``max_bytes`` — a failure message
    is at the tail of the log, not the head.
    """
    from models import Deployment

    module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
    if not module:
        raise NotFoundError("module", module_id)

    deployment = (
        db.query(Deployment)
        .filter(Deployment.id == deployment_id, Deployment.module_id == module_id)
        .first()
    )
    if not deployment:
        # Scoped to the module on purpose: a deployment id that exists but belongs
        # to another module must not be readable through this module's path.
        raise NotFoundError("deployment", deployment_id)

    stdout = deployment.stdout or ""
    stderr = deployment.stderr or ""

    # Truncate on BYTES, not characters. len() on a str counts characters, and
    # artifact logs are full of non-ASCII (✓/✗/box-drawing), so a character cap
    # could return up to 4× the advertised size to a scripted caller.
    truncated = False
    encoded = stdout.encode("utf-8")
    if len(encoded) > max_bytes:
        # Decode with errors="ignore" to drop a partial code point at the cut.
        stdout = encoded[-max_bytes:].decode("utf-8", errors="ignore")
        truncated = True

    logger.info(
        f"Retrieved output for deployment {deployment_id} (module {module_id}, "
        f"{len(stdout)} chars, truncated={truncated})"
    )

    return DeploymentOutputResponse(
        module_id=module_id,
        deployment_id=deployment.id,
        action=deployment.action,
        status=deployment.status,
        exit_code=deployment.exit_code,
        started_at=deployment.started_at.isoformat() if deployment.started_at else None,
        completed_at=deployment.completed_at.isoformat() if deployment.completed_at else None,
        duration_seconds=deployment.duration_seconds,
        stdout=stdout,
        stderr=stderr,
        truncated=truncated,
    )


@router.get("/project/{project_id}/deployments", dependencies=[Depends(require_viewer)])
def get_project_deployment_history(
    project_id: int,
    action: str | None = Query(None, description="Filter by action"),
    status: str | None = Query(None, description="Filter by status"),
    limit: int = Query(100, ge=1, le=500, description="Max deployments to return"),
    db: Session = Depends(get_db)
):
    """
    Get deployment history for all modules in a project.

    Useful for project-level deployment timeline visualization.
    """
    from models import Deployment

    # Check if project exists
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError("project", project_id)

    # Build query - join with ProjectModule to filter by project_id
    query = db.query(Deployment).join(Deployment.module).filter(
        ProjectModule.project_id == project_id
    ).options(
        joinedload(Deployment.module).joinedload(ProjectModule.library_module).load_only(
            ModuleLibrary.id, ModuleLibrary.name,
        )
    )

    # Apply filters
    if action and action != "all":
        query = query.filter(Deployment.action == action)

    if status and status != "all":
        query = query.filter(Deployment.status == status)

    # Apply ordering and limit
    query = query.order_by(Deployment.started_at.desc()).limit(limit)

    # Execute query
    deployments = query.all()

    logger.info(f"Retrieved {len(deployments)} deployments for project {project_id}")

    # Format response
    return {
        "project_id": project_id,
        "project_name": project.name,
        "total_deployments": len(deployments),
        "deployments": [
            {
                "id": dep.id,
                "module_id": dep.module_id,
                "module_name": dep.module.library_module.name if dep.module and dep.module.library_module else "Unknown",
                "module_path": dep.module.path_in_project if dep.module else "",
                "action": dep.action,
                "status": dep.status,
                "triggered_by": dep.triggered_by,
                "started_at": dep.started_at.isoformat() if dep.started_at else None,
                "completed_at": dep.completed_at.isoformat() if dep.completed_at else None,
                "duration_seconds": dep.duration_seconds,
                "resources_to_add": dep.resources_to_add or 0,
                "resources_to_change": dep.resources_to_change or 0,
                "resources_to_destroy": dep.resources_to_destroy or 0,
                "exit_code": dep.exit_code,
                "environment": dep.environment,
            }
            for dep in deployments
        ]
    }


# ============================================================================
# State Management
# ============================================================================

@router.get("/{module_id}/state-info", dependencies=[Depends(require_viewer)])
def get_module_state_info(module_id: int, db: Session = Depends(get_db)):
    """
    Get state file metadata for a module (existence, lineage, serial number).
    Handles both encrypted and unencrypted state files.
    """
    from services.state_decryption_service import get_state_metadata, get_state_resources_list

    module = db.query(ProjectModule).options(
        joinedload(ProjectModule.project).load_only(Project.id, Project.name),
        joinedload(ProjectModule.library_module).load_only(ModuleLibrary.id, ModuleLibrary.name),
    ).filter(ProjectModule.id == module_id).first()

    if not module:
        raise NotFoundError("module", module_id)

    # Check if state file exists
    state_file_path = f"/app/state/{module.project_id}/{module.id}/terraform.tfstate"
    state_exists = os.path.exists(state_file_path)

    result = {
        "module_id": module.id,
        "module_name": module.library_module.name if module.library_module else "Unknown",
        "state_exists": state_exists,
        "state_location": f"/app/state/{module.project_id}/{module.id}/",
        "status": module.status,
        "last_deployed_at": module.last_deployed_at.isoformat() if module.last_deployed_at else None,
    }

    # If state file exists, read metadata using shared service
    if state_exists:
        try:
            metadata = get_state_metadata(state_file_path)

            if "error" in metadata:
                result["error"] = metadata["error"]
            else:
                result.update({
                    "lineage": metadata.get("lineage", "unknown"),
                    "serial": metadata.get("serial", 0),
                    "is_encrypted": metadata.get("is_encrypted", False),
                    "terraform_version": metadata.get("terraform_version", "unknown"),
                })

                if metadata.get("is_encrypted"):
                    result["encryption_version"] = metadata.get("encryption_version", "unknown")
                    success, resources = get_state_resources_list(
                        state_file_path, module.project_id, module.id, db
                    )
                    if success:
                        result["resources_count"] = len(resources)
                    else:
                        result["resources_count"] = None
                    result["outputs_count"] = len(module.outputs) if module.outputs else 0
                else:
                    with open(state_file_path) as f:
                        state_data = json.load(f)
                        result["resources_count"] = len(state_data.get("resources", []))
                        # Keep response shape consistent with the encrypted branch
                        # (FEAT-0145 / ERR-0023): always include outputs_count.
                        result["outputs_count"] = len(state_data.get("outputs", {}))

                # File modification time
                result["state_modified_at"] = datetime.fromtimestamp(
                    metadata.get("last_modified", 0)
                ).isoformat()

        except Exception as e:
            logger.error(f"Failed to read state metadata for module {module_id}: {e}")
            result["error"] = "Failed to read state file metadata"

    return result


@router.get("/{module_id}/state-resources", dependencies=[Depends(require_viewer)])
def get_module_state_resources(module_id: int, db: Session = Depends(get_db)):
    """
    Get list of resources managed by a module's state.
    Handles both encrypted and unencrypted state files.
    """
    from services.state_decryption_service import get_state_resources_list, is_state_encrypted

    module = db.query(ProjectModule).options(
        joinedload(ProjectModule.project).load_only(Project.id, Project.name),
        joinedload(ProjectModule.library_module).load_only(ModuleLibrary.id, ModuleLibrary.name),
    ).filter(ProjectModule.id == module_id).first()

    if not module:
        raise NotFoundError("module", module_id)

    # Check if state file exists
    state_file_path = f"/app/state/{module.project_id}/{module.id}/terraform.tfstate"
    if not os.path.exists(state_file_path):
        return {
            "module_id": module.id,
            "resources": [],
            "resources_count": 0,
            "error": "No state file found"
        }

    # Use shared service to get resources (handles encryption automatically)
    success, resources = get_state_resources_list(
        state_file_path, module.project_id, module_id, db
    )

    if success:
        return {
            "module_id": module.id,
            "resources": resources,
            "resources_count": len(resources),
            "is_encrypted": is_state_encrypted(state_file_path)
        }
    else:
        return {
            "module_id": module.id,
            "resources": [],
            "resources_count": 0,
            "error": resources[0] if resources else "Unknown error",
            "is_encrypted": is_state_encrypted(state_file_path)
        }


@router.post("/{module_id}/recover-state")
def recover_state_file(module_id: int, request: Request, user: User = Depends(require_module_owner), db: Session = Depends(get_db)):
    """
    Recover state file for a module that has deployed infrastructure but lost state.
    Uses outputs from database to rebuild state via tofu refresh.
    """
    from models import Task as TaskModel
    from tasks.opentofu_tasks import run_opentofu_refresh

    module = db.query(ProjectModule).options(
        joinedload(ProjectModule.project).load_only(Project.id, Project.name),
        joinedload(ProjectModule.library_module).load_only(ModuleLibrary.id, ModuleLibrary.name),
    ).filter(ProjectModule.id == module_id).first()

    if not module:
        raise NotFoundError("module", module_id)

    # Verify module is in a state that needs recovery
    if module.status != ModuleStatus.APPLIED:
        raise BadRequestError(f"Module status is '{module.status}'. Only 'applied' modules can recover state.")

    # Check if state file already exists
    state_file_path = f"/app/state/{module.project_id}/{module.id}/terraform.tfstate"
    if os.path.exists(state_file_path):
        raise BadRequestError("State file already exists. No recovery needed.")

    # Check if outputs exist (proof infrastructure was deployed)
    if not module.outputs or len(module.outputs) == 0:
        raise BadRequestError("No outputs found in database. Cannot verify infrastructure exists.")

    logger.info(f"Initiating state recovery for module {module_id}")

    # Create task record
    task = TaskModel(
        project_id=module.project_id,
        module_id=module_id,
        task_type="refresh",
        status=TaskStatus.QUEUED,
        triggered_by=get_username_from_request(request),
        logs="State recovery initiated...\n"
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    # Queue refresh task
    celery_task = run_opentofu_refresh.delay(task.id, module_id)

    logger.info(f"State recovery task queued: task_id={task.id}, celery_task_id={celery_task.id}")

    return {
        "success": True,
        "message": "State recovery started",
        "task_id": task.id,
        "celery_task_id": celery_task.id
    }


@router.post("/{module_id}/repair-access")
def repair_infrastructure_access(
    module_id: int,
    user: User = Depends(require_module_owner),
    db: Session = Depends(get_db),
):
    """Repair/rematerialize infrastructure access metadata without full redeploy."""
    module = db.query(ProjectModule).options(
        joinedload(ProjectModule.library_module).load_only(ModuleLibrary.id, ModuleLibrary.name),
    ).filter(ProjectModule.id == module_id).first()

    if not module:
        raise NotFoundError("module", module_id)

    if module.status != ModuleStatus.APPLIED:
        raise BadRequestError("Only applied modules can repair infrastructure access metadata.")

    normalized = normalize_module_outputs_in_place(module)
    db.commit()

    return {
        "success": True,
        "module_id": module.id,
        "module_name": module.library_module.name if module.library_module else f"module-{module.id}",
        "changed": normalized.changed,
        "infrastructure_private_key_available": normalized.key_available,
        "infrastructure_private_key_path": normalized.outputs.get("infrastructure_private_key_path"),
        "infrastructure_access_status": normalized.outputs.get("infrastructure_access_status"),
        "infrastructure_access_message": normalized.outputs.get("infrastructure_access_message"),
    }
