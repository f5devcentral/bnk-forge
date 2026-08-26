"""Add waf_dashboard_tabs table + tab_id on waf_panels for custom dashboard tabs.

Revision ID: v2_144
Revises: v2_143
"""
import sqlalchemy as sa

from alembic import op

revision = "v2_144"
down_revision = "v2_143"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "waf_dashboard_tabs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("cluster_id", sa.Integer(), nullable=False, index=True),  # no FK to keep schema portable, matches waf_panels
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("tab_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), onupdate=sa.text("now()")),
    )
    op.create_index("idx_waf_dashboard_tabs_cluster", "waf_dashboard_tabs", ["cluster_id", "tab_order"])
    op.add_column("waf_panels", sa.Column("tab_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("waf_panels", "tab_id")
    op.drop_index("idx_waf_dashboard_tabs_cluster", "waf_dashboard_tabs")
    op.drop_table("waf_dashboard_tabs")
