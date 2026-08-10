"""Add per-project DPU connectivity matrix storage.

Revision ID: v2_086
Revises: v2_085
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_086"
down_revision = "v2_085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Persists the latest "Run DPU connectivity matrix" output keyed by
    # project. Status follows the same vocab discovery uses
    # (pending/in_progress/completed/failed).
    bind = op.get_bind()
    for col_name, col_type in [
        ("dpu_connectivity_matrix", sa.JSON()),
        ("dpu_connectivity_status", sa.String(length=32)),
        ("dpu_connectivity_run_at", sa.DateTime(timezone=True)),
    ]:
        result = bind.execute(sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'project_dpu_settings' AND column_name = :col"
        ).bindparams(col=col_name))
        if not result.fetchone():
            op.add_column(
                "project_dpu_settings",
                sa.Column(col_name, col_type, nullable=True),
            )


def downgrade() -> None:
    op.drop_column("project_dpu_settings", "dpu_connectivity_run_at")
    op.drop_column("project_dpu_settings", "dpu_connectivity_status")
    op.drop_column("project_dpu_settings", "dpu_connectivity_matrix")
