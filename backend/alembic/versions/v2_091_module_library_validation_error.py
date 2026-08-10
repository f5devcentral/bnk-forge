"""Add validation_error column to module_library for quarantine path.

Revision ID: v2_091
Revises: v2_090
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_091"
down_revision = "v2_090"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("module_library", sa.Column("validation_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("module_library", "validation_error")
