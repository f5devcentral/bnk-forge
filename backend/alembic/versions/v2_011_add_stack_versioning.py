"""add stack versioning and custom stacks

Revision ID: v2_011
Revises: v2_010
Create Date: 2026-01-30

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = 'v2_011'
down_revision = 'v2_010'
branch_labels = None
depends_on = None


def upgrade():
    # Add new columns to stack_templates for Phase 4 features
    op.add_column('stack_templates', sa.Column('version', sa.String(20), server_default='1.0.0'))
    op.add_column('stack_templates', sa.Column('created_by', sa.String(50), server_default='system'))
    op.add_column('stack_templates', sa.Column('is_public', sa.Boolean, server_default='false'))
    op.add_column('stack_templates', sa.Column('forked_from', sa.Integer, nullable=True))

    # Add foreign key constraint
    op.create_foreign_key(
        'fk_stack_template_forked_from',
        'stack_templates',
        'stack_templates',
        ['forked_from'],
        ['id'],
        ondelete='SET NULL'
    )

    # Add indexes for new fields
    op.create_index('idx_stack_template_created_by', 'stack_templates', ['created_by'])
    op.create_index('idx_stack_template_is_public', 'stack_templates', ['is_public'])
    op.create_index('idx_stack_template_created_by_public', 'stack_templates', ['created_by', 'is_public'])


def downgrade():
    # Drop indexes
    op.drop_index('idx_stack_template_created_by_public', 'stack_templates')
    op.drop_index('idx_stack_template_is_public', 'stack_templates')
    op.drop_index('idx_stack_template_created_by', 'stack_templates')

    # Drop foreign key
    op.drop_constraint('fk_stack_template_forked_from', 'stack_templates', type_='foreignkey')

    # Drop columns
    op.drop_column('stack_templates', 'forked_from')
    op.drop_column('stack_templates', 'is_public')
    op.drop_column('stack_templates', 'created_by')
    op.drop_column('stack_templates', 'version')
