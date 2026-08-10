"""Add environment field to projects table

Revision ID: v2_003
Revises: v2_002
Create Date: 2026-01-26

Add environment field (dev, staging, prod) to projects table to replace hardcoded "dev" value.
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = 'v2_003'
down_revision = 'v2_002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add environment column with default 'dev'
    op.add_column('projects', sa.Column('environment', sa.String(50), nullable=False, server_default='dev'))


def downgrade() -> None:
    # Remove environment column
    op.drop_column('projects', 'environment')
