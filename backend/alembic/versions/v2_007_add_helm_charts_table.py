"""add helm_charts table

Revision ID: v2_007
Revises: v2_006
Create Date: 2026-01-28

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = 'v2_007'
down_revision = 'v2_006'
branch_labels = None
depends_on = None


def upgrade():
    # Create helm_charts table
    op.create_table(
        'helm_charts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('version', sa.String(length=100), nullable=False),
        sa.Column('app_version', sa.String(length=100), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('file_path', sa.String(length=500), nullable=False),
        sa.Column('file_size', sa.Integer(), nullable=True),
        sa.Column('checksum', sa.String(length=64), nullable=True),
        sa.Column('chart_metadata', sa.JSON(), nullable=True),
        sa.Column('uploaded_by', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Create indexes
    op.create_index(op.f('ix_helm_charts_id'), 'helm_charts', ['id'], unique=False)
    op.create_index(op.f('ix_helm_charts_name'), 'helm_charts', ['name'], unique=False)
    op.create_index(op.f('ix_helm_charts_created_at'), 'helm_charts', ['created_at'], unique=False)
    op.create_index('idx_helm_chart_name_version', 'helm_charts', ['name', 'version'], unique=True)


def downgrade():
    # Drop indexes
    op.drop_index('idx_helm_chart_name_version', table_name='helm_charts')
    op.drop_index(op.f('ix_helm_charts_created_at'), table_name='helm_charts')
    op.drop_index(op.f('ix_helm_charts_name'), table_name='helm_charts')
    op.drop_index(op.f('ix_helm_charts_id'), table_name='helm_charts')

    # Drop table
    op.drop_table('helm_charts')
