"""DEPLOY-ENGINE-EXT-003a: add module_library engine/manifest fields.

Revision ID: v2_050
Revises: v2_049
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_050"
down_revision = "v2_049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("module_library", sa.Column("engine_type", sa.String(length=50), nullable=True))
    op.add_column("module_library", sa.Column("pack_manifest", sa.JSON(), nullable=True))
    op.create_index("ix_module_library_engine_type", "module_library", ["engine_type"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_module_library_engine_type", table_name="module_library")
    op.drop_column("module_library", "pack_manifest")
    op.drop_column("module_library", "engine_type")
