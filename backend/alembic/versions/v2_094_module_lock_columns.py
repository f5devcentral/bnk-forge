"""Module-level heartbeat + fencing-token lock columns.

Replaces Redis workspace_lock_service with Postgres-native locking.
See memory: rfc_robust_module_locking.md, handoff_phase1_module_locking.md.
"""
import sqlalchemy as sa

from alembic import op

revision = "v2_094"
down_revision = "v2_093"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_modules",
        sa.Column("holding_task_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "project_modules",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "project_modules",
        sa.Column(
            "lock_fence_token",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_index(
        "ix_project_modules_heartbeat_at",
        "project_modules",
        ["heartbeat_at"],
        postgresql_where=sa.text("holding_task_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_project_modules_heartbeat_at", table_name="project_modules")
    op.drop_column("project_modules", "lock_fence_token")
    op.drop_column("project_modules", "heartbeat_at")
    op.drop_column("project_modules", "holding_task_id")
