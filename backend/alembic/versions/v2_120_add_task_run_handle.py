"""Add run_handle column to tasks table.

Introduces a stable, per-run identifier (uuid4 hex string) written to every
Task row created by a single deploy/destroy run. Enables the new
GET /api/projects/{project_id}/orchestration/{run_handle} endpoint to derive
run progress entirely from Task rows without the parallel_executions table.

D-001 Phase 3 — S2.

Revision ID: v2_120
Revises: v2_119
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_120"
down_revision = "v2_119"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("run_handle", sa.String(255), nullable=True),
    )
    op.create_index(
        "idx_task_run_handle",
        "tasks",
        ["run_handle"],
    )


def downgrade() -> None:
    op.drop_index("idx_task_run_handle", table_name="tasks")
    op.drop_column("tasks", "run_handle")
