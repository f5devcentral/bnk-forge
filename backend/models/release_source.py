"""ReleaseSource model — first-class BNK release source entities (ADR-494)."""

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text
from sqlalchemy.sql import func

from database import Base


class ReleaseSource(Base):
    """
    A configured origin the BNK release Catalog syncs releases from.

    Modelled on the shape of ModuleSource but without git/OAuth repo-auth
    machinery.  kind = oci | mirror | manual.  credential_encrypted holds an
    optional pull-secret/token, stored via core.encryption.encrypt_value.
    """

    __tablename__ = "release_source"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    kind = Column(String(30), nullable=False)  # stores ReleaseSourceKind value

    url = Column(String(500), nullable=True)  # manual kind may have none

    # Single optional pull-secret/token — encrypted at rest; never returned in responses.
    credential_encrypted = Column(Text, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)
    auto_sync = Column(Boolean, nullable=False, default=False)
    sync_interval_hours = Column(Integer, nullable=True)

    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    sync_status = Column(String(50), nullable=False, default="idle")  # idle|syncing|success|error
    sync_error = Column(Text, nullable=True)

    release_count = Column(Integer, nullable=False, default=0)
    description = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_release_source_kind", "kind"),
        Index("idx_release_source_active", "is_active"),
        Index("idx_release_source_sync_status", "sync_status"),
    )
