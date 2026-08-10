"""ConfigSnapshot model for config snapshots and rollback (UX-011)."""

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base


class ConfigSnapshot(Base):
    """
    Point-in-time snapshot of a project's cluster configuration.

    Snapshots are created:
    - Manually by users (trigger='manual')
    - Automatically before apply operations (trigger='auto-before-apply')
    - Automatically before upgrade operations (trigger='auto-before-upgrade')

    Each snapshot stores the full exported config (resources + module variables)
    and can be restored to roll back configuration changes.
    """
    __tablename__ = "config_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    cluster_id = Column(Integer, ForeignKey("kubernetes_clusters.id", ondelete="SET NULL"), nullable=True)

    # Snapshot metadata
    trigger = Column(String(50), nullable=False)  # 'manual', 'auto-before-apply', 'auto-before-upgrade'
    label = Column(String(255), nullable=True)  # user-provided label
    created_by = Column(String(255), nullable=True)  # username

    # The full exported config (JSON blob from export_cluster_config or module-only export)
    config_data = Column(JSON, nullable=False)

    # Summary stats for quick display without loading full config
    resource_count = Column(Integer, default=0)
    module_count = Column(Integer, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    project = relationship("Project", back_populates="config_snapshots")

    __table_args__ = (
        Index("idx_config_snapshot_project", "project_id"),
        Index("idx_config_snapshot_project_created", "project_id", "created_at"),
        Index("idx_config_snapshot_trigger", "trigger"),
    )
