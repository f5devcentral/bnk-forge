"""Add per-interface start-host offset to project_dpu_settings.

Used by the /dpus/auto-assign-vlan-ips endpoint to deterministically
assign per-DPU IPs starting at `<network>.<start_host>` and incrementing
per DPU (stable order: oldest DPU first).

Revision ID: v2_066
Revises: v2_065
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_066"
down_revision = "v2_065"
branch_labels = None
depends_on = None


_COLS = [
    "external_vlan_start_host",
    "internal_vlan_start_host",
    "storage_vlan_start_host",
]


def upgrade() -> None:
    for name in _COLS:
        op.add_column(
            "project_dpu_settings",
            sa.Column(name, sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    for name in reversed(_COLS):
        op.drop_column("project_dpu_settings", name)
