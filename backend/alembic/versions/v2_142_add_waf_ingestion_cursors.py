"""Add waf_ingestion_cursors table for ClickHouse ingest tracking.

Revision ID: v2_142
Revises: v2_141
Create Date: 2026-08-22
"""

import sqlalchemy as sa
from alembic import op

revision = "v2_142"
down_revision = "v2_141"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "waf_ingestion_cursors",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("cluster_id", sa.Integer(), nullable=False),
        sa.Column("log_file", sa.String(512), nullable=False),
        sa.Column("last_line_num", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("cluster_id", "log_file", name="uq_waf_cursor_cluster_file"),
    )
    op.create_index("ix_waf_ingestion_cursors_cluster_id", "waf_ingestion_cursors", ["cluster_id"])


def downgrade() -> None:
    op.drop_index("ix_waf_ingestion_cursors_cluster_id", table_name="waf_ingestion_cursors")
    op.drop_table("waf_ingestion_cursors")
