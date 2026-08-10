"""Make blueprint_releases.imported_at nullable for discovered releases.

Revision ID: v2_090
Revises: v2_089
Create Date: 2026-05-02 03:40:00.000000
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "v2_090"
down_revision = "v2_089"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "blueprint_releases",
        "imported_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
        existing_server_default=sa.text("CURRENT_TIMESTAMP"),
    )


def downgrade() -> None:
    op.alter_column(
        "blueprint_releases",
        "imported_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        existing_server_default=sa.text("CURRENT_TIMESTAMP"),
    )
