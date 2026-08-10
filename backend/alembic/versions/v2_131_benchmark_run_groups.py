"""Add benchmark_run_groups + scenario columns on benchmark_runs.

Phase 6: SCENARIO + RUN-GROUP model. A scenario (e.g. prefix-cache) expands into one
parent BenchmarkRunGroup plus N child BenchmarkRuns (one per concurrency point / phase).
Each child run remains a single aiperf invocation. Aggregate metrics roll up on the group.

Revision ID: v2_131
Revises: v2_130
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_131"
down_revision = "v2_130"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "benchmark_run_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scenario_key", sa.String(100), nullable=False),
        sa.Column("scenario_name", sa.String(255), nullable=True),
        sa.Column("run_label", sa.String(255), nullable=True),
        sa.Column(
            "target_id", sa.Integer(),
            sa.ForeignKey("benchmark_targets.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "proxy_id", sa.Integer(),
            sa.ForeignKey("proxy_deployments.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "agent_id", sa.Integer(),
            sa.ForeignKey("benchmark_agents.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("proxy", sa.String(50), nullable=True),
        sa.Column("model", sa.String(255), nullable=True),
        sa.Column("base_url", sa.String(512), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("total_runs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_runs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_runs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("aggregate_json", sa.JSON(), nullable=True),
        sa.Column("avg_latency_p50", sa.Float(), nullable=True),
        sa.Column("avg_latency_p99", sa.Float(), nullable=True),
        sa.Column("peak_rps", sa.Float(), nullable=True),
        sa.Column("total_output_tokens", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # Single index per column. scenario_key/target_id are covered by the named idx_*
    # below, so no duplicate ix_* is created for them. status keeps a single-column
    # ix_* (the named idx is the composite status,created_at).
    op.create_index("ix_benchmark_run_groups_id", "benchmark_run_groups", ["id"])
    op.create_index("ix_benchmark_run_groups_status", "benchmark_run_groups", ["status"])
    op.create_index("ix_benchmark_run_groups_created_at", "benchmark_run_groups", ["created_at"])
    op.create_index("idx_benchmark_run_group_scenario", "benchmark_run_groups", ["scenario_key"])
    op.create_index("idx_benchmark_run_group_status_created", "benchmark_run_groups", ["status", "created_at"])
    op.create_index("idx_benchmark_run_group_target", "benchmark_run_groups", ["target_id"])

    # New columns on benchmark_runs
    op.add_column(
        "benchmark_runs",
        sa.Column(
            "run_group_id", sa.Integer(),
            sa.ForeignKey("benchmark_run_groups.id", ondelete="CASCADE"), nullable=True,
        ),
    )
    op.add_column("benchmark_runs", sa.Column("scenario_key", sa.String(100), nullable=True))
    op.add_column("benchmark_runs", sa.Column("variant_label", sa.String(100), nullable=True))
    # run_group_id is covered by the named idx_benchmark_run_group (no duplicate ix_*).
    op.create_index("ix_benchmark_runs_scenario_key", "benchmark_runs", ["scenario_key"])
    op.create_index("idx_benchmark_run_group", "benchmark_runs", ["run_group_id"])


def downgrade() -> None:
    op.drop_index("idx_benchmark_run_group", table_name="benchmark_runs")
    op.drop_index("ix_benchmark_runs_scenario_key", table_name="benchmark_runs")
    op.drop_column("benchmark_runs", "variant_label")
    op.drop_column("benchmark_runs", "scenario_key")
    op.drop_column("benchmark_runs", "run_group_id")

    op.drop_index("idx_benchmark_run_group_target", table_name="benchmark_run_groups")
    op.drop_index("idx_benchmark_run_group_status_created", table_name="benchmark_run_groups")
    op.drop_index("idx_benchmark_run_group_scenario", table_name="benchmark_run_groups")
    op.drop_index("ix_benchmark_run_groups_created_at", table_name="benchmark_run_groups")
    op.drop_index("ix_benchmark_run_groups_status", table_name="benchmark_run_groups")
    op.drop_index("ix_benchmark_run_groups_id", table_name="benchmark_run_groups")
    op.drop_table("benchmark_run_groups")
