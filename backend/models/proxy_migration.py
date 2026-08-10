"""
Proxy Migration models — state machine run + per-step records.

Mirrors the bare_metal (BareMetalDeployment / DeploymentStep) shape.
One ProxyMigration run encapsulates: translate → deploy_bnk → verify →
cutover (operator-confirmed) → teardown_old.
"""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base
from models.enums import MigrationStepStatus, ProxyMigrationStatus


class ProxyMigration(Base):
    """A single proxy-to-BNK migration run."""

    __tablename__ = "proxy_migrations"

    id = Column(Integer, primary_key=True, index=True)
    cluster_id = Column(Integer, ForeignKey("kubernetes_clusters.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)

    # Source descriptor — snapshot of the detected proxy at migration start
    # Keys: proxy_type, source_kind, class_name, namespace
    source_descriptor = Column(JSON, nullable=False)

    # Translated manifest snapshot (combined_yaml from P2 translate_to_bnk)
    translated_manifest = Column(Text, nullable=True)

    # BNK Gateway coordinates (populated after deploy_bnk step)
    bnk_gateway_name = Column(String(255), nullable=True)
    bnk_gateway_namespace = Column(String(255), nullable=True)

    # Verify result snapshot (JSON: programmed, reachable, passed, reasons)
    verify_result = Column(JSON, nullable=True)

    # Old proxy teardown info (how to tear it down)
    # Keys: helm_release, helm_namespace, kubectl_resources (list of "kind/name ns")
    teardown_info = Column(JSON, nullable=True)

    # Run status
    status = Column(String(50), nullable=False, default=ProxyMigrationStatus.PENDING)
    current_step = Column(String(50), nullable=True)

    # Error
    error_message = Column(Text, nullable=True)
    error_step = Column(String(50), nullable=True)

    # Timing
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Float, nullable=True)

    # Who triggered + who confirmed cutover + who confirmed teardown
    triggered_by = Column(String(255), nullable=True)
    cutover_confirmed_by = Column(String(255), nullable=True)
    cutover_confirmed_at = Column(DateTime(timezone=True), nullable=True)
    teardown_confirmed_by = Column(String(255), nullable=True)
    teardown_confirmed_at = Column(DateTime(timezone=True), nullable=True)

    # Celery task reference
    celery_task_id = Column(String(255), nullable=True)

    # Steps
    steps = relationship(
        "ProxyMigrationStep",
        back_populates="migration",
        order_by="ProxyMigrationStep.step_index",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_proxy_migrations_cluster_status", "cluster_id", "status"),
        Index("ix_proxy_migrations_project_id", "project_id"),
    )


class ProxyMigrationStep(Base):
    """A single step within a ProxyMigration run."""

    __tablename__ = "proxy_migration_steps"

    id = Column(Integer, primary_key=True, index=True)
    migration_id = Column(Integer, ForeignKey("proxy_migrations.id", ondelete="CASCADE"), nullable=False, index=True)

    step_index = Column(Integer, nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(String(255), nullable=False)

    status = Column(String(50), nullable=False, default=MigrationStepStatus.PENDING)

    # Step output (logs, verify result JSON, etc.)
    output_log = Column(Text, nullable=True)
    result_data = Column(JSON, nullable=True)  # structured output (e.g. VerifyResult)

    error_message = Column(Text, nullable=True)
    exit_code = Column(Integer, nullable=True)

    # Timing
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Float, nullable=True)

    # Whether operator confirmation is required before this step can proceed
    requires_confirm = Column(Boolean, default=False)

    migration = relationship("ProxyMigration", back_populates="steps")

    __table_args__ = (
        Index("ix_proxy_migration_steps_migration_idx", "migration_id", "step_index"),
    )
