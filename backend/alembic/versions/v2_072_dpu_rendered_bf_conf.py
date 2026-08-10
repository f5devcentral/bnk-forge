"""Persist the last rendered bf.conf per DPU.

Lets operators download / inspect exactly the file that was streamed to
rshim on the most recent flash — crucial for debugging netplan, hostname,
or password-hash issues after the fact.

Revision ID: v2_072
Revises: v2_071
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_072"
down_revision = "v2_071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dpus",
        sa.Column("last_rendered_bf_conf", sa.Text(), nullable=True),
    )
    op.add_column(
        "dpus",
        sa.Column(
            "last_rendered_bf_conf_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("dpus", "last_rendered_bf_conf_at")
    op.drop_column("dpus", "last_rendered_bf_conf")
