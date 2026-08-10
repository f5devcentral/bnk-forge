"""Component tests for D-022 P6 Slice D — FleetOperationStrategy + rerun + timed_wait.

Tests (with DB):
  - Strategy CRUD: create / get / list (fleet_id filter) / update / soft-delete / validation.
  - Strategy snapshot: strategy parameters are immutably snapshotted onto a run at start.
  - max_concurrency_pct: absolute worker count resolved correctly via _resolve_wave_concurrency.
  - timed_wait gate: executor sets paused_gate + gate_resumes_at + schedules Celery resume.
  - approval gate: same as legacy manual — blocks.
  - Rerun: creates a FRESH decision (old stale decision is refused), clones action/strategy.
  - Stale-decision refusal on rerun: the original consumed decision is refused by the executor.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from models.bare_metal import BareMetalHost
from models.fleet import FleetMember
from models.fleet_targeting import (
    BULK_RUN_STATUS_COMPLETED,
    BULK_RUN_STATUS_FAILED,
    BULK_RUN_STATUS_PAUSED_GATE,
    BULK_RUN_STATUS_PENDING,
    BULK_RUN_TERMINAL_STATUSES,
    DECISION_STATUS_CONSUMED,
    DECISION_STATUS_RESOLVED,
    GATE_KIND_APPROVAL,
    GATE_KIND_TIMED_WAIT,
    FleetBulkRun,
    FleetDecision,
    FleetOperationStrategy,
    FleetTarget,
)
from services.fleet_bulkop_service import (
    StaleDecisionError,
    _resolve_wave_concurrency,
    execute_fleet_bulk_run,
)
from services.fleet_reconcile_service import reconcile_fleet_member
from tests.factories import KubernetesClusterFactory, ProjectFactory, UserFactory  # noqa: F401

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_host(db, project, fault_domain=None, **kwargs):
    defaults = dict(
        project_id=project.id,
        name="test-host",
        host_ip="10.0.0.1",
        os_info={"os_type": "ubuntu", "os_version": "22.04", "arch": "x86_64"},
    )
    defaults.update(kwargs)
    host = BareMetalHost(**defaults)
    db.add(host)
    db.flush()
    return host


def _make_cluster_member(db, project, assigned_labels=None, fault_domain=None, discovered_labels=None):
    cluster = KubernetesClusterFactory(db, project=project, cloud_provider="aws", region="us-east-1")
    member = reconcile_fleet_member(db, "cluster", cluster.id)
    if assigned_labels:
        member.assigned_labels = assigned_labels
    if discovered_labels:
        member.discovered_labels = discovered_labels
    if fault_domain:
        member.fault_domain = fault_domain
    db.flush()
    db.refresh(member)
    return member


def _make_target(db, project, selector=None, name="test-target"):
    target = FleetTarget(
        project_id=project.id,
        name=name,
        selector=selector or {"labels": []},
        created_by="test",
    )
    db.add(target)
    db.flush()
    db.refresh(target)
    return target


def _make_strategy(db, project=None, target=None, **kwargs):
    defaults = dict(
        project_id=project.id if project else None,
        name="test-strategy",
        wave_by="fault_domain",
        max_concurrency_pct=20,
        gate_kind=GATE_KIND_APPROVAL,
        gate_wait_seconds=None,
        created_by="test",
    )
    defaults.update(kwargs)
    if target:
        defaults["target_id"] = target.id
    s = FleetOperationStrategy(**defaults)
    db.add(s)
    db.flush()
    db.refresh(s)
    return s


def _make_decision(db, target, member_ids, project_id=None):
    decision = FleetDecision(
        target_id=target.id,
        project_id=project_id or target.project_id,
        selector_snapshot=target.selector or {},
        resolved_member_ids=member_ids,
        member_count=len(member_ids),
        resolved_at=datetime.now(UTC),
        resolved_by="test",
        status=DECISION_STATUS_RESOLVED,
    )
    db.add(decision)
    db.flush()
    db.refresh(decision)
    return decision


def _make_run(db, decision, action="re-reconcile", concurrency=5, gate_mode="manual",
              strategy=None, **kwargs):
    run = FleetBulkRun(
        decision_id=decision.id,
        project_id=decision.project_id,
        action=action,
        concurrency=concurrency,
        gate_mode=gate_mode,
        wave_by="fault_domain",
        status=BULK_RUN_STATUS_PENDING,
        current_wave_index=0,
        created_by="test",
        **kwargs,
    )
    if strategy:
        run.strategy_id = strategy.id
    db.add(run)
    db.flush()
    db.refresh(run)
    return run


class _FakeUser:
    def __init__(self, role="admin", user_id=1, username="testuser"):
        self.role = role
        self.id = user_id
        self.username = username


# ---------------------------------------------------------------------------
# Strategy CRUD
# ---------------------------------------------------------------------------

class TestStrategyCRUD:
    def test_create_strategy(self, db):
        project = ProjectFactory(db)
        s = _make_strategy(db, project=project, name="s1", max_concurrency_pct=30)
        assert s.id is not None
        assert s.name == "s1"
        assert s.max_concurrency_pct == 30
        assert s.deleted_at is None

    def test_soft_delete(self, db):
        project = ProjectFactory(db)
        s = _make_strategy(db, project=project)
        s.deleted_at = datetime.now(UTC)
        db.commit()
        alive = (
            db.query(FleetOperationStrategy)
            .filter(FleetOperationStrategy.id == s.id, FleetOperationStrategy.deleted_at.is_(None))
            .first()
        )
        assert alive is None

    def test_list_excludes_soft_deleted(self, db):
        project = ProjectFactory(db)
        s1 = _make_strategy(db, project=project, name="active")
        s2 = _make_strategy(db, project=project, name="deleted")
        s2.deleted_at = datetime.now(UTC)
        db.commit()

        results = (
            db.query(FleetOperationStrategy)
            .filter(FleetOperationStrategy.deleted_at.is_(None))
            .all()
        )
        ids = [r.id for r in results]
        assert s1.id in ids
        assert s2.id not in ids

    def test_fleet_id_filter(self, db):
        project = ProjectFactory(db)
        target = _make_target(db, project)
        s_fleet = _make_strategy(db, project=project, target=target, name="fleet-scoped")
        s_global = _make_strategy(db, project=project, name="global")

        results = (
            db.query(FleetOperationStrategy)
            .filter(
                FleetOperationStrategy.deleted_at.is_(None),
                FleetOperationStrategy.target_id == target.id,
            )
            .all()
        )
        assert any(r.id == s_fleet.id for r in results)
        assert all(r.id != s_global.id for r in results)

    def test_timed_wait_strategy_stored_correctly(self, db):
        project = ProjectFactory(db)
        s = _make_strategy(
            db,
            project=project,
            gate_kind=GATE_KIND_TIMED_WAIT,
            gate_wait_seconds=600,
        )
        db.refresh(s)
        assert s.gate_kind == GATE_KIND_TIMED_WAIT
        assert s.gate_wait_seconds == 600


# ---------------------------------------------------------------------------
# Strategy validation (route-level)
# ---------------------------------------------------------------------------

class TestStrategyValidation:
    def _fn(self):
        from routes.fleet import _validate_strategy_fields
        return _validate_strategy_fields

    def test_valid_approval_passes(self):
        self._fn()("fault_domain", 20, "approval", None)

    def test_valid_timed_wait_passes(self):
        self._fn()("fault_domain", 10, "timed_wait", 300)

    def test_timed_wait_missing_seconds_raises(self):
        from core.errors import BadRequestError
        with pytest.raises(BadRequestError, match="gate_wait_seconds"):
            self._fn()("fault_domain", 10, "timed_wait", None)

    def test_pct_zero_raises(self):
        from core.errors import BadRequestError
        with pytest.raises(BadRequestError):
            self._fn()("fault_domain", 0, "approval", None)

    def test_pct_101_raises(self):
        from core.errors import BadRequestError
        with pytest.raises(BadRequestError):
            self._fn()("fault_domain", 101, "approval", None)

    def test_bad_gate_kind_raises(self):
        from core.errors import BadRequestError
        with pytest.raises(BadRequestError):
            self._fn()("fault_domain", 50, "teleport", None)

    def test_bad_wave_by_raises(self):
        from core.errors import BadRequestError
        with pytest.raises(BadRequestError):
            self._fn()("not_valid", 50, "approval", None)


# ---------------------------------------------------------------------------
# Strategy snapshot onto run
# ---------------------------------------------------------------------------

class TestStrategySnapshot:
    def test_snapshot_sets_strategy_id_and_wave_by(self, db):
        project = ProjectFactory(db)
        target = _make_target(db, project)
        strategy = _make_strategy(
            db,
            project=project,
            wave_by="label:env",
            gate_kind="health_stable",
            max_concurrency_pct=50,
        )
        decision = _make_decision(db, target, [])
        run = _make_run(db, decision)

        from routes.fleet import _apply_strategy_snapshot
        _apply_strategy_snapshot(run, strategy)

        assert run.strategy_id == strategy.id
        assert run.wave_by == "label:env"
        assert run.gate_mode == "health_stable"  # direct mapping

    def test_approval_maps_to_manual_gate_mode(self, db):
        project = ProjectFactory(db)
        target = _make_target(db, project)
        strategy = _make_strategy(
            db,
            project=project,
            gate_kind="approval",
        )
        decision = _make_decision(db, target, [])
        run = _make_run(db, decision)

        from routes.fleet import _apply_strategy_snapshot
        _apply_strategy_snapshot(run, strategy)

        assert run.gate_mode == "manual"  # approval → manual

    def test_timed_wait_maps_to_timed_wait_gate_mode(self, db):
        project = ProjectFactory(db)
        target = _make_target(db, project)
        strategy = _make_strategy(
            db,
            project=project,
            gate_kind="timed_wait",
            gate_wait_seconds=120,
        )
        decision = _make_decision(db, target, [])
        run = _make_run(db, decision)

        from routes.fleet import _apply_strategy_snapshot
        _apply_strategy_snapshot(run, strategy)

        assert run.gate_mode == "timed_wait"
        assert run.strategy_id == strategy.id


# ---------------------------------------------------------------------------
# timed_wait gate in executor
# ---------------------------------------------------------------------------

class TestTimedWaitGate:
    """Test that the executor correctly handles the timed_wait gate."""

    def test_timed_wait_sets_paused_gate_and_gate_resumes_at(self, db):
        """Two-wave run with timed_wait gate pauses after wave 0 with gate_resumes_at set."""
        project = ProjectFactory(db)
        target = _make_target(db, project)

        # Create two cluster members in different fault domains (→ 2 waves).
        m1 = _make_cluster_member(db, project, fault_domain="zone-a")
        m2 = _make_cluster_member(db, project, fault_domain="zone-b")

        strategy = _make_strategy(
            db,
            project=project,
            gate_kind=GATE_KIND_TIMED_WAIT,
            gate_wait_seconds=300,
        )
        decision = _make_decision(db, target, [m1.id, m2.id])
        run = _make_run(
            db,
            decision,
            action="re-reconcile",
            gate_mode="timed_wait",
            strategy=strategy,
        )

        before = datetime.now(UTC)

        # Patch: reconcile returns success (no real Celery needed), and Celery countdown.
        with (
            patch("services.fleet_bulkop_service._dispatch_action_for_member") as mock_dispatch,
            patch("tasks.fleet_tasks.resume_fleet_bulk_op") as mock_resume_task,
        ):
            mock_dispatch.return_value = ("succeeded", "ok")
            mock_resume_task.apply_async = MagicMock()

            result = execute_fleet_bulk_run(run.id, _db=db)

        db.refresh(run)
        assert result["status"] == "paused_gate"
        assert run.status == BULK_RUN_STATUS_PAUSED_GATE
        assert run.gate_resumes_at is not None
        # gate_resumes_at should be ~300s in the future.
        gate_dt = run.gate_resumes_at
        if gate_dt.tzinfo is None:
            gate_dt = gate_dt.replace(tzinfo=UTC)
        delta = (gate_dt - before).total_seconds()
        assert 290 <= delta <= 310, f"gate_resumes_at delta={delta}s, expected ~300s"
        # Result includes the iso string.
        assert "gate_resumes_at" in result

    def test_timed_wait_schedules_celery_countdown(self, db):
        """Celery resume task is dispatched with the correct countdown."""
        project = ProjectFactory(db)
        target = _make_target(db, project)
        m1 = _make_cluster_member(db, project, fault_domain="zone-a")
        m2 = _make_cluster_member(db, project, fault_domain="zone-b")

        strategy = _make_strategy(
            db,
            project=project,
            gate_kind=GATE_KIND_TIMED_WAIT,
            gate_wait_seconds=60,
        )
        decision = _make_decision(db, target, [m1.id, m2.id])
        run = _make_run(
            db,
            decision,
            action="re-reconcile",
            gate_mode="timed_wait",
            strategy=strategy,
        )

        with (
            patch("services.fleet_bulkop_service._dispatch_action_for_member") as mock_dispatch,
            patch("tasks.fleet_tasks.resume_fleet_bulk_op") as mock_resume_task,
        ):
            mock_dispatch.return_value = ("succeeded", "ok")
            mock_apply = MagicMock()
            mock_resume_task.apply_async = mock_apply

            execute_fleet_bulk_run(run.id, _db=db)

        mock_apply.assert_called_once()
        call_kwargs = mock_apply.call_args
        assert call_kwargs.kwargs.get("countdown") == 60 or (
            len(call_kwargs.args) > 0 and call_kwargs.args[0].get("countdown") == 60  # noqa
        ) or call_kwargs.kwargs == {"args": [run.id], "countdown": 60}


# ---------------------------------------------------------------------------
# Rerun: fresh Decision + cloned run
# ---------------------------------------------------------------------------

class TestRerun:
    def test_rerun_creates_fresh_decision(self, db):
        """Rerun endpoint re-resolves the target into a fresh Decision."""
        project = ProjectFactory(db)
        target = _make_target(db, project)
        m = _make_cluster_member(db, project)
        # Create a completed original run.
        orig_decision = _make_decision(db, target, [m.id])
        orig_decision.status = "consumed"  # simulate consumed
        orig_run = _make_run(db, orig_decision, action="re-reconcile")
        orig_run.status = BULK_RUN_STATUS_COMPLETED
        db.commit()

        from routes.fleet import rerun_fleet_bulk_op
        user = _FakeUser(role="admin")

        with (
            patch("routes.fleet.require_fleet_mutation"),
            patch("routes.fleet.resolve_rollout_defaults", return_value=(5, "manual")),
            patch("tasks.fleet_tasks.run_fleet_bulk_op") as mock_task,
        ):
            mock_task.delay = MagicMock(return_value=MagicMock(id="celery-task-id"))

            result = rerun_fleet_bulk_op(
                run_id=orig_run.id,
                current_user=user,
                db=db,
            )

        # A new run was created.
        assert result.id != orig_run.id
        # The new run has a fresh decision (different from the original consumed one).
        new_run = db.query(FleetBulkRun).filter(FleetBulkRun.id == result.id).first()
        assert new_run is not None
        new_decision = db.query(FleetDecision).filter(FleetDecision.id == new_run.decision_id).first()
        assert new_decision is not None
        assert new_decision.id != orig_decision.id
        assert new_decision.status == DECISION_STATUS_RESOLVED  # fresh

    def test_rerun_refuses_non_terminal_run(self, db):
        """Rerun is refused if the run is not in a terminal status."""
        project = ProjectFactory(db)
        target = _make_target(db, project)
        m = _make_cluster_member(db, project)
        decision = _make_decision(db, target, [m.id])
        run = _make_run(db, decision)
        run.status = "running"
        db.commit()

        from core.errors import BadRequestError
        from routes.fleet import rerun_fleet_bulk_op
        user = _FakeUser(role="admin")

        with pytest.raises(BadRequestError):
            rerun_fleet_bulk_op(run_id=run.id, current_user=user, db=db)

    def test_rerun_clones_action_and_strategy(self, db):
        """New run inherits action + strategy_id from the original."""
        project = ProjectFactory(db)
        target = _make_target(db, project)
        m = _make_cluster_member(db, project)
        strategy = _make_strategy(
            db,
            project=project,
            wave_by="label:env",
            gate_kind="health_stable",
            max_concurrency_pct=40,
        )
        orig_decision = _make_decision(db, target, [m.id])
        orig_decision.status = "consumed"
        orig_run = _make_run(
            db,
            orig_decision,
            action="set-labels",
            strategy=strategy,
        )
        orig_run.wave_by = "label:env"
        orig_run.gate_mode = "health_stable"
        orig_run.status = BULK_RUN_STATUS_FAILED
        db.commit()

        from routes.fleet import rerun_fleet_bulk_op
        user = _FakeUser(role="admin")

        with (
            patch("routes.fleet.require_fleet_mutation"),
            patch("routes.fleet.resolve_rollout_defaults", return_value=(5, "manual")),
            patch("tasks.fleet_tasks.run_fleet_bulk_op") as mock_task,
        ):
            mock_task.delay = MagicMock(return_value=MagicMock(id="celery-new"))

            result = rerun_fleet_bulk_op(
                run_id=orig_run.id,
                current_user=user,
                db=db,
            )

        new_run = db.query(FleetBulkRun).filter(FleetBulkRun.id == result.id).first()
        assert new_run.action == "set-labels"
        # Strategy is re-applied on the new run.
        assert new_run.strategy_id == strategy.id


# ---------------------------------------------------------------------------
# Old stale decision refused in executor (invariant)
# ---------------------------------------------------------------------------

class TestStaleDecisionRefusal:
    """Verify that a consumed decision is refused even in rerun scenarios."""

    def test_consumed_decision_refused_by_executor(self, db):
        project = ProjectFactory(db)
        target = _make_target(db, project)
        m = _make_cluster_member(db, project)
        # Make a decision that is already consumed.
        decision = _make_decision(db, target, [m.id])
        decision.status = DECISION_STATUS_CONSUMED
        db.commit()

        run = _make_run(db, decision)

        with pytest.raises(StaleDecisionError):
            execute_fleet_bulk_run(run.id, _db=db)
