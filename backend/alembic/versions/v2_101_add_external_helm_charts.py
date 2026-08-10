"""Add external_helm_charts table.

Admin-managed catalog of external Helm charts referenced by blueprints.
Each entry pins a chart+version from an external Helm repo.

Revision ID: v2_090
Revises: v2_089
Create Date: 2026-04-28
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_101"
down_revision = "v2_100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: table may already exist if migrations ran twice during
    # backup restore (alembic Config reuse caused double-run).
    bind = op.get_bind()
    result = bind.execute(sa.text(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = 'external_helm_charts'"
    ))
    if result.fetchone():
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


def downgrade() -> None:
    op.drop_table("external_helm_charts")
