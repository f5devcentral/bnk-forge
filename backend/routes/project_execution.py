"""
Project Module Execution routes — OpenTofu workflow operations.

Thin HTTP handlers delegating to ProjectModuleService.

Split from monolithic project_modules.py (2,487 lines).
"""

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from models import User
from routes.auth import get_username_from_request, require_module_owner, require_viewer
from schemas.projects import (
    ModuleActionsListResponse,
    ModuleActionSubmitResponse,
    ModuleReportContentResponse,
    ModuleReportsListResponse,
)
from services.module_reports_service import ModuleReportsService
from services.project_module_service import ProjectModuleService

router = APIRouter(prefix="/api/project-modules", tags=["project-execution"])


# ============================================================================
# Pydantic Request Models
# ============================================================================

class OpenTofuActionRequest(BaseModel):
    """Schema for OpenTofu actions"""
    auto_approve: bool = False


class ModuleActionRequest(BaseModel):
    """Request body for running a manifest-declared module action (D-034)."""
    inputs: dict[str, Any] | None = None


# ============================================================================
# OpenTofu Workflow Endpoints
# ============================================================================

@router.post("/{module_id}/init")
@handle_route_errors("submit opentofu init")
def opentofu_init(module_id: int, request: Request, user: User = Depends(require_module_owner), db: Session = Depends(get_db)):
    """Run OpenTofu init on a module using task queue"""
    svc = ProjectModuleService(db)
    result = svc.submit_init(module_id, triggered_by=get_username_from_request(request))
    db.commit()
    if hasattr(db, "expire_all"):
        db.expire_all()
    return result


@router.get("/{module_id}/plan-status", dependencies=[Depends(require_viewer)])
def get_plan_status(module_id: int, db: Session = Depends(get_db)):
    """Get plan status for a module"""
    svc = ProjectModuleService(db)
    return svc.get_plan_status(module_id)


@router.get("/{module_id}/validate", dependencies=[Depends(require_viewer)])
def validate_module(module_id: int, operation: str = "plan", db: Session = Depends(get_db)):
    """Validate a module before running plan or apply"""
    svc = ProjectModuleService(db)
    return svc.validate_module(module_id, operation)


@router.post("/{module_id}/plan")
@handle_route_errors("submit opentofu plan")
def opentofu_plan(module_id: int, request: Request, user: User = Depends(require_module_owner), db: Session = Depends(get_db)):
    """Run OpenTofu plan on a module using task queue"""
    svc = ProjectModuleService(db)
    result = svc.submit_plan(module_id, triggered_by=get_username_from_request(request))
    db.commit()
    if hasattr(db, "expire_all"):
        db.expire_all()
    return result


@router.post("/{module_id}/apply")
@handle_route_errors("submit opentofu apply")
def opentofu_apply(
    module_id: int,
    request: OpenTofuActionRequest,
    http_request: Request = None,
    user: User = Depends(require_module_owner),
    db: Session = Depends(get_db),
):
    """Run OpenTofu apply on a module using task queue"""
    svc = ProjectModuleService(db)
    result = svc.submit_apply(
        module_id,
        triggered_by=get_username_from_request(http_request) if http_request else "user",
        auto_approve=request.auto_approve,
    )
    db.commit()
    if hasattr(db, "expire_all"):
        db.expire_all()
    return result


@router.post("/{module_id}/destroy")
@handle_route_errors("submit opentofu destroy")
def opentofu_destroy(
    module_id: int,
    request: OpenTofuActionRequest,
    http_request: Request = None,
    user: User = Depends(require_module_owner),
    db: Session = Depends(get_db),
):
    """Run OpenTofu destroy on a module using task queue"""
    svc = ProjectModuleService(db)
    result = svc.submit_destroy(
        module_id,
        triggered_by=get_username_from_request(http_request) if http_request else "user",
    )
    db.commit()
    if hasattr(db, "expire_all"):
        db.expire_all()
    return result


# ============================================================================
# Module Actions (D-034 — manifest-declared test/scenario actions)
# ============================================================================

@router.get(
    "/{module_id}/actions",
    response_model=ModuleActionsListResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("list module actions")
def list_module_actions(module_id: int, db: Session = Depends(get_db)):
    """List the actions declared in a container module's artifact manifest"""
    svc = ProjectModuleService(db)
    return svc.list_module_actions(module_id)


@router.post("/{module_id}/actions/{action_name}", response_model=ModuleActionSubmitResponse)
@handle_route_errors("submit module action")
def run_module_action(
    module_id: int,
    action_name: str,
    request: ModuleActionRequest | None = None,
    http_request: Request = None,
    user: User = Depends(require_module_owner),
    db: Session = Depends(get_db),
):
    """Run a manifest-declared action on a post-apply container module using task queue"""
    svc = ProjectModuleService(db)
    result = svc.submit_action(
        module_id,
        action_name,
        inputs=request.inputs if request else None,
        triggered_by=get_username_from_request(http_request) if http_request else "user",
    )
    db.commit()
    if hasattr(db, "expire_all"):
        db.expire_all()
    return result


# ============================================================================
# Module Reports (D-034 PR-2.5 — surface the tool-written run/scenario reports)
# ============================================================================

@router.get("/{module_id}/reports", response_model=ModuleReportsListResponse)
@handle_route_errors("list module reports")
def list_module_reports(
    module_id: int,
    user: User = Depends(require_module_owner),
    db: Session = Depends(get_db),
):
    """List the report runs a module's tool wrote into its workspace"""
    return ModuleReportsService(db).list_runs(module_id)


@router.get("/{module_id}/reports/content", response_model=ModuleReportContentResponse)
@handle_route_errors("read module report file")
def read_module_report_content(
    module_id: int,
    path: str,
    user: User = Depends(require_module_owner),
    db: Session = Depends(get_db),
):
    """Read one report file (workspace-relative under the declared reports dir)"""
    return ModuleReportsService(db).read_content(module_id, path)


# ============================================================================
# Cancel
# ============================================================================

@router.post("/{module_id}/cancel")
@handle_route_errors("cancel deployment")
def cancel_deployment(module_id: int, user: User = Depends(require_module_owner), db: Session = Depends(get_db)):
    """Cancel a running deployment operation"""
    svc = ProjectModuleService(db)
    result = svc.cancel_operation(module_id)
    db.commit()
    return result


# ============================================================================
# Deploy & Retry
# ============================================================================

@router.post("/{module_id}/deploy")
@handle_route_errors("deploy module")
def deploy_module(module_id: int, request: Request, user: User = Depends(require_module_owner), db: Session = Depends(get_db)):
    """Trigger deployment for a specific module using task queue"""
    svc = ProjectModuleService(db)
    result = svc.deploy_module(module_id, triggered_by=get_username_from_request(request))
    db.commit()
    return result


@router.post("/{module_id}/retry")
@handle_route_errors("retry deployment")
def retry_deployment(module_id: int, request: Request, user: User = Depends(require_module_owner), db: Session = Depends(get_db)):
    """Retry a failed deployment using task queue"""
    svc = ProjectModuleService(db)
    result = svc.retry_deployment(module_id, triggered_by=get_username_from_request(request))
    db.commit()
    return result
