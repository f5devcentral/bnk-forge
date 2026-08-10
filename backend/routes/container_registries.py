"""
API routes for container registries — first-class OCI registry access management.

A container registry is an access method for pulling/pushing artifacts (images,
helm charts, manifests) used by container-engine deployments. Mirrors SSH
credentials: global, name-unique, encrypted secrets never serialized.

Standalone types (ghcr/quay/far) carry their own secret and are testable now.
Derived types (ecr/acr/icr/gar) reference a CloudCredentialTemplate; their /test
returns a structured 'not yet implemented' until the supply-chain phase.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from models import User
from routes.auth import require_operator, require_viewer
from services.container_registry_service import ContainerRegistryService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/container-registries", tags=["container-registries"])


# ============================================================================
# Pydantic Schemas
# ============================================================================

class ContainerRegistryCreate(BaseModel):
    name: str
    description: str | None = None
    type: str  # ghcr | quay | ecr | acr | icr | gar | far
    registry_host: str
    # Standalone (ghcr/quay) secret
    username: str | None = None
    token: str | None = None
    # Standalone (far) secret — base64 *.tgz auth key or raw service-account JSON
    far_service_account: str | None = None
    # Derived (ecr/acr/icr/gar) — references a cloud credential template
    credential_template_id: int | None = None


class ContainerRegistryUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    type: str | None = None
    registry_host: str | None = None
    username: str | None = None
    token: str | None = None
    far_service_account: str | None = None
    credential_template_id: int | None = None


class ContainerRegistryResponse(BaseModel):
    id: int
    name: str
    description: str | None
    type: str
    registry_host: str
    username: str | None
    has_token: bool
    has_far_service_account: bool
    credential_template_id: int | None
    created_at: datetime
    updated_at: datetime
    created_by: str | None = None
    last_test_status: str | None = None
    last_test_at: datetime | None = None
    last_test_message: str | None = None

    class Config:
        from_attributes = True


# ============================================================================
# CRUD Endpoints
# ============================================================================

@router.get("", response_model=list[ContainerRegistryResponse], dependencies=[Depends(require_viewer)])
def list_container_registries(db: Session = Depends(get_db)):
    """List all container registries."""
    return ContainerRegistryService(db).list_registries()


@router.get("/{registry_id}", response_model=ContainerRegistryResponse, dependencies=[Depends(require_viewer)])
def get_container_registry(registry_id: int, db: Session = Depends(get_db)):
    """Get a specific container registry by ID."""
    return ContainerRegistryService(db).get_registry(registry_id)


@router.post("", response_model=ContainerRegistryResponse, status_code=201)
@handle_route_errors("create container registry")
def create_container_registry(
    data: ContainerRegistryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    """Create a new container registry."""
    result = ContainerRegistryService(db).create_registry(data, created_by=current_user.username)
    db.commit()
    return result


@router.put("/{registry_id}", response_model=ContainerRegistryResponse, dependencies=[Depends(require_operator)])
@handle_route_errors("update container registry")
def update_container_registry(registry_id: int, data: ContainerRegistryUpdate, db: Session = Depends(get_db)):
    """Update a container registry."""
    result = ContainerRegistryService(db).update_registry(registry_id, data)
    db.commit()
    return result


@router.delete("/{registry_id}", status_code=204, dependencies=[Depends(require_operator)])
@handle_route_errors("delete container registry")
def delete_container_registry(registry_id: int, db: Session = Depends(get_db)):
    """Delete a container registry."""
    ContainerRegistryService(db).delete_registry(registry_id)
    db.commit()
    return None


# ============================================================================
# Testing Endpoint
# ============================================================================

@router.post("/{registry_id}/test", dependencies=[Depends(require_operator)])
@handle_route_errors("test container registry")
def test_container_registry(registry_id: int, db: Session = Depends(get_db)):
    """Test registry connectivity using this registry's credentials.

    Persists the outcome on the row so subsequent fetches return it. Derived
    registry types return a structured 'not yet implemented' result.
    """
    return ContainerRegistryService(db).test_registry(registry_id)
