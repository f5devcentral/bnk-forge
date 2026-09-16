"""Add account_id and discovery_status to kubernetes_clusters.

Revision ID: v2_156
Revises: v2_155

Adds cloud-account metadata and a coarse discovery status to the
KubernetesCluster table so that fleet-health and cluster-list views can
surface per-cluster cloud context (account/subscription) and discovery
state without extra joins.
"""
import sqlalchemy as sa

from alembic import op

revision = "v2_156"
down_revision = "v2_155"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "kubernetes_clusters",
        sa.Column("account_id", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "kubernetes_clusters",
        sa.Column("discovery_status", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("kubernetes_clusters", "discovery_status")
    op.drop_column("kubernetes_clusters", "account_id")
