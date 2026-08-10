"""BnkUpgrade model for tracking BNK upgrade operations."""

from sqlalchemy import JSON, BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base
from models.enums import BnkUpgradeStatus


class BnkUpgrade(Base):
    """
    Track BNK upgrade operations on clusters.

    Statuses:
      planning → ready → in_progress → health_check → completed
      Or: failed, rolling_back, rolled_back, cancelled
    """
    __tablename__ = "bnk_upgrades"

    id = Column(Integer, primary_key=True, index=True)
    cluster_id = Column(Integer, ForeignKey("kubernetes_clusters.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)

    from_version = Column(String(100))
    to_version = Column(String(100), nullable=False)
    from_bnk_info = Column(JSON)

    plan = Column(JSON)
    total_steps = Column(Integer, default=0)
    current_step = Column(Integer, default=0)

    pre_checks = Column(JSON)
    pre_check_passed = Column(Boolean, default=False)

    status = Column(String(50), default=BnkUpgradeStatus.PLANNING, nullable=False, index=True)
    celery_task_id = Column(String(255), nullable=True, index=True)

    step_results = Column(JSON, default=list)

    pre_health = Column(JSON)
    post_health = Column(JSON)
    health_gate_passed = Column(Boolean, nullable=True)

    rollback_available = Column(Boolean, default=False)
    rollback_info = Column(JSON)
    rollback_reason = Column(Text)

    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    duration_seconds = Column(Float)

    error_message = Column(Text)
    error_step = Column(Integer)

    triggered_by = Column(String(255), default="user")
    approved_by = Column(String(255))

    # Heartbeat + fencing-token lock columns (Phase 1.6 — EntityLockService)
    lock_fence_token = Column(BigInteger, nullable=False, server_default="0")
    holding_task_id = Column(BigInteger, nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    lock_acquired_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    cluster = relationship("KubernetesCluster")
    project = relationship("Project")

    __table_args__ = (
        Index("idx_bnk_upgrade_cluster_status", "cluster_id", "status"),
        Index("idx_bnk_upgrade_created", "created_at"),
    )
