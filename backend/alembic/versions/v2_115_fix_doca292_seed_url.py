"""Fix seeded DOCA 2.9.2 ubuntu/arm64 host package URL.

The row written by v2_111 used the correct build number (012101-24.10),
but an earlier migration had written the wrong build number (012000-24.10)
and the wrong OS version (ubuntu2404 instead of ubuntu2204).

Correct the doca_host_url on the seed row to match the 22.04 BFB it
is paired with and the correct build number from init_db.py.

Revision ID: v2_113
Revises: v2_112
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_115"
down_revision = "v2_114"
branch_labels = None
depends_on = None

_WRONG_URL = "https://www.mellanox.com/downloads/DOCA/DOCA_v2.9.2/host/doca-host_2.9.2-012000-24.10-ubuntu2404_arm64.deb"
_CORRECT_URL = "https://www.mellanox.com/downloads/DOCA/DOCA_v2.9.2/host/doca-host_2.9.2-012101-24.10-ubuntu2204_arm64.deb"


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE bluefield_software_images "
            "SET doca_host_url = :correct "
            "WHERE doca_version = '2.9.2' AND host_os = 'ubuntu' AND host_arch = 'arm64' "
            "AND doca_host_url = :wrong"
        ),
        {"correct": _CORRECT_URL, "wrong": _WRONG_URL},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE bluefield_software_images "
            "SET doca_host_url = :wrong "
            "WHERE doca_version = '2.9.2' AND host_os = 'ubuntu' AND host_arch = 'arm64' "
            "AND doca_host_url = :correct"
        ),
        {"correct": _CORRECT_URL, "wrong": _WRONG_URL},
    )
