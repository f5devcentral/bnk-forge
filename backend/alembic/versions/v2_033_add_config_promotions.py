"""Add config_promotions table for cross-cluster config promotion history (UX-013).

Revision ID: v2_033
Revises: v2_032_config_snapshots
Create Date: 2026-02-19
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "v2_033"
down_revision = "v2_032_config_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "config_promotions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "source_cluster_id",
            sa.Integer(),
            sa.ForeignKey("kubernetes_clusters.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "target_cluster_id",
            sa.Integer(),
            sa.ForeignKey("kubernetes_clusters.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_cluster_name", sa.String(255), nullable=False),
        sa.Column("target_cluster_name", sa.String(255), nullable=False),
        sa.Column("change_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("changes_summary", sa.JSON(), nullable=True),
        sa.Column("applied_by", sa.String(255), nullable=True),
        sa.Column("success", sa.Boolean(), server_default="1"),
        sa.Column("error_message", sa.String(1000), nullable=True),
        sa.Column("applied_count", sa.Integer(), server_default="0"),
        sa.Column("failed_count", sa.Integer(), server_default="0"),
        sa.Column("skipped_count", sa.Integer(), server_default="0"),
        sa.Column("applied_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_config_promotion_applied_at", "config_promotions", ["applied_at"])
    op.create_index("idx_config_promotion_source", "config_promotions", ["source_cluster_id"])
    op.create_index("idx_config_promotion_target", "config_promotions", ["target_cluster_id"])


def downgrade() -> None:
    op.drop_index("idx_config_promotion_target", table_name="config_promotions")
    op.drop_index("idx_config_promotion_source", table_name="config_promotions")
    op.drop_index("idx_config_promotion_applied_at", table_name="config_promotions")
    op.drop_table("config_promotions")
