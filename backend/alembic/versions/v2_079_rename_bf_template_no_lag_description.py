"""Tighten the description for the no-LAG bf.conf template.

Old: "Interfaces attached to p0 and/or p1 uplink"
New: "VLAN interfaces attached p0 and/or p1 uplinks"

The new wording is more accurate about what the template actually does
(VLAN sub-interfaces on the physical uplinks, no bond). Only touches
the row whose description still matches the original seed.

Revision ID: v2_079
Revises: v2_078
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_079"
down_revision = "v2_078"
branch_labels = None
depends_on = None


_OLD = "Interfaces attached to p0 and/or p1 uplink"
_NEW = "VLAN interfaces attached p0 and/or p1 uplinks"


def upgrade() -> None:
    op.execute(
        sa.text("UPDATE bf_conf_templates SET description = :new WHERE description = :old")
        .bindparams(old=_OLD, new=_NEW)
    )


def downgrade() -> None:
    op.execute(
        sa.text("UPDATE bf_conf_templates SET description = :old WHERE description = :new")
        .bindparams(old=_OLD, new=_NEW)
    )
