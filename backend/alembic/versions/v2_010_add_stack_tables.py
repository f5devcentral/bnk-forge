"""Add stack templates and instances for deployment stacks

Revision ID: v2_010
Revises: v2_009
Create Date: 2026-01-30

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = 'v2_010'
down_revision = 'v2_009'
branch_labels = None
depends_on = None


def upgrade():
    # Create stack_templates table
    op.create_table(
        'stack_templates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('slug', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('category', sa.String(length=50), nullable=True),
        sa.Column('cloud_provider', sa.String(length=50), nullable=True),
        sa.Column('icon', sa.String(length=50), nullable=True),
        sa.Column('color', sa.String(length=20), nullable=True),
        sa.Column('estimated_time', sa.String(length=100), nullable=True),
        sa.Column('estimated_cost', sa.String(length=100), nullable=True),
        sa.Column('difficulty', sa.String(length=50), nullable=True),
        sa.Column('modules', sa.JSON(), nullable=True),
        sa.Column('variable_templates', sa.JSON(), nullable=True),
        sa.Column('prerequisites', sa.JSON(), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=True),
        sa.Column('is_featured', sa.Boolean(), server_default='false', nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_stack_templates_name'), 'stack_templates', ['name'], unique=False)
    op.create_index(op.f('ix_stack_templates_slug'), 'stack_templates', ['slug'], unique=True)
    op.create_index('idx_stack_template_category_provider', 'stack_templates', ['category', 'cloud_provider'], unique=False)
    op.create_index('idx_stack_template_active_featured', 'stack_templates', ['is_active', 'is_featured'], unique=False)

    # Create stack_instances table
    op.create_table(
        'stack_instances',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('template_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('variables', sa.JSON(), nullable=True),
        sa.Column('deployed_modules', sa.JSON(), server_default='[]', nullable=True),
        sa.Column('current_step', sa.Integer(), server_default='0', nullable=True),
        sa.Column('total_steps', sa.Integer(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['template_id'], ['stack_templates.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_stack_instances_name'), 'stack_instances', ['name'], unique=False)
    op.create_index(op.f('ix_stack_instances_project_id'), 'stack_instances', ['project_id'], unique=False)
    op.create_index(op.f('ix_stack_instances_template_id'), 'stack_instances', ['template_id'], unique=False)
    op.create_index('idx_stack_instance_project_status', 'stack_instances', ['project_id', 'status'], unique=False)
    op.create_index('idx_stack_instance_template', 'stack_instances', ['template_id'], unique=False)


def downgrade():
    # Drop stack_instances table
    op.drop_index('idx_stack_instance_template', table_name='stack_instances')
    op.drop_index('idx_stack_instance_project_status', table_name='stack_instances')
    op.drop_index(op.f('ix_stack_instances_template_id'), table_name='stack_instances')
    op.drop_index(op.f('ix_stack_instances_project_id'), table_name='stack_instances')
    op.drop_index(op.f('ix_stack_instances_name'), table_name='stack_instances')
    op.drop_table('stack_instances')

    # Drop stack_templates table
    op.drop_index('idx_stack_template_active_featured', table_name='stack_templates')
    op.drop_index('idx_stack_template_category_provider', table_name='stack_templates')
    op.drop_index(op.f('ix_stack_templates_slug'), table_name='stack_templates')
    op.drop_index(op.f('ix_stack_templates_name'), table_name='stack_templates')
    op.drop_table('stack_templates')
