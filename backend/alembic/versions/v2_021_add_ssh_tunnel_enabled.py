"""Add ssh_tunnel_enabled boolean to kubernetes_clusters

Per-cluster opt-in toggle for SSH tunneling. Not all on-prem clusters
need tunneling (e.g. K8s API directly reachable on same network).

Revision ID: v2_021_add_ssh_tunnel_enabled
Revises: v2_020_add_ssh_tunnel_fields
Create Date: 2026-02-15
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = 'v2_021_add_ssh_tunnel_enabled'
down_revision = 'v2_020_add_ssh_tunnel_fields'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('kubernetes_clusters',
                  sa.Column('ssh_tunnel_enabled', sa.Boolean(), nullable=False,
                            server_default='false'))


def downgrade() -> None:
    op.drop_column('kubernetes_clusters', 'ssh_tunnel_enabled')
