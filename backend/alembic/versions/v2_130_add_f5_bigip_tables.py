"""Add F5 BIG-IP device and credential tables (D-023 Phase 1).

Introduces two new tables:
  * f5_credentials  — encrypted-at-rest BIG-IP management credentials
  * f5_bigip_devices — classic TMOS devices registered for read-only discovery

Revision ID: v2_130
Revises: v2_129
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_130"
down_revision = "v2_129"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "f5_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("auth_type", sa.String(20), nullable=False, server_default="password"),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("password_encrypted", sa.Text(), nullable=True),
        sa.Column("token_encrypted", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("last_test_status", sa.String(16), nullable=True),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_f5_credentials_id", "f5_credentials", ["id"])
    op.create_index("ix_f5_credentials_name", "f5_credentials", ["name"])

    op.create_table(
        "f5_bigip_devices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("mgmt_host", sa.String(255), nullable=False),
        sa.Column("mgmt_port", sa.Integer(), nullable=False, server_default="443"),
        sa.Column("mgmt_credential_id", sa.Integer(), nullable=True),
        sa.Column("verify_https_cert", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("device_info", sa.JSON(), nullable=True),
        sa.Column("last_discovery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_discovery_status", sa.String(32), nullable=True),
        sa.Column("last_discovery_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mgmt_credential_id"], ["f5_credentials.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_f5_bigip_devices_id", "f5_bigip_devices", ["id"])
    op.create_index(
        "idx_f5_device_project_name", "f5_bigip_devices", ["project_id", "name"], unique=True
    )


def downgrade() -> None:
    op.drop_index("idx_f5_device_project_name", "f5_bigip_devices")
    op.drop_index("ix_f5_bigip_devices_id", "f5_bigip_devices")
    op.drop_table("f5_bigip_devices")

    op.drop_index("ix_f5_credentials_name", "f5_credentials")
    op.drop_index("ix_f5_credentials_id", "f5_credentials")
    op.drop_table("f5_credentials")
