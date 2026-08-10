"""Add operator_command_queue table for HTTP polling mode

When operators use polling instead of WebSocket, commands are queued
in this table. Operators poll for pending commands, execute them,
and POST results back.

Revision ID: v2_027_op_cmd_queue
Revises: v2_026_op_connectivity
"""
import sqlalchemy as sa

from alembic import op

revision = 'v2_027_op_cmd_queue'
down_revision = 'v2_026_op_connectivity'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'operator_command_queue',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('command_id', sa.String(64), unique=True, nullable=False, index=True),
        sa.Column('operator_id', sa.String(64), nullable=False, index=True),
        sa.Column('action', sa.String(100), nullable=False),
        sa.Column('payload', sa.JSON, default=dict),
        sa.Column('timeout_seconds', sa.Integer, default=300, nullable=False),
        sa.Column('status', sa.String(50), default='queued', nullable=False, index=True),
        sa.Column('picked_up_at', sa.DateTime, nullable=True),
        sa.Column('result', sa.JSON, nullable=True),
        sa.Column('output_lines', sa.JSON, default=list),
        sa.Column('error_message', sa.String(2000), nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('completed_at', sa.DateTime, nullable=True),
    )
    op.create_index('idx_cmd_queue_operator_status', 'operator_command_queue', ['operator_id', 'status'])


def downgrade():
    op.drop_index('idx_cmd_queue_operator_status', table_name='operator_command_queue')
    op.drop_table('operator_command_queue')
