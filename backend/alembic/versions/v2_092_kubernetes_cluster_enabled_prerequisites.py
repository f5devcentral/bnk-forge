"""Add enabled_prerequisites JSON column to kubernetes_clusters.

Revision ID: v2_092
Revises: v2_091
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_092"
down_revision = "v2_091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "kubernetes_clusters",
        sa.Column("enabled_prerequisites", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("kubernetes_clusters", "enabled_prerequisites")
