"""Add fleet policy + evaluation tables for D-022 Phase 3.

Adds two tables:
  fleet_policies           — desired-state policy over a fleet Target
  fleet_policy_evaluations — immutable per-evaluation compliance snapshot

String statuses (no DB enum) to allow future extension without migration.

Revision ID: v2_124
Revises: v2_123
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_124"
down_revision = "v2_123"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # fleet_policies
    op.create_table(
        "fleet_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("mode", sa.String(50), nullable=False),
        sa.Column("desired", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_id"], ["fleet_targets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fleet_policies_id", "fleet_policies", ["id"])
    op.create_index("idx_fleet_policy_project", "fleet_policies", ["project_id"])
    op.create_index("idx_fleet_policy_target", "fleet_policies", ["target_id"])
    op.create_index("idx_fleet_policy_kind", "fleet_policies", ["kind"])

    # fleet_policy_evaluations
    op.create_table(
        "fleet_policy_evaluations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("policy_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evaluated_by", sa.String(255), nullable=True),
        sa.Column("selector_snapshot", sa.JSON(), nullable=True),
        sa.Column("desired_snapshot", sa.JSON(), nullable=False),
        sa.Column("compliant_count", sa.Integer(), nullable=False),
        sa.Column("drifted_count", sa.Integer(), nullable=False),
        sa.Column("unknown_count", sa.Integer(), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=False),
        sa.Column("member_results", sa.JSON(), nullable=False),
        sa.Column("enforced_run_id", sa.Integer(), nullable=True),
        sa.Column("mode", sa.String(50), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["policy_id"], ["fleet_policies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["enforced_run_id"], ["fleet_bulk_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fleet_policy_evaluations_id", "fleet_policy_evaluations", ["id"])
    op.create_index("idx_policy_eval_policy", "fleet_policy_evaluations", ["policy_id"])
    op.create_index("idx_policy_eval_project", "fleet_policy_evaluations", ["project_id"])
    op.create_index("idx_policy_eval_evaluated_at", "fleet_policy_evaluations", ["evaluated_at"])


def downgrade() -> None:
    op.drop_index("idx_policy_eval_evaluated_at", table_name="fleet_policy_evaluations")
    op.drop_index("idx_policy_eval_project", table_name="fleet_policy_evaluations")
    op.drop_index("idx_policy_eval_policy", table_name="fleet_policy_evaluations")
    op.drop_index("ix_fleet_policy_evaluations_id", table_name="fleet_policy_evaluations")
    op.drop_table("fleet_policy_evaluations")

    op.drop_index("idx_fleet_policy_kind", table_name="fleet_policies")
    op.drop_index("idx_fleet_policy_target", table_name="fleet_policies")
    op.drop_index("idx_fleet_policy_project", table_name="fleet_policies")
    op.drop_index("ix_fleet_policies_id", table_name="fleet_policies")
    op.drop_table("fleet_policies")
