"""Rename bf.conf templates to shorter, clearer names.

The original seed names were near-duplicates of the descriptions,
which made the dropdown on the DPU Project Settings card confusing
when picking between LAG vs. non-LAG. Shorter labels fit the dropdown
better and read unambiguously:

  "F5/Nvidia RCP RA with LACP based LAG uplinks" → "Uplinks via LAG (bond0)"
  "Interfaces attached to p0 and/or p1 uplink"   → "Uplinks via p0/p1 (no LAG)"

Only renames rows whose `name` still matches the original seed —
user-authored templates or already-renamed ones are left untouched.

Revision ID: v2_077
Revises: v2_076
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_077"
down_revision = "v2_076"
branch_labels = None
depends_on = None


_RENAMES = [
    ("F5/Nvidia RCP RA with LACP based LAG uplinks", "Uplinks via LAG (bond0)"),
    ("Interfaces attached to p0 and/or p1 uplink", "Uplinks via p0/p1 (no LAG)"),
]


def upgrade() -> None:
    conn = op.get_bind()
    for old, new in _RENAMES:
        conn.execute(
            sa.text("UPDATE bf_conf_templates SET name = :new WHERE name = :old"),
            {"old": old, "new": new},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for old, new in _RENAMES:
        conn.execute(
            sa.text("UPDATE bf_conf_templates SET name = :old WHERE name = :new"),
            {"old": old, "new": new},
        )
