"""Add is_baseline column to benchmark_runs.

Revision ID: v2_147
Revises: v2_146
Create Date: 2026-07-20

Marks a completed run as the reference baseline for its (target_id, scenario_key,
config_id) context, so trend/regression tracking has a fixed comparison point.
One baseline per context is enforced in BenchmarkService.set_baseline, not the DB.
NOT NULL with server-side default so existing rows backfill to False.
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_147"
down_revision = "v2_146"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "benchmark_runs",
        sa.Column("is_baseline", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_benchmark_runs_is_baseline", "benchmark_runs", ["is_baseline"])


def downgrade() -> None:
    op.drop_index("ix_benchmark_runs_is_baseline", table_name="benchmark_runs")
    op.drop_column("benchmark_runs", "is_baseline")
