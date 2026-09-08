"""Blueprint source and release catalog endpoints."""

from datetime import datetime
from typing import Any, TypeAlias

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.errors import BadRequestError, handle_route_errors
from database import get_db
from routes.auth import require_operator, require_viewer
from schemas.catalog_prune import PruneRequest, PruneResponse
from services.blueprint_catalog_service import BlueprintCatalogService
from services.blueprint_sync_service import BlueprintSyncService

router = APIRouter(prefix="/api/blueprint-catalog", tags=["blueprint-catalog"])

JSONValue: TypeAlias = dict[str, Any] | list[Any] | str | int | float | bool | None
JSONMetadata: TypeAlias = dict[str, JSONValue]


class BlueprintSourceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    source_type: str = Field(..., description="git or builtin")
    url: str = Field(..., min_length=1, max_length=500)
    branch: str | None = Field(default="main", max_length=100)
    git_ref: str | None = Field(default=None, max_length=100)
    is_active: bool = Field(default=True)
    description: str | None = None
    make_default: bool = Field(default=False)


class BlueprintSourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    url: str | None = Field(default=None, min_length=1, max_length=500)
    branch: str | None = Field(default=None, max_length=100)
    git_ref: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None
    description: str | None = None
    make_default: bool | None = None


class BlueprintSourceResponse(BaseModel):
    id: int
    name: str
    source_type: str
    url: str
    branch: str | None
    git_ref: str | None
    sync_status: str
    sync_error: str | None
    last_synced_at: datetime | None
    release_count: int
    is_active: bool
    is_default: bool
    description: str | None
    created_at: datetime
    updated_at: datetime


class BlueprintSourceSyncResponse(BaseModel):
    success: bool
    source_id: int
    source_name: str
    sync_status: str
    results: JSONMetadata


class BlueprintReleaseCreate(BaseModel):
    blueprint_source_id: int
    manifest: JSONMetadata
    source_path: str | None = Field(default=None, max_length=500)
    source_ref: str | None = Field(default=None, max_length=255)
    release_state: str | None = Field(default=None)
    state_reason: str | None = None
    is_active: bool = Field(default=True)


class BlueprintReleaseStateUpdate(BaseModel):
    release_state: str
    state_reason: str | None = None
    is_active: bool | None = None


class BlueprintReleaseImportRequest(BaseModel):
    state_reason: str | None = None


class BlueprintReleaseResponse(BaseModel):
    id: int
    blueprint_source_id: int
    source_name: str | None
    source_type: str | None = None
    blueprint_id: str
    blueprint_version: str
    blueprint_name: str
    blueprint_description: str | None
    category: str | None = None
    cloud_provider: str | None = None
    bnk_version: str | None = None
    tags: list[str] = Field(default_factory=list)
    schema_version: int
    source_path: str | None
    source_ref: str | None
    content_sha256: str
    validation_state: str
    validation_errors: list[JSONMetadata] | None
    release_state: str
    state_reason: str | None
    is_active: bool
    imported_at: datetime | None
    created_at: datetime
    updated_at: datetime
    deployed_project_count: int = 0
    is_featured: bool = False
    mutable_lifecycle_fields: list[str]


@router.get("/sources", response_model=list[BlueprintSourceResponse], dependencies=[Depends(require_viewer)])
def list_blueprint_sources(
    source_type: str | None = None,
    is_active: bool | None = None,
    db: Session = Depends(get_db),
):
    return BlueprintCatalogService(db).list_sources(source_type=source_type, is_active=is_active)


@router.get("/sources/{source_id}", response_model=BlueprintSourceResponse, dependencies=[Depends(require_viewer)])
def get_blueprint_source(source_id: int, db: Session = Depends(get_db)):
    return BlueprintCatalogService(db).get_source(source_id)


@router.post("/sources", response_model=BlueprintSourceResponse, dependencies=[Depends(require_operator)])
@handle_route_errors("create blueprint source")
def create_blueprint_source(source_data: BlueprintSourceCreate = Body(...), db: Session = Depends(get_db)):
    result = BlueprintCatalogService(db).create_source(source_data)
    db.commit()
    return result


@router.delete("/sources/{source_id}", dependencies=[Depends(require_operator)])
@handle_route_errors("delete blueprint source")
def delete_blueprint_source(source_id: int, db: Session = Depends(get_db)):
    result = BlueprintCatalogService(db).delete_source(source_id)
    db.commit()
    return result


@router.put("/sources/{source_id}", response_model=BlueprintSourceResponse, dependencies=[Depends(require_operator)])
@handle_route_errors("update blueprint source")
def update_blueprint_source(source_id: int, source_data: BlueprintSourceUpdate, db: Session = Depends(get_db)):
    result = BlueprintCatalogService(db).update_source(source_id, source_data)
    db.commit()
    return result


