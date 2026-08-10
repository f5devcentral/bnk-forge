"""
Deployment Stacks API endpoints.

Thin HTTP handlers delegating to StackService.
"""

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from models import User
from routes.auth import require_operator, require_project_owner, require_viewer
from schemas.stacks import (
    DuplicateTemplateRequest,
    ImportedBlueprintAddToProjectRequest,
    ImportedBlueprintProjectCreateRequest,
    ImportedBlueprintProjectCreateResponse,
    ImportTemplateRequest,
    PublishTemplateRequest,
    SaveAsTemplateRequest,
    StackInstanceCreate,
    StackInstanceDetailResponse,
    StackInstanceResponse,
    StackPreviewResponse,
    StackStatusResponse,
    StackTemplateListResponse,
    StackTemplateResponse,
)
from services.imported_blueprint_service import ImportedBlueprintService
from services.stack_service import StackService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stacks", tags=["stacks"])


# ============================================================================
# Stack Templates Endpoints
# ============================================================================

@router.get("/templates", response_model=list[StackTemplateListResponse], dependencies=[Depends(require_viewer)])
def list_stack_templates(
    category: str | None = None,
    cloud_provider: str | None = None,
    tags: str | None = None,
    is_featured: bool | None = None,
    created_by: str | None = None,
    is_public: bool | None = None,
    db: Session = Depends(get_db)
):
    """List all available stack templates."""
    svc = StackService(db)
    return svc.list_templates(
        category=category, cloud_provider=cloud_provider, tags=tags,
        is_featured=is_featured, created_by=created_by, is_public=is_public,
    )


@router.get("/templates/{slug}", response_model=StackTemplateResponse, dependencies=[Depends(require_viewer)])
def get_stack_template(slug: str, db: Session = Depends(get_db)):
    """Get stack template details by slug."""
    svc = StackService(db)
    return svc.get_template(slug)


@router.get("/templates/{slug}/preview", response_model=StackPreviewResponse, dependencies=[Depends(require_viewer)])
def preview_stack_template(slug: str, db: Session = Depends(get_db)):
    """Preview what modules will be created from this stack template."""
    svc = StackService(db)
    return svc.preview_template(slug)


@router.get("/templates/{slug}/required-inputs", dependencies=[Depends(require_viewer)])
def get_stack_required_inputs(
    slug: str,
    project_id: int | None = Query(None, description="Project ID for pre-populating defaults from project context"),
    bare_metal_host_id: int | None = Query(None, description="Bare-metal host ID for version profile and DPU context"),
    db: Session = Depends(get_db),
):
    """Get all required user inputs for a stack template."""
    if slug.startswith("release-"):
        release_id = int(slug.removeprefix("release-"))
        return ImportedBlueprintService(db).get_required_inputs(release_id)
    svc = StackService(db)
    return svc.get_required_inputs(slug, project_id=project_id, bare_metal_host_id=bare_metal_host_id)


@router.get("/releases/{release_id}", response_model=StackTemplateResponse, dependencies=[Depends(require_viewer)])
def get_imported_blueprint_release(release_id: int, db: Session = Depends(get_db)):
    """Get imported blueprint release details in stack-template-compatible shape."""
    return ImportedBlueprintService(db).get_template_like(release_id)


@router.get("/releases/{release_id}/preview", response_model=StackPreviewResponse, dependencies=[Depends(require_viewer)])
def preview_imported_blueprint_release(release_id: int, db: Session = Depends(get_db)):
    """Preview modules for an imported blueprint release."""
    return ImportedBlueprintService(db).preview_release(release_id)


@router.get("/releases/{release_id}/required-inputs", dependencies=[Depends(require_viewer)])
def get_imported_blueprint_required_inputs(release_id: int, db: Session = Depends(get_db)):
    """Get required blueprint-level inputs for an imported blueprint release."""
    return ImportedBlueprintService(db).get_required_inputs(release_id)


