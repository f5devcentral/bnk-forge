"""Add discovery job and discovered node persistence tables.

Revision ID: v2_053
Revises: v2_052
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_053"
down_revision = "v2_052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discovery_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("ssh_credential_id", sa.Integer(), nullable=True),
        sa.Column("ssh_username", sa.String(length=255), nullable=True),
        sa.Column("ssh_port", sa.Integer(), nullable=True, server_default="22"),
        sa.Column("ssh_auth_type", sa.String(length=50), nullable=True),
        sa.Column("ssh_password_encrypted", sa.Text(), nullable=True),
        sa.Column("ssh_key_encrypted", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="pending"),
        sa.Column("total_nodes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_nodes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_nodes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ssh_credential_id"], ["ssh_credentials.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_discovery_jobs_id"), "discovery_jobs", ["id"], unique=False)
    op.create_index(op.f("ix_discovery_jobs_celery_task_id"), "discovery_jobs", ["celery_task_id"], unique=False)
    op.create_index(op.f("ix_discovery_jobs_status"), "discovery_jobs", ["status"], unique=False)
    op.create_index(op.f("ix_discovery_jobs_project_id"), "discovery_jobs", ["project_id"], unique=False)
    op.create_index("idx_discovery_job_project_status", "discovery_jobs", ["project_id", "status"], unique=False)

    op.create_table(
        "discovered_nodes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("discovery_job_id", sa.Integer(), nullable=False),
        sa.Column("ip_address", sa.String(length=255), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("ssh_credential_id", sa.Integer(), nullable=True),
        sa.Column("ssh_username", sa.String(length=255), nullable=True),
        sa.Column("ssh_port", sa.Integer(), nullable=True),
        sa.Column("ssh_auth_type", sa.String(length=50), nullable=True),
        sa.Column("ssh_password_encrypted", sa.Text(), nullable=True),
        sa.Column("ssh_key_encrypted", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("os_type", sa.String(length=100), nullable=True),
        sa.Column("os_version", sa.String(length=100), nullable=True),
        sa.Column("os_pretty_name", sa.String(length=255), nullable=True),
        sa.Column("kernel_version", sa.String(length=100), nullable=True),
        sa.Column("architecture", sa.String(length=50), nullable=True),
        sa.Column("is_dpu_host", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_dpu_node", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("dpu_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dpu_details", sa.JSON(), nullable=True),
        sa.Column("k8s_installed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("k8s_running", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("k8s_version", sa.String(length=100), nullable=True),
        sa.Column("k8s_distribution", sa.String(length=100), nullable=True),
        sa.Column("k8s_role", sa.String(length=50), nullable=True),
        sa.Column("container_runtime", sa.String(length=100), nullable=True),
        sa.Column("network_interfaces", sa.JSON(), nullable=True),
        sa.Column("discovery_log", sa.Text(), nullable=True),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.ForeignKeyConstraint(["discovery_job_id"], ["discovery_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ssh_credential_id"], ["ssh_credentials.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_discovered_nodes_id"), "discovered_nodes", ["id"], unique=False)
    op.create_index(op.f("ix_discovered_nodes_discovery_job_id"), "discovered_nodes", ["discovery_job_id"], unique=False)
    op.create_index(op.f("ix_discovered_nodes_status"), "discovered_nodes", ["status"], unique=False)
    op.create_index("idx_discovered_node_job_ip", "discovered_nodes", ["discovery_job_id", "ip_address"], unique=True)


def downgrade() -> None:
    op.drop_table("discovered_nodes")
    op.drop_table("discovery_jobs")
