"""Add alert_channels and alert_history tables

Sprint 7 Day 2 Operations — configurable alert destinations for
health changes, drift detection, and deploy events.

Revision ID: v2_029_alert_channels
Revises: v2_028_project_type
"""
import sqlalchemy as sa

from alembic import op

revision = 'v2_029_alert_channels'
down_revision = 'v2_028_project_type'
branch_labels = None
depends_on = None


def upgrade():
    # Alert Channels
    op.create_table(
        'alert_channels',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('channel_type', sa.String(50), nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('url', sa.Text(), nullable=True),
        sa.Column('headers', sa.JSON(), nullable=True),
        sa.Column('secret', sa.Text(), nullable=True),
        sa.Column('email_to', sa.JSON(), nullable=True),
        sa.Column('email_from', sa.String(255), nullable=True),
        sa.Column('event_types', sa.JSON(), server_default='[]'),
        sa.Column('project_ids', sa.JSON(), server_default='[]'),
        sa.Column('cluster_ids', sa.JSON(), server_default='[]'),
        sa.Column('min_interval_seconds', sa.Integer(), server_default='60'),
        sa.Column('last_fired_at', sa.DateTime(), nullable=True),
        sa.Column('total_sent', sa.Integer(), server_default='0'),
        sa.Column('total_failed', sa.Integer(), server_default='0'),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('last_success_at', sa.DateTime(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_by', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_alert_channels_id', 'alert_channels', ['id'])
    op.create_index('ix_alert_channels_enabled', 'alert_channels', ['enabled'])

    # Alert History
    op.create_table(
        'alert_history',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('channel_id', sa.Integer(), sa.ForeignKey('alert_channels.id', ondelete='CASCADE'), nullable=False),
        sa.Column('event_type', sa.String(50), nullable=False),
        sa.Column('severity', sa.String(20), nullable=True),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='SET NULL'), nullable=True),
        sa.Column('cluster_id', sa.Integer(), sa.ForeignKey('kubernetes_clusters.id', ondelete='SET NULL'), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(20), server_default='sent'),
        sa.Column('http_status_code', sa.Integer(), nullable=True),
        sa.Column('response_body', sa.Text(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_alert_history_id', 'alert_history', ['id'])
    op.create_index('idx_alert_history_channel_created', 'alert_history', ['channel_id', 'created_at'])
    op.create_index('idx_alert_history_event_type', 'alert_history', ['event_type'])
    op.create_index('idx_alert_history_created', 'alert_history', ['created_at'])


def downgrade():
    op.drop_table('alert_history')
    op.drop_table('alert_channels')
