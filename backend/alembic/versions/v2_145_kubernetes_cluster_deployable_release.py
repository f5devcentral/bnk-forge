"""ADR-478 P1b: add deployable_release_id FK to kubernetes_clusters.

Revision ID: v2_145
Revises: v2_144
Create Date: 2026-07-21

Adds kubernetes_clusters.deployable_release_id: nullable FK → bnk_deployable_release.id
ON DELETE SET NULL. Stamped at the Phase-2 cluster-link seam (ssh_tasks.py) so the
cluster row durably records the BNK release it was built with.
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_145"
down_revision = "v2_144"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "kubernetes_clusters",
        sa.Column(
            "deployable_release_id",
            sa.Integer(),
            sa.ForeignKey("bnk_deployable_release.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("kubernetes_clusters", "deployable_release_id")
