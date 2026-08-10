"""Add state encryption fields to projects table

Revision ID: v2_005
Revises: v2_004
Create Date: 2026-01-27

Add OpenTofu 1.8+ state encryption configuration fields to projects table.
Supports multiple key providers: pbkdf2, aws_kms, gcp_kms, azure_key_vault, openbao.
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = 'v2_005'
down_revision = 'v2_004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add state encryption fields
    op.add_column('projects', sa.Column('state_encryption_enabled', sa.Boolean(), nullable=False, server_default='true'))
    op.add_column('projects', sa.Column('encryption_provider', sa.String(50), nullable=False, server_default='pbkdf2'))
    op.add_column('projects', sa.Column('encryption_passphrase_encrypted', sa.Text(), nullable=True))
    op.add_column('projects', sa.Column('encryption_kms_key_id', sa.String(500), nullable=True))
    op.add_column('projects', sa.Column('encryption_kms_region', sa.String(50), nullable=True))
    op.add_column('projects', sa.Column('encryption_vault_url', sa.String(500), nullable=True))
    op.add_column('projects', sa.Column('encryption_vault_key_name', sa.String(255), nullable=True))
    op.add_column('projects', sa.Column('encryption_openbao_address', sa.String(500), nullable=True))
    op.add_column('projects', sa.Column('encryption_openbao_token_encrypted', sa.Text(), nullable=True))
    op.add_column('projects', sa.Column('encryption_openbao_transit_key', sa.String(255), nullable=True))


def downgrade() -> None:
    # Remove state encryption fields
    op.drop_column('projects', 'encryption_openbao_transit_key')
    op.drop_column('projects', 'encryption_openbao_token_encrypted')
    op.drop_column('projects', 'encryption_openbao_address')
    op.drop_column('projects', 'encryption_vault_key_name')
    op.drop_column('projects', 'encryption_vault_url')
    op.drop_column('projects', 'encryption_kms_region')
    op.drop_column('projects', 'encryption_kms_key_id')
    op.drop_column('projects', 'encryption_passphrase_encrypted')
    op.drop_column('projects', 'encryption_provider')
    op.drop_column('projects', 'state_encryption_enabled')
