"""Add performance indexes for FK columns and frequently queried fields

PERFORMANCE OPTIMIZATION (ADR-019):
Adds indexes on foreign key columns and frequently queried status/filter fields.
These indexes significantly improve JOIN and WHERE clause performance as data grows.

Revision ID: v2_016_add_performance_indexes
Revises: v2_015
Create Date: 2026-02-04
"""
from alembic import op

revision = 'v2_016_add_performance_indexes'
down_revision = 'v2_015'
branch_labels = None
depends_on = None


def upgrade():
    # Foreign key indexes (critical for JOINs)
    # These columns are used in relationships but may not have automatic indexes

    # K8s-related tables
    op.create_index('idx_k8s_gateways_cluster_id', 'k8s_gateways', ['cluster_id'], if_not_exists=True)
    op.create_index('idx_firewall_policies_cluster_id', 'firewall_policies', ['cluster_id'], if_not_exists=True)
    op.create_index('idx_egress_configurations_cluster_id', 'egress_configurations', ['cluster_id'], if_not_exists=True)
    op.create_index('idx_snat_pools_cluster_id', 'snat_pools', ['cluster_id'], if_not_exists=True)

    # Project-related tables
    op.create_index('idx_projects_credential_template_id', 'projects', ['credential_template_id'], if_not_exists=True)
    op.create_index('idx_environments_project_id', 'environments', ['project_id'], if_not_exists=True)
    op.create_index('idx_project_modules_project_id', 'project_modules', ['project_id'], if_not_exists=True)
    op.create_index('idx_project_modules_module_library_id', 'project_modules', ['module_library_id'], if_not_exists=True)

    # Module-related tables
    op.create_index('idx_variable_mappings_project_module_id', 'variable_mappings', ['project_module_id'], if_not_exists=True)
    op.create_index('idx_deployment_logs_module_id', 'deployment_logs', ['module_id'], if_not_exists=True)
    op.create_index('idx_module_snapshots_project_module_id', 'module_snapshots', ['project_module_id'], if_not_exists=True)

    # Task-related tables
    op.create_index('idx_drift_checks_task_id', 'drift_checks', ['task_id'], if_not_exists=True)
    op.create_index('idx_cost_estimates_task_id', 'cost_estimates', ['task_id'], if_not_exists=True)

    # Stack-related tables
    op.create_index('idx_stack_instances_template_id', 'stack_instances', ['template_id'], if_not_exists=True)

    # Status/filter indexes (frequently queried)
    op.create_index('idx_kubernetes_clusters_status', 'kubernetes_clusters', ['status'], if_not_exists=True)
    op.create_index('idx_sync_jobs_status', 'sync_jobs', ['status'], if_not_exists=True)
    op.create_index('idx_project_modules_status', 'project_modules', ['status'], if_not_exists=True)
    op.create_index('idx_tasks_status', 'tasks', ['status'], if_not_exists=True)
    op.create_index('idx_tasks_created_at', 'tasks', ['created_at'], if_not_exists=True)

    # Composite indexes for common queries
    op.create_index('idx_tasks_status_created', 'tasks', ['status', 'created_at'], if_not_exists=True)
    op.create_index('idx_project_modules_project_status', 'project_modules', ['project_id', 'status'], if_not_exists=True)


def downgrade():
    # Drop all indexes (order doesn't matter for DROP)
    op.drop_index('idx_k8s_gateways_cluster_id', table_name='k8s_gateways', if_exists=True)
    op.drop_index('idx_firewall_policies_cluster_id', table_name='firewall_policies', if_exists=True)
    op.drop_index('idx_egress_configurations_cluster_id', table_name='egress_configurations', if_exists=True)
    op.drop_index('idx_snat_pools_cluster_id', table_name='snat_pools', if_exists=True)
    op.drop_index('idx_projects_credential_template_id', table_name='projects', if_exists=True)
    op.drop_index('idx_environments_project_id', table_name='environments', if_exists=True)
    op.drop_index('idx_project_modules_project_id', table_name='project_modules', if_exists=True)
    op.drop_index('idx_project_modules_module_library_id', table_name='project_modules', if_exists=True)
    op.drop_index('idx_variable_mappings_project_module_id', table_name='variable_mappings', if_exists=True)
    op.drop_index('idx_deployment_logs_module_id', table_name='deployment_logs', if_exists=True)
    op.drop_index('idx_module_snapshots_project_module_id', table_name='module_snapshots', if_exists=True)
    op.drop_index('idx_drift_checks_task_id', table_name='drift_checks', if_exists=True)
    op.drop_index('idx_cost_estimates_task_id', table_name='cost_estimates', if_exists=True)
    op.drop_index('idx_stack_instances_template_id', table_name='stack_instances', if_exists=True)
    op.drop_index('idx_kubernetes_clusters_status', table_name='kubernetes_clusters', if_exists=True)
    op.drop_index('idx_sync_jobs_status', table_name='sync_jobs', if_exists=True)
    op.drop_index('idx_project_modules_status', table_name='project_modules', if_exists=True)
    op.drop_index('idx_tasks_status', table_name='tasks', if_exists=True)
    op.drop_index('idx_tasks_created_at', table_name='tasks', if_exists=True)
    op.drop_index('idx_tasks_status_created', table_name='tasks', if_exists=True)
    op.drop_index('idx_project_modules_project_status', table_name='project_modules', if_exists=True)
