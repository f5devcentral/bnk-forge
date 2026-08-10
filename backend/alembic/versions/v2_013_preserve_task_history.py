"""Preserve task history when modules are deleted

Change ondelete from CASCADE to SET NULL for tasks, drift_checks, and cost_estimates
to preserve history when modules are deleted (e.g., during stack destruction).

Revision ID: v2_013
Revises: v2_012
Create Date: 2026-02-03
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = 'v2_013'
down_revision = 'v2_012'
branch_labels = None
depends_on = None


def upgrade():
    # Tasks: Change module_id foreign key from CASCADE to SET NULL
    # We need to drop and recreate the constraint

    # Drop existing foreign key constraint (name may vary)
    try:
        op.drop_constraint('tasks_module_id_fkey', 'tasks', type_='foreignkey')
    except:
        pass  # Constraint might not exist or have different name

    # Recreate with SET NULL
    op.create_foreign_key(
        'tasks_module_id_fkey',
        'tasks',
        'project_modules',
        ['module_id'],
        ['id'],
        ondelete='SET NULL'
    )

    # DriftCheck: Change module_id foreign key from CASCADE to SET NULL
    try:
        op.drop_constraint('drift_checks_module_id_fkey', 'drift_checks', type_='foreignkey')
    except:
        pass

    op.create_foreign_key(
        'drift_checks_module_id_fkey',
        'drift_checks',
        'project_modules',
        ['module_id'],
        ['id'],
        ondelete='SET NULL'
    )

    # CostEstimate: Change module_id foreign key from CASCADE to SET NULL
    # Also need to make it nullable
    try:
        op.drop_constraint('cost_estimates_module_id_fkey', 'cost_estimates', type_='foreignkey')
    except:
        pass

    # Make column nullable first
    op.alter_column('cost_estimates', 'module_id', nullable=True)

    op.create_foreign_key(
        'cost_estimates_module_id_fkey',
        'cost_estimates',
        'project_modules',
        ['module_id'],
        ['id'],
        ondelete='SET NULL'
    )


def downgrade():
    # Tasks: Revert to CASCADE
    try:
        op.drop_constraint('tasks_module_id_fkey', 'tasks', type_='foreignkey')
    except:
        pass

    op.create_foreign_key(
        'tasks_module_id_fkey',
        'tasks',
        'project_modules',
        ['module_id'],
        ['id'],
        ondelete='CASCADE'
    )

    # DriftCheck: Revert to CASCADE
    try:
        op.drop_constraint('drift_checks_module_id_fkey', 'drift_checks', type_='foreignkey')
    except:
        pass

    op.create_foreign_key(
        'drift_checks_module_id_fkey',
        'drift_checks',
        'project_modules',
        ['module_id'],
        ['id'],
        ondelete='CASCADE'
    )

    # CostEstimate: Revert to CASCADE and make non-nullable
    try:
        op.drop_constraint('cost_estimates_module_id_fkey', 'cost_estimates', type_='foreignkey')
    except:
        pass

    op.alter_column('cost_estimates', 'module_id', nullable=False)

    op.create_foreign_key(
        'cost_estimates_module_id_fkey',
        'cost_estimates',
        'project_modules',
        ['module_id'],
        ['id'],
        ondelete='CASCADE'
    )
