"""Replace doca_apt_repo_url with templated doca-host download URLs.

The correct NVIDIA DOCA install flow downloads a doca-host .deb/.rpm
package which configures the repo, rather than manually adding an apt
source. The URL varies by OS/arch but follows a fixed template pattern
per release. This migration renames the old column and adds the rpm
template column.

Revision ID: v2_110
Revises: v2_109
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_112"
down_revision = "v2_111"
branch_labels = None
depends_on = None

_SEED_BFB_FILENAME = "bf-bundle-2.9.2-32_25.02_ubuntu-22.04_prod.bfb"
_DEB_TPL = "https://www.mellanox.com/downloads/DOCA/DOCA_v2.9.2/host/doca-host_2.9.2-012101-24.10-{os}{osver}_{arch}.deb"
_RPM_TPL = "https://www.mellanox.com/downloads/DOCA/DOCA_v2.9.2/host/doca-host-2.9.2-012101_24.10_{os}{osver}.{arch}.rpm"


def upgrade() -> None:
    # 1. Rename doca_apt_repo_url → doca_host_deb_url_template
    with op.batch_alter_table("bluefield_software_images") as batch_op:
        batch_op.alter_column(
            "doca_apt_repo_url",
            new_column_name="doca_host_deb_url_template",
        )

    # 2. Add doca_host_rpm_url_template column
    op.add_column(
        "bluefield_software_images",
        sa.Column("doca_host_rpm_url_template", sa.String(512), nullable=True),
    )

    # 3. Update seed row with correct template URLs
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE bluefield_software_images "
            "SET doca_host_deb_url_template = :deb_tpl, "
            "    doca_host_rpm_url_template = :rpm_tpl "
            "WHERE image_filename = :fn"
        ),
        {
            "deb_tpl": _DEB_TPL,
            "rpm_tpl": _RPM_TPL,
            "fn": _SEED_BFB_FILENAME,
        },
    )


def downgrade() -> None:
    op.drop_column("bluefield_software_images", "doca_host_rpm_url_template")
    with op.batch_alter_table("bluefield_software_images") as batch_op:
        batch_op.alter_column(
            "doca_host_deb_url_template",
            new_column_name="doca_apt_repo_url",
        )
