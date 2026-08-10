"""Add deleted_at to fleet_policies (D-022 P5 S3 — soft-delete).

Revision ID: v2_127
Revises: v2_126
Create Date: 2026-06-05

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "v2_127"
down_revision = "v2_126"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fleet_policies",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_fleet_policy_deleted",
        "fleet_policies",
        ["deleted_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_fleet_policy_deleted", table_name="fleet_policies")
    op.drop_column("fleet_policies", "deleted_at")
