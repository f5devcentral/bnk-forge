"""Drop deprecated cost estimation tables and columns.

The cost estimation feature (Infracost integration) was deprecated in v2
and has been fully removed. This migration drops:
- The `cost_estimates` table
- The `last_estimated_cost` and `last_estimated_at` columns from `project_modules`

Revision ID: v2_042
Revises: v2_041
"""
import sqlalchemy as sa

from alembic import op

revision = "v2_042"
down_revision = "v2_041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the cost_estimates table entirely
    op.drop_table("cost_estimates")

    # Drop denormalized cost columns from project_modules
    op.drop_column("project_modules", "last_estimated_cost")
    op.drop_column("project_modules", "last_estimated_at")


def downgrade() -> None:
    # Recreate denormalized cost columns on project_modules
    op.add_column("project_modules", sa.Column("last_estimated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("project_modules", sa.Column("last_estimated_cost", sa.Float(), nullable=True))

    # Recreate the cost_estimates table
    op.create_table(
        "cost_estimates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("module_id", sa.Integer(), sa.ForeignKey("project_modules.id", ondelete="SET NULL"), nullable=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=True),
        sa.Column("total_monthly_cost", sa.Float(), nullable=True),
        sa.Column("past_total_monthly_cost", sa.Float(), nullable=True),
        sa.Column("diff_total_monthly_cost", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(10), server_default="USD"),
        sa.Column("resources", sa.JSON(), nullable=True),
        sa.Column("cost_components", sa.JSON(), nullable=True),
        sa.Column("infracost_json", sa.JSON(), nullable=True),
        sa.Column("infracost_version", sa.String(50), nullable=True),
        sa.Column("plan_summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(50), server_default="pending"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_cost_estimates_id", "cost_estimates", ["id"])
    op.create_index("ix_cost_estimates_project_id", "cost_estimates", ["project_id"])
    op.create_index("ix_cost_estimates_module_id", "cost_estimates", ["module_id"])
    op.create_index("idx_cost_project_module", "cost_estimates", ["project_id", "module_id"])
    op.create_index("idx_cost_created", "cost_estimates", ["created_at"])
