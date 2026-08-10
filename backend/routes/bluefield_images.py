"""Admin CRUD for DOCA Releases (unified BFB + host DOCA catalog).

Each row pairs a BFB image (DPU side) with host DOCA package info.
Selectable as the release to flash when provisioning DPUs (see DPU tab
on Existing Kubernetes projects).
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from routes.auth import require_operator, require_viewer
from schemas.dpu import (
    BluefieldSoftwareImageCreate,
    BluefieldSoftwareImageResponse,
    BluefieldSoftwareImageUpdate,
)
from services.bluefield_image_service import BluefieldImageService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bluefield-images", tags=["bluefield-images"])


@router.get(
    "",
    response_model=list[BluefieldSoftwareImageResponse],
    dependencies=[Depends(require_viewer)],
)
def list_bluefield_images(db: Session = Depends(get_db)):
    """List all DOCA release images."""
    return BluefieldImageService(db).list_images()


@router.get(
    "/default",
    response_model=BluefieldSoftwareImageResponse | None,
    dependencies=[Depends(require_viewer)],
)
def get_default_bluefield_image(db: Session = Depends(get_db)):
    """Get the default DOCA release, or null if none is set."""
    return BluefieldImageService(db).get_default()


@router.get(
    "/{image_id}",
    response_model=BluefieldSoftwareImageResponse,
    dependencies=[Depends(require_viewer)],
)
def get_bluefield_image(image_id: int, db: Session = Depends(get_db)):
    """Get a DOCA release by id."""
    return BluefieldImageService(db).get_image(image_id)


@router.post(
    "",
    response_model=BluefieldSoftwareImageResponse,
    status_code=201,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("create Bluefield image")
def create_bluefield_image(
    data: BluefieldSoftwareImageCreate, db: Session = Depends(get_db)
):
    """Register a new Bluefield software image."""
    result = BluefieldImageService(db).create_image(data)
    db.commit()
    return result


@router.patch(
    "/{image_id}",
    response_model=BluefieldSoftwareImageResponse,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("update Bluefield image")
def update_bluefield_image(
    image_id: int,
    data: BluefieldSoftwareImageUpdate,
    db: Session = Depends(get_db),
):
    """Update an existing Bluefield software image (partial)."""
    result = BluefieldImageService(db).update_image(image_id, data)
    db.commit()
    return result


@router.delete(
    "/{image_id}",
    status_code=204,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("delete Bluefield image")
def delete_bluefield_image(image_id: int, db: Session = Depends(get_db)):
    """Delete a Bluefield software image."""
    BluefieldImageService(db).delete_image(image_id)
    db.commit()
    return None
