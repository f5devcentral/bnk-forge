"""Add doca_software_images table.

Admin-managed catalog of DOCA packages (mst-tools, rshim, mlnx-sf, OVS,
firmware) selectable when configuring bare-metal blueprints. Mirrors the
existing bluefield_software_images pattern.

Revision ID: v2_107
Revises: v2_106
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_109"
down_revision = "v2_108"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "doca_software_images",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("version", sa.String(64), nullable=False, unique=True),
        sa.Column("package_name", sa.String(255), nullable=False),
        sa.Column("apt_repo_url", sa.String(512), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("target", sa.String(32), nullable=False, server_default="both"),
        sa.Column("architecture", sa.String(32), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_doca_software_images_id", "doca_software_images", ["id"])


def downgrade() -> None:
    op.drop_index("ix_doca_software_images_id", table_name="doca_software_images")
    op.drop_table("doca_software_images")
