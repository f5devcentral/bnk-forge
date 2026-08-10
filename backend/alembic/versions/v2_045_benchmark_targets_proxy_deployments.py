"""Add benchmark targets and proxy deployments tables.

Phase 4b: Benchmark Targets — K8s clusters + LLM endpoints that benchmarks
run against, with proxy deployments managed via Helm.

Also adds target_id and proxy_deployment_id FKs to benchmark_runs for
linking runs to the specific target/proxy they tested.

Revision ID: v2_045
Revises: v2_044
"""
import sqlalchemy as sa

from alembic import op

revision = "v2_045"
down_revision = "v2_044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- benchmark_targets (K8s cluster + LLM endpoint for testing) --
    op.create_table(
        "benchmark_targets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("cluster_id", sa.Integer(), sa.ForeignKey("kubernetes_clusters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("llm_base_url", sa.String(500), nullable=False),
        sa.Column("llm_model", sa.String(255), nullable=False),
        sa.Column("llm_namespace", sa.String(255), server_default="default", nullable=False),
        sa.Column("llm_endpoint", sa.String(255), server_default="/v1/chat/completions", nullable=False),
        sa.Column("proxy_namespace", sa.String(255), server_default="perf-proxies", nullable=False),
        sa.Column("status", sa.String(50), server_default="active", nullable=False),
        sa.Column("last_validated", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validation_msg", sa.Text(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_benchmark_targets_id", "benchmark_targets", ["id"])
    op.create_index("ix_benchmark_targets_name", "benchmark_targets", ["name"], unique=True)
    op.create_index("idx_benchmark_target_cluster", "benchmark_targets", ["cluster_id"])
    op.create_index("idx_benchmark_target_status", "benchmark_targets", ["status"])

    # -- proxy_deployments (proxy deployed to a target cluster via Helm) --
    op.create_table(
        "proxy_deployments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("benchmark_targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("proxy_type", sa.String(50), nullable=False),
        sa.Column("helm_release", sa.String(255), nullable=True),
        sa.Column("helm_chart", sa.String(255), nullable=True),
        sa.Column("helm_version", sa.String(50), nullable=True),
        sa.Column("helm_values", sa.JSON(), nullable=True),
        sa.Column("proxy_url", sa.String(500), nullable=True),
        sa.Column("external_url", sa.String(500), nullable=True),
        sa.Column("status", sa.String(50), server_default="pending", nullable=False),
        sa.Column("status_message", sa.Text(), nullable=True),
        sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_proxy_deployments_id", "proxy_deployments", ["id"])
    op.create_index("idx_proxy_deployment_target_type", "proxy_deployments", ["target_id", "proxy_type"], unique=True)
    op.create_index("idx_proxy_deployment_status", "proxy_deployments", ["status"])

    # -- Add target/proxy FK columns to benchmark_runs --
    op.add_column("benchmark_runs", sa.Column("target_id", sa.Integer(), sa.ForeignKey("benchmark_targets.id", ondelete="SET NULL"), nullable=True))
    op.add_column("benchmark_runs", sa.Column("proxy_deployment_id", sa.Integer(), sa.ForeignKey("proxy_deployments.id", ondelete="SET NULL"), nullable=True))
    op.create_index("idx_benchmark_run_target", "benchmark_runs", ["target_id"])


def downgrade() -> None:
    op.drop_index("idx_benchmark_run_target", table_name="benchmark_runs")
    op.drop_column("benchmark_runs", "proxy_deployment_id")
    op.drop_column("benchmark_runs", "target_id")
    op.drop_table("proxy_deployments")
    op.drop_table("benchmark_targets")
