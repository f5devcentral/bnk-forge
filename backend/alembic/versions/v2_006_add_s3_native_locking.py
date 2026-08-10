"""Add S3 backend native locking support

Revision ID: v2_006
Revises: v2_005
Create Date: 2026-01-27

Add S3 backend configuration fields to projects table.
OpenTofu 1.10+ supports native S3 state locking without DynamoDB.
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = 'v2_006'
down_revision = 'v2_005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add S3 backend configuration fields
    op.add_column('projects', sa.Column('s3_state_bucket', sa.String(255), nullable=True))
    op.add_column('projects', sa.Column('s3_state_key_prefix', sa.String(255), nullable=True))
    op.add_column('projects', sa.Column('s3_state_region', sa.String(50), nullable=True))
    op.add_column('projects', sa.Column('s3_use_native_locking', sa.Boolean(), nullable=False, server_default='true'))
    op.add_column('projects', sa.Column('s3_dynamodb_table', sa.String(255), nullable=True))


def downgrade() -> None:
    # Remove S3 backend configuration fields
    op.drop_column('projects', 's3_dynamodb_table')
    op.drop_column('projects', 's3_use_native_locking')
    op.drop_column('projects', 's3_state_region')
    op.drop_column('projects', 's3_state_key_prefix')
    op.drop_column('projects', 's3_state_bucket')
