"""Tasks gain an ``archived`` flag for operations-log housekeeping.

Revision ID: v2_135
Revises: v2_134
Create Date: 2026-06-22

Adds a single boolean column used by the operations log delete/archive/cleanup
controls (#21):
  - archived  Boolean, NOT NULL, default false — archived tasks are hidden from
              the default ops-log view but retained until explicitly deleted.

All existing rows remain valid: the column defaults to ``false`` so no backfill
is required.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "v2_135"
down_revision = "v2_134"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("archived", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("idx_task_archived", "tasks", ["archived"])


def downgrade() -> None:
    op.drop_index("idx_task_archived", table_name="tasks")
    op.drop_column("tasks", "archived")
