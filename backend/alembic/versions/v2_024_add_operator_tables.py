"""Add operator registration tokens and connected operators tables

Revision ID: v2_024_add_operator_tables
Revises: v2_023_audit_log_enhancements
Create Date: 2026-02-16
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = 'v2_024_add_operator_tables'
down_revision = 'v2_023_audit_log_enhancements'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create operator_registration_tokens table
    op.create_table(
        'operator_registration_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('token_hash', sa.String(length=255), nullable=False),
        sa.Column('token_prefix', sa.String(length=20), nullable=False),
        sa.Column('max_uses', sa.Integer(), nullable=True),
        sa.Column('use_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('labels', sa.JSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_by', sa.String(length=255), nullable=True),
        sa.Column('created_by', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_operator_registration_tokens_id', 'operator_registration_tokens', ['id'])
    op.create_index('ix_operator_registration_tokens_name', 'operator_registration_tokens', ['name'])
    op.create_index('ix_operator_registration_tokens_token_hash', 'operator_registration_tokens', ['token_hash'], unique=True)
    op.create_index('idx_op_token_active_expires', 'operator_registration_tokens', ['is_active', 'expires_at'])

    # Create connected_operators table
    op.create_table(
        'connected_operators',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('operator_id', sa.String(length=64), nullable=False),
        sa.Column('registration_token_id', sa.Integer(), nullable=True),
        sa.Column('cluster_name', sa.String(length=255), nullable=False),
        sa.Column('cluster_id', sa.Integer(), nullable=True),
        sa.Column('labels', sa.JSON(), nullable=True),
        sa.Column('is_connected', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('last_heartbeat_at', sa.DateTime(), nullable=True),
        sa.Column('last_connected_at', sa.DateTime(), nullable=True),
        sa.Column('last_disconnected_at', sa.DateTime(), nullable=True),
        sa.Column('disconnect_reason', sa.String(length=500), nullable=True),
        sa.Column('operator_version', sa.String(length=50), nullable=True),
        sa.Column('kubernetes_version', sa.String(length=50), nullable=True),
        sa.Column('node_count', sa.Integer(), nullable=True),
        sa.Column('nodes_ready', sa.Integer(), nullable=True),
        sa.Column('last_health_report', sa.JSON(), nullable=True),
        sa.Column('last_health_at', sa.DateTime(), nullable=True),
        sa.Column('commands_executed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('commands_failed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('uptime_seconds', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='registered'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['registration_token_id'], ['operator_registration_tokens.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['cluster_id'], ['kubernetes_clusters.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_connected_operators_id', 'connected_operators', ['id'])
    op.create_index('ix_connected_operators_operator_id', 'connected_operators', ['operator_id'], unique=True)
    op.create_index('ix_connected_operators_cluster_name', 'connected_operators', ['cluster_name'])
    op.create_index('ix_connected_operators_is_connected', 'connected_operators', ['is_connected'])
    op.create_index('ix_connected_operators_status', 'connected_operators', ['status'])
    op.create_index('ix_connected_operators_cluster_id', 'connected_operators', ['cluster_id'])
    op.create_index('idx_op_connected_status', 'connected_operators', ['is_connected', 'status'])
    op.create_index('idx_op_cluster_name', 'connected_operators', ['cluster_name'])


def downgrade() -> None:
    op.drop_table('connected_operators')
    op.drop_table('operator_registration_tokens')
