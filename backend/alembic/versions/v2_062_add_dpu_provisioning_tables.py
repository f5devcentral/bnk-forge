"""Add DPU Provisioning tables.

Three tables for Blueprint 1 (DPU Initialization):
  - bluefield_software_images: admin catalog of BFB images
  - project_dpu_settings:      per-project DPU defaults (1:1 with Project)
  - dpus:                      per-DPU inventory and user-edited VLAN plan

Revision ID: v2_062
Revises: v2_061
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_062"
down_revision = "v2_061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bluefield_software_images",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("image_filename", sa.String(length=255), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("image_filename"),
    )
    op.create_index("ix_bluefield_software_images_id", "bluefield_software_images", ["id"])

    op.create_table(
        "project_dpu_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("bluefield_image_id", sa.Integer(), nullable=True),
        sa.Column("high_speed_mtu", sa.Integer(), nullable=True),
        sa.Column("high_speed_lag", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("dns_servers", sa.JSON(), nullable=True),
        sa.Column("default_oob_username", sa.String(length=255), nullable=True),
        sa.Column("default_oob_password_encrypted", sa.Text(), nullable=True),
        sa.Column("default_os_username", sa.String(length=255), nullable=True),
        sa.Column("default_os_password_encrypted", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["bluefield_image_id"], ["bluefield_software_images.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id"),
    )
    op.create_index("ix_project_dpu_settings_id", "project_dpu_settings", ["id"])
    op.create_index("ix_project_dpu_settings_project_id", "project_dpu_settings", ["project_id"])

    op.create_table(
        "dpus",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("bmc_ip", sa.String(length=255), nullable=False),
        sa.Column("bmc_username_encrypted", sa.Text(), nullable=True),
        sa.Column("bmc_password_encrypted", sa.Text(), nullable=True),
        sa.Column("serial_number", sa.String(length=255), nullable=True),
        sa.Column("oob0_mac", sa.String(length=32), nullable=True),
        sa.Column("oob0_ipv4", sa.String(length=64), nullable=False, server_default="dhcp"),
        sa.Column("external_vlan_id", sa.Integer(), nullable=True),
        sa.Column("external_vlan_ipv4", sa.String(length=64), nullable=True),
        sa.Column("external_vlan_ipv6", sa.String(length=128), nullable=True),
        sa.Column("internal_vlan_id", sa.Integer(), nullable=True),
        sa.Column("internal_vlan_ipv4", sa.String(length=64), nullable=True),
        sa.Column("internal_vlan_ipv6", sa.String(length=128), nullable=True),
        sa.Column("storage_vlan_id", sa.Integer(), nullable=True),
        sa.Column("storage_vlan_ipv4", sa.String(length=64), nullable=True),
        sa.Column("storage_vlan_ipv6", sa.String(length=128), nullable=True),
        sa.Column("last_discovery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_discovery_status", sa.String(length=50), nullable=True),
        sa.Column("last_discovery_error", sa.Text(), nullable=True),
        sa.Column("last_discovery_payload", sa.JSON(), nullable=True),
        sa.Column("arm_os_ip", sa.String(length=255), nullable=True),
        sa.Column("arm_os_reachable", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dpus_id", "dpus", ["id"])
    op.create_index("ix_dpus_project_id", "dpus", ["project_id"])
    op.create_index("idx_dpu_project_bmc_ip", "dpus", ["project_id", "bmc_ip"], unique=True)


def downgrade() -> None:
    op.drop_index("idx_dpu_project_bmc_ip", table_name="dpus")
    op.drop_index("ix_dpus_project_id", table_name="dpus")
    op.drop_index("ix_dpus_id", table_name="dpus")
    op.drop_table("dpus")

    op.drop_index("ix_project_dpu_settings_project_id", table_name="project_dpu_settings")
    op.drop_index("ix_project_dpu_settings_id", table_name="project_dpu_settings")
    op.drop_table("project_dpu_settings")

    op.drop_index("ix_bluefield_software_images_id", table_name="bluefield_software_images")
    op.drop_table("bluefield_software_images")
