"""
Drift Detection API routes.

Thin HTTP handlers delegating to DriftService.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from models import User
from routes.auth import require_module_owner, require_project_owner, require_viewer
from schemas.drift import (
    ClusterDriftStatusResponse,
    DriftCheckResponse,
    DriftSettingsRequest,
    DriftSettingsResponse,
    DriftSummaryResponse,
    TriggerDriftCheckRequest,
)
from services.drift_service import DriftService

router = APIRouter()


# ============================================================================
# Drift Settings Endpoints
# ============================================================================

@router.get("/api/projects/{project_id}/drift/settings", response_model=DriftSettingsResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("get drift settings")
def get_drift_settings(project_id: int, db: Session = Depends(get_db)):
    """Get drift detection settings for a project."""
    svc = DriftService(db)
    result = svc.get_settings(project_id)
    db.commit()
    return result


@router.put("/api/projects/{project_id}/drift/settings", response_model=DriftSettingsResponse)
@handle_route_errors("update drift settings")
def update_drift_settings(
    project_id: int,
    settings_data: DriftSettingsRequest,
    user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
):
    """Update drift detection settings for a project."""
    svc = DriftService(db)
    result = svc.update_settings(project_id, settings_data.model_dump())
    db.commit()
    return result


@router.post("/api/projects/{project_id}/drift/enable", response_model=DriftSettingsResponse)
@handle_route_errors("enable drift detection")
def enable_drift_detection(project_id: int, user: User = Depends(require_project_owner), db: Session = Depends(get_db)):
    """Enable drift detection for a project."""
    svc = DriftService(db)
    result = svc.enable_drift(project_id)
    db.commit()
    return result


@router.post("/api/projects/{project_id}/drift/disable", response_model=DriftSettingsResponse)
@handle_route_errors("disable drift detection")
def disable_drift_detection(project_id: int, user: User = Depends(require_project_owner), db: Session = Depends(get_db)):
    """Disable drift detection for a project."""
    svc = DriftService(db)
    result = svc.disable_drift(project_id)
    db.commit()
    return result


# ============================================================================
# Drift Check Endpoints
# ============================================================================

@router.get("/api/projects/{project_id}/drift/checks", response_model=list[DriftCheckResponse], dependencies=[Depends(require_viewer)])
def get_drift_checks(
    project_id: int,
    module_id: int | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """Get drift check history for a project."""
    svc = DriftService(db)
    return svc.get_checks(project_id, module_id=module_id, status=status, limit=limit, offset=offset)


@router.get("/api/drift/checks/{check_id}", response_model=DriftCheckResponse, dependencies=[Depends(require_viewer)])
def get_drift_check_details(check_id: int, db: Session = Depends(get_db)):
    """Get detailed information about a specific drift check."""
    svc = DriftService(db)
    return svc.get_check_details(check_id)


@router.post("/api/projects/{project_id}/drift/check-now")
@handle_route_errors("trigger project drift check")
def trigger_project_drift_check(
    project_id: int,
    request: TriggerDriftCheckRequest,
    user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
):
    """Trigger an immediate drift check for project modules."""
    svc = DriftService(db)
    result = svc.trigger_project_check(project_id, module_ids=request.module_ids)
    db.commit()
    return result


@router.post("/api/project-modules/{module_id}/drift/check-now")
@handle_route_errors("trigger module drift check")
def trigger_module_drift_check(module_id: int, user: User = Depends(require_module_owner), db: Session = Depends(get_db)):
    """Trigger an immediate drift check for a specific module."""
    svc = DriftService(db)
    result = svc.trigger_module_check(module_id)
    db.commit()
    return result


# ============================================================================
# Drift Dashboard Endpoints
# ============================================================================

@router.get("/api/drift/summary", response_model=DriftSummaryResponse, dependencies=[Depends(require_viewer)])
def get_global_drift_summary(db: Session = Depends(get_db)):
    """Get global drift summary across all projects."""
    svc = DriftService(db)
    return svc.get_global_summary()


@router.get("/api/drift/recent", response_model=list[DriftCheckResponse], dependencies=[Depends(require_viewer)])
def get_recent_drifted(limit: int = Query(10, ge=1, le=50), db: Session = Depends(get_db)):
    """Get recent drift checks where drift was detected, across all projects."""
    svc = DriftService(db)
    return svc.get_recent_drifted(limit=limit)


@router.get("/api/projects/{project_id}/drift/summary", response_model=DriftSummaryResponse, dependencies=[Depends(require_viewer)])
def get_project_drift_summary(project_id: int, db: Session = Depends(get_db)):
    """Get drift summary for a specific project."""
    svc = DriftService(db)
    return svc.get_project_summary(project_id)


@router.get("/api/drift/stats", dependencies=[Depends(require_viewer)])
def get_drift_stats(
    project_id: int | None = Query(None),
    days: int | None = Query(None, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Get drift statistics, optionally filtered by project and time range."""
    svc = DriftService(db)
    return svc.get_stats(project_id=project_id, days=days)


@router.get("/api/clusters/{cluster_id}/drift/status", response_model=ClusterDriftStatusResponse, dependencies=[Depends(require_viewer)])
def get_cluster_drift_status(cluster_id: int, db: Session = Depends(get_db)):
    """Get drift status for all modules deployed to a cluster's project."""
    svc = DriftService(db)
    return svc.get_cluster_drift_status(cluster_id)
