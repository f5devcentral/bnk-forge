"""Operator registration, connection, and command queue models."""

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base
from models.enums import OperatorCommandStatus, OperatorStatus


class OperatorRegistrationToken(Base):
    """Registration tokens for BNK operators."""
    __tablename__ = "operator_registration_tokens"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    token_hash = Column(String(255), nullable=False, unique=True, index=True)
    token_prefix = Column(String(20), nullable=False)

    max_uses = Column(Integer, nullable=True)
    use_count = Column(Integer, default=0, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    labels = Column(JSON, default=dict)

    is_active = Column(Boolean, default=True, nullable=False, index=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoked_by = Column(String(255), nullable=True)

    created_by = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_used_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    operators = relationship("ConnectedOperator", back_populates="registration_token", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_op_token_active_expires", "is_active", "expires_at"),
    )


class ConnectedOperator(Base):
    """Track operators connected to BNK-Forge."""
    __tablename__ = "connected_operators"

    id = Column(Integer, primary_key=True, index=True)
    operator_id = Column(String(64), unique=True, nullable=False, index=True)

    registration_token_id = Column(Integer, ForeignKey("operator_registration_tokens.id", ondelete="SET NULL"), nullable=True)
    cluster_name = Column(String(255), nullable=False, index=True)
    cluster_id = Column(Integer, ForeignKey("kubernetes_clusters.id", ondelete="SET NULL"), nullable=True, index=True)

    connectivity_mode = Column(
        String(50), default="direct_ws", nullable=False, server_default="direct_ws",
        index=True,
    )

    connectivity_config = Column(JSON, default=dict)
    labels = Column(JSON, default=dict)

    is_connected = Column(Boolean, default=False, nullable=False, index=True)
    last_heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    last_connected_at = Column(DateTime(timezone=True), nullable=True)
    last_disconnected_at = Column(DateTime(timezone=True), nullable=True)
    disconnect_reason = Column(String(500), nullable=True)

    operator_version = Column(String(50), nullable=True)
    kubernetes_version = Column(String(50), nullable=True)
    node_count = Column(Integer, nullable=True)
    nodes_ready = Column(Integer, nullable=True)

    last_health_report = Column(JSON, nullable=True)
    last_health_at = Column(DateTime(timezone=True), nullable=True)

    commands_executed = Column(Integer, default=0, nullable=False)
    commands_failed = Column(Integer, default=0, nullable=False)
    uptime_seconds = Column(Integer, default=0, nullable=False)

    status = Column(String(50), default=OperatorStatus.REGISTERED, nullable=False, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    registration_token = relationship("OperatorRegistrationToken", back_populates="operators")
    cluster = relationship("KubernetesCluster")

    __table_args__ = (
        Index("idx_op_connected_status", "is_connected", "status"),
        Index("idx_op_cluster_name", "cluster_name"),
    )


class OperatorCommandQueue(Base):
    """Queued commands for operators using polling mode."""
    __tablename__ = "operator_command_queue"

    id = Column(Integer, primary_key=True, index=True)
    command_id = Column(String(64), unique=True, nullable=False, index=True)
    operator_id = Column(String(64), nullable=False, index=True)

    action = Column(String(100), nullable=False)
    payload = Column(JSON, default=dict)
    timeout_seconds = Column(Integer, default=300, nullable=False)

    status = Column(String(50), default=OperatorCommandStatus.QUEUED, nullable=False, index=True)
    picked_up_at = Column(DateTime(timezone=True), nullable=True)

    result = Column(JSON, nullable=True)
    output_lines = Column(JSON, default=list)
    error_message = Column(String(2000), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_cmd_queue_operator_status", "operator_id", "status"),
    )
