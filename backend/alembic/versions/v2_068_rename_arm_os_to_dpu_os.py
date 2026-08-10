"""Rename dpus.arm_os_* columns to dpu_os_* for terminology consistency.

"ARM OS" was ambiguous — the BMC also runs on ARM (a secondary ARMv7
core). Nvidia's own docs and BFB filenames refer to the BlueField-3
main-core OS as "DPU OS", which pairs cleanly with "DPU BMC".

Revision ID: v2_068
Revises: v2_067
"""

from alembic import op

revision = "v2_068"
down_revision = "v2_067"
branch_labels = None
depends_on = None


_RENAMES = [
    ("arm_os_ip", "dpu_os_ip"),
    ("arm_os_reachable", "dpu_os_reachable"),
    ("arm_os_status", "dpu_os_status"),
]


def upgrade() -> None:
    for old, new in _RENAMES:
        op.alter_column("dpus", old, new_column_name=new)


def downgrade() -> None:
    for old, new in _RENAMES:
        op.alter_column("dpus", new, new_column_name=old)
