"""DPU host hostname (in-band).

Captures the host's own hostname (from SSH discovery) alongside its IP
so in-band DPUs can render it in the UI + use it as the DPU's bf.conf
hostname prefix ("{host}-dpu" instead of "dpu-{id}").

Revision ID: v2_076
Revises: v2_075
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_076"
down_revision = "v2_075"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("dpus", sa.Column("host_hostname", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("dpus", "host_hostname")
