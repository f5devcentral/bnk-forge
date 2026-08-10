"""Add outputs and variables columns to project_modules

Revision ID: v2_001
Revises:
Create Date: 2026-01-20

BNK-Forge v2 migration:
- Add 'outputs' column for storing OpenTofu outputs after apply
- Add 'variables' column (alias for variable_overrides)
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON

from alembic import op

# revision identifiers, used by Alembic.
revision = 'v2_001'
down_revision = None  # First v2 migration
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add outputs column
    op.add_column('project_modules',
        sa.Column('outputs', JSON, nullable=True,
                  comment='OpenTofu outputs captured after apply')
    )

    # Add variables column (new name for variable_overrides)
    op.add_column('project_modules',
        sa.Column('variables', JSON, nullable=True,
                  comment='User-provided variable values')
    )


def downgrade() -> None:
    op.drop_column('project_modules', 'outputs')
    op.drop_column('project_modules', 'variables')
