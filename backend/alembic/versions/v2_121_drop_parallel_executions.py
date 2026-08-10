"""Drop parallel_executions and parallel_execution_modules tables.

D-001 Phase 3 S3b: The parallel_executions and parallel_execution_modules tables
are no longer used. Run progress is tracked entirely via Task rows (run_handle
column added in v2_120). The ParallelExecution ORM model and ParallelExecutionModule
ORM model are removed; the ParallelExecutionStatus enum vocabulary is retained.

Data is NOT restored on downgrade (these tables stored ephemeral session records;
restoring them would not restore any meaningful state).

Revision ID: v2_121
Revises: v2_120
Create Date: 2026-05-27
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON

from alembic import op

revision = "v2_121"
down_revision = "v2_120"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop parallel_execution_modules first (FK → parallel_executions.id ondelete=CASCADE)
    op.drop_index("idx_pem_module", table_name="parallel_execution_modules")
    op.drop_index("idx_pem_execution_status", table_name="parallel_execution_modules")
    op.drop_table("parallel_execution_modules")

    # Drop parallel_executions (PG auto-drops its indexes)
    op.drop_index("idx_parallel_exec_started", table_name="parallel_executions")
    op.drop_index("idx_parallel_exec_action", table_name="parallel_executions")
    op.drop_index("idx_parallel_exec_project_status", table_name="parallel_executions")
    op.drop_table("parallel_executions")


def downgrade() -> None:
    # Recreate parallel_executions with the EXACT schema as it existed at v2_118:
    #   - v2_012: original table (DateTime columns, JSON result cols, metadata col)
    #   - v2_038: added parallel_execution_modules; JSON cols NOT dropped (explicit
    #             migration note: "JSON columns NOT dropped from source tables to allow
    #             gradual transition"); successful_modules / failed_modules / skipped_modules
    #             and metadata remain
    #   - v2_039: DateTime → TIMESTAMPTZ (started_at, completed_at, created_at, updated_at)
    # Data is NOT restored — these were ephemeral session records.
    op.create_table(
        "parallel_executions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), server_default="pending"),
        sa.Column("current_layer", sa.Integer(), server_default="0"),
        sa.Column("total_layers", sa.Integer(), nullable=True),
        sa.Column("layers_data", JSON, nullable=True),
        sa.Column("execution_mode", sa.String(50), server_default="parallel"),
        sa.Column("orchestrator_task_id", sa.String(255), unique=True, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("estimated_duration_minutes", sa.Integer(), nullable=True),
        # JSON result columns from v2_012 — NOT dropped by v2_038
        sa.Column("successful_modules", JSON, server_default="[]"),
        sa.Column("failed_modules", JSON, server_default="[]"),
        sa.Column("skipped_modules", JSON, server_default="[]"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_layer", sa.Integer(), nullable=True),
        sa.Column("triggered_by", sa.String(255), server_default="user"),
        # Column name is "metadata" (from v2_012) — NOT "execution_metadata"
        sa.Column("metadata", JSON, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "idx_parallel_exec_project_status",
        "parallel_executions",
        ["project_id", "status"],
    )
    op.create_index(
        "idx_parallel_exec_action",
        "parallel_executions",
        ["action"],
    )
    op.create_index(
        "idx_parallel_exec_started",
        "parallel_executions",
        ["started_at"],
    )

    # Recreate parallel_execution_modules (DDL from v2_038)
    op.create_table(
        "parallel_execution_modules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "execution_id",
            sa.Integer(),
            sa.ForeignKey("parallel_executions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("module_id", sa.Integer(), nullable=False),
        sa.Column("result_status", sa.String(20), nullable=False),
    )
    op.create_index(
        "idx_pem_execution_status",
        "parallel_execution_modules",
        ["execution_id", "result_status"],
    )
    op.create_index(
        "idx_pem_module",
        "parallel_execution_modules",
        ["module_id"],
    )
