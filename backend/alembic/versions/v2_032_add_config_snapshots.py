"""Add config_snapshots table for UX-011 Config Snapshots and Rollback

Stores point-in-time snapshots of cluster/project configuration.
Snapshots are created manually or auto-created before apply/upgrade operations.
Supports one-click rollback by restoring a previous snapshot.

Revision ID: v2_032_config_snapshots
Revises: v2_031_bnk_upgrades
"""
import sqlalchemy as sa

from alembic import op

revision = 'v2_032_config_snapshots'
down_revision = 'v2_031_bnk_upgrades'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'config_snapshots',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('cluster_id', sa.Integer(), sa.ForeignKey('kubernetes_clusters.id', ondelete='SET NULL'), nullable=True),

        # Snapshot metadata
        sa.Column('trigger', sa.String(50), nullable=False),
        sa.Column('label', sa.String(255), nullable=True),
        sa.Column('created_by', sa.String(255), nullable=True),

        # Config data (full export JSON)
        sa.Column('config_data', sa.JSON(), nullable=False),

        # Summary stats
        sa.Column('resource_count', sa.Integer(), server_default='0'),
        sa.Column('module_count', sa.Integer(), server_default='0'),

        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_config_snapshots_id', 'config_snapshots', ['id'])
    op.create_index('idx_config_snapshot_project', 'config_snapshots', ['project_id'])
    op.create_index('idx_config_snapshot_project_created', 'config_snapshots', ['project_id', 'created_at'])
    op.create_index('idx_config_snapshot_trigger', 'config_snapshots', ['trigger'])


def downgrade():
    op.drop_table('config_snapshots')
