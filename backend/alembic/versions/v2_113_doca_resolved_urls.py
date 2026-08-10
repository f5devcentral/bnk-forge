"""Replace doca-host URL templates with resolved URLs.

Instead of storing URL templates with {os}{osver}_{arch} placeholders,
store fully resolved URLs — one row per OS/arch/version combo. The user
selects a single "DOCA Release" in the blueprint and the probe gets the
exact download URL without needing to resolve templates at runtime.

New columns: host_os, host_arch, doca_host_url
Dropped columns: doca_host_deb_url_template, doca_host_rpm_url_template

Revision ID: v2_111
Revises: v2_110
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_113"
down_revision = "v2_112"
branch_labels = None
depends_on = None

_SEED_BFB_FILENAME = "bf-bundle-2.9.2-32_25.02_ubuntu-22.04_prod.bfb"
_RESOLVED_URL = "https://www.mellanox.com/downloads/DOCA/DOCA_v2.9.2/host/doca-host_2.9.2-012101-24.10-ubuntu2204_arm64.deb"


def upgrade() -> None:
    # 1. Add the 3 new columns
    op.add_column(
        "bluefield_software_images",
        sa.Column("host_os", sa.String(32), nullable=True),
    )
    op.add_column(
        "bluefield_software_images",
        sa.Column("host_arch", sa.String(32), nullable=True),
    )
    op.add_column(
        "bluefield_software_images",
        sa.Column("doca_host_url", sa.String(512), nullable=True),
    )

    # 2. Update existing seed row with resolved values
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE bluefield_software_images "
            "SET host_os = :host_os, host_arch = :host_arch, doca_host_url = :url "
            "WHERE image_filename = :fn"
        ),
        {
            "host_os": "ubuntu",
            "host_arch": "arm64",
            "url": _RESOLVED_URL,
            "fn": _SEED_BFB_FILENAME,
        },
    )

    # 3. Drop the old template columns
    op.drop_column("bluefield_software_images", "doca_host_deb_url_template")
    op.drop_column("bluefield_software_images", "doca_host_rpm_url_template")


def downgrade() -> None:
    # Add template columns back
    op.add_column(
        "bluefield_software_images",
        sa.Column("doca_host_deb_url_template", sa.String(512), nullable=True),
    )
    op.add_column(
        "bluefield_software_images",
        sa.Column("doca_host_rpm_url_template", sa.String(512), nullable=True),
    )
    # Drop new columns
    op.drop_column("bluefield_software_images", "doca_host_url")
    op.drop_column("bluefield_software_images", "host_arch")
    op.drop_column("bluefield_software_images", "host_os")
