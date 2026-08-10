"""Component tests for fleet bulk-ops list + cooperative cancel — D-022 P5 Slice 1.

Tests:
  - cancel from pending/running/paused_gate → succeeds (HTTP 200, status=cancelled)
  - cancel refused (409) when terminal (completed/failed/cancelled)
  - executor cooperative cancel: status set to cancelled before wave N → loop stops,
    does not start wave N, does not orphan in-flight wave (already complete before check)
  - list endpoint: returns all runs newest-first
  - list with fleet_id filter (2-hop join via FleetDecision.target_id)
  - list with status filter
  - RBAC: non-owner cannot cancel another project's run (403)
  - RBAC: list scoped — non-admin only sees own project's runs
"""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from models.fleet import FleetMember
from models.fleet_targeting import (
    BULK_RUN_STATUS_CANCELLED,
    BULK_RUN_STATUS_COMPLETED,
    BULK_RUN_STATUS_FAILED,
    BULK_RUN_STATUS_PAUSED_GATE,
    BULK_RUN_STATUS_PENDING,
    BULK_RUN_STATUS_RUNNING,
    DECISION_STATUS_RESOLVED,
    RESULT_STATUS_SUCCEEDED,
    FleetBulkRun,
    FleetDecision,
    FleetTarget,
)
from tests.factories import KubernetesClusterFactory, ProjectFactory, UserFactory  # noqa: F401

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_target(db, project, name="t"):
    t = FleetTarget(project_id=project.id, name=name, selector={"labels": []}, created_by="test")
    db.add(t)
    db.flush()
    db.refresh(t)
    return t


def _make_decision(db, target, project, resolved_member_ids=None):
    d = FleetDecision(
        target_id=target.id,
        project_id=project.id,
        selector_snapshot={"labels": []},
        resolved_member_ids=resolved_member_ids or [],
        member_count=len(resolved_member_ids or []),
        resolved_at=datetime.now(UTC),
        resolved_by="test",
        status=DECISION_STATUS_RESOLVED,
    )
    db.add(d)
    db.flush()
    db.refresh(d)
    return d


def _make_run(db, decision, project, status=BULK_RUN_STATUS_PENDING, action="re-reconcile"):
    run = FleetBulkRun(
        decision_id=decision.id,
        project_id=project.id,
        action=action,
        wave_by="fault_domain",
        concurrency=5,
        gate_mode="manual",
        status=status,
        current_wave_index=0,
    )
    db.add(run)
    db.flush()
    db.refresh(run)
    return run


def _make_cluster_member(db, project, fault_domain=None):
    from services.fleet_reconcile_service import reconcile_fleet_member
    cluster = KubernetesClusterFactory(db, project=project, cloud_provider="aws", region="us-east-1")
    member = reconcile_fleet_member(db, "cluster", cluster.id)
    if fault_domain:
        member.fault_domain = fault_domain
    db.flush()
    db.refresh(member)
    return member


# ---------------------------------------------------------------------------
# Cancel endpoint — HTTP-level tests
# ---------------------------------------------------------------------------

