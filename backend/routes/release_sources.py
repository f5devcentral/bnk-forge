"""API routes for BNK release source management (ADR-494)."""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from routes.auth import require_admin, require_viewer
from schemas.release_source import (
    PullTagsRequest,
    PullTagsSummary,
    ReleaseSourceCreate,
    ReleaseSourceResponse,
    ReleaseSourceTagList,
    ReleaseSourceUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/bare-metal/release-sources",
    tags=["bare-metal"],
)


class SyncSourceRequest(BaseModel):
    manifest_yaml: str


class SyncSourceResponse(BaseModel):
    source: ReleaseSourceResponse
    sync_result: dict[str, int]


@router.get("", response_model=list[ReleaseSourceResponse], dependencies=[Depends(require_viewer)])
@handle_route_errors("list release sources")
def list_release_sources(
    active_only: bool = False,
    db: Session = Depends(get_db),
) -> list[ReleaseSourceResponse]:
    """List all BNK release sources."""
    from services.release_source_service import ReleaseSourceService

    svc = ReleaseSourceService(db)
    return [svc.to_response(s) for s in svc.list_sources(active_only=active_only)]


@router.post("", response_model=ReleaseSourceResponse, dependencies=[Depends(require_admin)])
@handle_route_errors("create release source")
def create_release_source(
    body: ReleaseSourceCreate,
    db: Session = Depends(get_db),
) -> ReleaseSourceResponse:
    """Create a new BNK release source."""
    from services.release_source_service import ReleaseSourceService

    svc = ReleaseSourceService(db)
    source = svc.create_source(body)
    db.commit()
    return svc.to_response(source)


@router.get("/{source_id}", response_model=ReleaseSourceResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("get release source")
def get_release_source(
    source_id: int,
    db: Session = Depends(get_db),
) -> ReleaseSourceResponse:
    """Get a specific BNK release source."""
    from services.release_source_service import ReleaseSourceService

    svc = ReleaseSourceService(db)
    return svc.to_response(svc.get_source(source_id))


@router.patch("/{source_id}", response_model=ReleaseSourceResponse, dependencies=[Depends(require_admin)])
@handle_route_errors("update release source")
def update_release_source(
    source_id: int,
    body: ReleaseSourceUpdate,
    db: Session = Depends(get_db),
) -> ReleaseSourceResponse:
    """Partial update of a BNK release source."""
    from services.release_source_service import ReleaseSourceService

    svc = ReleaseSourceService(db)
    source = svc.update_source(source_id, body)
    db.commit()
    return svc.to_response(source)


@router.delete("/{source_id}", status_code=204, dependencies=[Depends(require_admin)])
@handle_route_errors("delete release source")
def delete_release_source(
    source_id: int,
    db: Session = Depends(get_db),
) -> None:
    """Delete a BNK release source. Catalog rows retain source_id → NULL via FK ON DELETE SET NULL."""
    from services.release_source_service import ReleaseSourceService

    ReleaseSourceService(db).delete_source(source_id)
    db.commit()
    return None


@router.get("/{source_id}/tags", response_model=ReleaseSourceTagList, dependencies=[Depends(require_viewer)])
@handle_route_errors("list release source tags")
def list_release_source_tags(
    source_id: int,
    db: Session = Depends(get_db),
) -> ReleaseSourceTagList:
    """List available manifest tags from the OCI/mirror registry.

    Best-effort: on listing failure returns tags=[] with list_error set
    (never 500s). The UI should keep a manual tag-entry fallback.
    """
    from services.release_source_service import ReleaseSourceService

    return ReleaseSourceService(db).list_available_tags(source_id)


@router.post("/{source_id}/tags:pull", response_model=PullTagsSummary, dependencies=[Depends(require_admin)])
@handle_route_errors("pull release source tags")
def pull_release_source_tags(
    source_id: int,
    body: PullTagsRequest,
    db: Session = Depends(get_db),
) -> PullTagsSummary:
    """Pull selected manifest tags from the OCI/mirror registry and upsert Catalog rows.

    Idempotent: already-present releases are reported in skipped[], not re-inserted.
    Partial batch failure (one tag fails, others succeed) keeps sync_status=success.
    """
    from services.release_source_service import ReleaseSourceService

    svc = ReleaseSourceService(db)
    try:
        result = svc.pull_tags(source_id, body.tags)
        db.commit()
    except Exception:
        db.commit()
        raise
    return result


@router.post("/{source_id}/sync", response_model=SyncSourceResponse, dependencies=[Depends(require_admin)])
@handle_route_errors("sync release source")
def sync_release_source(
    source_id: int,
    body: SyncSourceRequest,
    db: Session = Depends(get_db),
) -> SyncSourceResponse:
    """Sync catalog releases from the supplied manifest YAML.

    Persists sync_status='error' even when the sync fails, so the caller can
    inspect the error via GET /{source_id}.
    """
    from services.release_source_service import ReleaseSourceService

    svc = ReleaseSourceService(db)
    try:
        result = svc.sync_source(source_id, body.manifest_yaml)
        db.commit()
    except Exception:
        # Persist the error state set by sync_source before re-raising.
        db.commit()
        raise
    source = svc.get_source(source_id)
    return SyncSourceResponse(source=svc.to_response(source), sync_result=result)
