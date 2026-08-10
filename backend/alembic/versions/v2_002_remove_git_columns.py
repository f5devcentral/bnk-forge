"""Remove git-related columns from projects table

Revision ID: v2_002
Revises: v2_001
Create Date: 2026-01-22

BNK-Forge v2 Phase 2 migration:
- Remove git integration fields (git_url, branch, clone_path, auth_token_encrypted, sync_status, last_synced_at, sync_error, auth_type)
- v2 doesn't use git integration - variables stored in DB and injected at runtime
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = 'v2_002'
down_revision = 'v2_001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Remove git-related columns from projects table
    op.drop_column('projects', 'git_url')
    op.drop_column('projects', 'branch')
    op.drop_column('projects', 'clone_path')
    op.drop_column('projects', 'auth_token_encrypted')
    op.drop_column('projects', 'auth_type')
    op.drop_column('projects', 'sync_status')
    op.drop_column('projects', 'last_synced_at')
    op.drop_column('projects', 'sync_error')


def downgrade() -> None:
    # Re-add git columns if needed (for rollback)
    op.add_column('projects', sa.Column('git_url', sa.String(1000), nullable=True))
    op.add_column('projects', sa.Column('branch', sa.String(255), nullable=True))
    op.add_column('projects', sa.Column('clone_path', sa.String(1000), nullable=True))
    op.add_column('projects', sa.Column('auth_token_encrypted', sa.String(500), nullable=True))
    op.add_column('projects', sa.Column('auth_type', sa.String(50), nullable=True))
    op.add_column('projects', sa.Column('sync_status', sa.String(50), nullable=True))
    op.add_column('projects', sa.Column('last_synced_at', sa.DateTime(), nullable=True))
    op.add_column('projects', sa.Column('sync_error', sa.Text(), nullable=True))
