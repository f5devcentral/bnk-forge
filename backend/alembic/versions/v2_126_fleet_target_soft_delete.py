"""Add description, pinned_member_ids, deleted_at to fleet_targets (D-022 P5 S2).

Revision ID: v2_126
Revises: v2_125
Create Date: 2026-06-05

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "v2_126"
down_revision = "v2_125"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("fleet_targets", sa.Column("description", sa.Text(), nullable=True))
    op.add_column(
        "fleet_targets",
        sa.Column("pinned_member_ids", sa.JSON(), nullable=True),
    )
    op.add_column(
        "fleet_targets",
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_fleet_target_deleted",
        "fleet_targets",
        ["deleted_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_fleet_target_deleted", table_name="fleet_targets")
    op.drop_column("fleet_targets", "deleted_at")
    op.drop_column("fleet_targets", "pinned_member_ids")
    op.drop_column("fleet_targets", "description")
