"""Add discovery persistence columns to bare_metal_hosts.

Revision ID: v2_059
Revises: v2_058
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_059"
down_revision = "v2_058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add typed discovery fact columns for direct access by step executors
    op.add_column("bare_metal_hosts", sa.Column("nic_mode", sa.String(50), nullable=True))
    op.add_column("bare_metal_hosts", sa.Column("mst_device", sa.String(255), nullable=True))
    op.add_column("bare_metal_hosts", sa.Column("rshim_present", sa.Boolean(), nullable=True))
    op.add_column("bare_metal_hosts", sa.Column("default_route_iface", sa.String(100), nullable=True))
    op.add_column("bare_metal_hosts", sa.Column("phase_c_deposited", sa.Boolean(), nullable=True))
    op.add_column("bare_metal_hosts", sa.Column("phase_c_completed", sa.Boolean(), nullable=True))
    op.add_column("bare_metal_hosts", sa.Column("pf_interfaces", sa.JSON(), nullable=True))
    op.add_column("bare_metal_hosts", sa.Column("vf_count", sa.Integer(), nullable=True))
    op.add_column("bare_metal_hosts", sa.Column("hugepages_host_gb", sa.Integer(), nullable=True))
    # Add full discovery result cache
    op.add_column("bare_metal_hosts", sa.Column("last_discovery_result", sa.JSON(), nullable=True))

    # Add deployment phase/step selection columns (CON-021)
    op.add_column("bare_metal_deployments", sa.Column("selected_phases", sa.JSON(), nullable=True))
    op.add_column("bare_metal_deployments", sa.Column("selected_steps", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("bare_metal_deployments", "selected_steps")
    op.drop_column("bare_metal_deployments", "selected_phases")
    op.drop_column("bare_metal_hosts", "last_discovery_result")
    op.drop_column("bare_metal_hosts", "hugepages_host_gb")
    op.drop_column("bare_metal_hosts", "vf_count")
    op.drop_column("bare_metal_hosts", "pf_interfaces")
    op.drop_column("bare_metal_hosts", "phase_c_completed")
    op.drop_column("bare_metal_hosts", "phase_c_deposited")
    op.drop_column("bare_metal_hosts", "default_route_iface")
    op.drop_column("bare_metal_hosts", "rshim_present")
    op.drop_column("bare_metal_hosts", "mst_device")
    op.drop_column("bare_metal_hosts", "nic_mode")
