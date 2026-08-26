"""add auto-discovery metadata to kubernetes_clusters

Revision ID: v2_156
Revises: v2_155
Create Date: 2026-08-27 06:30:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'v2_156'
down_revision = 'v2_155'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("kubernetes_clusters", sa.Column("account_id", sa.String(100), nullable=True))
    op.add_column("kubernetes_clusters", sa.Column("discovery_status", sa.String(50), nullable=True))

def downgrade() -> None:
    op.drop_column("kubernetes_clusters", "discovery_status")
    op.drop_column("kubernetes_clusters", "account_id")
