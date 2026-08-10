"""Add bnk_version column to stack_templates.

Revision ID: v2_092
Revises: v2_090
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_103"
down_revision = "v2_102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "stack_templates",
        sa.Column(
            "bnk_version",
            sa.String(length=50),
            nullable=True,
            comment="Target BNK version compatibility (e.g. '2.2', '2.1+', null for infra-only)",
        ),
    )


def downgrade() -> None:
    op.drop_column("stack_templates", "bnk_version")
