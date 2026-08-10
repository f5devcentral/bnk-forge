"""add helm source tracking columns

Revision ID: v2_008
Revises: v2_007
Create Date: 2026-01-29

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = 'v2_008'
down_revision = 'v2_007'
branch_labels = None
depends_on = None


def upgrade():
    # Add source tracking columns to helm_charts table
    op.add_column('helm_charts', sa.Column('source_type', sa.String(length=50), server_default='uploaded', nullable=True))
    op.add_column('helm_charts', sa.Column('source_chart', sa.String(length=255), nullable=True))
    op.add_column('helm_charts', sa.Column('source_repository', sa.String(length=255), nullable=True))

    # Create indexes for better query performance
    op.create_index(op.f('ix_helm_charts_source_type'), 'helm_charts', ['source_type'], unique=False)


def downgrade():
    # Drop indexes
    op.drop_index(op.f('ix_helm_charts_source_type'), table_name='helm_charts')

    # Drop columns
    op.drop_column('helm_charts', 'source_repository')
    op.drop_column('helm_charts', 'source_chart')
    op.drop_column('helm_charts', 'source_type')
