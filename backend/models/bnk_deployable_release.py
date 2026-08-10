"""BnkDeployableRelease model — deployable BNK release catalog (ADR-478)."""

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base
from models.enums import ReleaseSourceType


class BnkDeployableRelease(Base):
    """
    A deployable BNK release — the full component version matrix required to
    deploy BNK onto a bare-metal host.

    Each row represents one deployable release (e.g. "bnk-2.2", "bnk-2.3.1").
    The optional bnk_release_id FK links to the BnkRelease GA-label row for
    display purposes only; it does not affect deployment logic.

    Seeded by the BnkDeployableReleaseService; admin rows have source_type=manual.
    """

    __tablename__ = "bnk_deployable_release"

    id = Column(Integer, primary_key=True, index=True)

    # Identity
    name = Column(String(100), nullable=False, unique=True, index=True)  # e.g. "bnk-2.2", "bnk-2.3.1"
    display_name = Column(String(255), nullable=False)                    # e.g. "BNK 2.2 (GA)"
    description = Column(Text, nullable=True)
    is_default = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)

    # Provenance
    source_type = Column(String(30), nullable=False, default=ReleaseSourceType.MANUAL)

    # GA-label link (display only — does not gate deployment)
    bnk_release_id = Column(
        Integer,
        ForeignKey("bnk_releases.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Core versions
    bnk_manifest_version = Column(String(50), nullable=False)
    bnk_cr_kind = Column(String(50), nullable=False)    # e.g. "CNEInstance"
    flo_version = Column(String(50), nullable=False)
    k8s_version = Column(String(50), nullable=False)
    doca_version = Column(String(50), nullable=False)

    # Runtime versions
    containerd_version = Column(String(50), nullable=False)
    runc_version = Column(String(50), nullable=False)

    # Ecosystem versions
    calico_version = Column(String(50), nullable=False)
    cert_manager_version = Column(String(50), nullable=False)
    gateway_api_version = Column(String(50), nullable=False)
    multus_version = Column(String(50), nullable=False)
    sriov_version = Column(String(50), nullable=False)

    # Storage
    storage_class_type = Column(String(50), nullable=False)       # "local-path" or "nfs"
    storage_provisioner = Column(String(255), nullable=False)

    # Feature flags
    feature_flags = Column(JSON, nullable=True)   # {"ipv6": false, "tmm_node_labels": true, ...}

    # Full version manifest (catch-all for additional components)
    full_manifest = Column(JSON, nullable=True)

    # Source provenance (ADR-494) — which ReleaseSource this entry was synced from
    source_id = Column(
        Integer,
        ForeignKey("release_source.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_synced = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    bnk_release = relationship("BnkRelease", foreign_keys=[bnk_release_id])
    source = relationship("ReleaseSource")

    __table_args__ = (
        Index("idx_bnk_deployable_release_default", "is_default"),
        Index("idx_bnk_deployable_release_active", "is_active"),
        Index("idx_bnk_deployable_release_name", "name"),
    )
