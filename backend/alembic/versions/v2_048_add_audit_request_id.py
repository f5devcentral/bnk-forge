"""OBS-001: Add request_id correlation column to audit_logs.

Revision ID: v2_048
Revises: v2_047
"""
import sqlalchemy as sa

from alembic import op

revision = "v2_048"
down_revision = "v2_047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("request_id", sa.String(36), nullable=True))
    op.create_index("idx_audit_request_id", "audit_logs", ["request_id"])


def downgrade() -> None:
    op.drop_index("idx_audit_request_id", table_name="audit_logs")
    op.drop_column("audit_logs", "request_id")
