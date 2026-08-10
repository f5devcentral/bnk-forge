"""PLATFORM-CONTEXT-002: add project and cluster platform context fields.

Revision ID: v2_049
Revises: v2_048
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_049"
down_revision = "v2_048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("target_platform_profile", sa.String(length=50), nullable=True))
    op.add_column("projects", sa.Column("platform_provider", sa.String(length=50), nullable=True))
    op.add_column("projects", sa.Column("management_boundary", sa.String(length=100), nullable=True))

    op.add_column("kubernetes_clusters", sa.Column("detected_platform_profile", sa.String(length=50), nullable=True))
    op.add_column("kubernetes_clusters", sa.Column("detected_platform_provider", sa.String(length=50), nullable=True))
    op.add_column("kubernetes_clusters", sa.Column("platform_capabilities", sa.JSON(), nullable=True))
    op.add_column("kubernetes_clusters", sa.Column("platform_constraints", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("kubernetes_clusters", "platform_constraints")
    op.drop_column("kubernetes_clusters", "platform_capabilities")
    op.drop_column("kubernetes_clusters", "detected_platform_provider")
    op.drop_column("kubernetes_clusters", "detected_platform_profile")

    op.drop_column("projects", "management_boundary")
    op.drop_column("projects", "platform_provider")
    op.drop_column("projects", "target_platform_profile")
