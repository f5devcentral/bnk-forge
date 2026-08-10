"""
Task Management API Routes.

Provides endpoints for:
- Viewing task history
- Getting task status
- Cancelling tasks
- Real-time task updates via WebSocket
"""
import asyncio
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session, joinedload


def _iso_z(dt: datetime | None) -> str | None:
    """Serialize a datetime as an ISO-8601 string with a trailing ``Z``.

    Task model columns are ``DateTime(timezone=True)``, so ``isoformat()``
    already emits ``+00:00``; appending ``"Z"`` directly produces
    ``"…+00:00Z"`` which JavaScript rejects as Invalid Date. Normalise to
    UTC and replace the offset with ``Z`` so browsers parse it cleanly.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.isoformat() + "Z"
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")

from celery_app import celery_app
from core.errors import BadRequestError, NotFoundError, handle_route_errors
from database import get_db
from models import ModuleLibrary, Project, ProjectModule, Task
from routes.auth import require_operator, require_viewer
from services.websocket_service import ws_manager

# Tasks that have finished and are safe to delete/archive from the ops log.
_TERMINAL_STATUSES = ("completed", "failed", "cancelled")

router = APIRouter(prefix="/api/tasks", tags=["tasks"], dependencies=[Depends(require_viewer)])
# WebSocket router — no auth dependency (WebSocket doesn't have Request headers for JWT)
ws_router = APIRouter(prefix="/api/tasks", tags=["tasks"])
logger = logging.getLogger(__name__)


@router.get("")
@handle_route_errors("list tasks")
def get_tasks(
    db: Session = Depends(get_db),
    project_id: int | None = Query(None, description="Filter by project ID"),
    module_id: int | None = Query(None, description="Filter by module ID"),
    status: str | None = Query(None, description="Filter by status"),
    task_type: str | None = Query(None, description="Filter by task type"),
    archived: bool | None = Query(
        None,
        description="Filter by archived state. Omit for the active view (non-archived only); "
        "pass true to list archived tasks.",
    ),
    limit: int = Query(50, ge=1, le=200, description="Number of tasks to return"),
    offset: int = Query(0, ge=0, description="Number of tasks to skip")
):
    """
    Get task history with optional filtering.

    Returns list of tasks ordered by creation time (newest first). Archived
    tasks are hidden by default; pass ``archived=true`` to list them.
    """
    query = db.query(Task)

    # Apply filters
    if project_id is not None:
        query = query.filter(Task.project_id == project_id)
    if module_id is not None:
        query = query.filter(Task.module_id == module_id)
    if status is not None:
        query = query.filter(Task.status == status)
    if task_type is not None:
        query = query.filter(Task.task_type == task_type)
    # Default (archived omitted) shows only the active log; an explicit value filters to it.
    if archived is None:
        query = query.filter(Task.archived.is_(False))
    else:
        query = query.filter(Task.archived.is_(archived))

    # Get total count
    total = query.count()

    # Order by creation time (newest first) and paginate
    # Eager load relationships to avoid N+1 queries
    # B3: load_only() — task list only needs project.name and library_module.name
    tasks = query.options(
        joinedload(Task.project).load_only(Project.id, Project.name),
        joinedload(Task.module).joinedload(ProjectModule.library_module).load_only(ModuleLibrary.id, ModuleLibrary.name)
    ).order_by(desc(Task.created_at)).limit(limit).offset(offset).all()

    # Format response
    tasks_data = []
    for task in tasks:
        task_dict = {
            "id": task.id,
            "celery_task_id": task.celery_task_id,
            "task_type": task.task_type,
            "status": task.status,
            "project_id": task.project_id,
            "project_name": task.project.name if task.project else None,
            "module_id": task.module_id,
            "module_name": task.module.library_module.name if task.module and task.module.library_module else None,
            "created_at": _iso_z(task.created_at),
            "started_at": _iso_z(task.started_at),
            "completed_at": _iso_z(task.completed_at),
            "duration_seconds": task.duration_seconds,
            "triggered_by": task.triggered_by,
            "command": task.command,
            "exit_code": task.exit_code,
            "error": task.error,
            "archived": task.archived,
        }
        tasks_data.append(task_dict)

    return {
        "tasks": tasks_data,
        "total": total,
        "limit": limit,
        "offset": offset
    }


@router.get("/{task_id}")
@handle_route_errors("get task detail")
def get_task(
    task_id: int,
    db: Session = Depends(get_db),
    log_tail: int | None = Query(None, ge=0, le=10000, description="Return only last N lines of logs (0 for no logs, None for all)"),
    log_max_size: int | None = Query(200000, ge=0, description="Maximum log size in bytes (default 200KB)")
):
    """
    Get detailed information about a specific task including logs.

    Performance options:
    - log_tail: Get only last N lines (useful for large logs)
    - log_max_size: Truncate logs larger than N bytes (default 200KB)
    """
    # Eager load relationships to avoid N+1 queries
    # B3: load_only() — task detail only needs project.name and library_module.name
    task = db.query(Task).options(
        joinedload(Task.project).load_only(Project.id, Project.name),
        joinedload(Task.module).joinedload(ProjectModule.library_module).load_only(ModuleLibrary.id, ModuleLibrary.name)
    ).filter(Task.id == task_id).first()
    if not task:
        raise NotFoundError("task", task_id)

    # Process logs based on parameters
    logs = task.logs
    logs_truncated = False
    logs_size = len(logs) if logs else 0

    if logs and log_tail is not None:
        if log_tail == 0:
            # Don't include logs at all
            logs = None
        else:
            # Return only last N lines
            log_lines = logs.split('\n')
            if len(log_lines) > log_tail:
                logs = '\n'.join(log_lines[-log_tail:])
                logs_truncated = True
    elif logs and log_max_size and logs_size > log_max_size:
        # Truncate logs if too large
        logs = logs[-log_max_size:] + "\n... (truncated)"
        logs_truncated = True

    return {
        "id": task.id,
        "celery_task_id": task.celery_task_id,
        "task_type": task.task_type,
        "status": task.status,
        "project_id": task.project_id,
        "project_name": task.project.name if task.project else None,
        "module_id": task.module_id,
        "module_name": task.module.library_module.name if task.module and task.module.library_module else None,
        "created_at": _iso_z(task.created_at),
        "started_at": _iso_z(task.started_at),
        "completed_at": _iso_z(task.completed_at),
        "duration_seconds": task.duration_seconds,
        "triggered_by": task.triggered_by,
        "command": task.command,
        "working_directory": task.working_directory,
        "exit_code": task.exit_code,
        "logs": logs,
        "logs_truncated": logs_truncated,
        "logs_full_size": logs_size,
        "error": task.error,
        "meta_data": task.meta_data,
        "archived": task.archived,
    }


@router.post("/{task_id}/cancel")
@handle_route_errors("cancel task")
def cancel_task(task_id: int, db: Session = Depends(get_db)):
    """
    Cancel a running task.

    Sends a revoke signal to Celery to terminate the task.
    """
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise NotFoundError("task", task_id)

    if task.status not in ["queued", "in_progress"]:
        raise BadRequestError(
            f"Cannot cancel task with status: {task.status}",
            code="INVALID_TASK_STATUS"
        )

    # Revoke the Celery task
    celery_app.control.revoke(task.celery_task_id, terminate=True, signal="SIGTERM")

    # CP-006: Release workspace lock held by the cancelled task
    if task.module_id and task.project_id:
        try:
            from services.workspace_lock_service import get_lock_service
            lock_service = get_lock_service()
            lock_service.force_release(task.project_id, task.module_id)
            logger.info(f"Released workspace lock for project={task.project_id}, module={task.module_id}")
        except Exception as lock_err:
            logger.warning(f"Failed to release workspace lock on cancel: {lock_err}")

    # Update task status
    task.status = "cancelled"
    task.completed_at = datetime.now(UTC)
    task.error = "Task cancelled by user"
    if task.started_at:
        task.duration_seconds = (task.completed_at - task.started_at).total_seconds()

    # Update module status if applicable
    if task.module_id:
        module = db.query(ProjectModule).filter(ProjectModule.id == task.module_id).first()
        if module:
            # Revert to previous stable state
            if module.status in ["applying", "destroying", "initializing"]:
                module.status = "initialized"
                module.deployment_error = "Operation cancelled by user"

    db.commit()

    # S14-024: Publish WebSocket update so UI reflects the cancellation/revert
    from services.websocket_service import publish_task_update
    publish_task_update(
        task_id=task.id,
        status=task.status,
        project_id=task.project_id,
        module_id=task.module_id,
        task_type=task.task_type,
        error=task.error
    )

    return {
        "success": True,
        "message": "Task cancelled successfully",
        "task_id": task.id
    }


@router.get("/stats/summary")
@handle_route_errors("get task stats")
def get_task_stats(
    db: Session = Depends(get_db),
    project_id: int | None = Query(None, description="Filter by project ID"),
    days: int = Query(7, ge=1, le=90, description="Number of days to include")
):
    """
    Get task statistics summary.

    Returns counts by status and type for the specified time period.

    PERFORMANCE OPTIMIZATION (ADR-019):
    Uses SQL GROUP BY instead of loading all tasks into memory.
    """
    from sqlalchemy import func

    since = datetime.now(UTC) - timedelta(days=days)

    # Base filter for all queries
    base_filter = [Task.created_at >= since]
    if project_id is not None:
        base_filter.append(Task.project_id == project_id)

    # Count by status using SQL GROUP BY (not loading all rows into memory)
    status_query = db.query(
        Task.status,
        func.count(Task.id).label('count')
    ).filter(*base_filter).group_by(Task.status)

    by_status = {status: count for status, count in status_query.all()}

    # Count by type using SQL GROUP BY
    type_query = db.query(
        Task.task_type,
        func.count(Task.id).label('count')
    ).filter(*base_filter).group_by(Task.task_type)

    by_type = {task_type: count for task_type, count in type_query.all()}

    # Calculate average duration using SQL AVG (single scalar, not loading rows)
    avg_duration = db.query(
        func.avg(Task.duration_seconds)
    ).filter(
        *base_filter,
        Task.duration_seconds.isnot(None)
    ).scalar() or 0

    # Total is sum of all status counts
    total = sum(by_status.values())

    return {
        "period_days": days,
        "total_tasks": total,
        "by_status": by_status,
        "by_type": by_type,
        "average_duration_seconds": round(float(avg_duration), 2),
        "project_id": project_id
    }


@router.delete("/cleanup")
@handle_route_errors("cleanup old tasks")
def cleanup_old_tasks(
    db: Session = Depends(get_db),
    days: int = Query(60, ge=1, le=365, description="Delete tasks older than this many days")
):
    """
    Delete old completed tasks to free up database space.

    Only deletes tasks older than specified days with status: completed, failed, or cancelled.
    """
    cutoff_date = datetime.now(UTC) - timedelta(days=days)

    # Delete old completed/failed/cancelled tasks
    deleted = db.query(Task).filter(
        Task.created_at < cutoff_date,
        Task.status.in_(["completed", "failed", "cancelled"])
    ).delete(synchronize_session=False)

    db.commit()

    return {
        "success": True,
        "deleted_count": deleted,
        "cutoff_date": cutoff_date.isoformat()
    }


class TaskIdsRequest(BaseModel):
    """Body for bulk operations log operations (#21)."""
    task_ids: list[int]


# NOTE: the `/{task_id}` DELETE route below is registered AFTER the literal
# `/cleanup` route so it doesn't shadow it (FastAPI matches in registration order).


@router.post("/{task_id}/archive", dependencies=[Depends(require_operator)])
@handle_route_errors("archive task")
def archive_task(
    task_id: int,
    db: Session = Depends(get_db),
    archived: bool = Query(True, description="Set archived state (true=archive, false=unarchive)"),
):
    """Archive (or unarchive) a single task, hiding it from the default ops-log view."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise NotFoundError("task", task_id)

    task.archived = archived
    db.commit()

    return {"success": True, "task_id": task.id, "archived": task.archived}


@router.post("/bulk-archive", dependencies=[Depends(require_operator)])
@handle_route_errors("bulk archive tasks")
def bulk_archive_tasks(
    request: TaskIdsRequest,
    db: Session = Depends(get_db),
    archived: bool = Query(True, description="Set archived state (true=archive, false=unarchive)"),
):
    """Archive (or unarchive) multiple tasks at once."""
    if not request.task_ids:
        raise BadRequestError("No task IDs provided")

    updated = db.query(Task).filter(
        Task.id.in_(request.task_ids)
    ).update({Task.archived: archived}, synchronize_session=False)
    db.commit()

    return {"success": True, "updated_count": updated, "archived": archived}


@router.post("/bulk-delete", dependencies=[Depends(require_operator)])
@handle_route_errors("bulk delete tasks")
def bulk_delete_tasks(request: TaskIdsRequest, db: Session = Depends(get_db)):
    """Delete multiple finished tasks. Running/queued tasks are skipped (cancel first)."""
    if not request.task_ids:
        raise BadRequestError("No task IDs provided")

    deleted = db.query(Task).filter(
        Task.id.in_(request.task_ids),
        Task.status.in_(_TERMINAL_STATUSES),
    ).delete(synchronize_session=False)
    db.commit()

    return {"success": True, "deleted_count": deleted}


@router.delete("/{task_id}", dependencies=[Depends(require_operator)])
@handle_route_errors("delete task")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    """Delete a single finished task from the operations log.

    Only terminal tasks (completed/failed/cancelled) may be deleted — cancel a
    running task first so its workspace lock and module state are unwound.
    """
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise NotFoundError("task", task_id)

    if task.status not in _TERMINAL_STATUSES:
        raise BadRequestError(
            f"Cannot delete a task with status: {task.status}. Cancel it first.",
            code="INVALID_TASK_STATUS",
        )

    db.delete(task)
    db.commit()

    return {"success": True, "task_id": task_id, "deleted": True}


@ws_router.websocket("/ws/tasks")
async def websocket_task_updates(websocket: WebSocket):
    """
    WebSocket endpoint for real-time task updates.

    Message format:
    {
        "type": "task_update" | "ping",
        "task_id": int,
        "status": "queued" | "in_progress" | "completed" | "failed" | "cancelled",
        "project_id": int,
        "module_id": int (optional),
        "task_type": "init" | "plan" | "apply" | "destroy",
        "timestamp": str (ISO format),
        "exit_code": int (optional),
        "error": str (optional),
        "duration_seconds": float (optional),
        "metadata": dict (optional)
    }

    Usage:
        const ws = new WebSocket('ws://localhost/api/tasks/ws/tasks');
        ws.onmessage = (event) => {
            const update = JSON.parse(event.data);
            if (update.type === 'task_update') {
                // Handle task update
            }
        };
    """
    await ws_manager.connect(websocket)

    try:
        # Keep connection alive and listen for client messages
        while True:
            try:
                # Wait for client messages (ping/pong for keepalive)
                data = await asyncio.wait_for(websocket.receive_text(), timeout=60.0)

                # Echo back pong
                if data == "ping":
                    await websocket.send_json({
                        "type": "pong",
                        "timestamp": _iso_z(datetime.now(UTC))
                    })

            except TimeoutError:
                # Client hasn't sent anything for 60s, but that's ok
                # The keepalive task sends pings from server side
                continue

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
        logger.info("WebSocket disconnected by client")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        ws_manager.disconnect(websocket)
