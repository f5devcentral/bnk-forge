"""Add benchmark tables for AI performance dashboard (Phase 2).

Creates:
- benchmark_configs: reusable configuration presets
- benchmark_runs: individual benchmark execution sessions
- benchmark_agents: per-agent results within a run

Revision ID: v2_043
Revises: v2_042
"""
import sqlalchemy as sa

from alembic import op

revision = "v2_043"
down_revision = "v2_042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- benchmark_configs --
    op.create_table(
        "benchmark_configs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("model_provider", sa.String(100), nullable=True),
        sa.Column("model_name", sa.String(255), nullable=True),
        sa.Column("model_params", sa.JSON(), nullable=True),
        sa.Column("concurrency", sa.Integer(), server_default="1"),
        sa.Column("timeout_seconds", sa.Integer(), server_default="300"),
        sa.Column("retry_count", sa.Integer(), server_default="0"),
        sa.Column("evaluation_config", sa.JSON(), nullable=True),
        sa.Column("is_default", sa.Boolean(), server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_benchmark_configs_id", "benchmark_configs", ["id"])
    op.create_index("ix_benchmark_configs_name", "benchmark_configs", ["name"], unique=True)
    op.create_index("ix_benchmark_configs_is_default", "benchmark_configs", ["is_default"])
    op.create_index("idx_benchmark_config_provider_model", "benchmark_configs", ["model_provider", "model_name"])

    # -- benchmark_runs --
    op.create_table(
        "benchmark_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("config_id", sa.Integer(), sa.ForeignKey("benchmark_configs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("dataset_name", sa.String(255), nullable=False),
        sa.Column("dataset_version", sa.String(100), nullable=True),
        sa.Column("dataset_size", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(50), server_default="pending", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("total_agents", sa.Integer(), server_default="0"),
        sa.Column("completed_agents", sa.Integer(), server_default="0"),
        sa.Column("failed_agents", sa.Integer(), server_default="0"),
        sa.Column("aggregate_metrics", sa.JSON(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("triggered_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_benchmark_runs_id", "benchmark_runs", ["id"])
    op.create_index("ix_benchmark_runs_name", "benchmark_runs", ["name"])
    op.create_index("ix_benchmark_runs_status", "benchmark_runs", ["status"])
    op.create_index("ix_benchmark_runs_dataset_name", "benchmark_runs", ["dataset_name"])
    op.create_index("ix_benchmark_runs_config_id", "benchmark_runs", ["config_id"])
    op.create_index("ix_benchmark_runs_created_at", "benchmark_runs", ["created_at"])
    op.create_index("idx_benchmark_run_status_created", "benchmark_runs", ["status", "created_at"])
    op.create_index("idx_benchmark_run_dataset", "benchmark_runs", ["dataset_name", "dataset_version"])

    # -- benchmark_agents --
    op.create_table(
        "benchmark_agents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("benchmark_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_name", sa.String(255), nullable=False),
        sa.Column("model_provider", sa.String(100), nullable=True),
        sa.Column("model_name", sa.String(255), nullable=True),
        sa.Column("model_params", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(50), server_default="pending", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("total_tasks", sa.Integer(), server_default="0"),
        sa.Column("completed_tasks", sa.Integer(), server_default="0"),
        sa.Column("failed_tasks", sa.Integer(), server_default="0"),
        sa.Column("progress_pct", sa.Float(), server_default="0.0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("avg_latency_ms", sa.Float(), nullable=True),
        sa.Column("p50_latency_ms", sa.Float(), nullable=True),
        sa.Column("p95_latency_ms", sa.Float(), nullable=True),
        sa.Column("p99_latency_ms", sa.Float(), nullable=True),
        sa.Column("tokens_per_second", sa.Float(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("total_cost_usd", sa.Float(), nullable=True),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("accuracy", sa.Float(), nullable=True),
        sa.Column("completeness", sa.Float(), nullable=True),
        sa.Column("task_results", sa.JSON(), nullable=True),
        sa.Column("raw_output", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_benchmark_agents_id", "benchmark_agents", ["id"])
    op.create_index("ix_benchmark_agents_run_id", "benchmark_agents", ["run_id"])
    op.create_index("ix_benchmark_agents_agent_name", "benchmark_agents", ["agent_name"])
    op.create_index("ix_benchmark_agents_status", "benchmark_agents", ["status"])
    op.create_index("idx_benchmark_agent_run_status", "benchmark_agents", ["run_id", "status"])
    op.create_index("idx_benchmark_agent_model", "benchmark_agents", ["model_provider", "model_name"])
    op.create_index("idx_benchmark_agent_quality", "benchmark_agents", ["quality_score"])


def downgrade() -> None:
    op.drop_table("benchmark_agents")
    op.drop_table("benchmark_runs")
    op.drop_table("benchmark_configs")
