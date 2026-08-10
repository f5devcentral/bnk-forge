"""Add audit log enhancements — user_id FK, http fields, resource index

Revision ID: v2_023_audit_log_enhancements
Revises: v2_022_add_users_table
Create Date: 2026-02-15
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = 'v2_023_audit_log_enhancements'
down_revision = 'v2_022_add_users_table'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new columns to audit_logs
    op.add_column('audit_logs', sa.Column('user_id', sa.Integer(), nullable=True))
    op.add_column('audit_logs', sa.Column('http_method', sa.String(length=10), nullable=True))
    op.add_column('audit_logs', sa.Column('http_path', sa.String(length=500), nullable=True))
    op.add_column('audit_logs', sa.Column('http_status_code', sa.Integer(), nullable=True))
    op.add_column('audit_logs', sa.Column('duration_ms', sa.Integer(), nullable=True))

    # Add foreign key from audit_logs.user_id -> users.id (SET NULL on delete)
    op.create_foreign_key(
        'fk_audit_logs_user_id',
        'audit_logs', 'users',
        ['user_id'], ['id'],
        ondelete='SET NULL'
    )

    # Add resource index for faster filtering
    op.create_index('idx_audit_resource', 'audit_logs', ['resource_type', 'resource_id'])


def downgrade() -> None:
    op.drop_index('idx_audit_resource', table_name='audit_logs')
    op.drop_constraint('fk_audit_logs_user_id', 'audit_logs', type_='foreignkey')
    op.drop_column('audit_logs', 'duration_ms')
    op.drop_column('audit_logs', 'http_status_code')
    op.drop_column('audit_logs', 'http_path')
    op.drop_column('audit_logs', 'http_method')
    op.drop_column('audit_logs', 'user_id')
