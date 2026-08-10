"""API routes for bare-metal DPU deployments — lifecycle management."""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from models import User
from routes.auth import require_project_owner, require_viewer
from schemas.bare_metal import (
    BareMetalDeploymentCreate,
    BareMetalDeploymentListResponse,
    BareMetalDeploymentResponse,
    BareMetalDeploymentResumeRequest,
    DeploymentPlanPreview,
    DeploymentStepResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/projects/{project_id}/bare-metal/deployments",
    tags=["bare-metal"],
)


@router.get("", response_model=BareMetalDeploymentListResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("list bare-metal deployments")
def list_deployments(
    project_id: int,
    host_id: int | None = None,
    db: Session = Depends(get_db),
) -> BareMetalDeploymentListResponse:
    """List deployments for a project, optionally filtered by host."""
    from services.bare_metal.orchestrator import BareMetalDeploymentService
    svc = BareMetalDeploymentService(db)
    deployments = svc.list_deployments(project_id, host_id=host_id)
    return BareMetalDeploymentListResponse(
        deployments=[_deployment_to_response(d) for d in deployments]
    )


@router.post("", response_model=BareMetalDeploymentResponse)
@handle_route_errors("create bare-metal deployment")
def create_deployment(
    project_id: int,
    data: BareMetalDeploymentCreate,
    _user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
) -> BareMetalDeploymentResponse:
    """Start a new bare-metal deployment.

    Creates the deployment plan (steps based on host topology),
    then dispatches execution to a Celery worker.
    """
    from services.bare_metal.orchestrator import BareMetalDeploymentService
    svc = BareMetalDeploymentService(db)
    deployment = svc.create_deployment(
        host_id=data.host_id,
        project_id=project_id,
        resume_from_step=data.resume_from_step,
        selected_phases=data.selected_phases,
        selected_steps=data.selected_steps,
    )
    db.commit()

    # Dispatch to Celery (async)
    if not data.skip_discovery:
        from tasks.bare_metal_tasks import execute_bare_metal_deployment
        task = execute_bare_metal_deployment.delay(deployment.id, project_id)
        deployment.celery_task_id = task.id
        db.commit()

    return _deployment_to_response(deployment)


@router.post("/preview", response_model=DeploymentPlanPreview, dependencies=[Depends(require_viewer)])
@handle_route_errors("preview bare-metal deployment plan")
def preview_deployment(
    project_id: int,
    data: BareMetalDeploymentCreate,
    db: Session = Depends(get_db),
) -> DeploymentPlanPreview:
    """Preview a deployment plan without creating it.

    Returns the proposed step plan with selection and prerequisite warnings.
    """
    from services.bare_metal.orchestrator import BareMetalDeploymentService
    svc = BareMetalDeploymentService(db)
    result = svc.preview_deployment(
        host_id=data.host_id,
        project_id=project_id,
        selected_phases=data.selected_phases,
        selected_steps=data.selected_steps,
    )
    return DeploymentPlanPreview(**result)


@router.get("/{deployment_id}", response_model=BareMetalDeploymentResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("get bare-metal deployment")
def get_deployment(
    project_id: int,
    deployment_id: int,
    db: Session = Depends(get_db),
) -> BareMetalDeploymentResponse:
    """Get deployment status and steps."""
    from services.bare_metal.orchestrator import BareMetalDeploymentService
    deployment = BareMetalDeploymentService(db).get_deployment(deployment_id)
    return _deployment_to_response(deployment)


@router.post("/{deployment_id}/resume", response_model=BareMetalDeploymentResponse)
@handle_route_errors("resume bare-metal deployment")
def resume_deployment(
    project_id: int,
    deployment_id: int,
    data: BareMetalDeploymentResumeRequest,
    _user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
) -> BareMetalDeploymentResponse:
    """Resume a failed or paused deployment."""
    from services.bare_metal.orchestrator import BareMetalDeploymentService
    svc = BareMetalDeploymentService(db)
    deployment = svc.resume_deployment(deployment_id, from_step=data.from_step)
    db.commit()

    # Re-dispatch to Celery
    from tasks.bare_metal_tasks import execute_bare_metal_deployment
    task = execute_bare_metal_deployment.delay(deployment.id, project_id)
    deployment.celery_task_id = task.id
    db.commit()

    return _deployment_to_response(deployment)


@router.post("/{deployment_id}/cancel", response_model=BareMetalDeploymentResponse)
@handle_route_errors("cancel bare-metal deployment")
def cancel_deployment(
    project_id: int,
    deployment_id: int,
    _user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
) -> BareMetalDeploymentResponse:
    """Cancel a running or pending deployment."""
    from services.bare_metal.orchestrator import BareMetalDeploymentService
    svc = BareMetalDeploymentService(db)
    deployment = svc.cancel_deployment(deployment_id)
    db.commit()
    return _deployment_to_response(deployment)


@router.get("/{deployment_id}/steps/{step_index}/logs", dependencies=[Depends(require_viewer)])
@handle_route_errors("get deployment step logs")
def get_step_logs(
    project_id: int,
    deployment_id: int,
    step_index: int,
    db: Session = Depends(get_db),
):
    """Get output log for a specific deployment step."""
    from services.bare_metal.orchestrator import BareMetalDeploymentService
    deployment = BareMetalDeploymentService(db).get_deployment(deployment_id)

    step = next((s for s in deployment.steps if s.step_index == step_index), None)
    if step is None:
        from core.errors import NotFoundError
        raise NotFoundError("DeploymentStep", f"index {step_index}")

    return {
        "deployment_id": deployment_id,
        "step_index": step_index,
        "step_name": step.name,
        "status": step.status,
        "output_log": step.output_log,
        "error_message": step.error_message,
        "exit_code": step.exit_code,
    }


# --- Response builder ---

def _deployment_to_response(deployment) -> BareMetalDeploymentResponse:
    """Convert BareMetalDeployment ORM model to response schema."""
    steps = sorted(deployment.steps, key=lambda s: s.step_index) if deployment.steps else []
    return BareMetalDeploymentResponse(
        id=deployment.id,
        host_id=deployment.host_id,
        project_id=deployment.project_id,
        topology=deployment.topology,
        status=deployment.status,
        current_phase=deployment.current_phase,
        current_step_index=deployment.current_step_index or 0,
        celery_task_id=deployment.celery_task_id,
        resume_from_step=deployment.resume_from_step,
        selected_phases=deployment.selected_phases,
        selected_steps=deployment.selected_steps,
        started_at=deployment.started_at,
        completed_at=deployment.completed_at,
        duration_seconds=deployment.duration_seconds,
        error_message=deployment.error_message,
        error_phase=deployment.error_phase,
        error_step_index=deployment.error_step_index,
        triggered_by=deployment.triggered_by or "user",
        created_at=deployment.created_at,
        steps=[
            DeploymentStepResponse(
                id=s.id,
                step_index=s.step_index,
                phase=s.phase,
                name=s.name,
                description=s.description,
                status=s.status,
                already_done=s.already_done or False,
                connectivity_risk=s.connectivity_risk or False,
                connectivity_mechanism=s.connectivity_mechanism,
                started_at=s.started_at,
                completed_at=s.completed_at,
                duration_seconds=s.duration_seconds,
                error_message=s.error_message,
                exit_code=s.exit_code,
                created_at=s.created_at,
            )
            for s in steps
        ],
    )
