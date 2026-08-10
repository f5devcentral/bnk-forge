"""Persist SSH-credential test status on the row.

New columns record the outcome of the last `test_credential` call so
the UI can render a colour-coded status dot without needing to re-test
on every render. The SSHCredentials page auto-tests all credentials on
mount (and after create / edit); users can also hit a per-row Re-test
button.

Revision ID: v2_078
Revises: v2_077
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_078"
down_revision = "v2_077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ssh_credentials", sa.Column("last_test_status", sa.String(length=16), nullable=True))
    op.add_column("ssh_credentials", sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ssh_credentials", sa.Column("last_test_message", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("ssh_credentials", "last_test_message")
    op.drop_column("ssh_credentials", "last_test_at")
    op.drop_column("ssh_credentials", "last_test_status")
