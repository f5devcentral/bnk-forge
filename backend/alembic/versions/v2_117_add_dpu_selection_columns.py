"""Add DPU selection columns to bare_metal_hosts.

Supports the dual_dpu_obmc topology where one of two DPUs is selected
as the deployment target (non-mgmt DPU) and rshim is accessed via the
DPU's OpenBMC interface.

Revision ID: v2_115
Revises: v2_114
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_117"
down_revision = "v2_116"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bare_metal_hosts",
        sa.Column("deploy_dpu_pci_address", sa.String(50), nullable=True),
    )
    op.add_column(
        "bare_metal_hosts",
        sa.Column("deploy_dpu_index", sa.Integer(), nullable=True),
    )
    op.add_column(
        "bare_metal_hosts",
        sa.Column("rshim_source", sa.String(20), nullable=True),
    )
    op.add_column(
        "bare_metal_hosts",
        sa.Column("bond_mode", sa.String(20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bare_metal_hosts", "bond_mode")
    op.drop_column("bare_metal_hosts", "rshim_source")
    op.drop_column("bare_metal_hosts", "deploy_dpu_index")
    op.drop_column("bare_metal_hosts", "deploy_dpu_pci_address")
