"""Add module sources for multi-source module support

Revision ID: v2_009
Revises: v2_008
Create Date: 2026-01-30

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = 'v2_009'
down_revision = 'v2_008'
branch_labels = None
depends_on = None


def upgrade():
    # Create module_sources table
    op.create_table(
        'module_sources',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('source_type', sa.String(length=50), nullable=False),
        sa.Column('url', sa.String(length=500), nullable=False),
        sa.Column('branch', sa.String(length=100), nullable=True),
        sa.Column('git_ref', sa.String(length=100), nullable=True),
        sa.Column('auth_type', sa.String(length=50), nullable=True),
        sa.Column('auth_token_encrypted', sa.Text(), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(), nullable=True),
        sa.Column('sync_status', sa.String(length=50), server_default='pending', nullable=True),
        sa.Column('sync_error', sa.Text(), nullable=True),
        sa.Column('module_count', sa.Integer(), server_default='0', nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=True),
        sa.Column('auto_sync', sa.Boolean(), server_default='false', nullable=True),
        sa.Column('sync_interval_hours', sa.Integer(), server_default='24', nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_module_source_active', 'module_sources', ['is_active'], unique=False)
    op.create_index('idx_module_source_status', 'module_sources', ['sync_status'], unique=False)
    op.create_index('idx_module_source_type', 'module_sources', ['source_type'], unique=False)
    op.create_index(op.f('ix_module_sources_name'), 'module_sources', ['name'], unique=True)

    # Add multi-source fields to module_library
    op.add_column('module_library', sa.Column('module_source_id', sa.Integer(), nullable=True))
    op.add_column('module_library', sa.Column('source_path', sa.String(length=255), nullable=True))
    op.add_column('module_library', sa.Column('source_version', sa.String(length=100), nullable=True))
    op.add_column('module_library', sa.Column('local_path', sa.String(length=500), nullable=True))
    op.add_column('module_library', sa.Column('is_custom', sa.Boolean(), server_default='false', nullable=True))
    op.add_column('module_library', sa.Column('latest_version', sa.String(length=100), nullable=True))
    op.add_column('module_library', sa.Column('update_available', sa.Boolean(), server_default='false', nullable=True))

    # Create foreign key
    op.create_foreign_key(
        'fk_module_library_source_id',
        'module_library',
        'module_sources',
        ['module_source_id'],
        ['id'],
        ondelete='SET NULL'
    )
    op.create_index('ix_module_library_source_id', 'module_library', ['module_source_id'], unique=False)

    # NOTE: Default module source is seeded at application startup via seed_defaults()
    # See main.py lifespan handler and defaults_service.py


def downgrade():
    # Remove foreign key and index
    op.drop_constraint('fk_module_library_source_id', 'module_library', type_='foreignkey')
    op.drop_index('ix_module_library_source_id', table_name='module_library')

    # Remove columns from module_library
    op.drop_column('module_library', 'update_available')
    op.drop_column('module_library', 'latest_version')
    op.drop_column('module_library', 'is_custom')
    op.drop_column('module_library', 'local_path')
    op.drop_column('module_library', 'source_version')
    op.drop_column('module_library', 'source_path')
    op.drop_column('module_library', 'module_source_id')

    # Drop module_sources table
    op.drop_index(op.f('ix_module_sources_name'), table_name='module_sources')
    op.drop_index('idx_module_source_type', table_name='module_sources')
    op.drop_index('idx_module_source_status', table_name='module_sources')
    op.drop_index('idx_module_source_active', table_name='module_sources')
    op.drop_table('module_sources')
