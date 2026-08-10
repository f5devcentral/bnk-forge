"""
Project routes — thin controller layer (IMP-002).

All business logic lives in services.project_service.ProjectService.
Each route handler instantiates the service with the current db session,
delegates to a single service method, and returns the result.
"""


# Database
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

# Error handling decorator
from core.errors import handle_route_errors
from database import get_db

# Models (only needed for get_active_project which stays inline)
from models import Project, User

# Auth dependencies
from routes.auth import require_operator, require_project_owner, require_viewer

# Pydantic schemas
from schemas.models import ProjectCreate, ProjectUpdate
from schemas.projects import (
    ActiveProjectResponse,
    ProjectDependenciesResponse,
    ProjectDependencyItem,
    ProjectDetailResponse,
    ProjectListResponse,
    ProjectMutationResponse,
    SuccessResponse,
    TransferOwnershipRequest,
    TransferOwnershipResponse,
)

# Service
from services.project_service import ProjectService

# ============================================================
# FastAPI Router Setup
# ============================================================

router = APIRouter(prefix="/api/projects", tags=["projects"])

# ============================================================
# Route Handlers
# ============================================================

@router.get("", dependencies=[Depends(require_viewer)], response_model=ProjectListResponse)
@handle_route_errors("list projects")
def list_projects(db: Session = Depends(get_db)):
    """List all projects"""
    svc = ProjectService(db)
    return svc.list_projects()


@router.post("", response_model=ProjectMutationResponse)
@handle_route_errors("create project")
def create_project(
    project_data: ProjectCreate,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Create a new project (v2 - no git integration). Owner set to current user."""
    svc = ProjectService(db)
    return svc.create_project(project_data, user_id=user.id)


@router.get("/active", dependencies=[Depends(require_viewer)], response_model=ActiveProjectResponse)
@handle_route_errors("get active project")
def get_active_project(db: Session = Depends(get_db)):
    """Get currently active project"""
    project = db.query(Project).filter(Project.is_active).first()

    if not project:
        return {
            "active": False,
            "project": None,
            "message": "No active project"
        }

    return {
        "active": True,
        "project": {
            "id": project.id,
            "name": project.name,
            "color": project.color,
            "icon": project.icon
        }
    }


@router.get("/{project_id}", dependencies=[Depends(require_viewer)], response_model=ProjectDetailResponse)
@handle_route_errors("get project")
def get_project(project_id: int, db: Session = Depends(get_db)):
    """Get project details"""
    svc = ProjectService(db)
    return svc.get_project_detail(project_id)


@router.put("/{project_id}", response_model=ProjectMutationResponse)
@handle_route_errors("update project")
def update_project(project_id: int, project_data: ProjectUpdate, user: User = Depends(require_project_owner), db: Session = Depends(get_db)):
    """Update project (owner or admin only)."""
    svc = ProjectService(db)
    return svc.update_project(project_id, project_data)


@router.put("/{project_id}/dependencies", response_model=ProjectDependenciesResponse)
@handle_route_errors("update project dependencies")
def update_project_dependencies(
    project_id: int,
    dependencies: list[ProjectDependencyItem],
    user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
):
    """
    Update cross-project dependencies for a project.

    This allows one project to depend on outputs from another project.
    Example: A BNK application project depends on outputs from a base infrastructure project.

    Request body format:
    [
        {
            "project_id": 1,
            "outputs": ["eks_cluster_endpoint", "eks_cluster_name", "kubeconfig"]
        }
    ]
    """
    svc = ProjectService(db)
    return svc.update_dependencies(project_id, dependencies)


@router.delete("/{project_id}", response_model=SuccessResponse)
@handle_route_errors("delete project")
def delete_project(project_id: int, force: bool = False, user: User = Depends(require_project_owner), db: Session = Depends(get_db)):
    """Delete project

    Args:
        project_id: ID of project to delete
        force: Force delete even if it's the active/only project (use with caution)
    """
    svc = ProjectService(db)
    return svc.delete_project(project_id, force=force)


@router.post("/{project_id}/activate", response_model=ProjectMutationResponse)
@handle_route_errors("activate project")
def activate_project(project_id: int, user: User = Depends(require_project_owner), db: Session = Depends(get_db)):
    """Set project as active"""
    svc = ProjectService(db)
    return svc.set_active_project(project_id)


@router.post("/{project_id}/transfer", response_model=TransferOwnershipResponse)
@handle_route_errors("transfer project ownership")
def transfer_project_ownership(
    project_id: int,
    body: TransferOwnershipRequest,
    user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
):
    """MU-009: Transfer project ownership to another user.

    Only the current owner or an admin can transfer ownership.
    """
    svc = ProjectService(db)
    return svc.transfer_ownership(project_id, body.new_owner_id)