class TestCancelBulkOp:

    def test_cancel_pending_run_returns_cancelled(self, client, admin_headers, db, make_project, sample_user):
        p = make_project()
        t = _make_target(db, p)
        d = _make_decision(db, t, p)
        run = _make_run(db, d, p, status=BULK_RUN_STATUS_PENDING)
        db.commit()

        resp = client.post(f"/api/fleet/bulk-ops/{run.id}/cancel", headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "cancelled"
        assert body["completed_at"] is not None

    def test_cancel_running_run_returns_cancelled(self, client, admin_headers, db, make_project, sample_user):
        p = make_project()
        t = _make_target(db, p)
        d = _make_decision(db, t, p)
        run = _make_run(db, d, p, status=BULK_RUN_STATUS_RUNNING)
        db.commit()

        resp = client.post(f"/api/fleet/bulk-ops/{run.id}/cancel", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_cancel_paused_gate_run_returns_cancelled(self, client, admin_headers, db, make_project, sample_user):
        p = make_project()
        t = _make_target(db, p)
        d = _make_decision(db, t, p)
        run = _make_run(db, d, p, status=BULK_RUN_STATUS_PAUSED_GATE)
        db.commit()

        resp = client.post(f"/api/fleet/bulk-ops/{run.id}/cancel", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_cancel_completed_run_returns_409(self, client, admin_headers, db, make_project, sample_user):
        p = make_project()
        t = _make_target(db, p)
        d = _make_decision(db, t, p)
        run = _make_run(db, d, p, status=BULK_RUN_STATUS_COMPLETED)
        run.completed_at = datetime.now(UTC)
        db.commit()

        resp = client.post(f"/api/fleet/bulk-ops/{run.id}/cancel", headers=admin_headers)
        assert resp.status_code == 409

    def test_cancel_failed_run_returns_409(self, client, admin_headers, db, make_project, sample_user):
        p = make_project()
        t = _make_target(db, p)
        d = _make_decision(db, t, p)
        run = _make_run(db, d, p, status=BULK_RUN_STATUS_FAILED)
        run.completed_at = datetime.now(UTC)
        db.commit()

        resp = client.post(f"/api/fleet/bulk-ops/{run.id}/cancel", headers=admin_headers)
        assert resp.status_code == 409

    def test_cancel_already_cancelled_run_returns_409(self, client, admin_headers, db, make_project, sample_user):
        p = make_project()
        t = _make_target(db, p)
        d = _make_decision(db, t, p)
        run = _make_run(db, d, p, status=BULK_RUN_STATUS_CANCELLED)
        run.completed_at = datetime.now(UTC)
        db.commit()

        resp = client.post(f"/api/fleet/bulk-ops/{run.id}/cancel", headers=admin_headers)
        assert resp.status_code == 409

    def test_cancel_nonexistent_run_returns_404(self, client, admin_headers, db, sample_user):
        resp = client.post("/api/fleet/bulk-ops/99999/cancel", headers=admin_headers)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# RBAC: non-owner cannot cancel
# ---------------------------------------------------------------------------

class TestCancelRBAC:

    def test_non_owner_cannot_cancel(self, client, db, sample_user):
        """A non-admin user who does not own the project gets 403."""
        from services.auth_service import create_access_token

        # Create user_a who owns the project + run.
        user_a = UserFactory(db, username="cancel-rbac-owner", role="operator")
        p = ProjectFactory(db, user_id=user_a.id)
        t = _make_target(db, p)
        d = _make_decision(db, t, p)
        run = _make_run(db, d, p, status=BULK_RUN_STATUS_PENDING)
        db.commit()

        # Create user_b (operator) who does NOT own project p.
        user_b = UserFactory(db, username="cancel-rbac-other", role="operator")
        other_headers = {
            "Authorization": f"Bearer {create_access_token(data={'sub': user_b.username, 'role': 'operator'})}"
        }

        resp = client.post(f"/api/fleet/bulk-ops/{run.id}/cancel", headers=other_headers)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Executor cooperative cancel — unit tests (no HTTP)
# ---------------------------------------------------------------------------

class TestExecutorCooperativeCancel:

    def _make_two_wave_run(self, db, project):
        """Create a pending run with two members in separate fault domains."""
        m1 = _make_cluster_member(db, project, fault_domain="dc-1")
        m2 = _make_cluster_member(db, project, fault_domain="dc-2")

        target = FleetTarget(
            project_id=project.id,
            name="cancel-exec-test",
            selector={"labels": []},
            created_by="test",
        )
        db.add(target)
        db.flush()

        decision = FleetDecision(
            target_id=target.id,
            project_id=project.id,
            selector_snapshot={"labels": []},
            resolved_member_ids=[m1.id, m2.id],
            member_count=2,
            resolved_at=datetime.now(UTC),
            resolved_by="test",
            status=DECISION_STATUS_RESOLVED,
        )
        db.add(decision)
        db.flush()

        run = FleetBulkRun(
            decision_id=decision.id,
            project_id=project.id,
            action="re-reconcile",
            wave_by="fault_domain",
            concurrency=5,
            gate_mode="manual",
            status=BULK_RUN_STATUS_PENDING,
            current_wave_index=0,
        )
        db.add(run)
        db.commit()
        return run, m1, m2

    def test_cancel_after_wave0_stops_before_wave1(self, db, make_project):
        """Simulates cancellation between wave 0 and wave 1.

        After wave 0 completes, the run status is set to CANCELLED externally
        (directly in the shared test DB session, simulating a concurrent cancel).
        The executor must detect the cancel at the wave 1 boundary and return
        'cancelled' without dispatching wave 1's members.

        Strategy: patch _wave_gate_passes to write CANCELLED to the session after
        wave 0's gate is evaluated. The cooperative cancel check (db.refresh +
        status == CANCELLED) at the top of wave 1's iteration will then fire.
        """
        import services.fleet_bulkop_service as svc
        from services.fleet_bulkop_service import _execute_bulk_run_with_db

        p = make_project()
        run, _m1, _m2 = self._make_two_wave_run(db, p)

        wave_dispatched_members: list[int] = []

        def mock_dispatch(member_id, action, action_params):
            wave_dispatched_members.append(member_id)
            return RESULT_STATUS_SUCCEEDED, "mock ok"

        # Patch the between-wave gate to write CANCELLED after wave 0 completes.
        # The executor calls _wave_gate_passes after each non-last wave; after it
        # returns (gate passes → True), it loops back to the top of the for loop
        # where the cooperative cancel check runs db.refresh(run).
        gate_call_count = [0]

        def mock_gate(db_, wave_member_ids, gate_mode):
            gate_call_count[0] += 1
            # Write CANCELLED into the session so the next iteration's refresh sees it.
            r = db_.query(FleetBulkRun).filter(FleetBulkRun.id == run.id).first()
            if r:
                r.status = BULK_RUN_STATUS_CANCELLED
                r.completed_at = datetime.now(UTC)
                db_.commit()
            return True  # gate passes — cancel fires at top of next wave

        with patch.object(svc, "_dispatch_action_for_member", side_effect=mock_dispatch), \
             patch.object(svc, "_wave_gate_passes", side_effect=mock_gate):
            result = _execute_bulk_run_with_db(run.id, db=db)

        assert result["status"] == "cancelled", f"expected cancelled, got: {result}"
        # Only wave-0 had 1 member (dc-1); wave-1 (dc-2) was never dispatched.
        assert len(wave_dispatched_members) == 1, (
            f"Expected only 1 wave-0 dispatch, got {wave_dispatched_members}"
        )
        # Gate was checked exactly once (between wave 0 and wave 1).
        assert gate_call_count[0] == 1

    def test_cancel_pending_run_no_waves_executed(self, db, make_project):
        """If run is cancelled before executor starts (status already cancelled),
        the idempotency guard rejects it as terminal (no waves execute).
        """
        from services.fleet_bulkop_service import _execute_bulk_run_with_db

        p = make_project()
        run, _m1, _m2 = self._make_two_wave_run(db, p)

        # Set cancelled before executor even runs.
        run.status = BULK_RUN_STATUS_CANCELLED
        run.completed_at = datetime.now(UTC)
        db.commit()

        with pytest.raises(ValueError, match="already in status 'cancelled'"):
            _execute_bulk_run_with_db(run.id, db=db)


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------

class TestListBulkOps:

    def test_list_returns_all_runs_newest_first(self, client, admin_headers, db, make_project, sample_user):
        p = make_project()
        t = _make_target(db, p)
        d = _make_decision(db, t, p)
        run1 = _make_run(db, d, p, status=BULK_RUN_STATUS_COMPLETED)
        run2 = _make_run(db, d, p, status=BULK_RUN_STATUS_PENDING)
        db.commit()

        resp = client.get("/api/fleet/bulk-ops", headers=admin_headers)
        assert resp.status_code == 200
        ids = [r["id"] for r in resp.json()]
        # Newest-first: run2 was inserted after run1
        assert ids.index(run2.id) < ids.index(run1.id)

    def test_list_filter_by_status(self, client, admin_headers, db, make_project, sample_user):
        p = make_project()
        t = _make_target(db, p)
        d = _make_decision(db, t, p)
        run_done = _make_run(db, d, p, status=BULK_RUN_STATUS_COMPLETED)
        _make_run(db, d, p, status=BULK_RUN_STATUS_PENDING)
        db.commit()

        resp = client.get("/api/fleet/bulk-ops?status=completed", headers=admin_headers)
        assert resp.status_code == 200
        bodies = resp.json()
        assert all(r["status"] == "completed" for r in bodies)
        assert any(r["id"] == run_done.id for r in bodies)

    def test_list_filter_by_fleet_id(self, client, admin_headers, db, make_project, sample_user):
        p = make_project()
        # Two different targets — each with its own decision + run.
        t1 = _make_target(db, p, name="target-1")
        t2 = _make_target(db, p, name="target-2")
        d1 = _make_decision(db, t1, p)
        d2 = _make_decision(db, t2, p)
        run1 = _make_run(db, d1, p)
        _run2 = _make_run(db, d2, p)
        db.commit()

        # Filter by fleet_id = t1.id should return only run1.
        resp = client.get(f"/api/fleet/bulk-ops?fleet_id={t1.id}", headers=admin_headers)
        assert resp.status_code == 200
        returned_ids = {r["id"] for r in resp.json()}
        assert run1.id in returned_ids
        assert _run2.id not in returned_ids

    def test_list_rbac_non_admin_sees_only_own_project(self, client, db, sample_user):
        """Non-admin operator only sees runs for their own project."""
        from services.auth_service import create_access_token

        # Create two distinct operator users.
        user_a = UserFactory(db, username="list-rbac-user-a", role="operator")
        user_b = UserFactory(db, username="list-rbac-user-b", role="operator")

        # Create a project owned by user_a.
        p_a = ProjectFactory(db, user_id=user_a.id)
        t_a = _make_target(db, p_a, name="target-rbac-a")
        d_a = _make_decision(db, t_a, p_a)
        run_a = _make_run(db, d_a, p_a)

        # Create a project owned by user_b.
        p_b = ProjectFactory(db, user_id=user_b.id)
        t_b = _make_target(db, p_b, name="target-rbac-b")
        d_b = _make_decision(db, t_b, p_b)
        run_b = _make_run(db, d_b, p_b)
        db.commit()

        # user_a can see run_a but NOT run_b.
        headers_a = {
            "Authorization": f"Bearer {create_access_token(data={'sub': user_a.username, 'role': 'operator'})}"
        }
        resp = client.get("/api/fleet/bulk-ops", headers=headers_a)
        assert resp.status_code == 200
        ids_a = {r["id"] for r in resp.json()}
        assert run_a.id in ids_a
        assert run_b.id not in ids_a
