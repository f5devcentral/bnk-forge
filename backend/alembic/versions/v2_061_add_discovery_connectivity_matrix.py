"""Add connectivity matrix columns to discovery_jobs.

Stores the post-discovery inter-node ICMP connectivity sweep results
grouped by interface kind (builtin vs bluefield).

Revision ID: v2_061
Revises: v2_060
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_061"
down_revision = "v2_060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "discovery_jobs",
        sa.Column("connectivity_status", sa.String(50), nullable=True),
    )
    op.add_column(
        "discovery_jobs",
        sa.Column("connectivity_matrix", sa.JSON, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("discovery_jobs", "connectivity_matrix")
    op.drop_column("discovery_jobs", "connectivity_status")
