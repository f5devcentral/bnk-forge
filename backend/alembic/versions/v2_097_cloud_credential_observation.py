"""Cloud-credential passive observation columns.

Phase 2 of the unified connectivity RFC. Cloud APIs don't get their own
breaker — botocore's adaptive retry handles that — but every successful
or failed boto3 call writes a timestamp + last error here so the UI can
show ``"AWS access OK as of 4 min ago"`` or
``"AWS access failed: ExpiredToken"`` without forcing the user to click
"Test" and wait 30s.
"""
import sqlalchemy as sa

from alembic import op

revision = "v2_097"
down_revision = "v2_096"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cloud_credential_templates",
        sa.Column("last_successful_call_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "cloud_credential_templates",
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "cloud_credential_templates",
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "cloud_credential_templates",
        sa.Column("last_error_message", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cloud_credential_templates", "last_error_message")
    op.drop_column("cloud_credential_templates", "last_error_code")
    op.drop_column("cloud_credential_templates", "last_error_at")
    op.drop_column("cloud_credential_templates", "last_successful_call_at")
