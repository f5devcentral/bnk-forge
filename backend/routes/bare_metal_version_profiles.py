"""API routes for BNK version profiles — version matrix management."""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from routes.auth import require_viewer
from schemas.bare_metal import BnkVersionProfileListResponse, BnkVersionProfileResponse

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/bare-metal/version-profiles",
    tags=["bare-metal"],
)


@router.get("", response_model=BnkVersionProfileListResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("list version profiles")
def list_version_profiles(
    db: Session = Depends(get_db),
) -> BnkVersionProfileListResponse:
    """List all BNK version profiles."""
    from services.bare_metal.version_profiles import BnkVersionProfileService
    return BnkVersionProfileService(db).list_profiles()


@router.get("/{profile_id}", response_model=BnkVersionProfileResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("get version profile")
def get_version_profile(
    profile_id: int,
    db: Session = Depends(get_db),
) -> BnkVersionProfileResponse:
    """Get a specific version profile."""
    from services.bare_metal.version_profiles import BnkVersionProfileService
    return BnkVersionProfileService(db).get_profile(profile_id)
