"""ADR-494 Phase B: add running_release_id FK to kubernetes_clusters.

Revision ID: v2_149
Revises: v2_148
Create Date: 2026-07-23

Adds kubernetes_clusters.running_release_id: nullable FK → bnk_releases.id
ON DELETE SET NULL. Written by discovery/scan write-back so the cluster row
durably records the BNK release line it is currently running.
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_149"
down_revision = "v2_148"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "kubernetes_clusters",
        sa.Column(
            "running_release_id",
            sa.Integer(),
            sa.ForeignKey("bnk_releases.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("kubernetes_clusters", "running_release_id")