@router.post(
    "/releases/{release_id}/projects",
    response_model=ImportedBlueprintProjectCreateResponse,
)
@handle_route_errors("create project from imported blueprint release")
def create_project_from_imported_blueprint_release(
    release_id: int,
    body: ImportedBlueprintProjectCreateRequest,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    result = ImportedBlueprintService(db).create_project_from_release(release_id, body, user_id=user.id)
    db.commit()
    return result


@router.post(
    "/projects/{project_id}/releases/{release_id}",
    response_model=ImportedBlueprintProjectCreateResponse,
)
@handle_route_errors("add imported blueprint release to existing project")
def add_imported_blueprint_to_project(
    project_id: int,
    release_id: int,
    body: ImportedBlueprintAddToProjectRequest,
    user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
):
    result = ImportedBlueprintService(db).add_release_to_project(release_id, project_id, body, user_id=user.id)
    db.commit()
    return result


@router.get("/templates/{slug}/check-prerequisites", dependencies=[Depends(require_viewer)])
def check_stack_prerequisites(
    slug: str,
    project_id: int = Query(..., description="Project ID to check secrets against"),
    db: Session = Depends(get_db),
):
    """
    S19-001: Check if a project satisfies a stack template's prerequisites.

    Returns which required project secrets are present vs missing.
    """
    svc = StackService(db)
    return svc.check_prerequisites(slug, project_id)


# ============================================================================
# Stack Instances Endpoints
# ============================================================================

@router.post("/projects/{project_id}/stacks", response_model=StackInstanceResponse)
@handle_route_errors("create stack instance")
def create_stack_instance(project_id: int, stack_data: StackInstanceCreate, user: User = Depends(require_project_owner), db: Session = Depends(get_db)):
    """Create a new stack instance in a project."""
    svc = StackService(db)
    result = svc.create_instance(project_id, stack_data.template_id, stack_data.name, stack_data.variables)
    db.commit()
    return result


@router.get("/projects/{project_id}/stacks", response_model=list[StackInstanceResponse], dependencies=[Depends(require_viewer)])
def list_stack_instances(project_id: int, status: str | None = None, db: Session = Depends(get_db)):
    """List all stack instances in a project."""
    svc = StackService(db)
    return svc.list_instances(project_id, status=status)


@router.get("/projects/{project_id}/stacks/{stack_id}", response_model=StackInstanceDetailResponse, dependencies=[Depends(require_viewer)])
def get_stack_instance(project_id: int, stack_id: int, db: Session = Depends(get_db)):
    """Get stack instance details."""
    svc = StackService(db)
    return svc.get_instance(project_id, stack_id)


@router.post("/projects/{project_id}/stacks/{stack_id}/deploy")
def deploy_stack(project_id: int, stack_id: int, user: User = Depends(require_project_owner), db: Session = Depends(get_db)):
    """Start stack deployment."""
    svc = StackService(db)
    return svc.deploy_stack(project_id, stack_id)


@router.post("/projects/{project_id}/stacks/{stack_id}/run-deploy")
@handle_route_errors("run stack deployment")
def run_stack_deployment(project_id: int, stack_id: int, user: User = Depends(require_project_owner), db: Session = Depends(get_db)):
    """Deploy all stack modules (init + apply)."""
    svc = StackService(db)
    result = svc.run_deploy(project_id, stack_id)
    db.commit()
    return result


@router.get("/projects/{project_id}/stacks/{stack_id}/status", response_model=StackStatusResponse, dependencies=[Depends(require_viewer)])
def get_stack_status(project_id: int, stack_id: int, db: Session = Depends(get_db)):
    """Get deployment progress and status."""
    svc = StackService(db)
    return svc.get_stack_status(project_id, stack_id)


@router.delete("/projects/{project_id}/stacks/{stack_id}")
@handle_route_errors("delete stack instance")
def delete_stack_instance(
    project_id: int,
    stack_id: int,
    force: bool = Query(False, description="Force delete even if modules still exist"),
    user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
):
    """Delete a stack instance."""
    svc = StackService(db)
    result = svc.delete_stack(project_id, stack_id, force=force)
    db.commit()
    return result


# ============================================================================
# Phase 4: Custom Stacks & Marketplace Endpoints
# ============================================================================

@router.post("/projects/{project_id}/save-as-template", response_model=StackTemplateResponse)
@handle_route_errors("save project as template")
def save_project_as_template(project_id: int, template_data: SaveAsTemplateRequest, user: User = Depends(require_project_owner), db: Session = Depends(get_db)):
    """Save a project's current module configuration as a stack template."""
    svc = StackService(db)
    result = svc.save_project_as_template(project_id, template_data.model_dump())
    db.commit()
    return result


@router.post("/templates/{template_id}/duplicate", response_model=StackTemplateResponse, dependencies=[Depends(require_operator)])
@handle_route_errors("duplicate template")
def duplicate_template(template_id: int, duplicate_data: DuplicateTemplateRequest, db: Session = Depends(get_db)):
    """Duplicate an existing stack template."""
    svc = StackService(db)
    result = svc.duplicate_template(template_id, duplicate_data.model_dump())
    db.commit()
    return result


@router.get("/templates/{template_id}/export", dependencies=[Depends(require_viewer)])
def export_template(template_id: int, db: Session = Depends(get_db)):
    """Export a stack template as JSON."""
    svc = StackService(db)
    return svc.export_template(template_id)


@router.post("/templates/import", response_model=StackTemplateResponse, dependencies=[Depends(require_operator)])
@handle_route_errors("import template")
def import_template(import_data: ImportTemplateRequest, db: Session = Depends(get_db)):
    """Import a stack template from JSON."""
    svc = StackService(db)
    result = svc.import_template(import_data.template_json)
    db.commit()
    return result


@router.patch("/templates/{template_id}/publish", response_model=StackTemplateResponse, dependencies=[Depends(require_operator)])
@handle_route_errors("publish template")
def publish_template(template_id: int, publish_data: PublishTemplateRequest, db: Session = Depends(get_db)):
    """Publish or unpublish a stack template."""
    svc = StackService(db)
    result = svc.publish_template(template_id, publish_data.is_public)
    db.commit()
    return result
