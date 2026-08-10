"""Add stack_instance_id to project_modules

Revision ID: v2_014
Revises: v2_013
Create Date: 2026-02-03

Links ProjectModule to StackInstance for proper grouping and stack-level operations.
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = 'v2_014'
down_revision = 'v2_013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add stack_instance_id column to project_modules
    op.add_column(
        'project_modules',
        sa.Column('stack_instance_id', sa.Integer(), nullable=True)
    )

    # Add foreign key constraint
    op.create_foreign_key(
        'fk_project_modules_stack_instance',
        'project_modules',
        'stack_instances',
        ['stack_instance_id'],
        ['id'],
        ondelete='SET NULL'
    )

    # Add index for efficient querying
    op.create_index(
        'idx_project_module_stack_instance',
        'project_modules',
        ['stack_instance_id']
    )

    # Backfill existing modules: set stack_instance_id based on deployed_modules JSON
    # This is done in raw SQL for efficiency
    op.execute("""
        UPDATE project_modules pm
        SET stack_instance_id = si.id
        FROM stack_instances si
        WHERE pm.id = ANY(
            SELECT jsonb_array_elements_text(si.deployed_modules::jsonb)::integer
        )
        AND pm.stack_instance_id IS NULL
    """)


def downgrade() -> None:
    op.drop_index('idx_project_module_stack_instance', table_name='project_modules')
    op.drop_constraint('fk_project_modules_stack_instance', 'project_modules', type_='foreignkey')
    op.drop_column('project_modules', 'stack_instance_id')
