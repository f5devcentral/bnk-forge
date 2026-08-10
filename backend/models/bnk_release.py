"""BnkRelease model — BNK release registry (issue #217)."""

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text
from sqlalchemy.sql import func

from database import Base
from models.enums import ReleaseSourceType


class BnkRelease(Base):
    """
    Registry of known BNK GA releases with their FLO chart version bounds,
    Kubernetes compatibility, and provenance metadata.

    Each row represents one BNK GA line (e.g. "BNK 2.3 GA").  The evaluator
    service uses flo_version_prefix (primary) or manifest_version (secondary)
    to resolve a cluster's installed FLO version to a human-readable GA label.

    Rows are seeded by migration v2_131.  Admin-created rows (source_type=manual)
    and OCI-synced rows (source_type=oci) are added at runtime.
    """

    __tablename__ = "bnk_releases"

    id = Column(Integer, primary_key=True)

    # Human-facing label, e.g. "BNK 2.3 GA"
    ga_label = Column(String(100), nullable=False)

    # Product line, e.g. "BNK"
    product_line = Column(String(50), nullable=False, default="BNK")

    # manifest version string or pattern, e.g. "2.3.0"  (nullable = unknown)
    manifest_version = Column(String(100), nullable=True)

    # FLO chart version prefix that identifies this GA line, e.g. "2.21"
    # Matching rule: installed_flo_version.startswith(flo_version_prefix + ".")
    # Use None when only manifest matching is applicable.
    flo_version_prefix = Column(String(50), nullable=True)

    # FLO chart semver bounds for range matching (optional, more precise)
    flo_version_min = Column(String(50), nullable=True)   # inclusive
    flo_version_max = Column(String(50), nullable=True)   # exclusive

    # Kubernetes compatibility window
    min_k8s = Column(String(20), nullable=True)
    max_k8s = Column(String(20), nullable=True)

    release_date = Column(DateTime(timezone=True), nullable=True)

    # Provenance
    source_type = Column(
        String(30),
        nullable=False,
        default=ReleaseSourceType.MANUAL,
    )
    source_url = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("idx_bnk_release_ga_label", "ga_label"),
        Index("idx_bnk_release_flo_prefix", "flo_version_prefix"),
        Index("idx_bnk_release_active", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<BnkRelease id={self.id} ga_label={self.ga_label!r} flo_prefix={self.flo_version_prefix!r}>"
