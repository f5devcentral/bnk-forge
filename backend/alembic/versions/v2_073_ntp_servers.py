"""Project DPU settings: NTP server list.

Defaults to Ubuntu's own NTP round-robin `ntp.ubuntu.com` — the same
server systemd-timesyncd uses OOTB on Ubuntu, which is what Nvidia's
BFB OS runs. Rendered into /etc/systemd/timesyncd.conf by the bf.conf
templates at flash time.

Revision ID: v2_073
Revises: v2_072
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_073"
down_revision = "v2_072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_dpu_settings",
        sa.Column("ntp_servers", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("project_dpu_settings", "ntp_servers")
