"""Add project_type to projects table

Adds an explicit project_type column so the UI can drive conditional
sections (cloud region vs on-prem location, S3 backend vs local, etc.)
without inferring from credential templates.

Valid values: cloud-aws, cloud-azure, cloud-gcp, kubernetes

Also backfills existing projects:
  - Projects with cloud_provider='aws' or credential_template.provider='aws' → 'cloud-aws'
  - Projects with credential_template.provider='ssh' → 'kubernetes'
  - Others left NULL (user chooses on next edit)

Revision ID: v2_028_project_type
Revises: v2_027_op_cmd_queue
"""
import sqlalchemy as sa

from alembic import op

revision = 'v2_028_project_type'
down_revision = 'v2_027_op_cmd_queue'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('projects', sa.Column(
        'project_type', sa.String(50), nullable=True,
    ))

    # Backfill from cloud_provider where set
    op.execute("""
        UPDATE projects SET project_type = 'cloud-aws'
        WHERE cloud_provider = 'aws' AND project_type IS NULL
    """)
    op.execute("""
        UPDATE projects SET project_type = 'cloud-azure'
        WHERE cloud_provider = 'azure' AND project_type IS NULL
    """)
    op.execute("""
        UPDATE projects SET project_type = 'cloud-gcp'
        WHERE cloud_provider = 'gcp' AND project_type IS NULL
    """)
    # SSH credential template → kubernetes type
    op.execute("""
        UPDATE projects SET project_type = 'kubernetes'
        WHERE project_type IS NULL
          AND credential_template_id IN (
              SELECT id FROM cloud_credential_templates WHERE provider = 'ssh'
          )
    """)
    # on-prem / kubernetes / none cloud_provider → kubernetes type
    op.execute("""
        UPDATE projects SET project_type = 'kubernetes'
        WHERE project_type IS NULL
          AND cloud_provider IN ('on-prem', 'kubernetes', 'none')
    """)


def downgrade():
    op.drop_column('projects', 'project_type')
