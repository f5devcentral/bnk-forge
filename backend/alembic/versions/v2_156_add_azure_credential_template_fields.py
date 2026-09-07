"""Add Azure credential fields to cloud_credential_templates.

Revision ID: v2_156
Revises: v2_155
"""
import sqlalchemy as sa

from alembic import op

revision = "v2_156"
down_revision = "v2_155"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("cloud_credential_templates") as batch:
        batch.add_column(sa.Column("azure_auth_method", sa.String(50), nullable=True))
        batch.add_column(sa.Column("azure_client_id", sa.String(255), nullable=True))
        batch.add_column(sa.Column("azure_client_secret_encrypted", sa.Text(), nullable=True))
        batch.add_column(sa.Column("azure_sso_access_token_encrypted", sa.Text(), nullable=True))
        batch.add_column(sa.Column("azure_sso_refresh_token_encrypted", sa.Text(), nullable=True))
        batch.add_column(sa.Column("azure_sso_token_expiry", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("azure_sso_authenticated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("cloud_credential_templates") as batch:
        batch.drop_column("azure_sso_authenticated_at")
        batch.drop_column("azure_sso_token_expiry")
        batch.drop_column("azure_sso_refresh_token_encrypted")
        batch.drop_column("azure_sso_access_token_encrypted")
        batch.drop_column("azure_client_secret_encrypted")
        batch.drop_column("azure_client_id")
        batch.drop_column("azure_auth_method")
