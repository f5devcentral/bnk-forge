"""Retire duplicate BNK existing-cluster system template.

Safely deactivates the legacy seeded duplicate row (`f5-bnk-2.2`) so only the
canonical `bnk-on-k8s` existing-cluster blueprint remains user-visible.

Revision ID: v2_056
Revises: v2_055
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_056"
down_revision = "v2_055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE stack_templates
            SET is_active = FALSE,
                is_featured = FALSE,
                updated_at = CURRENT_TIMESTAMP
            WHERE slug = :slug
              AND created_by = 'system'
              AND is_active = TRUE
            """
        ),
        {"slug": "f5-bnk-2.2"},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE stack_templates
            SET is_active = TRUE,
                is_featured = TRUE,
                updated_at = CURRENT_TIMESTAMP
            WHERE slug = :slug
              AND created_by = 'system'
            """
        ),
        {"slug": "f5-bnk-2.2"},
    )
