"""Add connectivity_mode and connectivity_config to connected_operators

Supports 5 operator connectivity modes:
  - direct_ws:    Outbound WebSocket from operator to BNK-Forge
  - reverse_ssh:  Reverse SSH tunnel carries WS traffic into cluster
  - polling:      HTTP polling (works through any proxy/firewall)
  - ngrok_tunnel: BNK-Forge exposed via ngrok/cloudflare tunnel
  - in_cluster:   Both run in same K8s cluster

Revision ID: v2_026_op_connectivity
Revises: v2_025_cluster_ssh_cred
"""
import sqlalchemy as sa

from alembic import op

revision = 'v2_026_op_connectivity'
down_revision = 'v2_025_cluster_ssh_cred'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('connected_operators', sa.Column(
        'connectivity_mode', sa.String(50), nullable=False,
        server_default='direct_ws',
    ))
    op.add_column('connected_operators', sa.Column(
        'connectivity_config', sa.JSON, nullable=True,
        server_default='{}',
    ))
    op.create_index('idx_op_connectivity_mode', 'connected_operators', ['connectivity_mode'])


def downgrade():
    op.drop_index('idx_op_connectivity_mode', table_name='connected_operators')
    op.drop_column('connected_operators', 'connectivity_config')
    op.drop_column('connected_operators', 'connectivity_mode')
