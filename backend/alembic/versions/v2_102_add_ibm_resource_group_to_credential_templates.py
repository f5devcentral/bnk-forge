"""Add IBM resource group to credential templates.

Revision ID: v2_091
Revises: v2_090
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_102"
down_revision = "v2_101"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cloud_credential_templates",
        sa.Column("ibmcloud_resource_group", sa.String(length=255), nullable=True, server_default="default"),
    )
    op.execute("UPDATE cloud_credential_templates SET ibmcloud_resource_group = 'default' WHERE provider = 'ibm' AND ibmcloud_resource_group IS NULL")
    op.alter_column("cloud_credential_templates", "ibmcloud_resource_group", server_default=None)


def downgrade() -> None:
    op.drop_column("cloud_credential_templates", "ibmcloud_resource_group")
