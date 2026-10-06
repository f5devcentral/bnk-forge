"""Add routing_info to proxy_deployments.

Revision ID: v2_161
Revises: v2_160
"""
import sqlalchemy as sa

from alembic import op

revision = "v2_161"
down_revision = "v2_160"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("proxy_deployments") as batch:
        batch.add_column(sa.Column("routing_info", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("proxy_deployments") as batch:
        batch.drop_column("routing_info")
