"""Add platform_defaults column to stack_templates.

Stores per-platform variable overrides so a single blueprint can
target multiple platforms (EKS, AKS, GKE, OCP, generic/on-prem).

Revision ID: v2_054
Revises: v2_053
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_054"
down_revision = "v2_053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "stack_templates",
        sa.Column("platform_defaults", sa.JSON(), nullable=True, comment="Per-platform variable overrides: {platform_profile: {var: value}}"),
    )


def downgrade() -> None:
    op.drop_column("stack_templates", "platform_defaults")
