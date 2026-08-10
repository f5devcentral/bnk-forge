"""AlertChannel and AlertHistory models."""

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base
from models.enums import AlertStatus


class AlertChannel(Base):
    """Configurable alert destinations for health changes, drift detection, and deploy events."""
    __tablename__ = "alert_channels"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    channel_type = Column(String(50), nullable=False)
    enabled = Column(Boolean, default=True, index=True)

    url = Column(Text)
    headers = Column(JSON)
    secret = Column(Text)

    email_to = Column(JSON)
    email_from = Column(String(255))

    event_types = Column(JSON, default=list)
    project_ids = Column(JSON, default=list)
    cluster_ids = Column(JSON, default=list)

    min_interval_seconds = Column(Integer, default=60)
    last_fired_at = Column(DateTime(timezone=True))

    total_sent = Column(Integer, default=0)
    total_failed = Column(Integer, default=0)
    last_error = Column(Text)
    last_success_at = Column(DateTime(timezone=True))

    description = Column(Text)
    created_by = Column(String(255))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    alert_history = relationship("AlertHistory", back_populates="channel", cascade="all, delete-orphan")


class AlertHistory(Base):
    """Record of every alert sent (or failed to send)."""
    __tablename__ = "alert_history"

    id = Column(Integer, primary_key=True, index=True)
    channel_id = Column(Integer, ForeignKey("alert_channels.id", ondelete="CASCADE"), nullable=False, index=True)

    event_type = Column(String(50), nullable=False)
    severity = Column(String(20))
    title = Column(String(500), nullable=False)
    message = Column(Text)

    project_id = Column(Integer, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    cluster_id = Column(Integer, ForeignKey("kubernetes_clusters.id", ondelete="SET NULL"), nullable=True)
    payload = Column(JSON)

    status = Column(String(20), default=AlertStatus.SENT)
    http_status_code = Column(Integer)
    response_body = Column(Text)
    error_message = Column(Text)
    duration_ms = Column(Integer)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    # Relationships
    channel = relationship("AlertChannel", back_populates="alert_history")

    __table_args__ = (
        Index("idx_alert_history_channel_created", "channel_id", "created_at"),
        Index("idx_alert_history_event_type", "event_type"),
        Index("idx_alert_history_created", "created_at"),
    )
