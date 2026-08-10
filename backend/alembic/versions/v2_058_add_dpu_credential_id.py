"""Add dpu_credential_id to bare_metal_hosts.

Revision ID: v2_058
Revises: v2_057
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_058"
down_revision = "v2_057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bare_metal_hosts",
        sa.Column(
            "dpu_credential_id",
            sa.Integer(),
            sa.ForeignKey("ssh_credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("bare_metal_hosts", "dpu_credential_id")
