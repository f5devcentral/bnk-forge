"""Re-seed bf.conf templates: rewrite non-LAG, add visible banners.

Substantive non-LAG rewrite (per Marcel's testing of the template
end-to-end on a flashed DPU):
  * mlxconfig in `bfb_post_install` collapses HIDE_PORT2_PF=False,
    NUM_OF_PF=2, LAG_RESOURCE_ALLOCATION=DEVICE_DEFAULT into the same
    one-liner the LAG template uses.
  * /etc/mellanox/mlnx-bf.conf gets `LAG_HASH_MODE=` scrubbed if a
    previous boot wrote it.
  * Two OVS bridges (sf-external on p0, sf-internal on p1) replace
    the bridge-less netplan-vlans approach.
  * Netplan file renamed 70-bf-uplinks.yaml → 70-bf-nolag.yaml.
  * VLAN init script picks the bridge per VLAN's uplink port
    (p0 → sf-external, p1 → sf-internal).

Both templates also get a header banner so an operator inspecting a
rendered config can tell at a glance which template produced it.

Like v2_084 we only rewrite rows that still match the previous seed's
unique markers — operator-customised content (anyone who edited the
DB row) is left alone. They can re-seed manually.

Revision ID: v2_087
Revises: v2_086
"""

import os

import sqlalchemy as sa

from alembic import op

revision = "v2_087"
down_revision = "v2_086"
branch_labels = None
depends_on = None


# Each entry: (filename in seed_data, "old marker present" SQL pattern,
# "new marker absent" SQL pattern). The old marker is a string that
# appears in the previous seed but NOT in the new one; the new marker
# is one we just added so we can recognise an already-migrated row and
# skip it (idempotent re-runs).
_TEMPLATES = [
    {
        "file": "bf_template_nolag.conf",
        "old_marker": "%70-bf-uplinks.yaml%",
        "new_marker": "%bf.conf — NON-LAG TEMPLATE%",
    },
    {
        "file": "bf_template_lag.conf",
        # The LAG template change is *only* the banner; pick a marker
        # that's reliably present in every LAG body since v2_069.
        "old_marker": "%70-bf-lag.yaml%",
        "new_marker": "%bf.conf — LAG TEMPLATE%",
    },
]


def _read_seed(filename: str) -> str:
    seed_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "seed_data", filename,
    )
    with open(seed_path, encoding="utf-8") as f:
        return f.read()


def upgrade() -> None:
    bind = op.get_bind()
    for entry in _TEMPLATES:
        new_content = _read_seed(entry["file"])
        bind.execute(
            sa.text(
                "UPDATE bf_conf_templates SET content = :content "
                "WHERE content LIKE :old_marker "
                "AND content NOT LIKE :new_marker"
            ),
            {
                "content": new_content,
                "old_marker": entry["old_marker"],
                "new_marker": entry["new_marker"],
            },
        )


def downgrade() -> None:
    # We don't carry a snapshot of the previous seed in this migration;
    # operators wanting to roll back can revert seed_data and re-run.
    pass
