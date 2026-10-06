"""Repair benchmark run data written before the ingest/group fixes.

Revision ID: v2_160
Revises: v2_159

Three corrections, matching what the code now writes for new rows:

* A "completed" run with no requests measured nothing. It is marked failed with
  an empty success rate (it was shown as 100% success and lifted the average).
* ``peak_rps`` was a copy of the average rps (aiperf exports no per-second
  series). Copies are cleared so they no longer read as a measured peak.
* Run-group counters drifted when child runs were deleted. Counters, status and
  roll-ups are re-derived from the children that exist; a group with no children
  left is removed.

Downgrade is a no-op: the previous values were wrong, not a schema shape.
"""
import json

import sqlalchemy as sa

from alembic import op

revision = "v2_160"
down_revision = "v2_159"
branch_labels = None
depends_on = None

NO_REQUESTS_ERROR = "No requests were recorded (0 total): nothing was measured"
TERMINAL = {"completed", "failed", "cancelled"}


def upgrade() -> None:
    bind = op.get_bind()

    bind.execute(
        sa.text(
            "UPDATE benchmark_runs SET status = 'failed', success_rate_pct = NULL, "
            "error_message = COALESCE(error_message, :msg) "
            "WHERE status = 'completed' AND COALESCE(total_requests, 0) = 0"
        ),
        {"msg": NO_REQUESTS_ERROR},
    )
    bind.execute(
        sa.text(
            "UPDATE benchmark_runs SET peak_rps = NULL "
            "WHERE peak_rps IS NOT NULL AND peak_rps = overall_rps"
        )
    )

    groups = bind.execute(sa.text("SELECT id, status, aggregate_json FROM benchmark_run_groups")).fetchall()
    for group_id, group_status, aggregate in groups:
        children = bind.execute(
            sa.text(
                "SELECT status, latency_p50, latency_p99, overall_rps, total_output_tokens "
                "FROM benchmark_runs WHERE run_group_id = :gid"
            ),
            {"gid": group_id},
        ).fetchall()
        if not children:
            bind.execute(sa.text("DELETE FROM benchmark_run_groups WHERE id = :gid"), {"gid": group_id})
            continue

        statuses = [str(c.status) for c in children]
        completed = [c for c in children if c.status == "completed"]
        failed = statuses.count("failed")
        cancelled = statuses.count("cancelled")
        status = group_status
        if all(s in TERMINAL for s in statuses):
            if completed:
                status = "completed"
            elif cancelled and not failed:
                status = "cancelled"
            else:
                status = "failed"

        def _avg(values):
            values = [v for v in values if v is not None]
            return sum(values) / len(values) if values else None

        rps = [c.overall_rps for c in completed if c.overall_rps is not None]
        tokens = [c.total_output_tokens for c in completed if c.total_output_tokens is not None]
        counts: dict[str, int] = {}
        for s in statuses:
            counts[s] = counts.get(s, 0) + 1
        agg = aggregate if isinstance(aggregate, dict) else json.loads(aggregate or "{}")
        agg["status_counts"] = counts
        for variant in agg.get("variants", []):
            variant["peak_rps"] = None

        bind.execute(
            sa.text(
                "UPDATE benchmark_run_groups SET total_runs = :total, completed_runs = :completed, "
                "failed_runs = :failed, status = :status, avg_latency_p50 = :p50, "
                "avg_latency_p99 = :p99, peak_rps = :peak, total_output_tokens = :tokens, "
                "aggregate_json = :agg WHERE id = :gid"
            ),
            {
                "total": len(children),
                "completed": len(completed),
                "failed": failed,
                "status": status,
                "p50": _avg(c.latency_p50 for c in completed),
                "p99": _avg(c.latency_p99 for c in completed),
                "peak": max(rps) if rps else None,
                "tokens": sum(tokens) if tokens else None,
                "agg": json.dumps(agg),
                "gid": group_id,
            },
        )


def downgrade() -> None:
    pass
