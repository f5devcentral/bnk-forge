"""Add waf_panels table for panel builder.

Replaces waf_ingestion_cursors (Celery ingest removed; OTEL handles ingest now).

Revision ID: v2_143
Revises: v2_142
"""
import sqlalchemy as sa

from alembic import op

revision = "v2_143"
down_revision = "v2_142"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "waf_panels",
        # checkfirst=True handled at migration level — table may exist from manual creation
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("cluster_id", sa.Integer(), sa.ForeignKey("k8s_clusters.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("chart_type", sa.String(50), nullable=False, server_default="bar"),
        sa.Column("query_template", sa.String(100), nullable=False),
        sa.Column("groupby_field", sa.String(100), nullable=True),
        sa.Column("time_range", sa.String(10), nullable=False, server_default="7d"),
        sa.Column("panel_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("width", sa.String(10), nullable=False, server_default="full"),
        sa.Column("extra_config", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), onupdate=sa.text("now()")),
    )
    op.create_index("idx_waf_panels_cluster", "waf_panels", ["cluster_id", "panel_order"])


def downgrade() -> None:
    op.drop_index("idx_waf_panels_cluster", "waf_panels")
    op.drop_table("waf_panels")
