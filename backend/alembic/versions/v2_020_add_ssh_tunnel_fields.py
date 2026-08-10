"""Add SSH tunnel fields to credential templates and clusters

Adds SSH/On-Premises provider support:
- CloudCredentialTemplate: ssh_host, ssh_port, ssh_username, ssh_auth_type,
  ssh_password_encrypted, ssh_key_encrypted, ssh_key_passphrase_encrypted
- KubernetesCluster: ssh_remote_k8s_host, ssh_remote_k8s_port

Revision ID: v2_020_add_ssh_tunnel_fields
Revises: v2_019_encrypt_app_settings
Create Date: 2026-02-14
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = 'v2_020_add_ssh_tunnel_fields'
down_revision = 'v2_019_encrypt_app_settings'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SSH fields on CloudCredentialTemplate (provider = "ssh")
    op.add_column('cloud_credential_templates',
                  sa.Column('ssh_host', sa.String(255), nullable=True))
    op.add_column('cloud_credential_templates',
                  sa.Column('ssh_port', sa.Integer(), nullable=True, server_default='22'))
    op.add_column('cloud_credential_templates',
                  sa.Column('ssh_username', sa.String(255), nullable=True))
    op.add_column('cloud_credential_templates',
                  sa.Column('ssh_auth_type', sa.String(50), nullable=True))
    op.add_column('cloud_credential_templates',
                  sa.Column('ssh_password_encrypted', sa.Text(), nullable=True))
    op.add_column('cloud_credential_templates',
                  sa.Column('ssh_key_encrypted', sa.Text(), nullable=True))
    op.add_column('cloud_credential_templates',
                  sa.Column('ssh_key_passphrase_encrypted', sa.Text(), nullable=True))

    # Per-cluster remote K8s endpoint for SSH tunneling
    op.add_column('kubernetes_clusters',
                  sa.Column('ssh_remote_k8s_host', sa.String(255), nullable=True,
                            server_default='localhost'))
    op.add_column('kubernetes_clusters',
                  sa.Column('ssh_remote_k8s_port', sa.Integer(), nullable=True,
                            server_default='6443'))


def downgrade() -> None:
    # Remove cluster SSH tunnel columns
    op.drop_column('kubernetes_clusters', 'ssh_remote_k8s_port')
    op.drop_column('kubernetes_clusters', 'ssh_remote_k8s_host')

    # Remove credential template SSH columns
    op.drop_column('cloud_credential_templates', 'ssh_key_passphrase_encrypted')
    op.drop_column('cloud_credential_templates', 'ssh_key_encrypted')
    op.drop_column('cloud_credential_templates', 'ssh_password_encrypted')
    op.drop_column('cloud_credential_templates', 'ssh_auth_type')
    op.drop_column('cloud_credential_templates', 'ssh_username')
    op.drop_column('cloud_credential_templates', 'ssh_port')
    op.drop_column('cloud_credential_templates', 'ssh_host')
