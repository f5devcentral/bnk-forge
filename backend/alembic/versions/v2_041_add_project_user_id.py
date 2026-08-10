"""Add user_id FK to projects table for ownership enforcement.

MU-001: Links projects to their owning user. Backfills existing projects
with the first admin user found (the seeded default admin).

Revision ID: v2_041
Revises: v2_040
"""
import sqlalchemy as sa

from alembic import op

revision = "v2_041"
down_revision = "v2_040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add user_id column (nullable initially for backfill)
    op.add_column("projects", sa.Column("user_id", sa.Integer(), nullable=True))

    # 2. Create FK constraint
    op.create_foreign_key(
        "fk_projects_user_id",
        "projects",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 3. Create index for fast lookups
    op.create_index("idx_projects_user_id", "projects", ["user_id"])

    # 4. Backfill: assign all existing projects to the first admin user
    conn = op.get_bind()
    admin = conn.execute(
        sa.text("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1")
    ).fetchone()

    if admin:
        conn.execute(
            sa.text("UPDATE projects SET user_id = :uid WHERE user_id IS NULL"),
            {"uid": admin[0]},
        )


def downgrade() -> None:
    op.drop_index("idx_projects_user_id", table_name="projects")
    op.drop_constraint("fk_projects_user_id", "projects", type_="foreignkey")
    op.drop_column("projects", "user_id")
