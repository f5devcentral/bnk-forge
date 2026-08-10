"""Migrate all DateTime columns from TIMESTAMP to TIMESTAMPTZ.

All 105 DateTime columns across 28 tables were storing naive timestamps.
Code uses datetime.now(UTC) (timezone-aware), causing TypeError on comparison
with naive DB values. This migration converts all columns to TIMESTAMPTZ.

PostgreSQL treats existing naive values as UTC during conversion (server
timezone is UTC), so no data transformation is needed.

Revision ID: v2_039
Revises: v2_038
Create Date: 2026-02-23
"""
from alembic import op

# revision identifiers
revision = "v2_039"
down_revision = "v2_038"
branch_labels = None
depends_on = None

# All (table, column) pairs to migrate from TIMESTAMP → TIMESTAMPTZ
COLUMNS = [
    # projects
    ("projects", "created_at"),
    ("projects", "updated_at"),
    # project_modules
    ("project_modules", "last_deployed_at"),
    ("project_modules", "last_estimated_at"),
    ("project_modules", "last_init_at"),
    ("project_modules", "created_at"),
    ("project_modules", "updated_at"),
    # project_secrets
    ("project_secrets", "last_used_at"),
    ("project_secrets", "created_at"),
    ("project_secrets", "updated_at"),
    # environments
    ("environments", "created_at"),
    ("environments", "updated_at"),
    # deployments
    ("deployments", "started_at"),
    ("deployments", "completed_at"),
    ("deployments", "created_at"),
    ("deployments", "updated_at"),
    # deployment_logs
    ("deployment_logs", "timestamp"),
    ("deployment_logs", "created_at"),
    # tasks
    ("tasks", "created_at"),
    ("tasks", "started_at"),
    ("tasks", "completed_at"),
    # parallel_executions
    ("parallel_executions", "started_at"),
    ("parallel_executions", "completed_at"),
    ("parallel_executions", "created_at"),
    ("parallel_executions", "updated_at"),
    # application_settings
    ("application_settings", "created_at"),
    ("application_settings", "updated_at"),
    # sync_jobs
    ("sync_jobs", "started_at"),
    ("sync_jobs", "completed_at"),
    ("sync_jobs", "created_at"),
    # cloud_credential_templates
    ("cloud_credential_templates", "aws_credentials_expiry"),
    ("cloud_credential_templates", "aws_sso_token_expiry"),
    ("cloud_credential_templates", "aws_sso_authenticated_at"),
    ("cloud_credential_templates", "created_at"),
    ("cloud_credential_templates", "updated_at"),
    # users
    ("users", "last_login_at"),
    ("users", "created_at"),
    ("users", "updated_at"),
    # audit_logs
    ("audit_logs", "timestamp"),
    ("audit_logs", "created_at"),
    # notifications
    ("notifications", "created_at"),
    ("notifications", "read_at"),
    # helm_charts
    ("helm_charts", "created_at"),
    ("helm_charts", "updated_at"),
    # operator_registration_tokens
    ("operator_registration_tokens", "expires_at"),
    ("operator_registration_tokens", "revoked_at"),
    ("operator_registration_tokens", "created_at"),
    ("operator_registration_tokens", "last_used_at"),
    # connected_operators
    ("connected_operators", "last_heartbeat_at"),
    ("connected_operators", "last_connected_at"),
    ("connected_operators", "last_disconnected_at"),
    ("connected_operators", "last_health_at"),
    ("connected_operators", "created_at"),
    ("connected_operators", "updated_at"),
    # operator_command_queue
    ("operator_command_queue", "picked_up_at"),
    ("operator_command_queue", "created_at"),
    ("operator_command_queue", "completed_at"),
    # module_library
    ("module_library", "test_last_run"),
    ("module_library", "last_synced"),
    ("module_library", "created_at"),
    ("module_library", "updated_at"),
    # module_sources
    ("module_sources", "last_synced_at"),
    ("module_sources", "created_at"),
    ("module_sources", "updated_at"),
    # module_snapshots
    ("module_snapshots", "created_at"),
    # kubernetes_clusters
    ("kubernetes_clusters", "last_synced_at"),
    ("kubernetes_clusters", "created_at"),
    ("kubernetes_clusters", "updated_at"),
    # k8s_gateways
    ("k8s_gateways", "created_at"),
    ("k8s_gateways", "updated_at"),
    # firewall_policies
    ("firewall_policies", "created_at"),
    ("firewall_policies", "updated_at"),
    # egress_configurations
    ("egress_configurations", "created_at"),
    ("egress_configurations", "updated_at"),
    # snat_pools
    ("snat_pools", "created_at"),
    ("snat_pools", "updated_at"),
    # stack_templates
    ("stack_templates", "created_at"),
    ("stack_templates", "updated_at"),
    # stack_instances
    ("stack_instances", "started_at"),
    ("stack_instances", "completed_at"),
    ("stack_instances", "created_at"),
    ("stack_instances", "updated_at"),
    # drift_checks
    ("drift_checks", "last_check_at"),
    ("drift_checks", "next_check_at"),
    ("drift_checks", "created_at"),
    ("drift_checks", "updated_at"),
    # drift_settings
    ("drift_settings", "created_at"),
    ("drift_settings", "updated_at"),
    # cost_estimates
    ("cost_estimates", "created_at"),
    ("cost_estimates", "updated_at"),
    # variable_mappings
    ("variable_mappings", "created_at"),
    ("variable_mappings", "updated_at"),
    # variable_mapping_templates
    ("variable_mapping_templates", "created_at"),
    ("variable_mapping_templates", "updated_at"),
    # alert_channels
    ("alert_channels", "last_fired_at"),
    ("alert_channels", "last_success_at"),
    ("alert_channels", "created_at"),
    ("alert_channels", "updated_at"),
    # alert_history
    ("alert_history", "created_at"),
    # config_snapshots
    ("config_snapshots", "created_at"),
    # config_promotions
    ("config_promotions", "applied_at"),
    # bnk_upgrades
    ("bnk_upgrades", "started_at"),
    ("bnk_upgrades", "completed_at"),
    ("bnk_upgrades", "created_at"),
    ("bnk_upgrades", "updated_at"),
]


def upgrade():
    # PostgreSQL: ALTER COLUMN ... TYPE TIMESTAMPTZ USING col AT TIME ZONE 'UTC'
    # The USING clause ensures existing naive values are treated as UTC.
    for table, column in COLUMNS:
        op.execute(
            f'ALTER TABLE {table} ALTER COLUMN "{column}" '
            f"TYPE TIMESTAMPTZ USING \"{column}\" AT TIME ZONE 'UTC'"
        )


def downgrade():
    # Revert to TIMESTAMP (drops timezone info)
    for table, column in COLUMNS:
        op.execute(
            f'ALTER TABLE {table} ALTER COLUMN "{column}" TYPE TIMESTAMP'
        )
