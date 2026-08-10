"""Remove Terragrunt tables

Revision ID: v2_004
Revises: v2_003
Create Date: 2026-01-27

Description:
    Remove v1 Terragrunt tables that are no longer used in v2 architecture:
    - terraform_states (depends on terragrunt_modules)
    - terragrunt_modules (v1 only)

"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = 'v2_004'
down_revision = 'v2_003'
branch_labels = None
depends_on = None


def upgrade():
    # Drop terraform_states table first (has FK to terragrunt_modules)
    op.drop_table('terraform_states')

    # Drop terragrunt_modules table
    op.drop_table('terragrunt_modules')


def downgrade():
    # Recreate terragrunt_modules table
    op.create_table('terragrunt_modules',
        sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
        sa.Column('project_id', sa.INTEGER(), autoincrement=False, nullable=True),
        sa.Column('name', sa.VARCHAR(length=255), autoincrement=False, nullable=False),
        sa.Column('path', sa.VARCHAR(length=1000), autoincrement=False, nullable=False),
        sa.Column('source', sa.VARCHAR(length=1000), autoincrement=False, nullable=True),
        sa.Column('version', sa.VARCHAR(length=100), autoincrement=False, nullable=True),
        sa.Column('dependencies', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True),
        sa.Column('variables', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True),
        sa.Column('outputs', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True),
        sa.Column('locals', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True),
        sa.Column('include_config', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True),
        sa.Column('terraform_config', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True),
        sa.Column('status', sa.VARCHAR(length=50), autoincrement=False, nullable=True),
        sa.Column('last_synced_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
        sa.Column('last_state_check', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
        sa.Column('meta_data', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True),
        sa.Column('created_at', postgresql.TIMESTAMP(), server_default=sa.text('now()'), autoincrement=False, nullable=True),
        sa.Column('updated_at', postgresql.TIMESTAMP(), server_default=sa.text('now()'), autoincrement=False, nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name='terragrunt_modules_project_id_fkey'),
        sa.PrimaryKeyConstraint('id', name='terragrunt_modules_pkey')
    )
    op.create_index('idx_module_status', 'terragrunt_modules', ['status'], unique=False)
    op.create_index('idx_module_synced', 'terragrunt_modules', ['last_synced_at'], unique=False)
    op.create_index('idx_module_project', 'terragrunt_modules', ['project_id'], unique=False)
    op.create_index('idx_module_project_path', 'terragrunt_modules', ['project_id', 'path'], unique=True)
    op.create_index('ix_terragrunt_modules_name', 'terragrunt_modules', ['name'], unique=False)
    op.create_index('ix_terragrunt_modules_path', 'terragrunt_modules', ['path'], unique=False)

    # Recreate terraform_states table
    op.create_table('terraform_states',
        sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
        sa.Column('module_id', sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column('serial', sa.INTEGER(), autoincrement=False, nullable=True),
        sa.Column('lineage', sa.VARCHAR(length=255), autoincrement=False, nullable=True),
        sa.Column('terraform_version', sa.VARCHAR(length=50), autoincrement=False, nullable=True),
        sa.Column('resources', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True),
        sa.Column('outputs', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True),
        sa.Column('state_data', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True),
        sa.Column('checksum', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
        sa.Column('size_bytes', sa.INTEGER(), autoincrement=False, nullable=True),
        sa.Column('last_modified', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
        sa.Column('created_at', postgresql.TIMESTAMP(), server_default=sa.text('now()'), autoincrement=False, nullable=True),
        sa.Column('updated_at', postgresql.TIMESTAMP(), server_default=sa.text('now()'), autoincrement=False, nullable=True),
        sa.ForeignKeyConstraint(['module_id'], ['terragrunt_modules.id'], name='terraform_states_module_id_fkey'),
        sa.PrimaryKeyConstraint('id', name='terraform_states_pkey')
    )
