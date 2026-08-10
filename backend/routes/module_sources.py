"""
Module Sources API endpoints.

Thin HTTP handlers delegating to ModuleSourceService.
"""

import logging
from datetime import datetime
from typing import Any, TypeAlias

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from models import User
from routes.auth import get_current_user, require_operator, require_viewer
from services.module_source_service import ModuleSourceService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/module-sources", tags=["module-sources"])

JSONValue: TypeAlias = dict[str, Any] | list[Any] | str | int | float | bool | None
JSONMetadata: TypeAlias = dict[str, JSONValue]


# ============================================================================
# Pydantic Schemas
# ============================================================================

class ModuleSourceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    source_type: str = Field(..., description="git, registry, or builtin")
    url: str = Field(..., min_length=1, max_length=500)
    branch: str | None = Field(default="main", max_length=100)
    git_ref: str | None = Field(default=None, max_length=100)
    auth_type: str | None = Field(default=None, description="none, token, ssh, or basic")
    auth_token: str | None = Field(default=None, description="Will be encrypted server-side")
    credential_type: str | None = Field(
        default=None,
        description=(
            "none, github_app, gitlab_project_token, gitlab_group_token, gitlab_deploy_token, oauth_token, "
            "ssh_deploy_key, service_account_token, legacy_pat"
        ),
    )
    credential_scope: str | None = Field(default=None, max_length=255)
    credential_capabilities: list[str] | None = Field(default=None)
    credential_metadata: JSONMetadata | None = Field(default=None)
    credential_expires_at: datetime | None = Field(default=None)
    credential_last_rotated_at: datetime | None = Field(default=None)
    credential_validation_status: str | None = Field(default=None)
    credential_last_validated_at: datetime | None = Field(default=None)
    credential_validation_error: str | None = Field(default=None)
    auto_sync: bool = Field(default=False)
    sync_interval_hours: int = Field(default=24, ge=1, le=168)
    description: str | None = None
    make_default: bool = Field(default=False)


class ModuleSourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    url: str | None = Field(default=None, min_length=1, max_length=500)
    branch: str | None = Field(default=None, max_length=100)
    git_ref: str | None = Field(default=None, max_length=100)
    auth_type: str | None = Field(default=None)
    auth_token: str | None = Field(default=None, description="Will be encrypted server-side")
    credential_type: str | None = Field(default=None)
    credential_scope: str | None = Field(default=None, max_length=255)
    credential_capabilities: list[str] | None = Field(default=None)
    credential_metadata: JSONMetadata | None = Field(default=None)
    credential_expires_at: datetime | None = Field(default=None)
    credential_last_rotated_at: datetime | None = Field(default=None)
    credential_validation_status: str | None = Field(default=None)
    credential_last_validated_at: datetime | None = Field(default=None)
    credential_validation_error: str | None = Field(default=None)
    auto_sync: bool | None = None
    sync_interval_hours: int | None = Field(default=None, ge=1, le=168)
    is_active: bool | None = None
    description: str | None = None
    make_default: bool | None = None


class ModuleSourceResponse(BaseModel):
    id: int
    name: str
    source_type: str
    url: str
    branch: str | None
    git_ref: str | None
    auth_type: str | None
    credential_type: str
    credential_scope: str | None
    credential_capabilities: list[str] | None
    credential_metadata: JSONMetadata | None
    credential_recommendation: JSONMetadata | None = None
    credential_expires_at: datetime | None
    credential_last_rotated_at: datetime | None
    credential_validation_status: str | None
    credential_last_validated_at: datetime | None
    credential_validation_error: str | None
    has_secret: bool
    last_synced_at: datetime | None
    sync_status: str
    sync_error: str | None
    module_count: int
    is_active: bool
    is_default: bool
    auto_sync: bool
    sync_interval_hours: int
    description: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ModuleSourceCredentialValidationResponse(BaseModel):
    success: bool
    source_id: int
    source_name: str
    credential_type: str
    credential_scope: str | None
    credential_validation_status: str
    credential_last_validated_at: datetime | None
    provider: str
    validation: dict[str, JSONValue]


# ============================================================================
# Endpoints
# ============================================================================

@router.get("", response_model=list[ModuleSourceResponse], dependencies=[Depends(require_viewer)])
def list_module_sources(source_type: str | None = None, is_active: bool | None = None,
                        db: Session = Depends(get_db)):
    """List all module sources."""
    return ModuleSourceService(db).list_sources(source_type=source_type, is_active=is_active)


@router.get("/{source_id}", response_model=ModuleSourceResponse, dependencies=[Depends(require_viewer)])
def get_module_source(source_id: int, db: Session = Depends(get_db)):
    """Get a specific module source by ID"""
    return ModuleSourceService(db).get_source(source_id)


@router.post("", response_model=ModuleSourceResponse, dependencies=[Depends(require_operator)])
@handle_route_errors("create module source")
def create_module_source(source_data: ModuleSourceCreate, db: Session = Depends(get_db)):
    """Create a new module source."""
    result = ModuleSourceService(db).create_source(source_data)
    db.commit()
    return result


@router.put("/{source_id}", response_model=ModuleSourceResponse, dependencies=[Depends(require_operator)])
@handle_route_errors("update module source")
def update_module_source(source_id: int, source_data: ModuleSourceUpdate, db: Session = Depends(get_db)):
    """Update an existing module source"""
    result = ModuleSourceService(db).update_source(source_id, source_data)
    db.commit()
    return result


@router.delete("/{source_id}", dependencies=[Depends(require_operator)])
@handle_route_errors("delete module source")
def delete_module_source(source_id: int, db: Session = Depends(get_db)):
    """Delete a module source and all its catalog modules."""
    result = ModuleSourceService(db).delete_source(source_id)
    db.commit()
    return result


@router.get("/{source_id}/modules", dependencies=[Depends(require_viewer)])
def list_source_modules(source_id: int, db: Session = Depends(get_db)):
    """List all modules from a specific source."""
    return ModuleSourceService(db).list_source_modules(source_id)


@router.post("/{source_id}/sync", dependencies=[Depends(require_operator)])
def sync_module_source(source_id: int, db: Session = Depends(get_db)):
    """Sync a module source (parse and import modules)."""
    return ModuleSourceService(db).sync_source(source_id)


@router.post(
    "/{source_id}/validate-credentials",
    response_model=ModuleSourceCredentialValidationResponse,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("validate module source credentials")
def validate_module_source_credentials(
    source_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Validate provider credentials for a module source."""
    result = ModuleSourceService(db).validate_source_credentials(source_id, user_obj=user)
    db.commit()
    return result
