"""Add discovery status + error columns to bare_metal_hosts.

Adds last_discovery_status and last_discovery_error so the frontend can
poll host state for real-time discovery progress feedback.
"""
import sqlalchemy as sa

from alembic import op

revision = "v2_107"
down_revision = "v2_106"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bare_metal_hosts",
        sa.Column("last_discovery_status", sa.String(32), nullable=True),
    )
    op.add_column(
        "bare_metal_hosts",
        sa.Column("last_discovery_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bare_metal_hosts", "last_discovery_error")
    op.drop_column("bare_metal_hosts", "last_discovery_status")
