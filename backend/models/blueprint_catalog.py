"""Blueprint source and immutable imported release catalog models."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.orm import deferred, relationship
from sqlalchemy.sql import func

from database import Base
from models.enums import BlueprintReleaseState, BlueprintValidationState, ModuleSyncStatus


class BlueprintSource(Base):
    """Approved external sources for governed blueprint imports."""

    __tablename__ = "blueprint_sources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    source_type = Column(String(50), nullable=False, index=True)

    url = Column(String(500), nullable=False)
    branch = Column(String(100), nullable=True)
    git_ref = Column(String(100), nullable=True)

    sync_status = Column(String(50), nullable=False, default=ModuleSyncStatus.PENDING, index=True)
    sync_error = Column(Text, nullable=True)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    release_count = Column(Integer, nullable=False, default=0)

    is_active = Column(Boolean, nullable=False, default=True, index=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    releases = relationship("BlueprintRelease", back_populates="source", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_blueprint_source_type", "source_type"),
        Index("idx_blueprint_source_status", "sync_status"),
        Index("idx_blueprint_source_active", "is_active"),
    )


class BlueprintRelease(Base):
    """Immutable imported blueprint release record.

    Structural content fields are immutable after creation.
    Lifecycle fields (`release_state`, `state_reason`, `is_active`, `imported_at`) are
    explicitly mutable to support promotion/deprecation/inactivation progression.
    """

    __tablename__ = "blueprint_releases"

    id = Column(Integer, primary_key=True, index=True)
    blueprint_source_id = Column(Integer, ForeignKey("blueprint_sources.id", ondelete="CASCADE"), nullable=False, index=True)

    blueprint_id = Column(String(255), nullable=False, index=True)
    blueprint_version = Column(String(100), nullable=False)
    blueprint_name = Column(String(255), nullable=False)
    blueprint_description = Column(Text, nullable=True)
    schema_version = Column(Integer, nullable=False)

    source_path = Column(String(500), nullable=True)
    source_ref = Column(String(255), nullable=True)
    content_sha256 = Column(String(64), nullable=False)

    manifest = deferred(Column(JSON, nullable=False))
    validation_state = Column(String(50), nullable=False, default=BlueprintValidationState.VALID, index=True)
    validation_errors = deferred(Column(JSON, nullable=True))

    release_state = Column(String(50), nullable=False, default=BlueprintReleaseState.IMPORTED, index=True)
    state_reason = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)

    imported_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    source = relationship("BlueprintSource", back_populates="releases")

    __table_args__ = (
        UniqueConstraint(
            "blueprint_source_id",
            "blueprint_id",
            "blueprint_version",
            name="uq_blueprint_release_source_identity_version",
        ),
        Index("idx_blueprint_release_identity", "blueprint_id", "blueprint_version"),
        Index("idx_blueprint_release_state", "release_state"),
        Index("idx_blueprint_release_validation", "validation_state"),
    )


_IMMUTABLE_BLUEPRINT_RELEASE_FIELDS = {
    "blueprint_source_id",
    "blueprint_id",
    "blueprint_version",
    "blueprint_name",
    "blueprint_description",
    "schema_version",
    "source_path",
    "source_ref",
    "content_sha256",
    "manifest",
    "validation_state",
    "validation_errors",
}

_MUTABLE_BLUEPRINT_RELEASE_LIFECYCLE_FIELDS = {
    "release_state",
    "state_reason",
    "is_active",
    "imported_at",
}


@event.listens_for(BlueprintRelease, "before_update")
def _enforce_blueprint_release_structural_immutability(_mapper, _connection, target: BlueprintRelease) -> None:
    """Prevent structural updates while allowing lifecycle progression updates."""
    state = sqlalchemy_inspect(target)
    changed_immutable_fields = [
        field_name
        for field_name in _IMMUTABLE_BLUEPRINT_RELEASE_FIELDS
        if state.attrs[field_name].history.has_changes()
    ]
    if changed_immutable_fields:
        raise ValueError(
            "Blueprint release is structurally immutable after import; "
            f"attempted to modify: {', '.join(sorted(changed_immutable_fields))}"
        )
