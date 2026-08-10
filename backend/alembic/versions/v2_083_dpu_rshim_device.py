"""Add per-DPU `rshim_device` column.

Hosts that carry more than one BlueField DPU expose them as separate
`/dev/rshimN` devices (rshim0, rshim1, ...). Without per-DPU mapping
every action lands on `rshim0`, which silently drives the wrong DPU on
multi-DPU hosts. This column persists the rshim index Forge resolved
during probe so flash / serial console / force-restart / factory-reset
all target the right device.

Null means "never probed" — callers default to `rshim0` so existing
single-DPU rows keep working without a backfill.

Revision ID: v2_083
Revises: v2_082
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_083"
down_revision = "v2_082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dpus",
        sa.Column("rshim_device", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dpus", "rshim_device")
