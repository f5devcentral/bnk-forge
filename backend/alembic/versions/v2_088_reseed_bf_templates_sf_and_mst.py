"""Re-seed bf.conf templates: 1 SF per PF, persist mst_pci across reboots.

Two small changes on top of v2_087:
  * Drop SF count from 3 per PF (sfnum=0,1,2) to just 1 (sfnum=1) —
    F5's BNK CNE runbook only creates the SF the OVS bridges actually
    reference (en3f*pf*sf1). Extras consumed `PF_TOTAL_SF` slots for
    no benefit.
  * Add `mst_pci` to /etc/modules-load.d/custom.conf so `mst start`
    works after a reboot without depending on `bfb_post_install`
    running again.

Same gating mechanism as v2_087 — only re-seeds rows that match the
v2_087 marker AND don't yet have the v2_088 marker (`mst_pci`).

Revision ID: v2_088
Revises: v2_087
"""

import os

import sqlalchemy as sa

from alembic import op

revision = "v2_088"
down_revision = "v2_087"
branch_labels = None
depends_on = None


_TEMPLATES = [
    {
        "file": "bf_template_nolag.conf",
        "old_marker": "%bf.conf — NON-LAG TEMPLATE%",
    },
    {
        "file": "bf_template_lag.conf",
        "old_marker": "%bf.conf — LAG TEMPLATE%",
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
        # The v2_088 marker we look for: presence of `mst_pci` in the
        # body (not in v2_087's seed). Skips rows operators have
        # customised since v2_087 — they keep their edits.
        bind.execute(
            sa.text(
                "UPDATE bf_conf_templates SET content = :content "
                "WHERE content LIKE :old_marker "
                "AND content NOT LIKE '%mst_pci%'"
            ),
            {
                "content": new_content,
                "old_marker": entry["old_marker"],
            },
        )


def downgrade() -> None:
    pass
