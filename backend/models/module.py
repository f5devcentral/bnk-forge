"""ModuleLibrary, ModuleSource, and ModuleSnapshot models."""

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
from models.enums import ModuleSyncStatus, ModuleTestStatus


class ModuleLibrary(Base):
    """Official BNK-Forge and user-added module catalog."""
    __tablename__ = "module_library"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    category = Column(String(50), nullable=False, index=True)
    path = Column(String(500))
    provider = Column(String(50))
    description = Column(Text)
    git_source = Column(String(1000), nullable=False)
    version = Column(String(100))
    module_source_kind = Column(String(50), nullable=True, index=True)
    execution_engine = Column(String(50), nullable=True, index=True)
    deploy_model = Column(String(50), nullable=True, index=True)
    engine_type = Column(String(50), nullable=True, index=True)

    # B3: Deferred loading for large JSON/Text columns
    variables_schema = deferred(Column(JSON))
    pack_manifest = deferred(Column(JSON))
    # Resolved references graph for module_source_kind='artifact'
    # ({"root", "nodes", "edges"}) — see services/module_metadata.py.
    artifact_references = deferred(Column(JSON))
    dependencies = Column(JSON)
    workflow_compatibility = Column(JSON)

    # Rich metadata from module.json — deferred (large JSON blobs)
    dependencies_metadata = deferred(Column(JSON))
    inputs_metadata = deferred(Column(JSON))
    outputs_metadata = deferred(Column(JSON))
    deployment_order = Column(Integer, default=999)

    # Module status and testing
    is_official = Column(Boolean, default=False)
    is_tested = Column(Boolean, default=False)
    test_status = Column(String(50), default=ModuleTestStatus.NOT_TESTED)
    test_last_run = Column(DateTime(timezone=True))
    test_error = Column(Text)

    # Multi-source support
    module_source_id = Column(Integer, ForeignKey("module_sources.id", ondelete="SET NULL"), nullable=True, index=True)
    source_path = Column(String(255))
    source_version = Column(String(100))
    local_path = Column(String(500))
    is_custom = Column(Boolean, default=False)
    latest_version = Column(String(100))
    update_available = Column(Boolean, default=False)

    # Registry-imported module provenance + smoke-test results (v2_105).
    tarball_sha256 = Column(String(64), nullable=True)
    upstream_source_url = Column(String(1000), nullable=True)
    upstream_subpath = Column(String(500), nullable=True)
    metadata_overrides = deferred(Column(JSON, nullable=True))
    last_smoke_test_status = Column(String(20), nullable=True)
    last_smoke_test_output = deferred(Column(Text, nullable=True))
    last_smoke_test_at = Column(DateTime(timezone=True), nullable=True)

    # D-033 multi-version identity: one immutable row per (source, path, version).
    # content_sha256 is the canonical hash of the synced manifest content; NULL marks
    # a legacy (pre-D-033) row that may be grandfather-updated in place exactly once.
    # is_latest is recomputed per (module_source_id, path) after every sync.
    content_sha256 = Column(String(64), nullable=True)
    is_latest = Column(Boolean, nullable=False, default=True, server_default="true")

    # Metadata — readme deferred (can be 1-100KB)
    readme = deferred(Column(Text))
    tags = Column(JSON)
    is_active = Column(Boolean, default=True)
    validation_error = Column(Text, nullable=True)
    last_synced = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    source = relationship("ModuleSource", back_populates="modules")
    project_modules = relationship("ProjectModule", back_populates="library_module", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_module_category_provider", "category", "provider"),
        Index("idx_module_official", "is_official"),
        Index("idx_module_tested", "is_tested"),
        Index("idx_module_library_path", "path"),
        Index("idx_module_library_path_active", "path", "is_active"),
        Index("idx_module_library_path_latest", "path", "is_latest"),
        UniqueConstraint(
            "module_source_id",
            "path",
            "version",
            name="uq_module_library_source_path_version",
        ),
    )


# D-033: structural fields frozen once a row carries a content hash. Lifecycle,
# health, and admin-annotation fields stay mutable (is_active, is_latest, test/smoke
# results, metadata_overrides, validation_error, last_synced, timestamps).
_IMMUTABLE_MODULE_VERSION_FIELDS = {
    "name",
    "category",
    "path",
    "provider",
    "description",
    "git_source",
    "version",
    "module_source_kind",
    "execution_engine",
    "deploy_model",
    "engine_type",
    "variables_schema",
    "pack_manifest",
    "artifact_references",
    "dependencies",
    "workflow_compatibility",
    "dependencies_metadata",
    "inputs_metadata",
    "outputs_metadata",
    "deployment_order",
    "is_official",
    "module_source_id",
    "source_path",
    "source_version",
    "local_path",
    "is_custom",
    "tarball_sha256",
    "upstream_source_url",
    "upstream_subpath",
    "readme",
    "tags",
    "content_sha256",
}


