"""Add fleet_operation_strategies table + strategy_id/gate_resumes_at to fleet_bulk_runs (D-022 P6 Slice D).

Revision ID: v2_128
Revises: v2_127
Create Date: 2026-06-05

New table: fleet_operation_strategies
  id, project_id (FK projects nullable), name, description (Text null),
  target_id (FK fleet_targets nullable = reusable across fleets),
  wave_by (String: "fault_domain" | "label:<key>"),
  max_concurrency_pct (Integer 1-100),
  gate_kind (String: "approval"|"timed_wait"|"health_stable"),
  gate_wait_seconds (Integer null), created_by, created_at, updated_at,
  deleted_at (DateTime null soft-delete) + index on deleted_at.

Altered table: fleet_bulk_runs
  + strategy_id (FK fleet_operation_strategies nullable)
  + gate_resumes_at (DateTime null)
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "v2_128"
down_revision = "v2_127"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create fleet_operation_strategies table.
    op.create_table(
        "fleet_operation_strategies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("fleet_targets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("wave_by", sa.String(100), nullable=False, server_default="fault_domain"),
        sa.Column("max_concurrency_pct", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("gate_kind", sa.String(50), nullable=False, server_default="approval"),
        sa.Column("gate_wait_seconds", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_fleet_strategy_project", "fleet_operation_strategies", ["project_id"])
    op.create_index("idx_fleet_strategy_target", "fleet_operation_strategies", ["target_id"])
    op.create_index("idx_fleet_strategy_deleted", "fleet_operation_strategies", ["deleted_at"])

    # Add strategy_id + gate_resumes_at columns to fleet_bulk_runs.
    op.add_column(
        "fleet_bulk_runs",
        sa.Column(
            "strategy_id",
            sa.Integer(),
            sa.ForeignKey("fleet_operation_strategies.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "fleet_bulk_runs",
        sa.Column("gate_resumes_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_fleet_bulk_run_strategy", "fleet_bulk_runs", ["strategy_id"])


def downgrade() -> None:
    op.drop_index("idx_fleet_bulk_run_strategy", table_name="fleet_bulk_runs")
    op.drop_column("fleet_bulk_runs", "gate_resumes_at")
    op.drop_column("fleet_bulk_runs", "strategy_id")

    op.drop_index("idx_fleet_strategy_deleted", table_name="fleet_operation_strategies")
    op.drop_index("idx_fleet_strategy_target", table_name="fleet_operation_strategies")
    op.drop_index("idx_fleet_strategy_project", table_name="fleet_operation_strategies")
    op.drop_table("fleet_operation_strategies")
