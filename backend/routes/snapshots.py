"""
Config Snapshot API routes (UX-011).

Endpoints:
  - GET    /api/projects/{project_id}/snapshots         — List snapshots for a project
  - POST   /api/projects/{project_id}/snapshots         — Create manual snapshot
  - GET    /api/snapshots/{id}                          — Get snapshot detail
  - POST   /api/snapshots/{id}/restore                  — Restore from snapshot
  - POST   /api/snapshots/diff                          — Diff two snapshots
  - DELETE /api/snapshots/{id}                          — Delete a snapshot
"""

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.errors import BadRequestError, NotFoundError, handle_route_errors
from database import get_db
from models import User
from routes.auth import require_admin, require_project_owner, require_viewer
from services.snapshot_service import SnapshotService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["snapshots"])


# ============================================================================
# Request/Response Models
# ============================================================================

class CreateSnapshotRequest(BaseModel):
    label: str | None = None
    cluster_id: int | None = None


class DiffSnapshotsRequest(BaseModel):
    snapshot_a_id: int
    snapshot_b_id: int


# ============================================================================
# Project-Scoped Endpoints
# ============================================================================

@router.get("/projects/{project_id}/snapshots", dependencies=[Depends(require_viewer)])
@handle_route_errors("list project snapshots")
def list_project_snapshots(
    project_id: int,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List config snapshots for a project, newest first."""
    service = SnapshotService(db)
    snapshots, total = service.list_snapshots(project_id, limit=limit, offset=offset)
    return {
        "snapshots": [
            {
                "id": s.id,
                "project_id": s.project_id,
                "cluster_id": s.cluster_id,
                "trigger": s.trigger,
                "label": s.label,
                "created_by": s.created_by,
                "resource_count": s.resource_count,
                "module_count": s.module_count,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in snapshots
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/projects/{project_id}/snapshots")
@handle_route_errors("create snapshot")
def create_snapshot(
    project_id: int,
    request: CreateSnapshotRequest,
    current_user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
):
    """Create a manual config snapshot for a project."""
    try:
        service = SnapshotService(db)
        username = current_user.username if hasattr(current_user, "username") else current_user.get("sub", "unknown")
        snapshot = service.create_snapshot(
            project_id=project_id,
            trigger="manual",
            label=request.label,
            username=username,
            cluster_id=request.cluster_id,
        )
    except ValueError:
        raise NotFoundError("project", project_id)

    db.commit()
    return {
        "id": snapshot.id,
        "project_id": snapshot.project_id,
        "cluster_id": snapshot.cluster_id,
        "trigger": snapshot.trigger,
        "label": snapshot.label,
        "created_by": snapshot.created_by,
        "resource_count": snapshot.resource_count,
        "module_count": snapshot.module_count,
        "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
        "message": "Snapshot created successfully",
    }


# ============================================================================
# Snapshot-Scoped Endpoints
# ============================================================================

@router.get("/snapshots/{snapshot_id}", dependencies=[Depends(require_viewer)])
def get_snapshot(
    snapshot_id: int,
    db: Session = Depends(get_db),
):
    """Get full snapshot detail including config data."""
    service = SnapshotService(db)
    snapshot = service.get_snapshot(snapshot_id)
    if not snapshot:
        raise NotFoundError("snapshot", snapshot_id)

    return {
        "id": snapshot.id,
        "project_id": snapshot.project_id,
        "cluster_id": snapshot.cluster_id,
        "trigger": snapshot.trigger,
        "label": snapshot.label,
        "created_by": snapshot.created_by,
        "resource_count": snapshot.resource_count,
        "module_count": snapshot.module_count,
        "config_data": snapshot.config_data,
        "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
    }


@router.post("/snapshots/{snapshot_id}/restore", dependencies=[Depends(require_admin)])
@handle_route_errors("restore snapshot")
def restore_snapshot(
    snapshot_id: int,
    db: Session = Depends(get_db),
):
    """
    Restore cluster config from a snapshot.

    This re-applies all CRD resources from the snapshot to the cluster
    using server-side apply. Module state is not modified.

    Requires admin role since this is a destructive operation.
    """
    try:
        service = SnapshotService(db)
        result = service.restore_snapshot(snapshot_id)
        return result
    except ValueError as e:
        if "not found" in str(e).lower():
            raise NotFoundError("snapshot", snapshot_id)
        raise BadRequestError(str(e), code="RESTORE_ERROR")


@router.post("/snapshots/diff", dependencies=[Depends(require_viewer)])
@handle_route_errors("diff snapshots")
def diff_snapshots(
    request: DiffSnapshotsRequest,
    db: Session = Depends(get_db),
):
    """Compare two snapshots and return differences."""
    try:
        service = SnapshotService(db)
        result = service.diff_snapshots(request.snapshot_a_id, request.snapshot_b_id)
        return result
    except ValueError as e:
        raise BadRequestError(str(e), code="DIFF_ERROR")


@router.delete("/snapshots/{snapshot_id}", dependencies=[Depends(require_admin)])
def delete_snapshot(
    snapshot_id: int,
    db: Session = Depends(get_db),
):
    """Delete a config snapshot. Requires admin role."""
    service = SnapshotService(db)
    deleted = service.delete_snapshot(snapshot_id)
    if not deleted:
        raise NotFoundError("snapshot", snapshot_id)

    db.commit()
    return {"message": f"Snapshot {snapshot_id} deleted", "id": snapshot_id}
