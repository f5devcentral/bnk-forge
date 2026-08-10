"""Add bnk_upgrades table for BNK upgrade workflow

Sprint 8.2 — tracks upgrade operations from version A to B,
including pre-upgrade validation, rolling execution, health gates,
and rollback state.

Revision ID: v2_031_bnk_upgrades
Revises: v2_030
"""
import sqlalchemy as sa

from alembic import op

revision = 'v2_031_bnk_upgrades'
down_revision = 'v2_030'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'bnk_upgrades',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('cluster_id', sa.Integer(), sa.ForeignKey('kubernetes_clusters.id', ondelete='CASCADE'), nullable=False),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='SET NULL'), nullable=True),

        # Version info
        sa.Column('from_version', sa.String(100), nullable=True),
        sa.Column('to_version', sa.String(100), nullable=False),
        sa.Column('from_bnk_info', sa.JSON(), nullable=True),

        # Upgrade plan
        sa.Column('plan', sa.JSON(), nullable=True),
        sa.Column('total_steps', sa.Integer(), server_default='0'),
        sa.Column('current_step', sa.Integer(), server_default='0'),

        # Pre-upgrade validation
        sa.Column('pre_checks', sa.JSON(), nullable=True),
        sa.Column('pre_check_passed', sa.Boolean(), server_default='false'),

        # Execution
        sa.Column('status', sa.String(50), server_default='planning', nullable=False),
        sa.Column('celery_task_id', sa.String(255), nullable=True),

        # Step results
        sa.Column('step_results', sa.JSON(), server_default='[]'),

        # Health gates
        sa.Column('pre_health', sa.JSON(), nullable=True),
        sa.Column('post_health', sa.JSON(), nullable=True),
        sa.Column('health_gate_passed', sa.Boolean(), nullable=True),

        # Rollback
        sa.Column('rollback_available', sa.Boolean(), server_default='false'),
        sa.Column('rollback_info', sa.JSON(), nullable=True),
        sa.Column('rollback_reason', sa.Text(), nullable=True),

        # Timing
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),

        # Error
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('error_step', sa.Integer(), nullable=True),

        # User
        sa.Column('triggered_by', sa.String(255), server_default='user'),
        sa.Column('approved_by', sa.String(255), nullable=True),

        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_bnk_upgrades_id', 'bnk_upgrades', ['id'])
    op.create_index('idx_bnk_upgrade_cluster_status', 'bnk_upgrades', ['cluster_id', 'status'])
    op.create_index('idx_bnk_upgrade_created', 'bnk_upgrades', ['created_at'])
    op.create_index('ix_bnk_upgrades_celery_task', 'bnk_upgrades', ['celery_task_id'])


def downgrade():
    op.drop_table('bnk_upgrades')
