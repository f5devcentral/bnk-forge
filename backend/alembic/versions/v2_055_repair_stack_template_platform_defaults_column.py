"""Repair missing stack_templates.platform_defaults column.

This migration is a bounded schema repair for environments where
revision v2_054 is marked as applied but the physical column is absent.

Revision ID: v2_055
Revises: v2_054
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_055"
down_revision = "v2_054"
branch_labels = None
depends_on = None


def _column_exists(bind: sa.engine.Connection, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    columns = inspector.get_columns(table_name)
    return any(column.get("name") == column_name for column in columns)


def upgrade() -> None:
    bind = op.get_bind()

    if not _column_exists(bind, "stack_templates", "platform_defaults"):
        op.add_column(
            "stack_templates",
            sa.Column(
                "platform_defaults",
                sa.JSON(),
                nullable=True,
                comment="Per-platform variable overrides: {platform_profile: {var: value}}",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()

    if _column_exists(bind, "stack_templates", "platform_defaults"):
        op.drop_column("stack_templates", "platform_defaults")
