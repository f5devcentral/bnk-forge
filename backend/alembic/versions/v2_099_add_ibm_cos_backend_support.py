"""Add IBM COS credential and backend fields.

Revision ID: v2_088
Revises: v2_087
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_099"
down_revision = "v2_098"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cloud_credential_templates",
        sa.Column("ibm_cos_hmac_access_key_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "cloud_credential_templates",
        sa.Column("ibm_cos_hmac_secret_access_key_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "project_backend_configs",
        sa.Column("state_storage_provider", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "project_backend_configs",
        sa.Column("s3_endpoint", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("project_backend_configs", "s3_endpoint")
    op.drop_column("project_backend_configs", "state_storage_provider")
    op.drop_column("cloud_credential_templates", "ibm_cos_hmac_secret_access_key_encrypted")
    op.drop_column("cloud_credential_templates", "ibm_cos_hmac_access_key_id")
