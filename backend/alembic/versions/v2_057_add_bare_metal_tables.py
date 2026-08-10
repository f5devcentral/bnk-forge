"""Add bare-metal DPU deployment tables.

Revision ID: v2_057
Revises: v2_056
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_057"
down_revision = "v2_056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. bnk_version_profiles (referenced by bare_metal_hosts)
    op.create_table(
        "bnk_version_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=True),
        sa.Column("bnk_manifest_version", sa.String(length=50), nullable=False),
        sa.Column("bnk_cr_kind", sa.String(length=50), nullable=False),
        sa.Column("flo_version", sa.String(length=50), nullable=False),
        sa.Column("k8s_version", sa.String(length=50), nullable=False),
        sa.Column("doca_version", sa.String(length=50), nullable=False),
        sa.Column("containerd_version", sa.String(length=50), nullable=False),
        sa.Column("runc_version", sa.String(length=50), nullable=False),
        sa.Column("calico_version", sa.String(length=50), nullable=False),
        sa.Column("cert_manager_version", sa.String(length=50), nullable=False),
        sa.Column("gateway_api_version", sa.String(length=50), nullable=False),
        sa.Column("multus_version", sa.String(length=50), nullable=False),
        sa.Column("sriov_version", sa.String(length=50), nullable=False),
        sa.Column("storage_class_type", sa.String(length=50), nullable=False),
        sa.Column("storage_provisioner", sa.String(length=255), nullable=False),
        sa.Column("feature_flags", sa.JSON(), nullable=True),
        sa.Column("full_manifest", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index(op.f("ix_bnk_version_profiles_id"), "bnk_version_profiles", ["id"], unique=False)
    op.create_index(op.f("ix_bnk_version_profiles_name"), "bnk_version_profiles", ["name"], unique=True)
    op.create_index("idx_version_profile_default", "bnk_version_profiles", ["is_default"], unique=False)

    # 2. bare_metal_hosts
    op.create_table(
        "bare_metal_hosts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("host_ip", sa.String(length=255), nullable=False),
        sa.Column("ssh_credential_id", sa.Integer(), nullable=True),
        sa.Column("ssh_port", sa.Integer(), nullable=True),
        sa.Column("jumphost_chain", sa.JSON(), nullable=True),
        sa.Column("bmc_ip", sa.String(length=255), nullable=True),
        sa.Column("bmc_username_encrypted", sa.Text(), nullable=True),
        sa.Column("bmc_password_encrypted", sa.Text(), nullable=True),
        sa.Column("bmc_access_tier", sa.String(length=50), nullable=True),
        sa.Column("bmc_vendor", sa.String(length=100), nullable=True),
        sa.Column("ipmi_ip", sa.String(length=255), nullable=True),
        sa.Column("ipmi_username_encrypted", sa.Text(), nullable=True),
        sa.Column("ipmi_password_encrypted", sa.Text(), nullable=True),
        sa.Column("topology", sa.String(length=50), nullable=True),
        sa.Column("topology_auto_detected", sa.Boolean(), nullable=True),
        sa.Column("network_mode", sa.String(length=20), nullable=True),
        sa.Column("vlan_id", sa.Integer(), nullable=True),
        sa.Column("gateway_ip", sa.String(length=255), nullable=True),
        sa.Column("host_mgmt_ip", sa.String(length=255), nullable=True),
        sa.Column("dpu_mgmt_ip", sa.String(length=255), nullable=True),
        sa.Column("version_profile_id", sa.Integer(), nullable=True),
        sa.Column("os_info", sa.JSON(), nullable=True),
        sa.Column("dpu_info", sa.JSON(), nullable=True),
        sa.Column("k8s_info", sa.JSON(), nullable=True),
        sa.Column("last_discovery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("kubernetes_cluster_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["kubernetes_cluster_id"], ["kubernetes_clusters.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ssh_credential_id"], ["ssh_credentials.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["version_profile_id"], ["bnk_version_profiles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_bare_metal_hosts_id"), "bare_metal_hosts", ["id"], unique=False)
    op.create_index(op.f("ix_bare_metal_hosts_project_id"), "bare_metal_hosts", ["project_id"], unique=False)
    op.create_index("idx_bm_host_project_name", "bare_metal_hosts", ["project_id", "name"], unique=True)
    op.create_index("idx_bm_host_project_ip", "bare_metal_hosts", ["project_id", "host_ip"], unique=False)

    # 3. bare_metal_deployments
    op.create_table(
        "bare_metal_deployments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("host_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("topology", sa.String(length=50), nullable=False),
        sa.Column("version_profile_snapshot", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="pending"),
        sa.Column("current_phase", sa.String(length=50), nullable=True),
        sa.Column("current_step_index", sa.Integer(), nullable=True),
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        sa.Column("resume_from_step", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_phase", sa.String(length=50), nullable=True),
        sa.Column("error_step_index", sa.Integer(), nullable=True),
        sa.Column("triggered_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.ForeignKeyConstraint(["host_id"], ["bare_metal_hosts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_bare_metal_deployments_id"), "bare_metal_deployments", ["id"], unique=False)
    op.create_index(op.f("ix_bare_metal_deployments_host_id"), "bare_metal_deployments", ["host_id"], unique=False)
    op.create_index(op.f("ix_bare_metal_deployments_project_id"), "bare_metal_deployments", ["project_id"], unique=False)
    op.create_index(op.f("ix_bare_metal_deployments_status"), "bare_metal_deployments", ["status"], unique=False)
    op.create_index(op.f("ix_bare_metal_deployments_celery_task_id"), "bare_metal_deployments", ["celery_task_id"], unique=False)
    op.create_index("idx_bm_deploy_host_status", "bare_metal_deployments", ["host_id", "status"], unique=False)
    op.create_index("idx_bm_deploy_project_status", "bare_metal_deployments", ["project_id", "status"], unique=False)
    op.create_index("idx_bm_deploy_created", "bare_metal_deployments", ["created_at"], unique=False)

    # 4. bare_metal_deployment_steps
    op.create_table(
        "bare_metal_deployment_steps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("deployment_id", sa.Integer(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="pending"),
        sa.Column("probe_result", sa.JSON(), nullable=True),
        sa.Column("already_done", sa.Boolean(), nullable=True),
        sa.Column("connectivity_risk", sa.Boolean(), nullable=True),
        sa.Column("connectivity_mechanism", sa.String(length=100), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("output_log", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("meta_data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.ForeignKeyConstraint(["deployment_id"], ["bare_metal_deployments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_bare_metal_deployment_steps_id"), "bare_metal_deployment_steps", ["id"], unique=False)
    op.create_index(op.f("ix_bare_metal_deployment_steps_deployment_id"), "bare_metal_deployment_steps", ["deployment_id"], unique=False)
    op.create_index(op.f("ix_bare_metal_deployment_steps_status"), "bare_metal_deployment_steps", ["status"], unique=False)
    op.create_index("idx_bm_step_deploy_index", "bare_metal_deployment_steps", ["deployment_id", "step_index"], unique=True)
    op.create_index("idx_bm_step_status", "bare_metal_deployment_steps", ["status"], unique=False)


def downgrade() -> None:
    op.drop_table("bare_metal_deployment_steps")
    op.drop_table("bare_metal_deployments")
    op.drop_table("bare_metal_hosts")
    op.drop_table("bnk_version_profiles")
