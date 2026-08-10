"""Add fleet_members table for D-022 fleet inventory layer.

fleet_members is a thin polymorphic membership table that carries
discovered + assigned label sets for clusters, bare-metal hosts, and DPUs.
member_type is an open String (not a DB enum) so D-023 can add "f5-bigip"
without a migration.

Revision ID: v2_122
Revises: v2_121
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_122"
down_revision = "v2_121"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fleet_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("member_type", sa.String(32), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("discovered_labels", sa.JSON(), nullable=True),
        sa.Column("assigned_labels", sa.JSON(), nullable=True),
        sa.Column("fault_domain", sa.String(255), nullable=True),
        sa.Column("health_status", sa.String(50), nullable=True),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("member_type", "member_id", name="uq_fleet_member_type_id"),
    )
    op.create_index("idx_fleet_member_project", "fleet_members", ["project_id"])
    op.create_index("idx_fleet_member_type", "fleet_members", ["member_type"])


def downgrade() -> None:
    op.drop_index("idx_fleet_member_type", table_name="fleet_members")
    op.drop_index("idx_fleet_member_project", table_name="fleet_members")
    op.drop_table("fleet_members")
