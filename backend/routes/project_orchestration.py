import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from core.cache import invalidate_cache
from core.errors import BadRequestError, ConflictError, get_or_404, handle_route_errors
from database import get_db
from models import ModuleLibrary, Project, ProjectModule, StackInstance, User
from models import Task as TaskModel
from models.enums import StackInstanceStatus, TaskStatus
from routes.auth import get_username_from_request, require_project_owner, require_viewer
from schemas.projects import DeployAllRequest, DestroyAllRequest
from services.dependency_graph_service import DependencyGraphService

# Configure logging
logger = logging.getLogger(__name__)


# ============================================================
# S2 Response Models
# ============================================================

class LayerResult(BaseModel):
    module_id: int
    success: bool
    task_id: int | None = None
    error: str | None = None


class LayerProgress(BaseModel):
    layer_index: int
    module_ids: list[int]
    module_names: list[str]
    status: str  # "pending" | "in_progress" | "completed" | "failed"
    results: dict[str, LayerResult]


class RunProgress(BaseModel):
    run_handle: str
    project_id: int
    action: str | None
    status: str  # "in_progress" | "completed" | "failed"
    total_layers: int
    current_layer: int
    layers: list[LayerProgress]
    successful_modules: list[int]
    failed_modules: list[int]
    skipped_modules: list[int]
    started_at: str | None
    completed_at: str | None
    duration_seconds: float | None
    progress_percent: float
    error_message: str | None

# ============================================================
# FastAPI Router Setup
# ============================================================

router = APIRouter(prefix="/api/projects", tags=["project-orchestration"])

# ============================================================
# P2-3: Parallel Execution Endpoints
# ============================================================

@router.get("/{project_id}/execution-plan", dependencies=[Depends(require_viewer)])
def get_execution_plan(project_id: int, db: Session = Depends(get_db)):
    """
    Get execution plan showing how modules will be grouped into layers.

    Shows estimated time for sequential vs parallel execution, highlighting time savings.

    Returns:
        {
            "layers": [...],
            "total_estimated_time_sequential": int (minutes),
            "total_estimated_time_parallel": int (minutes),
            "time_savings_percent": float,
            "parallelization_factor": float
        }
    """
    from services.parallel_execution_service import ParallelExecutionService

    get_or_404(db, Project, id=project_id)

    service = ParallelExecutionService(db)
    plan = service.get_execution_plan(project_id)

    if "error" in plan:
        raise BadRequestError(plan["error"])

    return plan


@router.post("/{project_id}/deploy-all")
def deploy_all_modules(
    project_id: int,
    request: Request,
    body: DeployAllRequest = DeployAllRequest(),
    user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
):
    """
    Deploy all modules in project.

    Executes modules sequentially (single active module at a time).

    Args:
        project_id: Project ID
        parallel: Deprecated request field (ignored; execution is sequential)

    Returns:
        {
            "orchestrator_task_id": str,
            "execution_plan": dict,
            "total_modules": int,
            "total_layers": int,
            "estimated_time_minutes": int
        }
    """
    from services.parallel_execution_service import ParallelExecutionService

    get_or_404(db, Project, id=project_id)

    # S2: Task-row guard (Q5). FOR UPDATE + skip_locked locks any existing in-flight
    # Task rows, preventing a second request from proceeding when a run is warm (tasks exist).
    # Cold-start race (no in-flight tasks): two concurrent cold-start requests both pass this
    # guard since there are no rows to lock. The per-module non-terminal-task check in
    # _dispatch_first_wave (S15-008) acts as the secondary guard — each module's first-wave
    # dispatch is skipped if a task was already created, bounding the blast radius to a
    # duplicate PE record. This matches the old PE-level guard behaviour and is accepted.
    in_flight_task = db.query(TaskModel).filter(
        TaskModel.project_id == project_id,
        TaskModel.status.in_([TaskStatus.QUEUED, TaskStatus.PENDING, TaskStatus.IN_PROGRESS]),
    ).with_for_update(skip_locked=True).first()
    if in_flight_task:
        raise ConflictError(
            "execution",
            f"An operation is already in progress for this project "
            f"(task #{in_flight_task.id} is {in_flight_task.status})",
        )

    # Validate project is ready
    service = ParallelExecutionService(db)
    is_ready, error = service.validate_project_ready(project_id, "deploy")
    if not is_ready:
        raise BadRequestError(error)

    # Execute deployment (always parallel — sequential mode not supported)
    try:
        result = service.deploy_project_parallel(project_id, create_tasks=True, triggered_by=get_username_from_request(request))

        # ENG-006: Route handler owns the transaction boundary
        db.commit()
    except Exception:
        db.rollback()
        raise

    # Invalidate caches
    invalidate_cache(f"projects:{project_id}*")
    invalidate_cache("projects:list")

    logger.info(f"Started sequential deployment for project {project_id}")
    return result


