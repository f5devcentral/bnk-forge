"""Add IBM COS instance fields to credential templates.

Revision ID: v2_089
Revises: v2_088
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_100"
down_revision = "v2_099"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cloud_credential_templates",
        sa.Column("ibm_cos_instance_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "cloud_credential_templates",
        sa.Column("ibm_cos_instance_crn", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cloud_credential_templates", "ibm_cos_instance_crn")
    op.drop_column("cloud_credential_templates", "ibm_cos_instance_name")
