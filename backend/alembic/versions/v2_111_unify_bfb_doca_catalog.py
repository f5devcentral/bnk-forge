"""Unify BFB and DOCA catalogs into a single DOCA releases table.

Adds DOCA host-side fields (doca_version, doca_package, doca_apt_repo_url)
and is_default to bluefield_software_images, then drops the standalone
doca_software_images table. Updates the existing seed row to include DOCA
pairing data.

Revision ID: v2_109
Revises: v2_108
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_111"
down_revision = "v2_110"
branch_labels = None
depends_on = None

# The BFB seed filename used in v2_063 / init_db.py.
_SEED_BFB_FILENAME = "bf-bundle-2.9.2-32_25.02_ubuntu-22.04_prod.bfb"


def upgrade() -> None:
    # 1. Add DOCA columns to bluefield_software_images
    op.add_column(
        "bluefield_software_images",
        sa.Column("doca_version", sa.String(64), nullable=True),
    )
    op.add_column(
        "bluefield_software_images",
        sa.Column("doca_package", sa.String(255), nullable=True),
    )
    op.add_column(
        "bluefield_software_images",
        sa.Column("doca_apt_repo_url", sa.String(512), nullable=True),
    )
    op.add_column(
        "bluefield_software_images",
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
    )

    # 2. Update the existing seed row with DOCA pairing info
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE bluefield_software_images "
            "SET doca_version = :dv, doca_package = :dp, "
            "    doca_apt_repo_url = :url, is_default = :d, "
            "    notes = :notes "
            "WHERE image_filename = :fn"
        ),
        {
            "dv": "2.9.2",
            "dp": "doca-all",
            "url": "https://linux.mellanox.com/public/repo/doca/2.9.2/",
            "d": True,
            "fn": _SEED_BFB_FILENAME,
            "notes": "DOCA 2.9.2 LTS — BNK 2.2 tested. Host: doca-all 2.9.2, DPU: bf-bundle 2.9.2",
        },
    )

    # 3. Drop the standalone doca_software_images table
    op.drop_index("ix_doca_software_images_id", table_name="doca_software_images")
    op.drop_table("doca_software_images")


def downgrade() -> None:
    # Recreate doca_software_images
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

    # Remove DOCA columns from bluefield_software_images
    op.drop_column("bluefield_software_images", "is_default")
    op.drop_column("bluefield_software_images", "doca_apt_repo_url")
    op.drop_column("bluefield_software_images", "doca_package")
    op.drop_column("bluefield_software_images", "doca_version")
