"""Add per-cluster SSH credential template FK to kubernetes_clusters

Allows each cluster to have its own SSH credential for tunneling,
independent of the project's credential template.

Revision ID: v2_025_cluster_ssh_cred
Revises: v2_024_add_operator_tables
"""
import sqlalchemy as sa

from alembic import op

revision = 'v2_025_cluster_ssh_cred'
down_revision = 'v2_024_add_operator_tables'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('kubernetes_clusters',
                  sa.Column('ssh_credential_template_id', sa.Integer(),
                            sa.ForeignKey('cloud_credential_templates.id'),
                            nullable=True))


def downgrade():
    op.drop_column('kubernetes_clusters', 'ssh_credential_template_id')
