"""add parallel execution table

Revision ID: v2_012
Revises: v2_011
Create Date: 2026-01-30

"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON

from alembic import op

# revision identifiers, used by Alembic.
revision = 'v2_012'
down_revision = 'v2_011'
branch_labels = None
depends_on = None


def upgrade():
    # Create parallel_executions table
    op.create_table(
        'parallel_executions',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('project_id', sa.Integer, sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False, index=True),

        # Action type
        sa.Column('action', sa.String(50), nullable=False, index=True),

        # Status tracking
        sa.Column('status', sa.String(50), server_default='pending', index=True),
        sa.Column('current_layer', sa.Integer, server_default='0'),
        sa.Column('total_layers', sa.Integer),

        # Execution plan
        sa.Column('layers_data', JSON),
        sa.Column('execution_mode', sa.String(50), server_default='parallel'),

        # Celery orchestration
        sa.Column('orchestrator_task_id', sa.String(255), unique=True, nullable=True, index=True),

        # Timing
        sa.Column('started_at', sa.DateTime),
        sa.Column('completed_at', sa.DateTime),
        sa.Column('duration_seconds', sa.Float),
        sa.Column('estimated_duration_minutes', sa.Integer),

        # Results
        sa.Column('successful_modules', JSON, server_default='[]'),
        sa.Column('failed_modules', JSON, server_default='[]'),
        sa.Column('skipped_modules', JSON, server_default='[]'),

        # Error tracking
        sa.Column('error_message', sa.Text),
        sa.Column('error_layer', sa.Integer),

        # Metadata
        sa.Column('triggered_by', sa.String(255), server_default='user'),
        sa.Column('metadata', JSON),

        sa.Column('created_at', sa.DateTime, server_default=sa.func.now(), index=True),
        sa.Column('updated_at', sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # Create indexes
    op.create_index(
        'idx_parallel_exec_project_status',
        'parallel_executions',
        ['project_id', 'status']
    )
    op.create_index(
        'idx_parallel_exec_action',
        'parallel_executions',
        ['action']
    )
    op.create_index(
        'idx_parallel_exec_started',
        'parallel_executions',
        ['started_at']
    )


def downgrade():
    # Drop indexes
    op.drop_index('idx_parallel_exec_started', 'parallel_executions')
    op.drop_index('idx_parallel_exec_action', 'parallel_executions')
    op.drop_index('idx_parallel_exec_project_status', 'parallel_executions')

    # Drop table
    op.drop_table('parallel_executions')
