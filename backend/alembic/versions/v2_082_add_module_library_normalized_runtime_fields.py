"""Add normalized module source/engine/deploy-model fields.

Revision ID: v2_082
Revises: v2_081
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_082"
down_revision = "v2_081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("module_library", sa.Column("module_source_kind", sa.String(length=50), nullable=True))
    op.add_column("module_library", sa.Column("execution_engine", sa.String(length=50), nullable=True))
    op.add_column("module_library", sa.Column("deploy_model", sa.String(length=50), nullable=True))
    op.create_index("ix_module_library_module_source_kind", "module_library", ["module_source_kind"], unique=False)
    op.create_index("ix_module_library_execution_engine", "module_library", ["execution_engine"], unique=False)
    op.create_index("ix_module_library_deploy_model", "module_library", ["deploy_model"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_module_library_deploy_model", table_name="module_library")
    op.drop_index("ix_module_library_execution_engine", table_name="module_library")
    op.drop_index("ix_module_library_module_source_kind", table_name="module_library")
    op.drop_column("module_library", "deploy_model")
    op.drop_column("module_library", "execution_engine")
    op.drop_column("module_library", "module_source_kind")
