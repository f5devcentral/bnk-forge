"""D-025 P1: Add severity, category, action_url, dedupe_key, metadata to notifications.

Adds the extended notification columns required by the bell-only notification
foundation (ADR D-025). Existing rows default severity='info', category='general'.

Revision ID: v2_118
Revises: v2_117
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_118"
down_revision = "v2_117"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notifications", sa.Column("severity", sa.String(20), nullable=False, server_default="info"))
    op.add_column("notifications", sa.Column("category", sa.String(50), nullable=False, server_default="general"))
    op.add_column("notifications", sa.Column("action_url", sa.String(512), nullable=True))
    op.add_column("notifications", sa.Column("dedupe_key", sa.String(255), nullable=True))
    op.add_column("notifications", sa.Column("metadata", sa.JSON(), nullable=True))

    op.create_index("idx_notification_category", "notifications", ["category"])
    op.create_index("idx_notification_dedupe_key", "notifications", ["dedupe_key"])
    op.create_index("idx_notification_user_dedupe", "notifications", ["user", "dedupe_key"])


def downgrade() -> None:
    op.drop_index("idx_notification_user_dedupe", table_name="notifications")
    op.drop_index("idx_notification_dedupe_key", table_name="notifications")
    op.drop_index("idx_notification_category", table_name="notifications")

    op.drop_column("notifications", "metadata")
    op.drop_column("notifications", "dedupe_key")
    op.drop_column("notifications", "action_url")
    op.drop_column("notifications", "category")
    op.drop_column("notifications", "severity")
