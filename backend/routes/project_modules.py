"""
Project Module Management Routes — Module CRUD, Status, Variables, Dependencies.

Thin HTTP handlers delegating to ProjectModuleService.

Execution (init/plan/apply/destroy) -> routes/project_execution.py
Deployment history & state          -> routes/project_deployments.py
Variable mappings & templates       -> routes/project_variable_mappings.py
"""
import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session

from core.errors import BadRequestError, NotFoundError, handle_route_errors
from database import get_db
from models import User
from routes.auth import require_module_owner, require_project_owner, require_viewer
from services.project_module_service import ProjectModuleService

router = APIRouter(prefix="/api/project-modules", tags=["project-modules"])


# ============================================================================
# Pydantic Schemas
# ============================================================================

class AddModuleRequest(BaseModel):
    """Schema for adding a module to a project"""
    module_library_id: int = Field(..., gt=0, description="ID of module from library")
    path_in_project: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Path where module will be created (e.g., 'infra/vpc')"
    )
    variable_overrides: dict | None = Field(default_factory=dict)
    deployment_order: int = Field(default=0, ge=0, le=9999)

    @validator('path_in_project')
    @classmethod
    def validate_path(cls, v):
        """Validate path doesn't contain dangerous characters"""
        if '..' in v or v.startswith('/'):
            raise ValueError('Path cannot contain ".." or start with "/"')
        if not re.match(r'^[a-zA-Z0-9/_-]+$', v):
            raise ValueError('Path can only contain alphanumeric, /, _, and - characters')
        return v

    @validator('variable_overrides')
    @classmethod
    def validate_variables(cls, v):
        """Validate variable names and values"""
        if v:
            for key, value in v.items():
                if not re.match(r'^[a-z_][a-z0-9_]*$', key):
                    raise ValueError(
                        f'Invalid variable name "{key}". Must be lowercase, start with letter/underscore'
                    )
                value_str = str(value)
                dangerous_chars = [';', '|', '&', '`', '$', '(', ')']
                if any(char in value_str for char in dangerous_chars):
                    raise ValueError(
                        f'Variable value contains potentially dangerous characters: {value_str[:50]}'
                    )
        return v


class UpdateModuleRequest(BaseModel):
    """Schema for updating module configuration"""
    variable_overrides: dict | None = None
    deployment_order: int | None = None
    enabled: bool | None = None


class ValidateVariablesRequest(BaseModel):
    """Schema for validating variable values"""
    module_library_id: int = Field(..., gt=0)
    variables: dict = Field(..., description="Variable name -> value mapping")

    @validator('variables')
    @classmethod
    def validate_variable_names(cls, v):
        for key in v.keys():
            if not re.match(r'^[a-z_][a-z0-9_]*$', key):
                raise ValueError(
                    f'Invalid variable name "{key}". Must be lowercase, start with letter/underscore'
                )
        return v


class SetDependenciesRequest(BaseModel):
    """Schema for setting module dependencies"""
    dependencies: list[int] = Field(
        default_factory=list,
        description="List of module IDs this module depends on"
    )

    @validator('dependencies')
    @classmethod
    def validate_dependencies(cls, v):
        if len(v) != len(set(v)):
            raise ValueError('Dependencies list contains duplicates')
        return v


class ModuleStateTransitionEntry(BaseModel):
    """One row of a module's state-transition audit log (Phase 2)."""
    id: int
    from_status: str
    to_status: str
    task_id: int | None = None
    fence_token: int | None = None
    reason: str | None = None
    at: object  # datetime — Pydantic v2 serializes via model_config below

    model_config = {"from_attributes": True}


class ModuleStateHistoryResponse(BaseModel):
    """Response schema for GET /api/project-modules/{module_id}/state-history."""
    module_id: int
    count: int
    transitions: list[ModuleStateTransitionEntry]


# ============================================================================
# Variable Validation
# ============================================================================

@router.post("/validate-variables", dependencies=[Depends(require_viewer)])
def validate_variables(request: ValidateVariablesRequest, db: Session = Depends(get_db)):
    """Validate variable values against module schema"""
    svc = ProjectModuleService(db)
    return svc.validate_variables(request.module_library_id, request.variables)


# ============================================================================
# Module CRUD
# ============================================================================

@router.get("/project/{project_id}", dependencies=[Depends(require_viewer)])
def list_project_modules(project_id: int, db: Session = Depends(get_db)):
    """List all modules added to a project"""
    svc = ProjectModuleService(db)
    return svc.list_modules(project_id)


