"""Add per-project VLAN defaults to project_dpu_settings.

Three interfaces (external, internal, storage) each with a project-wide
VLAN id and IPv4/IPv6 subnets. Per-DPU IPs are auto-populated from these
subnets at DPU-create time so users only edit the host part.

Revision ID: v2_065
Revises: v2_064
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_065"
down_revision = "v2_064"
branch_labels = None
depends_on = None


_COLS = [
    ("external_vlan_id", sa.Integer()),
    ("external_vlan_ipv4_subnet", sa.String(length=64)),
    ("external_vlan_ipv6_subnet", sa.String(length=128)),
    ("internal_vlan_id", sa.Integer()),
    ("internal_vlan_ipv4_subnet", sa.String(length=64)),
    ("internal_vlan_ipv6_subnet", sa.String(length=128)),
    ("storage_vlan_id", sa.Integer()),
    ("storage_vlan_ipv4_subnet", sa.String(length=64)),
    ("storage_vlan_ipv6_subnet", sa.String(length=128)),
]


def upgrade() -> None:
    for name, col_type in _COLS:
        op.add_column("project_dpu_settings", sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLS):
        op.drop_column("project_dpu_settings", name)
