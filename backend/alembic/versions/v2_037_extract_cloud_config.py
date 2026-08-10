"""Extract cloud sync settings from projects table.

Creates project_cloud_configs table (IMP-019) and migrates existing data.

Columns extracted:
- cloud_sync_enabled, cloud_sync_interval_seconds
- cloud_credentials_encrypted (legacy)

cloud_provider and credential_template_id stay on projects as FK-like identifiers.

Columns are NOT dropped from the projects table in this migration to
allow for a gradual transition.

Revision ID: v2_037
Revises: v2_036
Create Date: 2026-02-22
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "v2_037"
down_revision = "v2_036"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "project_cloud_configs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("cloud_sync_enabled", sa.Boolean(), server_default="0"),
        sa.Column("cloud_sync_interval_seconds", sa.Integer(), server_default="600"),
        sa.Column("cloud_credentials_encrypted", sa.Text(), nullable=True),
    )

    # Data migration: copy existing data from projects into new table
    conn = op.get_bind()
    conn.execute(sa.text("""
        INSERT INTO project_cloud_configs (
            project_id, cloud_sync_enabled, cloud_sync_interval_seconds,
            cloud_credentials_encrypted
        )
        SELECT
            id, cloud_sync_enabled, cloud_sync_interval_seconds,
            cloud_credentials_encrypted
        FROM projects
    """))


def downgrade():
    op.drop_table("project_cloud_configs")
