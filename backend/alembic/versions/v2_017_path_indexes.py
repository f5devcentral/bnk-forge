"""Add indexes on module_library.path for faster lookups

PERFORMANCE OPTIMIZATION (PERF-004):
Adds indexes on module_library.path column which is frequently used
in WHERE clauses when looking up modules by path.

Revision ID: v2_017_path_indexes
Revises: v2_016_add_performance_indexes
Create Date: 2026-02-05
"""
from alembic import op

revision = 'v2_017_path_indexes'
down_revision = 'v2_016_add_performance_indexes'
branch_labels = None
depends_on = None


def upgrade():
    # Index for path lookups
    op.create_index('idx_module_library_path', 'module_library', ['path'], if_not_exists=True)
    # Composite index for path + is_active (common query pattern)
    op.create_index('idx_module_library_path_active', 'module_library', ['path', 'is_active'], if_not_exists=True)


def downgrade():
    op.drop_index('idx_module_library_path_active', table_name='module_library', if_exists=True)
    op.drop_index('idx_module_library_path', table_name='module_library', if_exists=True)
