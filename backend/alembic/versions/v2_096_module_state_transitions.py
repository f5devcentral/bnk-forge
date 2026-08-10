"""Module state-transition audit log.

Records every status change for project_modules so stuck-state debugging
becomes a SQL query instead of grepping worker stderr. Pairs with the
transition validator in services/module_state.py (Phase 2 of the locking
RFC; see memory: rfc_robust_module_locking.md).
"""
import sqlalchemy as sa

from alembic import op

revision = "v2_096"
down_revision = "v2_095"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "module_state_transitions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "module_id",
            sa.Integer(),
            sa.ForeignKey("project_modules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_status", sa.String(50), nullable=False),
        sa.Column("to_status", sa.String(50), nullable=False),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("fence_token", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # The canonical lookup query: most-recent transitions for one module.
    op.create_index(
        "ix_module_state_transitions_module_at",
        "module_state_transitions",
        ["module_id", sa.text("at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_module_state_transitions_module_at",
        table_name="module_state_transitions",
    )
    op.drop_table("module_state_transitions")
