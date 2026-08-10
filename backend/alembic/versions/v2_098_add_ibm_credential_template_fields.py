"""Add IBM Cloud credential-template fields.

Revision ID: v2_087
Revises: v2_086
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_098"
down_revision = "v2_097"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cloud_credential_templates",
        sa.Column("ibmcloud_api_key_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cloud_credential_templates", "ibmcloud_api_key_encrypted")
