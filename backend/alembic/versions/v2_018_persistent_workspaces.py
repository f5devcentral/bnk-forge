"""Add persistent workspace columns to project_modules

PERSISTENT WORKSPACES (WORK-001):
Adds columns to track persistent workspaces for modules:
- workspace_path: Full path to the workspace directory
- last_init_at: When tofu init was last run
- plan_output: Human-readable plan summary for UI
- plan_serial: Increments each plan, tracks plan versions
- vars_hash: SHA256 of tfvars.json, detects variable changes

This enables the "init once, plan once, apply saved plan" workflow.

Revision ID: v2_018_persistent_workspaces
Revises: v2_017_path_indexes
Create Date: 2026-02-06
"""
import sqlalchemy as sa

from alembic import op

revision = 'v2_018_persistent_workspaces'
down_revision = 'v2_017_path_indexes'
branch_labels = None
depends_on = None


def upgrade():
    # Add workspace_path column
    op.add_column('project_modules', sa.Column('workspace_path', sa.String(500), nullable=True))

    # Add last_init_at timestamp
    op.add_column('project_modules', sa.Column('last_init_at', sa.DateTime, nullable=True))

    # Add plan_output for storing human-readable plan summary
    op.add_column('project_modules', sa.Column('plan_output', sa.Text, nullable=True))

    # Add plan_serial to track plan versions
    op.add_column('project_modules', sa.Column('plan_serial', sa.Integer, nullable=True, server_default='0'))

    # Add vars_hash for detecting variable changes
    op.add_column('project_modules', sa.Column('vars_hash', sa.String(64), nullable=True))

    # Add index on workspace_path for lookups
    op.create_index('idx_project_module_workspace', 'project_modules', ['workspace_path'], if_not_exists=True)


def downgrade():
    op.drop_index('idx_project_module_workspace', table_name='project_modules', if_exists=True)
    op.drop_column('project_modules', 'vars_hash')
    op.drop_column('project_modules', 'plan_serial')
    op.drop_column('project_modules', 'plan_output')
    op.drop_column('project_modules', 'last_init_at')
    op.drop_column('project_modules', 'workspace_path')
