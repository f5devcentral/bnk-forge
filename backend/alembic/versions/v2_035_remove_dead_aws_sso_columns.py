"""Remove dead AWS SSO + cached credential columns from projects table (IMP-013).

These columns are fully replaced by CloudCredentialTemplate in models/system.py.
Projects now use credential_template_id FK to reference templates.

Revision ID: v2_035
Revises: v2_034
Create Date: 2026-02-22
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "v2_035"
down_revision = "v2_034"
branch_labels = None
depends_on = None

# Columns to remove (9 total)
_COLUMNS = [
    "aws_sso_enabled",
    "aws_sso_start_url",
    "aws_sso_region",
    "aws_sso_account_id",
    "aws_sso_role_name",
    "aws_access_key_id",
    "aws_secret_access_key_encrypted",
    "aws_session_token_encrypted",
    "aws_credentials_expiry",
]


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        for col in _COLUMNS:
            batch_op.drop_column(col)


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(sa.Column("aws_sso_enabled", sa.Boolean(), default=False))
        batch_op.add_column(sa.Column("aws_sso_start_url", sa.String(500)))
        batch_op.add_column(sa.Column("aws_sso_region", sa.String(50)))
        batch_op.add_column(sa.Column("aws_sso_account_id", sa.String(20)))
        batch_op.add_column(sa.Column("aws_sso_role_name", sa.String(255)))
        batch_op.add_column(sa.Column("aws_access_key_id", sa.String(100)))
        batch_op.add_column(sa.Column("aws_secret_access_key_encrypted", sa.Text()))
        batch_op.add_column(sa.Column("aws_session_token_encrypted", sa.Text()))
        batch_op.add_column(sa.Column("aws_credentials_expiry", sa.DateTime()))
