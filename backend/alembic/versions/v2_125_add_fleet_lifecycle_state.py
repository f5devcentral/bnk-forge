"""Add fleet lifecycle_state + Project fleet rollout-defaults (D-022 Phase 4).

Adds:
  fleet_members.lifecycle_state  — derived unified lifecycle state per member
  projects.fleet_default_concurrency — Project-level bulk-op concurrency default
  projects.fleet_default_gate_mode   — Project-level bulk-op gate mode default

All columns are nullable; NULL is the safe initial state (no backfill):
  - lifecycle_state NULL → treated as "unknown" by consumers until reconcile runs
  - fleet_default_* NULL → system literal fallback (concurrency=5, gate_mode=manual)

Revision ID: v2_125
Revises: v2_124
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_125"
down_revision = "v2_124"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # lifecycle_state on fleet_members
    op.add_column(
        "fleet_members",
        sa.Column("lifecycle_state", sa.String(50), nullable=True),
    )
    op.create_index(
        "idx_fleet_member_lifecycle",
        "fleet_members",
        ["lifecycle_state"],
        unique=False,
    )

    # Fleet rollout defaults on projects
    op.add_column(
        "projects",
        sa.Column("fleet_default_concurrency", sa.Integer(), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("fleet_default_gate_mode", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_index("idx_fleet_member_lifecycle", table_name="fleet_members")
    op.drop_column("fleet_members", "lifecycle_state")
    op.drop_column("projects", "fleet_default_gate_mode")
    op.drop_column("projects", "fleet_default_concurrency")
