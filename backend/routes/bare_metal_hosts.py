"""API routes for bare-metal DPU hosts — CRUD + discovery."""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from models import User
from routes.auth import require_project_owner, require_viewer
from schemas.bare_metal import (
    BareMetalDiscoveryRequest,
    BareMetalHostCreate,
    BareMetalHostListResponse,
    BareMetalHostResponse,
    BareMetalHostUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/projects/{project_id}/bare-metal/hosts",
    tags=["bare-metal"],
)


# --- CRUD ---

@router.get("", response_model=BareMetalHostListResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("list bare-metal hosts")
def list_hosts(
    project_id: int,
    db: Session = Depends(get_db),
) -> BareMetalHostListResponse:
    """List all bare-metal hosts for a project."""
    from services.bare_metal.host_service import BareMetalHostService
    return BareMetalHostService(db).list_hosts(project_id)


@router.post("", response_model=BareMetalHostResponse)
@handle_route_errors("create bare-metal host")
def create_host(
    project_id: int,
    data: BareMetalHostCreate,
    _user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
) -> BareMetalHostResponse:
    """Register a new bare-metal host."""
    from services.bare_metal.host_service import BareMetalHostService
    result = BareMetalHostService(db).create_host(project_id, data)
    db.commit()
    return result


@router.get("/{host_id}", response_model=BareMetalHostResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("get bare-metal host")
def get_host(
    project_id: int,
    host_id: int,
    db: Session = Depends(get_db),
) -> BareMetalHostResponse:
    """Get a specific bare-metal host."""
    from services.bare_metal.host_service import BareMetalHostService
    return BareMetalHostService(db).get_host(project_id, host_id)


@router.put("/{host_id}", response_model=BareMetalHostResponse)
@handle_route_errors("update bare-metal host")
def update_host(
    project_id: int,
    host_id: int,
    data: BareMetalHostUpdate,
    _user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
) -> BareMetalHostResponse:
    """Update a bare-metal host configuration."""
    from services.bare_metal.host_service import BareMetalHostService
    result = BareMetalHostService(db).update_host(project_id, host_id, data)
    db.commit()
    return result


@router.delete("/{host_id}")
@handle_route_errors("delete bare-metal host")
def delete_host(
    project_id: int,
    host_id: int,
    _user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
):
    """Remove a bare-metal host registration."""
    from services.bare_metal.host_service import BareMetalHostService
    BareMetalHostService(db).delete_host(project_id, host_id)
    db.commit()
    return {"status": "deleted", "host_id": host_id}


# --- Discovery ---

class DiscoveryTriggerResponse(BaseModel):
    host_id: int
    status: str


@router.post("/{host_id}/discover", response_model=DiscoveryTriggerResponse, status_code=202)
@handle_route_errors("trigger bare-metal discovery")
def trigger_discovery(
    project_id: int,
    host_id: int,
    data: BareMetalDiscoveryRequest,
    _user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
) -> DiscoveryTriggerResponse:
    """Trigger bare-metal discovery probes on a host (async via Celery).

    Returns 202 immediately. Frontend polls the host status to track progress.
    """
    from services.bare_metal.host_service import BareMetalHostService

    # Pre-flight: set pending status so UI sees it instantly
    host = BareMetalHostService(db).get_host_model(project_id, host_id)
    host.last_discovery_status = "pending"
    host.last_discovery_error = None
    db.commit()

    # Dispatch to Celery
    from tasks.bare_metal_tasks import run_bare_metal_discovery
    run_bare_metal_discovery.delay(host_id, project_id, data.model_dump())

    return DiscoveryTriggerResponse(host_id=host_id, status="pending")


@router.get("/{host_id}/discovery", response_model=BareMetalHostResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("get bare-metal discovery results")
def get_discovery_results(
    project_id: int,
    host_id: int,
    db: Session = Depends(get_db),
) -> BareMetalHostResponse:
    """Get the latest discovery results (cached on the host record)."""
    from services.bare_metal.host_service import BareMetalHostService
    return BareMetalHostService(db).get_host(project_id, host_id)


@router.get("/{host_id}/discovery-result", dependencies=[Depends(require_viewer)])
@handle_route_errors("get bare-metal host discovery result")
def get_discovery_result(
    project_id: int,
    host_id: int,
    db: Session = Depends(get_db),
):
    """Get the cached full discovery result for a host.

    Returns the complete discovery response from the last discovery run,
    or 404 if discovery has never been run.
    """
    from core.errors import NotFoundError
    from services.bare_metal.host_service import BareMetalHostService

    host = BareMetalHostService(db).get_host_model(project_id, host_id)

    if not host.last_discovery_result:
        raise NotFoundError("DiscoveryResult", f"host {host_id}")

    return host.last_discovery_result
