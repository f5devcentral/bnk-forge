"""Task model.

ParallelExecution model and parallel_executions / parallel_execution_modules tables
were dropped in D-001 Phase 3 S3b (migration v2_119). The ParallelExecutionStatus
enum is retained in models.enums for backward-compatible vocabulary.
"""

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import deferred, relationship
from sqlalchemy.sql import func

from database import Base
from models.enums import TaskStatus


class Task(Base):
    """Track asynchronous tasks executed via Celery task queue."""
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)

    celery_task_id = Column(String(255), unique=True, nullable=True, index=True)
    task_type = Column(String(50), nullable=False, index=True)

    status = Column(String(50), nullable=False, index=True, default=TaskStatus.QUEUED)

    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    module_id = Column(Integer, ForeignKey("project_modules.id", ondelete="SET NULL"), nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    started_at = Column(DateTime(timezone=True), nullable=True, index=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Float, nullable=True)

    triggered_by = Column(String(255), default="user")
    command = Column(Text)
    working_directory = Column(String(1000))

    exit_code = Column(Integer, nullable=True)
    logs = deferred(Column(Text))
    error = Column(Text, nullable=True)

    # NOTE: Column is 'meta_data' (not 'metadata') because 'metadata' is reserved by SQLAlchemy
    meta_data = Column(JSON)

    # Operations-log housekeeping (#21): archived tasks are hidden from the
    # default ops-log view but retained until explicitly deleted/cleaned up.
    archived = Column(Boolean, nullable=False, server_default="false", default=False, index=True)

    # D-001 Phase 3 (S2): stable per-run identifier shared by all Task rows
    # created by a single deploy/destroy run. Enables run-scoped progress queries
    # without the parallel_executions table. Nullable for pre-S2 rows.
    # Index defined explicitly in __table_args__ as idx_task_run_handle — no index=True here.
    run_handle = Column(String(255), nullable=True)

    # Relationships
    project = relationship("Project")
    module = relationship("ProjectModule")

    __table_args__ = (
        Index("idx_task_project_status", "project_id", "status"),
        Index("idx_task_module_status", "module_id", "status"),
        Index("idx_task_type_status", "task_type", "status"),
        Index("idx_task_created", "created_at"),
        Index("idx_task_run_handle", "run_handle"),
    )
