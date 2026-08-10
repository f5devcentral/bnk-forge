"""DriftCheck and DriftSettings models."""

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import deferred, relationship
from sqlalchemy.sql import func

from database import Base
from models.enums import DriftCheckStatus


class DriftCheck(Base):
    """Track scheduled drift detection checks for infrastructure modules."""
    __tablename__ = "drift_checks"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    module_id = Column(Integer, ForeignKey("project_modules.id", ondelete="SET NULL"), nullable=True, index=True)

    schedule_enabled = Column(Boolean, default=True)
    schedule_cron = Column(String(100), default="0 2 * * *")
    last_check_at = Column(DateTime(timezone=True), nullable=True)
    next_check_at = Column(DateTime(timezone=True), nullable=True)

    drift_detected = Column(Boolean, default=False, index=True)
    drift_summary = Column(Text)
    # B3: Deferred — drift_details can be 5-100KB
    drift_details = deferred(Column(JSON))
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True)

    status = Column(String(50), default=DriftCheckStatus.SCHEDULED, index=True)
    error_message = Column(Text)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    project = relationship("Project", back_populates="drift_checks")
    module = relationship("ProjectModule", back_populates="drift_checks")
    task = relationship("Task")

    __table_args__ = (
        Index("idx_drift_project_module", "project_id", "module_id"),
        Index("idx_drift_detected", "drift_detected", "created_at"),
        Index("idx_drift_next_check", "next_check_at"),
    )


class DriftSettings(Base):
    """Global and per-project drift detection settings."""
    __tablename__ = "drift_settings"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)

    enabled = Column(Boolean, default=False, index=True)
    schedule_type = Column(String(50), default="cron")
    schedule_value = Column(String(100), default="0 2 * * *")

    notify_on_drift = Column(Boolean, default=True)
    notification_channels = Column(JSON)
    notification_config = Column(JSON)

    check_all_modules = Column(Boolean, default=True)
    module_ids = Column(JSON)

    ignore_insignificant_changes = Column(Boolean, default=False)
    ignore_patterns = Column(JSON)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    project = relationship("Project", back_populates="drift_settings")

    __table_args__ = (
        Index("idx_drift_settings_enabled", "enabled"),
    )

