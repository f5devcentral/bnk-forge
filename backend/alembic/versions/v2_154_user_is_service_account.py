"""Add users.is_service_account for service-account provenance.

Revision ID: v2_154
Revises: v2_153

bonnyr-f5 #188: ensure_service_user identified service accounts by NAME
(a one-entry denylist of "admin"), so pointing MCP_SERVICE_USERNAME at any other
human row (operator, a named user) let the boot-time reconcile overwrite its
password, promote it to admin, clear its must-change gate and re-activate it.
Provenance recorded at creation lets the seeder refuse any pre-existing row it
did not create, independent of the name.
"""
import sqlalchemy as sa

from alembic import op

revision = "v2_154"
down_revision = "v2_153"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("is_service_account", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("is_service_account")