@router.post("/{project_id}/destroy-all")
def destroy_all_modules(
    project_id: int,
    request: Request,
    body: DestroyAllRequest = DestroyAllRequest(),
    user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
):
    """
    Destroy all modules in project (reverse layer order).

    Modules are destroyed from highest layer to lowest, sequentially.

    Args:
        project_id: Project ID
        parallel: Deprecated request field (ignored; execution is sequential)

    Returns:
        {
            "orchestrator_task_id": str,
            "execution_plan": dict,
            "total_modules": int,
            "total_layers": int,
            "estimated_time_minutes": int
        }
    """
    from services.parallel_execution_service import ParallelExecutionService

    get_or_404(db, Project, id=project_id)

    # S2: Task-row guard (Q5) — same pattern as deploy_all_modules above.
    # See deploy guard comment for cold-start race notes and accepted limitations.
    in_flight_task = db.query(TaskModel).filter(
        TaskModel.project_id == project_id,
        TaskModel.status.in_([TaskStatus.QUEUED, TaskStatus.PENDING, TaskStatus.IN_PROGRESS]),
    ).with_for_update(skip_locked=True).first()
    if in_flight_task:
        raise ConflictError(
            "execution",
            f"An operation is already in progress for this project "
            f"(task #{in_flight_task.id} is {in_flight_task.status})",
        )

    # Validate project is ready
    service = ParallelExecutionService(db)
    is_ready, error = service.validate_project_ready(project_id, "destroy")
    if not is_ready:
        logger.warning(f"Project validation warning: {error}")
        # Continue anyway for destroy - we can skip non-applied modules

    # S15-016: Update all project's StackInstance records to "destroying"
    # so they reflect the correct state while their modules are being destroyed
    project_stacks = db.query(StackInstance).filter(
        StackInstance.project_id == project_id,
        StackInstance.status.in_([StackInstanceStatus.DEPLOYED, StackInstanceStatus.FAILED, StackInstanceStatus.PENDING])
    ).all()
    for stack in project_stacks:
        stack.status = StackInstanceStatus.DESTROYING
        stack.error_message = None
    if project_stacks:
        db.commit()

    # Execute destruction (always parallel — sequential mode not supported)
    try:
        result = service.destroy_project_parallel(
            project_id,
            create_tasks=True,
            triggered_by=get_username_from_request(request),
            force_destroy=body.force_destroy,
        )

        # ENG-006: Route handler owns the transaction boundary
        db.commit()
    except Exception:
        db.rollback()
        raise

    # Invalidate caches
    invalidate_cache(f"projects:{project_id}*")
    invalidate_cache("projects:list")

    logger.info(f"Started sequential destruction for project {project_id}")
    return result


