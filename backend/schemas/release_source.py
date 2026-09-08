"""Pydantic schemas for ReleaseSource API (ADR-494)."""

from datetime import datetime

from pydantic import BaseModel, Field

from models.enums import ReleaseSourceKind

# ---------------------------------------------------------------------------
# Live-fetch schemas (tag listing and pull — ADR-494 Phase A)
# ---------------------------------------------------------------------------


class ReleaseSourceCreate(BaseModel):
    name: str
    kind: ReleaseSourceKind
    url: str | None = None
    credential: str | None = Field(default=None, description="Pull-secret or token; encrypted before storage.")
    is_active: bool = True
    auto_sync: bool = False
    sync_interval_hours: int | None = None
    description: str | None = None


class ReleaseSourceUpdate(BaseModel):
    name: str | None = None
    kind: ReleaseSourceKind | None = None
    url: str | None = None
    credential: str | None = Field(default=None, description="Set to update; omit to leave unchanged.")
    is_active: bool | None = None
    auto_sync: bool | None = None
    sync_interval_hours: int | None = None
    description: str | None = None


class ReleaseSourceResponse(BaseModel):
    id: int
    name: str
    kind: str
    url: str | None
    has_credential: bool  # True when credential_encrypted is set; never exposes ciphertext
    is_active: bool
    auto_sync: bool
    sync_interval_hours: int | None
    last_synced_at: datetime | None
    sync_status: str
    sync_error: str | None
    release_count: int
    description: str | None
    created_at: datetime
    updated_at: datetime


class ReleaseSourceTag(BaseModel):
    """A single tag from the OCI/mirror registry with catalog-membership annotation."""

    tag: str
    in_catalog: bool  # True when a bnk_deployable_release row exists for this manifest version
    prerelease: bool  # True when the base version segment indicates a pre-release


class ReleaseSourceTagList(BaseModel):
    """Response for GET /{id}/tags. tags is empty on listing failure."""

    tags: list[ReleaseSourceTag]
    list_error: str | None = None  # Non-null when the listing call failed (non-500)


class PullTagsRequest(BaseModel):
    """Request body for POST /{id}/tags:pull."""

    tags: list[str] = Field(..., description="Registry tags to pull (verbatim).")


class FailedTag(BaseModel):
    """A single tag that could not be added to the Catalog."""

    tag: str
    reason: str


class PullTagsSummary(BaseModel):
    """Response for POST /{id}/tags:pull.

    Nested model (not dict) so Pydantic's response_model serialisation
    preserves the reason field inside each FailedTag entry.
    """

    added: list[str]
    skipped: list[str]  # Already in Catalog; idempotent re-add
    failed: list[FailedTag]  # Pull / parse / FLO-missing failures
