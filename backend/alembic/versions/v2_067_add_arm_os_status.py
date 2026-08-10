"""Add arm_os_status column to dpus for UI state during ARM OS probe.

Values: "probing" | "reachable" | "unreachable" | "error" | NULL.

Revision ID: v2_067
Revises: v2_066
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_067"
down_revision = "v2_066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dpus",
        sa.Column("arm_os_status", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dpus", "arm_os_status")
