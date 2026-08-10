"""Add ssh_host_override to kubernetes_clusters.

Allows a cluster's SSH tunnel to connect to a different SSH host than the
stored credential's host — useful for kind clusters on nodes that share the
same SSH key as the project jumphost.

Revision ID: v2_060
Revises: v2_059
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_060"
down_revision = "v2_059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "kubernetes_clusters",
        sa.Column("ssh_host_override", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("kubernetes_clusters", "ssh_host_override")
