"""Rewrite benchmark tables for LLM inference load testing.

Drops the old "AI evaluation" benchmark tables and recreates them with the
correct schema for proxy-vs-proxy load testing comparison.

Old schema: BenchmarkAgent = AI model, BenchmarkConfig = model_provider+model_name
New schema: BenchmarkAgent = test client machine, BenchmarkConfig = RunConfig JSON,
            BenchmarkRun = load test result with proxy/tool/latency/RPS fields

Revision ID: v2_044
Revises: v2_043
"""
import sqlalchemy as sa

from alembic import op

revision = "v2_044"
down_revision = "v2_043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop old tables (order matters — agents has FK to runs)
    op.drop_table("benchmark_agents")
    op.drop_table("benchmark_runs")
    op.drop_table("benchmark_configs")

    # -- benchmark_agents (test client machines) --
    op.create_table(
        "benchmark_agents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("hostname", sa.String(255), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("capabilities", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(50), server_default="disconnected", nullable=False),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_benchmark_agents_id", "benchmark_agents", ["id"])
    op.create_index("ix_benchmark_agents_name", "benchmark_agents", ["name"], unique=True)
    op.create_index("idx_benchmark_agent_status", "benchmark_agents", ["status"])

    # -- benchmark_configs (saved RunConfig presets) --
    op.create_table(
        "benchmark_configs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tool", sa.String(50), server_default="aiperf", nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_benchmark_configs_id", "benchmark_configs", ["id"])
    op.create_index("ix_benchmark_configs_name", "benchmark_configs", ["name"], unique=True)
    op.create_index("idx_benchmark_config_tool", "benchmark_configs", ["tool"])

    # -- benchmark_runs (load test results) --
    op.create_table(
        "benchmark_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tool", sa.String(50), server_default="aiperf", nullable=False),
        sa.Column("proxy", sa.String(50), server_default="nodeport", nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("base_url", sa.String(512), nullable=False),
        sa.Column("run_label", sa.String(255), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("config_id", sa.Integer(), sa.ForeignKey("benchmark_configs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("benchmark_agents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(50), server_default="pending", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("config_snapshot", sa.JSON(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        # Denormalized metrics
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("total_requests", sa.Integer(), nullable=True),
        sa.Column("successful_requests", sa.Integer(), nullable=True),
        sa.Column("failed_requests", sa.Integer(), nullable=True),
        sa.Column("success_rate_pct", sa.Float(), nullable=True),
        sa.Column("latency_p50", sa.Float(), nullable=True),
        sa.Column("latency_p99", sa.Float(), nullable=True),
        sa.Column("overall_rps", sa.Float(), nullable=True),
        sa.Column("peak_rps", sa.Float(), nullable=True),
        sa.Column("tokens_per_sec", sa.Float(), nullable=True),
        sa.Column("total_output_tokens", sa.Integer(), nullable=True),
        # Timestamps
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_benchmark_runs_id", "benchmark_runs", ["id"])
    op.create_index("ix_benchmark_runs_tool", "benchmark_runs", ["tool"])
    op.create_index("ix_benchmark_runs_proxy", "benchmark_runs", ["proxy"])
    op.create_index("ix_benchmark_runs_model", "benchmark_runs", ["model"])
    op.create_index("ix_benchmark_runs_status", "benchmark_runs", ["status"])
    op.create_index("ix_benchmark_runs_config_id", "benchmark_runs", ["config_id"])
    op.create_index("ix_benchmark_runs_agent_id", "benchmark_runs", ["agent_id"])
    op.create_index("ix_benchmark_runs_created_at", "benchmark_runs", ["created_at"])
    op.create_index("idx_benchmark_run_proxy_created", "benchmark_runs", ["proxy", "created_at"])
    op.create_index("idx_benchmark_run_tool_proxy", "benchmark_runs", ["tool", "proxy"])
    op.create_index("idx_benchmark_run_status_created", "benchmark_runs", ["status", "created_at"])


def downgrade() -> None:
    op.drop_table("benchmark_runs")
    op.drop_table("benchmark_configs")
    op.drop_table("benchmark_agents")
