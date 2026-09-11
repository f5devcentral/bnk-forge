"""Add cluster metadata fields to kubernetes_clusters.

Revision ID: v2_157
Revises: v2_156

Adds node_count, connectivity_status, integration_status, zones, and
access_method so cluster list/detail views can surface per-cluster
metadata without extra joins or probes.
"""
import sqlalchemy as sa

from alembic import op

revision = "v2_157"
down_revision = "v2_156"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "kubernetes_clusters",
        sa.Column("node_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "kubernetes_clusters",
        sa.Column("connectivity_status", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "kubernetes_clusters",
        sa.Column("integration_status", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "kubernetes_clusters",
        sa.Column("zones", sa.JSON(), nullable=True),
    )
    op.add_column(
        "kubernetes_clusters",
        sa.Column("access_method", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("kubernetes_clusters", "access_method")
    op.drop_column("kubernetes_clusters", "zones")
    op.drop_column("kubernetes_clusters", "integration_status")
    op.drop_column("kubernetes_clusters", "connectivity_status")
    op.drop_column("kubernetes_clusters", "node_count")
