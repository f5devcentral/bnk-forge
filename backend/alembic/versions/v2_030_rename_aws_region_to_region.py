"""Rename aws_region to region on projects, credential_templates, and environments

Revision ID: v2_030
Revises: v2_029
Create Date: 2026-02-16

This migration renames the 'aws_region' column to 'region' across three tables,
reflecting BNK-Forge's provider-neutral support for AWS, Azure, GCP, and on-prem.
The column stores whichever region identifier is appropriate for the project's
cloud provider (e.g. 'us-west-2' for AWS, 'eastus' for Azure, 'us-central1' for GCP).
"""
from alembic import op

# revision identifiers
revision = 'v2_030'
down_revision = 'v2_029_alert_channels'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename aws_region -> region on all three tables
    op.alter_column('cloud_credential_templates', 'aws_region', new_column_name='region')
    op.alter_column('projects', 'aws_region', new_column_name='region')
    op.alter_column('environments', 'aws_region', new_column_name='region')


def downgrade() -> None:
    op.alter_column('cloud_credential_templates', 'region', new_column_name='aws_region')
    op.alter_column('projects', 'region', new_column_name='aws_region')
    op.alter_column('environments', 'region', new_column_name='aws_region')
