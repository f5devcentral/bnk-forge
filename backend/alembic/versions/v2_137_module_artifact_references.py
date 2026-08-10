"""ModuleLibrary gains a resolved artifact references graph column.

Revision ID: v2_137
Revises: v2_136
Create Date: 2026-06-17

Supports module_source_kind='artifact' (bnkforge.artifact.json). Stores the
resolved references graph ({"root", "nodes", "edges"}) produced by the artifact
manifest validator so downstream pull-secret resolution can walk the graph.

Nullable JSON column — existing rows remain valid, no backfill needed.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "v2_137"
down_revision = "v2_136"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "module_library",
        sa.Column("artifact_references", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("module_library", "artifact_references")
