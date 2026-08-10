"""Add IBM IAM token cache columns to cloud_credential_templates.

Lets IBMCloudService cache short-lived IAM bearer tokens between calls
instead of paying the ~250ms /identity/token round-trip on every region
listing, COS query, and engine_router kubeconfig refresh. Mirrors the
AWS aws_credentials_expiry / aws_sso_token_expiry pattern.

Revision ID: v2_104
Revises: v2_103
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_104"
down_revision = "v2_103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Idempotent: tolerate prior partial application from local debugging.
    has_token = bind.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'cloud_credential_templates' "
        "AND column_name = 'ibm_iam_token_encrypted'"
    )).fetchone()
    if not has_token:
        op.add_column(
            "cloud_credential_templates",
            sa.Column("ibm_iam_token_encrypted", sa.Text(), nullable=True),
        )

    has_expiry = bind.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'cloud_credential_templates' "
        "AND column_name = 'ibm_iam_token_expiry'"
    )).fetchone()
    if not has_expiry:
        op.add_column(
            "cloud_credential_templates",
            sa.Column("ibm_iam_token_expiry", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("cloud_credential_templates", "ibm_iam_token_expiry")
    op.drop_column("cloud_credential_templates", "ibm_iam_token_encrypted")
