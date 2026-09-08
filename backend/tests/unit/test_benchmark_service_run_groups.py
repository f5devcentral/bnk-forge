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

from models.benchmark import BenchmarkAgent, BenchmarkRun, BenchmarkRunGroup
from models.enums import BenchmarkRunStatus
from services.benchmark_service import BenchmarkService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _agent(db, name="test-agent"):
    agent = BenchmarkAgent(
        name=name,
        status="connected",
        managed=False,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


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


class TestGetFirstPendingRunForAgent:
    def test_returns_none_if_no_pending_runs(self, db):
        agent = _agent(db, name="agent-no-runs")
        svc = BenchmarkService(db)
        assert svc.get_first_pending_run_for_agent(agent.id) is None

    def test_returns_first_pending_run(self, db):
        agent = _agent(db, name="agent-first-pending")
        group = _group(db, status="running", total_runs=2)
        child1 = _child(db, group.id, "pending", agent_id=agent.id, variant_label="c1")
        _child(db, group.id, "pending", agent_id=agent.id, variant_label="c2")

        svc = BenchmarkService(db)
        found = svc.get_first_pending_run_for_agent(agent.id)
        assert found is not None
        assert found.id == child1.id

    def test_returns_none_if_another_run_already_running_for_agent(self, db):
        agent = _agent(db, name="agent-already-running")
        group = _group(db, status="running", total_runs=2)
        _child(db, group.id, "running", agent_id=agent.id, variant_label="c1")
        _child(db, group.id, "pending", agent_id=agent.id, variant_label="c2")

        svc = BenchmarkService(db)
        assert svc.get_first_pending_run_for_agent(agent.id) is None


# ---------------------------------------------------------------------------
# MAJOR-2 — group-sequential guard on the atomic claim.
#
# claim_pending_run(run_id, group_id=...) must refuse to claim a child while ANY
# sibling of that group is already RUNNING, so two children of one group can
# never both be RUNNING even when the connect-drain and _dispatch_next_group_child
# pick different sibling rows.
# ---------------------------------------------------------------------------

class TestGroupGuardedClaim:
    def test_claim_withGroupGuard_failsWhenSiblingRunning(self, db):
        group = _group(db, status="running", total_runs=2)
        _child(db, group.id, "running", variant_label="s0")
        s1 = _child(db, group.id, "pending", variant_label="s1")

        svc = BenchmarkService(db)
        won = svc.claim_pending_run(s1.id, group_id=group.id)
        db.commit()
        assert won is False
        db.refresh(s1)
        assert s1.status == BenchmarkRunStatus.PENDING  # untouched

    def test_claim_withGroupGuard_succeedsWhenNoSiblingRunning(self, db):
        group = _group(db, status="running", total_runs=2)
        s1 = _child(db, group.id, "pending", variant_label="s1")

        svc = BenchmarkService(db)
        assert svc.claim_pending_run(s1.id, group_id=group.id) is True
        db.commit()
        db.refresh(s1)
        assert s1.status == BenchmarkRunStatus.RUNNING

    def test_claim_twoSiblings_cannotBothBeRunning(self, db):
        # The MAJOR-2 invariant, proved at the state level: once one sibling wins
        # the claim, a claim of the OTHER sibling (a different row — as the drain
        # vs _dispatch_next_group_child race would pick) fails the NOT-EXISTS guard.
        group = _group(db, status="running", total_runs=2)
        s1 = _child(db, group.id, "pending", variant_label="s1")
        s2 = _child(db, group.id, "pending", variant_label="s2")

        svc = BenchmarkService(db)
        first = svc.claim_pending_run(s1.id, group_id=group.id)
        second = svc.claim_pending_run(s2.id, group_id=group.id)
        db.commit()

        assert first is True
        assert second is False
        db.refresh(s1)
        db.refresh(s2)
        running = [c for c in (s1, s2) if c.status == BenchmarkRunStatus.RUNNING]
        assert len(running) == 1  # never two siblings RUNNING at once

    def test_claim_groupGuard_onlyGuardsSameGroup(self, db):
        # A RUNNING child in group A must not block claiming a child of group B.
        group_a = _group(db, status="running", total_runs=1)
        _child(db, group_a.id, "running", variant_label="a0")
        group_b = _group(db, status="running", total_runs=1)
        b0 = _child(db, group_b.id, "pending", variant_label="b0")

        svc = BenchmarkService(db)
        assert svc.claim_pending_run(b0.id, group_id=group_b.id) is True
        db.commit()
        db.refresh(b0)
        assert b0.status == BenchmarkRunStatus.RUNNING


# ---------------------------------------------------------------------------
# MAJOR-1 — the initial POST dispatch claims the first child ATOMICALLY (and
# persists the claim) BEFORE the blocking dispatch round-trip, so a concurrent
# connect-drain cannot win a second claim of the same row and double-dispatch.
# ---------------------------------------------------------------------------

class TestInitialDispatchVsDrainRace:
    def test_initialClaim_blocksConcurrentDrainClaim_sameRow(self, db):
        group = _group(db, status="pending", total_runs=1)
        first = _child(db, group.id, "pending", variant_label="first")

        svc = BenchmarkService(db)
        # Initial POST dispatch claims + persists BEFORE its send round-trip.
        initial = svc.claim_pending_run(first.id, group_id=group.id)
        db.commit()
        # A WS (re)connect fires during the dispatch window and tries to claim the
        # same PENDING row via the drain — it must lose (row already RUNNING).
        drain = svc.claim_pending_run(first.id, group_id=group.id)
        db.commit()

        assert initial is True
        assert drain is False
        db.refresh(first)
        assert first.status == BenchmarkRunStatus.RUNNING  # claimed exactly once

    def test_drainFindsNothing_afterInitialClaimPersisted(self, db):
        # Once the initial dispatch has persisted its RUNNING claim, the drain's
        # agent-wide precheck returns nothing — no second dispatch is attempted.
        agent = _agent(db, name="agent-initial-vs-drain")
        group = _group(db, status="pending", total_runs=2)
        first = _child(db, group.id, "pending", agent_id=agent.id, variant_label="first")
        _child(db, group.id, "pending", agent_id=agent.id, variant_label="second")

        svc = BenchmarkService(db)
        assert svc.claim_pending_run(first.id, group_id=group.id) is True
        db.commit()
        assert svc.get_first_pending_run_for_agent(agent.id) is None


# ---------------------------------------------------------------------------
# MINOR-3 — the connect-drain's shared dispatch path (_dispatch_next_group_child).
#
# The drain routes grouped runs through the SAME gated dispatcher the terminal WS
# handlers use, so these cover the drain's actual claim→send→(group flip) path and
# its release_claimed_run rollback on send failure. send_command_to_agent is
# monkeypatched (no real WebSocket).
# ---------------------------------------------------------------------------

class TestDispatchNextGroupChildDrainPath:
    async def test_dispatch_claimsAndSends_thenGroupFlipsRunning(self, db, monkeypatch):
        import routes.benchmarks as bench_routes

        sent = []

        async def fake_send(agent_id, command):
            sent.append((agent_id, command))
            return True

        monkeypatch.setattr(bench_routes, "send_command_to_agent", fake_send)

        group = _group(db, status="pending", total_runs=1)
        child = _child(
            db, group.id, "pending", variant_label="only",
            config_snapshot={"concurrency": 7},
        )
        svc = BenchmarkService(db)

        await bench_routes._dispatch_next_group_child(svc, agent_id=42, group_id=group.id)
        db.commit()

        # claim → send happened exactly once, carrying the run_id + config.
        assert len(sent) == 1
        assert sent[0][0] == 42
        assert sent[0][1]["run_id"] == child.id
        assert sent[0][1]["config"] == {"concurrency": 7}
        db.refresh(child)
        assert child.status == BenchmarkRunStatus.RUNNING
        # The drain's group PENDING→RUNNING transition condition holds: a child is
        # now running, so the drain would flip the group.
        assert svc.find_running_group_child(group.id) is not None

    async def test_dispatch_revertsClaim_onSendFailure(self, db, monkeypatch):
        import routes.benchmarks as bench_routes

        async def fake_send(agent_id, command):
            return False  # WS send failed

        monkeypatch.setattr(bench_routes, "send_command_to_agent", fake_send)

        group = _group(db, status="pending", total_runs=1)
        child = _child(db, group.id, "pending", variant_label="only")
        svc = BenchmarkService(db)

        await bench_routes._dispatch_next_group_child(svc, agent_id=1, group_id=group.id)
        db.commit()

        db.refresh(child)
        # Winning claim was reverted so a later reconnect can re-dispatch it.
        assert child.status == BenchmarkRunStatus.PENDING
        assert child.started_at is None
        # No running child → the drain leaves the group PENDING.
        assert svc.find_running_group_child(group.id) is None

    async def test_dispatch_skipsWhenSiblingAlreadyRunning(self, db, monkeypatch):
        # MAJOR-2 deterministic: the drain races a run just having been dispatched
        # to a sibling. get_next_pending_group_run picks the pending child, but the
        # group-guarded claim refuses because a sibling is RUNNING → no send, and
        # the second sibling never starts.
        import routes.benchmarks as bench_routes

        sent = []

        async def fake_send(agent_id, command):
            sent.append(command)
            return True

        monkeypatch.setattr(bench_routes, "send_command_to_agent", fake_send)

        group = _group(db, status="running", total_runs=2)
        _child(db, group.id, "running", variant_label="s0")
        s1 = _child(db, group.id, "pending", variant_label="s1")
        svc = BenchmarkService(db)

        await bench_routes._dispatch_next_group_child(svc, agent_id=9, group_id=group.id)
        db.commit()

        assert sent == []  # nothing dispatched
        db.refresh(s1)
        assert s1.status == BenchmarkRunStatus.PENDING
        # Still exactly one running child in the group.
        running = [
            c for c in svc.get_run_group(group.id).runs
            if c.status == BenchmarkRunStatus.RUNNING
        ]
        assert len(running) == 1

    async def test_dispatch_noPendingChild_isNoop(self, db, monkeypatch):
        import routes.benchmarks as bench_routes

        sent = []

        async def fake_send(agent_id, command):
            sent.append(command)
            return True

        monkeypatch.setattr(bench_routes, "send_command_to_agent", fake_send)

        group = _group(db, status="completed", total_runs=1)
        _child(db, group.id, "completed", variant_label="done", latency_p50=0.1)
        svc = BenchmarkService(db)

        await bench_routes._dispatch_next_group_child(svc, agent_id=3, group_id=group.id)
        assert sent == []
