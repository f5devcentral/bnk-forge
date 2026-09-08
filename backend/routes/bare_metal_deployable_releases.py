"""API routes for BNK deployable releases — version matrix management (ADR-478)."""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from routes.auth import require_admin, require_viewer
from schemas.bare_metal import DeployableReleaseListResponse, DeployableReleaseResponse

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/bare-metal/deployable-releases",
    tags=["bare-metal"],
)


class ActivateReleaseRequest(BaseModel):
    is_active: bool


@router.get("", response_model=DeployableReleaseListResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("list deployable releases")
def list_deployable_releases(
    db: Session = Depends(get_db),
) -> DeployableReleaseListResponse:
    """List all BNK deployable releases."""
    from services.bare_metal.version_profiles import BnkDeployableReleaseService
    return BnkDeployableReleaseService(db).list_profiles()


@router.get("/{release_id}", response_model=DeployableReleaseResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("get deployable release")
def get_deployable_release(
    release_id: int,
    db: Session = Depends(get_db),
) -> DeployableReleaseResponse:
    """Get a specific deployable release."""
    from services.bare_metal.version_profiles import BnkDeployableReleaseService
    return BnkDeployableReleaseService(db).get_profile(release_id)


@router.post("/{release_id}/activate", response_model=DeployableReleaseResponse, dependencies=[Depends(require_admin)])
@handle_route_errors("activate deployable release")
def activate_deployable_release(
    release_id: int,
    body: ActivateReleaseRequest,
    db: Session = Depends(get_db),
) -> DeployableReleaseResponse:
    """Set is_active on a deployable release."""
    from services.bare_metal.version_profiles import BnkDeployableReleaseService
    svc = BnkDeployableReleaseService(db)
    result = svc.set_active(release_id, body.is_active)
    db.commit()
    return result


@router.post("/{release_id}/set-default", response_model=DeployableReleaseResponse, dependencies=[Depends(require_admin)])
@handle_route_errors("set default deployable release")
def set_default_deployable_release(
    release_id: int,
    db: Session = Depends(get_db),
) -> DeployableReleaseResponse:
    """Mark this release as the default, clearing is_default on all others."""
    from services.bare_metal.version_profiles import BnkDeployableReleaseService
    svc = BnkDeployableReleaseService(db)
    result = svc.set_default(release_id)
    db.commit()
    return result
