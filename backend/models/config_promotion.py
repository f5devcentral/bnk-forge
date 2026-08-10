"""ConfigPromotion model — promotion history for cross-cluster config promotion (UX-013)."""

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base


class ConfigPromotion(Base):
    """
    Audit log entry for cross-cluster config promotions.

    Records every promotion (dry_run=false) with source/target cluster IDs,
    change count, who applied it, and when. Provides a complete audit trail
    for cross-cluster configuration synchronization.
    """
    __tablename__ = "config_promotions"

    id = Column(Integer, primary_key=True, index=True)
    source_cluster_id = Column(
        Integer, ForeignKey("kubernetes_clusters.id", ondelete="SET NULL"), nullable=True
    )
    target_cluster_id = Column(
        Integer, ForeignKey("kubernetes_clusters.id", ondelete="SET NULL"), nullable=True
    )

    # Promotion metadata
    source_cluster_name = Column(String(255), nullable=False)
    target_cluster_name = Column(String(255), nullable=False)
    change_count = Column(Integer, nullable=False, default=0)
    changes_summary = Column(JSON, nullable=True)  # List of {action, kind, name, namespace}
    applied_by = Column(String(255), nullable=True)

    # Result
    success = Column(Boolean, default=True)
    error_message = Column(String(1000), nullable=True)
    applied_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    skipped_count = Column(Integer, default=0)

    applied_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    source_cluster = relationship(
        "KubernetesCluster", foreign_keys=[source_cluster_id]
    )
    target_cluster = relationship(
        "KubernetesCluster", foreign_keys=[target_cluster_id]
    )

    __table_args__ = (
        Index("idx_config_promotion_applied_at", "applied_at"),
        Index("idx_config_promotion_source", "source_cluster_id"),
        Index("idx_config_promotion_target", "target_cluster_id"),
    )
