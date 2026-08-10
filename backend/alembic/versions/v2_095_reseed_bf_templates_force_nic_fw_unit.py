"""Re-seed bf.conf templates: enable mlnx-fw-updater on every boot.

The BFB installer on BSP 2.9.x does not run a host NIC FW pass during
OS install — the bf.cfg ``WITH_NIC_FW_UPDATE`` / ``FORCE_NIC_FW_UPDATE``
directives that some older docs reference are silently ignored. NVIDIA
ships a different mechanism: ``openibd.service`` (already enabled in
the BFB) reads ``RUN_FW_UPDATER_ONBOOT`` from
``/etc/infiniband/openib.conf`` and runs ``mlnx_fw_updater.pl`` on
every boot when it's set to ``yes``. Default is unset → no auto
update.

Both seed templates' ``bfb_modify_os()`` now append
``RUN_FW_UPDATER_ONBOOT=yes`` to the target rootfs's openib.conf so
the existing on-boot path takes care of NIC FW upgrades. Downgrades
remain a manual operation (the in-box updater is upgrade-only without
``--force``).

Same gating mechanism as v2_087/v2_088 — only re-seeds rows that
match the original banner marker AND don't yet contain the v2_095
marker (``RUN_FW_UPDATER_ONBOOT=yes``). Operator-customised rows
keep their edits.

Revision ID: v2_095
Revises: v2_094
"""

import os

import sqlalchemy as sa

from alembic import op

revision = "v2_095"
down_revision = "v2_094"
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
        bind.execute(
            sa.text(
                "UPDATE bf_conf_templates SET content = :content "
                "WHERE content LIKE :old_marker "
                "AND content NOT LIKE :new_marker"
            ),
            {
                "content": new_content,
                "old_marker": entry["old_marker"],
                "new_marker": "%RUN_FW_UPDATER_ONBOOT=yes%",
            },
        )


def downgrade() -> None:
    pass