@router.post("/project/{project_id}/add")
@handle_route_errors("add module to project")
def add_module_to_project(
    project_id: int,
    request: AddModuleRequest,
    user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
):
    """Add a module from the library to a project"""
    svc = ProjectModuleService(db)
    result = svc.add_module(
        project_id=project_id,
        module_library_id=request.module_library_id,
        path_in_project=request.path_in_project,
        variable_overrides=request.variable_overrides,
        deployment_order=request.deployment_order,
    )
    db.commit()
    return result


@router.put("/{module_id}")
@handle_route_errors("update project module")
def update_project_module(
    module_id: int,
    request: UpdateModuleRequest,
    user: User = Depends(require_module_owner),
    db: Session = Depends(get_db),
):
    """Update module configuration"""
    svc = ProjectModuleService(db)
    result = svc.update_module(
        module_id=module_id,
        variable_overrides=request.variable_overrides,
        deployment_order=request.deployment_order,
        enabled=request.enabled,
    )
    db.commit()
    return result


class ChangeModuleVersionRequest(BaseModel):
    """Schema for re-pinning a project module to a different catalog version (D-033)."""
    target_version: str = Field(..., min_length=1, max_length=100, description="Catalog version to pin, e.g. '1.20.0'")


@router.post("/{module_id}/change-version")
@handle_route_errors("change project module version")
def change_project_module_version(
    module_id: int,
    request: ChangeModuleVersionRequest,
    user: User = Depends(require_module_owner),
    db: Session = Depends(get_db),
):
    """Re-pin a project module to a different catalog version of its module path.

    Explicit operator action (D-033): the new version's manifest takes effect on
    the module's next plan/apply/destroy — nothing is re-deployed by this call.
    """
    svc = ProjectModuleService(db)
    result = svc.change_module_version(module_id, request.target_version)
    db.commit()
    return result


@router.delete("/{module_id}")
@handle_route_errors("remove module from project")
def remove_module_from_project(module_id: int, user: User = Depends(require_module_owner), db: Session = Depends(get_db)):
    """Remove a module from a project"""
    svc = ProjectModuleService(db)
    result = svc.remove_module(module_id)
    db.commit()
    return result


# ============================================================================
# Status & Variables
# ============================================================================

@router.get("/{module_id}/status", dependencies=[Depends(require_viewer)])
def get_module_status(module_id: int, db: Session = Depends(get_db)):
    """Get current status of a module"""
    svc = ProjectModuleService(db)
    return svc.get_module_status(module_id)


