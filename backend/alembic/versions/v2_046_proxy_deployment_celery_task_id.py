"""Add celery_task_id to proxy_deployments for async deploy tracking.

Revision ID: v2_046
Revises: v2_045
"""
import sqlalchemy as sa

from alembic import op

revision = "v2_046"
down_revision = "v2_045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("proxy_deployments", sa.Column("celery_task_id", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("proxy_deployments", "celery_task_id")
