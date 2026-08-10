"""Service-level tests for benchmark run-group lifecycle fixes.

Covers:
  - C1: cancel_run cancel-the-whole-group semantics + find_running_group_child
  - C2: maybe_finalize_run_group status labelling (all-cancelled / mixed) and
        completed + failed + cancelled == total reconciliation
  - H1: claim_pending_run atomic PENDING→RUNNING claim (one winner)

These use the real (in-memory) DB session because the logic under test is
SQL-driven (conditional UPDATE, status aggregation) and a MagicMock would not
exercise the race-closing behavior the fixes depend on.
"""

from models.benchmark import BenchmarkRun, BenchmarkRunGroup
from models.enums import BenchmarkRunStatus
from services.benchmark_service import BenchmarkService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _group(db, **overrides):
    group = BenchmarkRunGroup(
        scenario_key=overrides.get("scenario_key", "baseline"),
        scenario_name="Baseline",
        run_label="baseline-envoy",
        proxy="envoy",
        model="tinyllama",
        base_url="http://envoy:10080",
        status=overrides.get("status", "running"),
        total_runs=overrides.get("total_runs", 0),
        completed_runs=0,
        failed_runs=0,
    )
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


def _child(db, group_id, status, **overrides):
    run = BenchmarkRun(
        tool="aiperf",
        proxy="envoy",
        model="tinyllama",
        base_url="http://envoy:10080",
        status=status,
        run_group_id=group_id,
        scenario_key="baseline",
        variant_label=overrides.get("variant_label", status),
        agent_id=overrides.get("agent_id"),
        latency_p50=overrides.get("latency_p50"),
        latency_p99=overrides.get("latency_p99"),
        peak_rps=overrides.get("peak_rps"),
        total_output_tokens=overrides.get("total_output_tokens"),
        config_snapshot=overrides.get("config_snapshot", {"concurrency": 50}),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


# ---------------------------------------------------------------------------
# C1 — cancel-the-whole-group
# ---------------------------------------------------------------------------

class TestCancelRunGroupSemantics:
    def test_cancel_groupChild_cancelsAllSiblingsAndFinalizesGroup(self, db):
        group = _group(db, status="running", total_runs=3)
        running = _child(db, group.id, "running", variant_label="c1")
        pending_a = _child(db, group.id, "pending", variant_label="c2")
        pending_b = _child(db, group.id, "pending", variant_label="c3")

        svc = BenchmarkService(db)
        svc.cancel_run(pending_a.id)
        db.commit()

        for child in (running, pending_a, pending_b):
            db.refresh(child)
            assert child.status == BenchmarkRunStatus.CANCELLED
        db.refresh(group)
        assert group.status == BenchmarkRunStatus.CANCELLED
        assert group.completed_at is not None

    def test_cancel_doesNotTouchAlreadyTerminalSiblings(self, db):
        group = _group(db, status="running", total_runs=3)
        completed = _child(db, group.id, "completed", variant_label="done", latency_p50=0.1)
        running = _child(db, group.id, "running", variant_label="live")
        pending = _child(db, group.id, "pending", variant_label="queued")

        svc = BenchmarkService(db)
        svc.cancel_run(running.id)
        db.commit()

        db.refresh(completed)
        db.refresh(running)
        db.refresh(pending)
        assert completed.status == BenchmarkRunStatus.COMPLETED  # untouched
        assert running.status == BenchmarkRunStatus.CANCELLED
        assert pending.status == BenchmarkRunStatus.CANCELLED
        db.refresh(group)
        # A group with a completed child + a cancel sweep still ends CANCELLED:
        # the user asked to stop the whole sweep.
        assert group.status == BenchmarkRunStatus.CANCELLED

    def test_find_running_group_child_returnsRunningChild(self, db):
        group = _group(db, status="running", total_runs=2)
        running = _child(db, group.id, "running", variant_label="r")
        _child(db, group.id, "pending", variant_label="p")

        svc = BenchmarkService(db)
        found = svc.find_running_group_child(group.id)
        assert found is not None
        assert found.id == running.id

    def test_find_running_group_child_noneWhenAllPending(self, db):
        group = _group(db, status="pending", total_runs=2)
        _child(db, group.id, "pending")
        _child(db, group.id, "pending", variant_label="p2")

        svc = BenchmarkService(db)
        assert svc.find_running_group_child(group.id) is None

    def test_cancel_standaloneRun_onlyCancelsThatRun(self, db):
        group = _group(db, status="running", total_runs=2)
        grouped = _child(db, group.id, "pending", variant_label="grouped")
        standalone = BenchmarkRun(
            tool="aiperf", proxy="nodeport", model="m", base_url="http://x",
            status="running", run_group_id=None,
        )
        db.add(standalone)
        db.commit()
        db.refresh(standalone)

        svc = BenchmarkService(db)
        svc.cancel_run(standalone.id)
        db.commit()

        db.refresh(standalone)
        db.refresh(grouped)
        db.refresh(group)
        assert standalone.status == BenchmarkRunStatus.CANCELLED
        # The unrelated group's child is untouched.
        assert grouped.status == BenchmarkRunStatus.PENDING
        assert group.status == BenchmarkRunStatus.RUNNING


# ---------------------------------------------------------------------------
# C2 — finalize labelling + count reconciliation
# ---------------------------------------------------------------------------

class TestFinalizeRunGroupLabelling:
    def test_allCancelled_finalizesCancelled(self, db):
        group = _group(db, status="running", total_runs=2)
        _child(db, group.id, "cancelled", variant_label="a")
        _child(db, group.id, "cancelled", variant_label="b")

        svc = BenchmarkService(db)
        result = svc.maybe_finalize_run_group(group.id)
        assert result.status == BenchmarkRunStatus.CANCELLED
        assert result.completed_runs == 0
        assert result.failed_runs == 0

    def test_anyCompleted_finalizesCompleted(self, db):
        group = _group(db, status="running", total_runs=3)
        _child(db, group.id, "completed", variant_label="c", latency_p50=0.1)
        _child(db, group.id, "cancelled", variant_label="x")
        _child(db, group.id, "failed", variant_label="f")

        svc = BenchmarkService(db)
        result = svc.maybe_finalize_run_group(group.id)
        assert result.status == BenchmarkRunStatus.COMPLETED

    def test_cancelledPlusFailed_noCompleted_finalizesFailed(self, db):
        # Pre-fix bug: an all-cancelled group was labelled FAILED. The fixed rule
        # reserves FAILED for groups that have a genuine FAILED child and no
        # COMPLETED — a cancelled+failed mix still ends FAILED.
        group = _group(db, status="running", total_runs=2)
        _child(db, group.id, "cancelled", variant_label="x")
        _child(db, group.id, "failed", variant_label="f")

        svc = BenchmarkService(db)
        result = svc.maybe_finalize_run_group(group.id)
        assert result.status == BenchmarkRunStatus.FAILED

    def test_counts_reconcile_completedFailedCancelledEqualsTotal(self, db):
        group = _group(db, status="running", total_runs=4)
        _child(db, group.id, "completed", variant_label="c1", latency_p50=0.1)
        _child(db, group.id, "completed", variant_label="c2", latency_p50=0.2)
        _child(db, group.id, "failed", variant_label="f1")
        _child(db, group.id, "cancelled", variant_label="x1")

        svc = BenchmarkService(db)
        result = svc.maybe_finalize_run_group(group.id)
        cancelled = result.aggregate_json["status_counts"].get("cancelled", 0)
        assert result.completed_runs == 2
        assert result.failed_runs == 1
        assert cancelled == 1
        assert result.completed_runs + result.failed_runs + cancelled == result.total_runs

    def test_aggregate_status_counts_includesCancelled(self, db):
        group = _group(db, status="running", total_runs=2)
        _child(db, group.id, "completed", variant_label="c1", latency_p50=0.1)
        _child(db, group.id, "cancelled", variant_label="x1")

        svc = BenchmarkService(db)
        result = svc.maybe_finalize_run_group(group.id)
        counts = result.aggregate_json["status_counts"]
        assert counts["completed"] == 1
        assert counts["cancelled"] == 1

    def test_notAllTerminal_doesNotFinalize(self, db):
        group = _group(db, status="running", total_runs=2)
        _child(db, group.id, "completed", variant_label="c", latency_p50=0.1)
        _child(db, group.id, "running", variant_label="r")

        svc = BenchmarkService(db)
        result = svc.maybe_finalize_run_group(group.id)
        assert result.status == BenchmarkRunStatus.RUNNING
        assert result.completed_at is None


# ---------------------------------------------------------------------------
# H1 — atomic claim
# ---------------------------------------------------------------------------

class TestClaimPendingRun:
    def test_claim_pendingRun_succeedsAndTransitionsToRunning(self, db):
        group = _group(db, status="running", total_runs=1)
        child = _child(db, group.id, "pending")

        svc = BenchmarkService(db)
        won = svc.claim_pending_run(child.id)
        db.commit()
        assert won is True
        db.refresh(child)
        assert child.status == BenchmarkRunStatus.RUNNING
        assert child.started_at is not None

    def test_claim_secondCallLoses_onlyOneWinner(self, db):
        # Simulates two terminal WS messages racing for the same lowest-id child:
        # the conditional UPDATE means exactly one claim wins (rowcount==1).
        group = _group(db, status="running", total_runs=1)
        child = _child(db, group.id, "pending")

        svc = BenchmarkService(db)
        first = svc.claim_pending_run(child.id)
        second = svc.claim_pending_run(child.id)
        db.commit()
        assert first is True
        assert second is False

    def test_claim_alreadyRunning_loses(self, db):
        group = _group(db, status="running", total_runs=1)
        child = _child(db, group.id, "running")

        svc = BenchmarkService(db)
        assert svc.claim_pending_run(child.id) is False

    def test_release_claimedRun_revertsToPending(self, db):
        # After a winning claim whose dispatch failed, release reverts RUNNING→PENDING
        # via a conditional bulk UPDATE (a stale ORM attribute write would no-op).
        group = _group(db, status="running", total_runs=1)
        child = _child(db, group.id, "pending")

        svc = BenchmarkService(db)
        assert svc.claim_pending_run(child.id) is True
        svc.release_claimed_run(child.id)
        db.commit()
        db.refresh(child)
        assert child.status == BenchmarkRunStatus.PENDING
        assert child.started_at is None
