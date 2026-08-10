"""Add fleet targeting + bulk-ops tables for D-022 Phase 2.

Adds four tables:
  fleet_targets    — named label selectors
  fleet_decisions  — immutable resolved member-id snapshots
  fleet_bulk_runs  — parent bulk operation orchestrator record
  fleet_bulk_run_results — per-member wave step results

String statuses (no DB enum) to allow future extension without migration.

Revision ID: v2_123
Revises: v2_122
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_123"
down_revision = "v2_122"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # fleet_targets
    op.create_table(
        "fleet_targets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("selector", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fleet_targets_id", "fleet_targets", ["id"])
    op.create_index("idx_fleet_target_project", "fleet_targets", ["project_id"])

    # fleet_decisions
    op.create_table(
        "fleet_decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("selector_snapshot", sa.JSON(), nullable=False),
        sa.Column("resolved_member_ids", sa.JSON(), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_by", sa.String(255), nullable=True),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["target_id"], ["fleet_targets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fleet_decisions_id", "fleet_decisions", ["id"])
    op.create_index("idx_fleet_decision_target", "fleet_decisions", ["target_id"])
    op.create_index("idx_fleet_decision_project", "fleet_decisions", ["project_id"])
    op.create_index("idx_fleet_decision_status", "fleet_decisions", ["status"])

    # fleet_bulk_runs
    op.create_table(
        "fleet_bulk_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("decision_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("action_params", sa.JSON(), nullable=True),
        sa.Column("wave_by", sa.String(50), nullable=False),
        sa.Column("concurrency", sa.Integer(), nullable=False),
        sa.Column("gate_mode", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("current_wave_index", sa.Integer(), nullable=False),
        sa.Column("total_waves", sa.Integer(), nullable=True),
        sa.Column("celery_task_id", sa.String(255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["decision_id"], ["fleet_decisions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fleet_bulk_runs_id", "fleet_bulk_runs", ["id"])
    op.create_index("idx_fleet_bulk_run_decision", "fleet_bulk_runs", ["decision_id"])
    op.create_index("idx_fleet_bulk_run_project", "fleet_bulk_runs", ["project_id"])
    op.create_index("idx_fleet_bulk_run_status", "fleet_bulk_runs", ["status"])

    # fleet_bulk_run_results
    op.create_table(
        "fleet_bulk_run_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("fleet_member_id", sa.Integer(), nullable=False),
        sa.Column("wave_index", sa.Integer(), nullable=False),
        sa.Column("fault_domain", sa.String(255), nullable=True),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["fleet_bulk_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "fleet_member_id", name="uq_bulk_run_member"),
    )
    op.create_index("ix_fleet_bulk_run_results_id", "fleet_bulk_run_results", ["id"])
    op.create_index("idx_fleet_bulk_run_result_run", "fleet_bulk_run_results", ["run_id"])


def downgrade() -> None:
    op.drop_index("idx_fleet_bulk_run_result_run", table_name="fleet_bulk_run_results")
    op.drop_index("ix_fleet_bulk_run_results_id", table_name="fleet_bulk_run_results")
    op.drop_table("fleet_bulk_run_results")

    op.drop_index("idx_fleet_bulk_run_status", table_name="fleet_bulk_runs")
    op.drop_index("idx_fleet_bulk_run_project", table_name="fleet_bulk_runs")
    op.drop_index("idx_fleet_bulk_run_decision", table_name="fleet_bulk_runs")
    op.drop_index("ix_fleet_bulk_runs_id", table_name="fleet_bulk_runs")
    op.drop_table("fleet_bulk_runs")

    op.drop_index("idx_fleet_decision_status", table_name="fleet_decisions")
    op.drop_index("idx_fleet_decision_project", table_name="fleet_decisions")
    op.drop_index("idx_fleet_decision_target", table_name="fleet_decisions")
    op.drop_index("ix_fleet_decisions_id", table_name="fleet_decisions")
    op.drop_table("fleet_decisions")

    op.drop_index("idx_fleet_target_project", table_name="fleet_targets")
    op.drop_index("ix_fleet_targets_id", table_name="fleet_targets")
    op.drop_table("fleet_targets")