@event.listens_for(ModuleLibrary, "before_update")
def _enforce_module_version_structural_immutability(_mapper, connection, target: ModuleLibrary) -> None:
    """Freeze structural fields on hashed (D-033) module-version rows.

    Rows with content_sha256 == NULL are legacy/pre-D-033 (or terraform-fallback)
    rows and remain fully mutable; the sync grandfather-updates such a row once,
    setting the hash in the same UPDATE — that transition is allowed because the
    persisted hash was NULL when the update began.
    """
    state = sqlalchemy_inspect(target)
    hash_history = state.attrs["content_sha256"].history
    if hash_history.has_changes():
        persisted_hash = next(iter(hash_history.deleted), None)
    else:
        persisted_hash = target.content_sha256
    if persisted_hash is None:
        return

    changed_immutable_fields = []
    for field_name in _IMMUTABLE_MODULE_VERSION_FIELDS:
        history = state.attrs[field_name].history
        if not history.has_changes():
            continue
        # flag_modified() and same-value setattr both register a change even
        # when the value is identical (History compares JSON columns by
        # identity). Tolerate no-op writes — idempotent catalog re-syncs and
        # seeders re-assert equal values; only a genuinely different value
        # violates immutability.
        added = history.added[0] if history.added else None
        deleted = history.deleted[0] if history.deleted else None
        if added == deleted:
            continue
        if not history.deleted:
            # flag_modified() discards the old value from history — fetch the
            # persisted value to distinguish a no-op re-assert from real drift.
            table = ModuleLibrary.__table__
            persisted = connection.execute(
                table.select()
                .with_only_columns(table.c[field_name])
                .where(table.c.id == target.id)
            ).scalar()
            if persisted == added:
                continue
        changed_immutable_fields.append(field_name)
    if changed_immutable_fields:
        raise ValueError(
            "Module version is structurally immutable once content-hashed; "
            f"attempted to modify: {', '.join(sorted(changed_immutable_fields))}"
        )


class ModuleSource(Base):
    """External module sources (Git repositories, OpenTofu Registry)."""
    __tablename__ = "module_sources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    source_type = Column(String(50), nullable=False, index=True)

    url = Column(String(500), nullable=False)
    branch = Column(String(100))
    git_ref = Column(String(100))

    auth_type = Column(String(50))
    auth_token_encrypted = Column(Text)

    # REPO-AUTH-002: normalized credential metadata foundation (non-secret)
    credential_type = Column(String(50), nullable=True, index=True)
    credential_scope = Column(String(255), nullable=True)
    credential_capabilities = Column(JSON, nullable=True)
    credential_metadata = deferred(Column(JSON, nullable=True))
    credential_expires_at = Column(DateTime(timezone=True), nullable=True)
    credential_last_rotated_at = Column(DateTime(timezone=True), nullable=True)
    credential_validation_status = Column(String(50), nullable=True, index=True)
    credential_last_validated_at = Column(DateTime(timezone=True), nullable=True)
    credential_validation_error = Column(Text, nullable=True)

    last_synced_at = Column(DateTime(timezone=True))
    sync_status = Column(String(50), default=ModuleSyncStatus.PENDING, index=True)
    sync_error = Column(Text)
    module_count = Column(Integer, default=0)

    is_active = Column(Boolean, default=True, index=True)
    auto_sync = Column(Boolean, default=False)
    sync_interval_hours = Column(Integer, default=24)

    description = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    modules = relationship("ModuleLibrary", back_populates="source")

    __table_args__ = (
        Index("idx_module_source_type", "source_type"),
        Index("idx_module_source_status", "sync_status"),
        Index("idx_module_source_active", "is_active"),
    )


class ModuleSnapshot(Base):
    """Snapshots of module configuration and state for rollback/recovery."""
    __tablename__ = "module_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    project_module_id = Column(Integer, ForeignKey("project_modules.id"), nullable=False)

    snapshot_type = Column(String(50), nullable=False, index=True)
    description = Column(Text)
    created_by = Column(String(255), default="system")

    variable_overrides = Column(JSON)
    deployment_order = Column(Integer)
    dependencies = Column(JSON)
    status = Column(String(50))

    terraform_state = deferred(Column(Text))
    terraform_state_serial = Column(Integer)
    state_checksum = Column(String(64))

    git_commit = Column(String(64))
    module_path = Column(String(1000))

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)

    # Relationships
    module = relationship("ProjectModule", back_populates="snapshots")

    __table_args__ = (
        Index("idx_snapshot_module_type", "project_module_id", "snapshot_type"),
        Index("idx_snapshot_created", "created_at"),
    )
