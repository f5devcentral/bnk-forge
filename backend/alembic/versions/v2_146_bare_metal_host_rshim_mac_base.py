"""ADR-478 P2: host-level tmfifo MAC base override for DPU flash.

Revision ID: v2_146
Revises: v2_145
Create Date: 2026-07-23

Adds bare_metal_hosts.net_rshim_mac_base: nullable string column.
When set, all DPUs flashed on the host enumerate their NET_RSHIM_MAC
from this base instead of the default "00:1a:ca:ff:ff:1".
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_146"
down_revision = "v2_145"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bare_metal_hosts",
        sa.Column("net_rshim_mac_base", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bare_metal_hosts", "net_rshim_mac_base")
