"""Add stage_detail to project_modules.

Tracks fine-grained execution progress (e.g. "Executing command 2/5...")
for poll-based UI updates during SSH module execution.

Revision ID: v2_089
Revises: v2_088
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_089"
down_revision = "v2_088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: column may already exist from a prior (since-renumbered)
    # migration that ran as the old v2_077 before the rebase onto staging.
    bind = op.get_bind()
    result = bind.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'project_modules' AND column_name = 'stage_detail'"
    ))
    if not result.fetchone():
        op.add_column(
            "project_modules",
            sa.Column("stage_detail", sa.String(length=255), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("project_modules", "stage_detail")