@router.get(
    "/{module_id}/state-history",
    dependencies=[Depends(require_viewer)],
    response_model=ModuleStateHistoryResponse,
)
@handle_route_errors("get module state history")
def get_module_state_history(
    module_id: int,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Return the audit log of status transitions for a module.

    Phase 2 of the locking RFC. Each row records `from_status -> to_status`
    plus the task_id, fence_token (if under-lock), reason, and timestamp.
    Use this when a module appears stuck or has misbehaved — the timeline
    shows exactly when each state was set, by which task, and why.
    """
    if limit < 1 or limit > 500:
        raise BadRequestError("limit must be between 1 and 500")
    from models import ModuleStateTransition, ProjectModule

    if db.query(ProjectModule).filter(ProjectModule.id == module_id).first() is None:
        raise NotFoundError("project module", module_id)

    rows = (
        db.query(ModuleStateTransition)
        .filter(ModuleStateTransition.module_id == module_id)
        .order_by(ModuleStateTransition.at.desc())
        .limit(limit)
        .all()
    )
    return ModuleStateHistoryResponse(
        module_id=module_id,
        count=len(rows),
        transitions=[
            ModuleStateTransitionEntry(
                id=r.id,
                from_status=r.from_status,
                to_status=r.to_status,
                task_id=r.task_id,
                fence_token=r.fence_token,
                reason=r.reason,
                at=r.at,
            )
            for r in rows
        ],
    )


@router.get("/{module_id}/variables", dependencies=[Depends(require_viewer)])
def get_module_variables(module_id: int, db: Session = Depends(get_db)):
    """Get variables for a project module"""
    svc = ProjectModuleService(db)
    return svc.get_module_variables(module_id)


# ============================================================================
# Dependency Management
# ============================================================================

@router.put("/{module_id}/dependencies")
@handle_route_errors("set module dependencies")
def set_module_dependencies(
    module_id: int,
    request: SetDependenciesRequest,
    user: User = Depends(require_module_owner),
    db: Session = Depends(get_db),
):
    """Set dependencies for a module"""
    svc = ProjectModuleService(db)
    result = svc.set_dependencies(module_id, request.dependencies)
    db.commit()
    return result


@router.get("/{module_id}/dependencies", dependencies=[Depends(require_viewer)])
def get_module_dependencies(module_id: int, db: Session = Depends(get_db)):
    """Get dependencies for a module and their current status"""
    svc = ProjectModuleService(db)
    return svc.get_dependencies(module_id)


@router.get("/{module_id}/dependents", dependencies=[Depends(require_viewer)])
def get_module_dependents(module_id: int, db: Session = Depends(get_db)):
    """Get all modules that depend on this module"""
    svc = ProjectModuleService(db)
    return svc.get_dependents(module_id)


@router.post("/project/{project_id}/calculate-order")
@handle_route_errors("calculate deployment order")
def calculate_module_deployment_order(project_id: int, user: User = Depends(require_project_owner), db: Session = Depends(get_db)):
    """Auto-calculate deployment order for all modules based on dependencies"""
    svc = ProjectModuleService(db)
    result = svc.calculate_deployment_order(project_id)
    db.commit()
    return result


@router.get("/project/{project_id}/dependency-graph", dependencies=[Depends(require_viewer)])
def get_project_dependency_graph(project_id: int, db: Session = Depends(get_db)):
    """Get dependency graph structure for visualization"""
    svc = ProjectModuleService(db)
    return svc.get_dependency_graph(project_id)


# ============================================================================
# Re-run (SSH modules only)
# ============================================================================

@router.post("/{module_id}/rerun")
@handle_route_errors("rerun module")
def rerun_module(module_id: int, user: User = Depends(require_module_owner), db: Session = Depends(get_db)):
    """Reset an SSH module and re-run it (init + apply).

    Only available for SSH engine modules that have already been applied/deployed.
    Resets the module to not_initialized, clears outputs, and dispatches
    init with auto_apply=True.
    """
    svc = ProjectModuleService(db)
    module = svc.get_module(module_id)
    if not module:
        raise NotFoundError("module", module_id)

    # Only allow rerun for SSH modules
    engine_type = module.library_module.engine_type if module.library_module else None
    if engine_type != "ssh":
        raise BadRequestError("Rerun is only available for SSH modules", code="RERUN_NOT_SUPPORTED")

    # Only allow rerun for completed modules
    if module.status in ("initializing", "planning", "applying", "destroying"):
        raise BadRequestError(f"Cannot rerun: module is currently {module.status}", code="OPERATION_IN_PROGRESS")

    # Validate BEFORE any mutation. Everything below nulls outputs/plan_output
    # and commits, so a rejection at dispatch time would already have destroyed
    # an applied module's outputs on a request that never ran.
    if not module.enabled:
        raise BadRequestError(
            "Module is disabled — enable it before rerunning", code="MODULE_DISABLED"
        )

    # Reset module state
    module.status = "not_initialized"
    module.deployment_error = None
    module.stage_detail = None
    module.outputs = None
    module.plan_output = None

    # If this module belongs to a stack that failed, resume the deploy chain.
    # Without this, should_chain in _update_stack_status_if_needed would see
    # stack.status == FAILED and skip triggering the next module.
    if module.stack_instance_id:
        from models import StackInstance, StackInstanceStatus

        stack = db.query(StackInstance).filter(
            StackInstance.id == module.stack_instance_id
        ).first()
        if stack and stack.status == StackInstanceStatus.FAILED:
            stack.status = StackInstanceStatus.DEPLOYING
            stack.error_message = None
            stack.completed_at = None

    db.commit()

    # Dispatch init with auto_apply=True
    from datetime import UTC, datetime

    from models import Task as TaskModel
    from services.execution.task_dispatch import dispatch_init

    task = TaskModel(
        task_type="init",
        status="queued",
        command="ssh-engine: init",
        project_id=module.project_id,
        module_id=module.id,
        created_at=datetime.now(UTC),
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    celery_result = dispatch_init(task.id, module, auto_apply=True)
    task.celery_task_id = celery_result.id
    module.status = "initializing"
    db.commit()

    return {"status": "rerunning", "task_id": task.id, "module_id": module_id}
