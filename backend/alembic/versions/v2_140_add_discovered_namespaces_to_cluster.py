"""Add discovered_namespaces JSON column to kubernetes_clusters.

Revision ID: v2_140
Revises: v2_139
Create Date: 2026-06-24

Adds:
  - kubernetes_clusters.discovered_namespaces: nullable JSON column (list of strings)
    Stores the set of namespaces where BNK/F5 components were actually discovered
    during a scan. Written back after each discovery run; used as the fast-path seed
    for subsequent discovery queries in addition to the static BNK_NAMESPACES fallback.
    NULL / empty means "not yet discovered" — no behaviour change for existing clusters.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "v2_140"
down_revision = "v2_139"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "kubernetes_clusters",
        sa.Column("discovered_namespaces", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("kubernetes_clusters", "discovered_namespaces")