@router.get("/{project_id}/orchestration/{run_handle}", dependencies=[Depends(require_viewer)])
def get_run_progress(
    project_id: int,
    run_handle: str,
    db: Session = Depends(get_db),
) -> RunProgress:
    """
    Get run progress derived entirely from Task rows (Q4 — D-001 Phase 3 S2).

    Returns the current status of a deploy/destroy run identified by run_handle.
    Progress is derived from Task rows (WHERE project_id=? AND run_handle=?),
    augmented with DependencyGraphService layer structure.

    Status derivation (single rule):
    - 'failed'      if any relevant Task failed
    - 'completed'   if all relevant modules are terminal (success/skip) with no live tasks
    - 'in_progress' otherwise

    Args:
        project_id: Project ID (scoping guard)
        run_handle: Stable run identifier returned by deploy-all / destroy-all

    Returns:
        RunProgress: derived progress shape per Q4
    """
    get_or_404(db, Project, id=project_id)

    # Load all Task rows for this run
    run_tasks = (
        db.query(TaskModel)
        .filter(
            TaskModel.project_id == project_id,
            TaskModel.run_handle == run_handle,
        )
        .all()
    )
    if not run_tasks:
        raise HTTPException(status_code=404, detail=f"Run '{run_handle}' not found for project {project_id}")

    # Group tasks by module_id (keep most-recent task per module for status)
    tasks_by_module: dict[int, TaskModel] = {}
    for task in run_tasks:
        if task.module_id is not None:
            prev = tasks_by_module.get(task.module_id)
            if prev is None or task.id > prev.id:
                tasks_by_module[task.module_id] = task

    module_ids = list(tasks_by_module.keys())

    # Load module records to get names and layer structure
    modules = (
        db.query(ProjectModule)
        .options(joinedload(ProjectModule.library_module))
        .filter(ProjectModule.id.in_(module_ids))
        .all()
    ) if module_ids else []
    modules_by_id = {m.id: m for m in modules}

    # Determine action from task types
    action: str | None = None
    for t in run_tasks:
        if t.task_type in ("destroy",):
            action = "destroy"
            break
        if t.task_type in ("apply", "init"):
            action = "deploy"
            break

    # Build layers using DependencyGraphService (deploy order; destroy reverses)
    graph_svc = DependencyGraphService(db)
    try:
        raw_layers = graph_svc.build_layers(project_id)
    except Exception:
        raw_layers = []

    # Filter layers to only modules in this run
    run_module_id_set = set(module_ids)
    ordered_layers = [
        [m for m in layer if m.id in run_module_id_set]
        for layer in (reversed(raw_layers) if action == "destroy" else raw_layers)
    ]
    ordered_layers = [layer for layer in ordered_layers if layer]

    # If we have modules not in any layer (e.g., run pre-dates layer info), add as layer 0
    layered_ids = {m.id for layer in ordered_layers for m in layer}
    stragglers = [mid for mid in module_ids if mid not in layered_ids]
    if stragglers:
        straggler_mods = [modules_by_id[mid] for mid in stragglers if mid in modules_by_id]
        if straggler_mods:
            ordered_layers.insert(0, straggler_mods)

    total_layers = len(ordered_layers)

    # Terminal status sets — values must match Task.status (TaskStatus enum), not ModuleStatus.
    # Task.status is one of: queued / pending / in_progress / completed / failed / cancelled.
    TERMINAL_SUCCESS = {"completed"}
    TERMINAL_FAIL = {"failed", "cancelled"}
    LIVE = {"queued", "pending", "in_progress"}

    # Derive per-module status from Task rows
    successful_modules: list[int] = []
    failed_modules: list[int] = []
    skipped_modules: list[int] = []

    for mid, task in tasks_by_module.items():
        status = (task.status or "").lower()
        if status in TERMINAL_SUCCESS:
            successful_modules.append(mid)
        elif status in TERMINAL_FAIL:
            # "failed" and "cancelled" both indicate the module did not succeed.
            # Cancelled tasks are counted here so they are treated as terminal.
            failed_modules.append(mid)
        # tasks with live status stay in "live" bucket (not yet terminal)

    # Build layer structures
    layer_list: list[LayerProgress] = []
    completed_layers = 0

    for idx, layer_modules in enumerate(ordered_layers):
        layer_module_ids = [m.id for m in layer_modules]
        layer_module_names = [
            m.library_module.name if m.library_module else f"module-{m.id}"
            for m in layer_modules
        ]
        layer_results: dict[str, LayerResult] = {}
        layer_failed = False
        layer_all_terminal = True
        layer_any_live = False

        for m in layer_modules:
            task = tasks_by_module.get(m.id)
            if task is None:
                # Not dispatched yet
                layer_all_terminal = False
                layer_results[str(m.id)] = LayerResult(module_id=m.id, success=False)
                continue

            task_status = (task.status or "").lower()
            if task_status in TERMINAL_SUCCESS:
                layer_results[str(m.id)] = LayerResult(
                    module_id=m.id, success=True, task_id=task.id
                )
            elif task_status in TERMINAL_FAIL:
                layer_failed = True
                layer_all_terminal = True  # terminal, just failed
                layer_results[str(m.id)] = LayerResult(
                    module_id=m.id, success=False, task_id=task.id,
                    error=task.error or task_status,
                )
            elif task_status in LIVE:
                layer_all_terminal = False
                layer_any_live = True
                layer_results[str(m.id)] = LayerResult(
                    module_id=m.id, success=False, task_id=task.id
                )
            else:
                layer_all_terminal = False
                layer_results[str(m.id)] = LayerResult(
                    module_id=m.id, success=False, task_id=task.id
                )

        if layer_all_terminal and not layer_failed:
            layer_status = "completed"
            completed_layers += 1
        elif layer_all_terminal and layer_failed:
            layer_status = "failed"
        elif layer_any_live:
            layer_status = "in_progress"
        else:
            layer_status = "pending"

        layer_list.append(LayerProgress(
            layer_index=idx,
            module_ids=layer_module_ids,
            module_names=layer_module_names,
            status=layer_status,
            results=layer_results,
        ))

    # Derive overall status
    all_tasks_for_run = list(tasks_by_module.values())
    any_failed = any((t.status or "").lower() in TERMINAL_FAIL for t in all_tasks_for_run)
    all_terminal = all(
        (t.status or "").lower() in TERMINAL_SUCCESS | TERMINAL_FAIL
        for t in all_tasks_for_run
    )

    # Q4 spec: once any module has failed the run is already failed, even if other
    # tasks are still draining. Report "failed" immediately (fail-closed barrier
    # ensures no new modules get dispatched, so the outcome is already determined).
    if any_failed:
        overall_status = "failed"
        error_message: str | None = f"Destroy stopped: {len(failed_modules)} module(s) failed" if action == "destroy" \
            else f"Deploy failed: {len(failed_modules)} module(s) failed"
    elif all_terminal and not any_failed:
        overall_status = "completed"
        error_message = None
    else:
        overall_status = "in_progress"
        error_message = None

    # Timestamps from task rows
    started_ats = [t.started_at or t.created_at for t in all_tasks_for_run if t.started_at or t.created_at]
    completed_ats = [t.completed_at for t in all_tasks_for_run if t.completed_at]
    started_at_dt = min(started_ats) if started_ats else None
    completed_at_dt = max(completed_ats) if (completed_ats and overall_status in ("completed", "failed")) else None

    duration_seconds: float | None = None
    if started_at_dt and completed_at_dt:
        duration_seconds = (completed_at_dt - started_at_dt).total_seconds()

    # Progress percent: completed-layers / total_layers
    progress_percent = round((completed_layers / total_layers) * 100, 1) if total_layers > 0 else 0.0

    # Current layer: first non-completed layer index (or last if all done)
    current_layer = 0
    for lp in layer_list:
        if lp.status not in ("completed",):
            current_layer = lp.layer_index
            break
    else:
        current_layer = total_layers - 1 if total_layers > 0 else 0

    return RunProgress(
        run_handle=run_handle,
        project_id=project_id,
        action=action,
        status=overall_status,
        total_layers=total_layers,
        current_layer=current_layer,
        layers=layer_list,
        successful_modules=successful_modules,
        failed_modules=failed_modules,
        skipped_modules=skipped_modules,
        started_at=started_at_dt.isoformat() if started_at_dt else None,
        completed_at=completed_at_dt.isoformat() if completed_at_dt else None,
        duration_seconds=duration_seconds,
        progress_percent=progress_percent,
        error_message=error_message,
    )


