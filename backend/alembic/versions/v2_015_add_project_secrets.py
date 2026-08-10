"""Add project_secrets table for file and value secrets

Revision ID: v2_015
Revises: v2_014
Create Date: 2026-02-03

This migration adds the project_secrets table to store:
1. File secrets (credentials JSON, certificates, etc.) - encrypted and mounted at runtime
2. Value secrets (API keys, passwords) - encrypted and injected as variables
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic
revision = 'v2_015'
down_revision = 'v2_014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create project_secrets table
    op.create_table(
        'project_secrets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('secret_type', sa.String(20), nullable=False),  # 'file' or 'value'

        # File secrets
        sa.Column('filename', sa.String(255), nullable=True),
        sa.Column('file_content_encrypted', sa.Text(), nullable=True),
        sa.Column('file_size', sa.Integer(), nullable=True),
        sa.Column('mime_type', sa.String(100), nullable=True),

        # Value secrets
        sa.Column('value_encrypted', sa.Text(), nullable=True),

        # Metadata - target mapping
        sa.Column('target_module_path', sa.String(500), nullable=True),
        sa.Column('target_variable_name', sa.String(255), nullable=True),

        # Status
        sa.Column('is_active', sa.Boolean(), default=True),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column('created_by', sa.String(255), nullable=True),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    )

    # Create indexes
    op.create_index('idx_project_secret_project', 'project_secrets', ['project_id'])
    op.create_index('idx_project_secret_name', 'project_secrets', ['project_id', 'name'], unique=True)
    op.create_index('idx_project_secret_target', 'project_secrets', ['project_id', 'target_module_path', 'target_variable_name'])


def downgrade() -> None:
    op.drop_index('idx_project_secret_target', table_name='project_secrets')
    op.drop_index('idx_project_secret_name', table_name='project_secrets')
    op.drop_index('idx_project_secret_project', table_name='project_secrets')
    op.drop_table('project_secrets')
