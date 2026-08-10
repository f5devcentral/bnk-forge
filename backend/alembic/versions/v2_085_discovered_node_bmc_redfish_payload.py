"""Add `bmc_redfish_payload` JSON column to discovered_nodes.

Phase 2 of BMC discovery polish: when the BMC probe authenticates,
we collect richer Redfish data (Manufacturer, Model, BIOS version, BMC
FW, NicMode, HostPrivilegeLevel, Power state). The frontend renders
this in the Discovery tab's expanded panel for `is_dpu_bmc` rows
instead of the OS/Kernel/Arch fields that never apply to a BMC.

Stored as a JSON blob keyed by Redfish-style field names so the UI can
render whatever happens to be present without us widening the column
set every time we want to surface another field.

Revision ID: v2_085
Revises: v2_084
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_085"
down_revision = "v2_084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    result = bind.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'discovered_nodes' AND column_name = 'bmc_redfish_payload'"
    ))
    if not result.fetchone():
        op.add_column(
            "discovered_nodes",
            sa.Column("bmc_redfish_payload", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("discovered_nodes", "bmc_redfish_payload")