@router.post(
    "/sources/{source_id}/sync",
    response_model=BlueprintSourceSyncResponse,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("sync blueprint source")
def sync_blueprint_source(source_id: int, db: Session = Depends(get_db)):
    source = BlueprintCatalogService(db)._get_source(source_id)
    if not source.is_active:
        raise BadRequestError("Source is inactive", code="SOURCE_INACTIVE")

    results = BlueprintSyncService(db).sync_git_source(source)
    return {
        "success": True,
        "source_id": source.id,
        "source_name": source.name,
        "sync_status": source.sync_status,
        "results": results,
    }


@router.get("/releases", response_model=list[BlueprintReleaseResponse], dependencies=[Depends(require_viewer)])
def list_blueprint_releases(
    source_id: int | None = None,
    blueprint_id: str | None = None,
    release_state: str | None = None,
    validation_state: str | None = None,
    is_active: bool | None = None,
    db: Session = Depends(get_db),
):
    return BlueprintCatalogService(db).list_releases(
        source_id=source_id,
        blueprint_id=blueprint_id,
        release_state=release_state,
        validation_state=validation_state,
        is_active=is_active,
    )


@router.get("/releases/{release_id}", response_model=BlueprintReleaseResponse, dependencies=[Depends(require_viewer)])
def get_blueprint_release(release_id: int, db: Session = Depends(get_db)):
    return BlueprintCatalogService(db).get_release(release_id)


@router.delete("/releases/{release_id}", dependencies=[Depends(require_operator)])
@handle_route_errors("delete blueprint release")
def delete_blueprint_release(release_id: int, db: Session = Depends(get_db)):
    result = BlueprintCatalogService(db).delete_release(release_id)
    db.commit()
    return result


@router.post(
    "/releases/{release_id}/unimport",
    response_model=BlueprintReleaseResponse,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("unimport blueprint release")
def unimport_blueprint_release(release_id: int, db: Session = Depends(get_db)):
    """Reverse an Import: move an imported/approved release back to
    'discovered' so it is no longer deployable. Blocked when any project
    has modules created from this release.
    """
    result = BlueprintCatalogService(db).unimport_release(release_id)
    db.commit()
    return result


@router.post("/releases", response_model=BlueprintReleaseResponse, dependencies=[Depends(require_operator)])
@handle_route_errors("create blueprint release")
def create_blueprint_release(release_data: BlueprintReleaseCreate = Body(...), db: Session = Depends(get_db)):
    result = BlueprintCatalogService(db).create_release(release_data)
    db.commit()
    return result


@router.patch("/releases/{release_id}/state", response_model=BlueprintReleaseResponse, dependencies=[Depends(require_operator)])
@handle_route_errors("update blueprint release state")
def update_blueprint_release_state(
    release_id: int,
    state_data: BlueprintReleaseStateUpdate = Body(...),
    db: Session = Depends(get_db),
):
    result = BlueprintCatalogService(db).update_release_state(release_id, state_data)
    db.commit()
    return result


@router.post(
    "/releases/{release_id}/import",
    response_model=BlueprintReleaseResponse,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("import blueprint release")
def import_blueprint_release(
    release_id: int,
    body: BlueprintReleaseImportRequest = Body(default=BlueprintReleaseImportRequest()),
    db: Session = Depends(get_db),
):
    result = BlueprintCatalogService(db).update_release_state(
        release_id,
        BlueprintReleaseStateUpdate(
            release_state="imported",
            state_reason=body.state_reason,
            is_active=True,
        ),
    )
    db.commit()
    return result


class BlueprintReleaseVisibilityUpdate(BaseModel):
    is_visible: bool


@router.patch(
    "/releases/{release_id}/visibility",
    response_model=BlueprintReleaseResponse,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("update blueprint release visibility")
def update_blueprint_release_visibility(
    release_id: int,
    body: BlueprintReleaseVisibilityUpdate = Body(...),
    db: Session = Depends(get_db),
):
    """Toggle whether an imported release is visible on the user-facing /stacks page.

    ON  (is_visible=true)  => release_state=imported, is_active=true
    OFF (is_visible=false) => is_active=false (release_state unchanged, keeps import history)

    Blocked for releases in 'discovered' state — import them first.
    """
    result = BlueprintCatalogService(db).set_release_visibility(release_id, body.is_visible)
    db.commit()
    return result


@router.post(
    "/sources/{source_id}/prune",
    response_model=PruneResponse,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("prune blueprint source")
def prune_blueprint_source_releases(
    source_id: int, request: PruneRequest, db: Session = Depends(get_db)
):
    """Retire superseded blueprint releases for this source.

    Every edit to a blueprint adds an immutable release, so a source under
    development ends up serving a version picker full of history. Deactivating
    (the default) hides a release without discarding it. `delete` removes rows
    outright and only ever those nothing was deployed from — a release a
    StackInstance points at is deactivated instead, because that FK is
    ON DELETE SET NULL and deleting would silently strip the stack of the record
    of what it was built from.
    """
    from services.catalog_prune_service import prune_blueprint_source

    result = prune_blueprint_source(
        db,
        source_id,
        keep=request.keep,
        delete=request.delete,
        dry_run=request.dry_run,
        include_in_use=request.include_in_use,
    )
    if not request.dry_run:
        db.commit()
    return result.as_dict()
