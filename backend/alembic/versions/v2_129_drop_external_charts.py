"""Drop external_helm_charts table.

External Charts feature removed — the table was orphaned (repo_url/chart_name
never used at deploy time). The cert-manager version fallback previously
seeded via this table now uses a module-level constant in blueprint_context.py.

Revision ID: v2_129
Revises: v2_128
Create Date: 2026-06-05
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_129"
down_revision = "v2_128"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The table was historically created by create_all at startup (no
    # create_table migration), so guard the drop to no-op when absent.
    bind = op.get_bind()
    if "external_helm_charts" in sa.inspect(bind).get_table_names():
        op.drop_table("external_helm_charts")


def downgrade() -> None:
    bind = op.get_bind()
    if "external_helm_charts" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "external_helm_charts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chart_key", sa.String(128), nullable=False, unique=True, index=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("repo_url", sa.String(512), nullable=False),
        sa.Column("chart_name", sa.String(255), nullable=False),
        sa.Column("chart_version", sa.String(64), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