# ============================================================
# Cross-Project Module Query
# ============================================================
# NOTE: GET /{project_id}/parallel-executions/{exec_id} and
#       GET /{project_id}/parallel-executions routes removed in D-001 Phase 3 S3b.
#       The parallel_executions table was dropped in migration v2_119.
#       Use GET /{project_id}/orchestration/{run_handle} instead.

@router.get("/modules/all", dependencies=[Depends(require_viewer)])
@handle_route_errors("get all modules")
def get_all_modules(
    page: int = 1,
    page_size: int = 50,
    project_id: int | None = None,
    status: str | None = None,
    db: Session = Depends(get_db)
):
    """
    Get all modules across all projects with pagination (PERF-018).

    Optimized endpoint to avoid N+1 queries when fetching deployment overview.
    Returns modules with project info for dashboard/search use cases.

    Args:
        page: Page number (1-indexed, default 1)
        page_size: Items per page (default 50, max 200)
        project_id: Optional filter by project
        status: Optional filter by module status

    Returns:
        Paginated list of modules with metadata
    """
    # Validate pagination params
    page = max(1, page)
    page_size = min(max(1, page_size), 200)  # Cap at 200 items
    offset = (page - 1) * page_size

    # B3: load_only() — module list only needs project.name and library_module id/name/category/path
    query = db.query(ProjectModule).options(
        joinedload(ProjectModule.project).load_only(Project.id, Project.name),
        joinedload(ProjectModule.library_module).load_only(
            ModuleLibrary.id, ModuleLibrary.name, ModuleLibrary.category, ModuleLibrary.path
        )
    )

    # Apply filters
    if project_id:
        query = query.filter(ProjectModule.project_id == project_id)
    if status:
        query = query.filter(ProjectModule.status == status)

    # Get total count for pagination metadata
    total_count = query.count()

    # Apply pagination
    modules = query.order_by(ProjectModule.id).offset(offset).limit(page_size).all()

    items = [
        {
            "id": m.id,
            "module_name": m.library_module.name if m.library_module else None,
            "status": m.status,
            "last_deployed_at": m.last_deployed_at.isoformat() if m.last_deployed_at else None,
            "deployment_order": m.deployment_order,
            "project_id": m.project_id,
            "project_name": m.project.name if m.project else None,
            "path_in_project": m.path_in_project,
            "deployment_error": m.deployment_error,
            "library_module": {
                "id": m.library_module.id,
                "name": m.library_module.name,
                "category": m.library_module.category,
                "path": m.library_module.path,
            } if m.library_module else None,
        }
        for m in modules
    ]

    return {
        "items": items,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_items": total_count,
            "total_pages": (total_count + page_size - 1) // page_size,
            "has_next": offset + len(modules) < total_count,
            "has_prev": page > 1
        }
    }
