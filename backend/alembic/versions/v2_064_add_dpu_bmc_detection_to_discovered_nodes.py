"""Add BlueField BMC detection columns to discovered_nodes.

Enables Register-as-DPU flow: discovery probes now additionally try a
Redfish service-root GET on each node IP. When the node is a BlueField
DPU's embedded BMC, the register button appears in the Discovery tab.

Revision ID: v2_064
Revises: v2_063
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_064"
down_revision = "v2_063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "discovered_nodes",
        sa.Column("is_dpu_bmc", sa.Boolean(), nullable=True, server_default=sa.false()),
    )
    op.add_column(
        "discovered_nodes",
        sa.Column("bmc_product", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "discovered_nodes",
        sa.Column("bmc_serial_number", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("discovered_nodes", "bmc_serial_number")
    op.drop_column("discovered_nodes", "bmc_product")
    op.drop_column("discovered_nodes", "is_dpu_bmc")
