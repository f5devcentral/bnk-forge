"""DPU in-band access mode.

Phase 1 of in-band DPU registration — adds the columns needed to track
DPUs that are managed through the host they are plugged into (via rshim
over SSH) rather than through a dedicated BMC.

Changes:
  - `dpus.bmc_ip` becomes nullable (in-band DPUs have no BMC IP).
  - `access_mode` distinguishes "bmc" (existing rows default to this) from
    "in-band".
  - `host_node_ip` + `host_ssh_credential_id` record how the backend reaches
    the host carrying the DPU.
  - `pci_address` identifies which DPU on that host this row represents.
  - The BMC uniqueness index becomes partial (only enforced when bmc_ip is
    not null); a new partial unique index covers in-band rows keyed by
    (project_id, host_node_ip, pci_address).

Revision ID: v2_074
Revises: v2_073
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_074"
down_revision = "v2_073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dpus",
        sa.Column("access_mode", sa.String(16), nullable=False, server_default="bmc"),
    )
    op.add_column("dpus", sa.Column("host_node_ip", sa.String(255), nullable=True))
    op.add_column(
        "dpus",
        sa.Column("host_ssh_credential_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_dpus_host_ssh_credential",
        "dpus",
        "ssh_credentials",
        ["host_ssh_credential_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("dpus", sa.Column("pci_address", sa.String(32), nullable=True))

    op.alter_column(
        "dpus", "bmc_ip", existing_type=sa.String(255), nullable=True,
    )

    # Replace the unconditional unique index with a partial one that only
    # fires on rows that actually have a bmc_ip (so multiple in-band rows
    # with NULL bmc_ip don't collide).
    op.drop_index("idx_dpu_project_bmc_ip", table_name="dpus")
    op.create_index(
        "idx_dpu_project_bmc_ip",
        "dpus",
        ["project_id", "bmc_ip"],
        unique=True,
        postgresql_where=sa.text("bmc_ip IS NOT NULL"),
    )
    op.create_index(
        "idx_dpu_project_inband",
        "dpus",
        ["project_id", "host_node_ip", "pci_address"],
        unique=True,
        postgresql_where=sa.text("access_mode = 'in-band'"),
    )


def downgrade() -> None:
    op.drop_index("idx_dpu_project_inband", table_name="dpus")
    op.drop_index("idx_dpu_project_bmc_ip", table_name="dpus")
    op.create_index(
        "idx_dpu_project_bmc_ip",
        "dpus",
        ["project_id", "bmc_ip"],
        unique=True,
    )
    op.alter_column(
        "dpus", "bmc_ip", existing_type=sa.String(255), nullable=False,
    )
    op.drop_column("dpus", "pci_address")
    op.drop_constraint("fk_dpus_host_ssh_credential", "dpus", type_="foreignkey")
    op.drop_column("dpus", "host_ssh_credential_id")
    op.drop_column("dpus", "host_node_ip")
    op.drop_column("dpus", "access_mode")
