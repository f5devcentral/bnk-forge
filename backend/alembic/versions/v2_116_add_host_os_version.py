"""Add host_os_version column to bluefield_software_images.

Allows distinct catalog entries for the same DOCA version on different
OS releases, e.g. "2.9.2 / ubuntu 22.04 / arm64" vs "2.9.2 / ubuntu
24.04 / arm64".

Steps:
1. Add nullable host_os_version column.
2. Back-fill the existing seed row (id=1) with '22.04'.
3. Drop the old unique constraint (doca_version, host_os, host_arch).
4. Create new constraint on (doca_version, host_os, host_os_version, host_arch).

Revision ID: v2_114
Revises: v2_113
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_116"
down_revision = "v2_115"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add column
    op.add_column(
        "bluefield_software_images",
        sa.Column("host_os_version", sa.String(32), nullable=True),
    )

    # 2. Back-fill existing seed row
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE bluefield_software_images "
            "SET host_os_version = '22.04' "
            "WHERE doca_version = '2.9.2' AND host_os = 'ubuntu' AND host_arch = 'arm64'"
        )
    )

    # 3. Drop old constraint, create new one
    op.drop_constraint("uq_doca_release", "bluefield_software_images", type_="unique")
    op.create_unique_constraint(
        "uq_doca_release",
        "bluefield_software_images",
        ["doca_version", "host_os", "host_os_version", "host_arch"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_doca_release", "bluefield_software_images", type_="unique")
    op.create_unique_constraint(
        "uq_doca_release",
        "bluefield_software_images",
        ["doca_version", "host_os", "host_arch"],
    )
    op.drop_column("bluefield_software_images", "host_os_version")
