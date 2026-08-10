"""Add maturity and outcomes columns to stack_templates.

Revision ID: v2_047
Revises: v2_046
"""
import sqlalchemy as sa

from alembic import op

revision = "v2_047"
down_revision = "v2_046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stack_templates", sa.Column("maturity", sa.String(50), nullable=True))
    op.add_column("stack_templates", sa.Column("outcomes", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("stack_templates", "outcomes")
    op.drop_column("stack_templates", "maturity")
