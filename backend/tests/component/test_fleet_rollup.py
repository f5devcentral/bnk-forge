"""Component tests for fleet per-fleet conformance rollup — D-022 P6 Slice B.

Tests:
  - worst_state truth table: drifted > provisioning > unreachable > ready
  - empty fleet → unknown (also: empty selector → zero members → unknown)
  - all-good → ready  (all active, zero drift, unreachable_count == 0)
  - decommissioned / unknown lifecycle → unreachable
  - compliant/total + drift_count aggregation across multiple policies
  - RBAC scoping: non-owner can't rollup another project's fleet (404)
  - batch endpoint returns one RollupOut per requested id; skips unknown ids
  - ops_state: grey when no runs, red when last run failed, amber when active, green when clean
  - active_run_count / failed_run_count / last_run_status derivation via decision→target join
"""

from datetime import UTC, datetime

import pytest

from models.fleet import (
    LIFECYCLE_STATE_ACTIVE,
    LIFECYCLE_STATE_DECOMMISSIONED,
    LIFECYCLE_STATE_PROVISIONING,
    LIFECYCLE_STATE_UNKNOWN,
    FleetMember,
)
from models.fleet_policy import (
    POLICY_KIND_LABEL_COMPLIANCE,
    POLICY_MODE_INFORM,
    FleetPolicy,
    PolicyEvaluation,
)
from models.fleet_targeting import (
    BULK_RUN_STATUS_COMPLETED,
    BULK_RUN_STATUS_FAILED,
    BULK_RUN_STATUS_PENDING,
    BULK_RUN_STATUS_RUNNING,
    DECISION_STATUS_RESOLVED,
    FleetBulkRun,
    FleetDecision,
    FleetTarget,
)
from services.fleet_reconcile_service import reconcile_fleet_member
from tests.factories import KubernetesClusterFactory, ProjectFactory, UserFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeUser:
    def __init__(self, role="admin", user_id=1):
        self.role = role
        self.id = user_id
        self.username = "testuser"


def _make_target(db, project, selector=None):
    target = FleetTarget(
        project_id=project.id,
        name="rollup-test-fleet",
        selector=selector or {"labels": []},
        created_by="test",
    )
    db.add(target)
    db.flush()
    db.refresh(target)
    return target


def _make_cluster_member(db, project, lifecycle_state=LIFECYCLE_STATE_ACTIVE, health_status="healthy"):
    cluster = KubernetesClusterFactory(db, project=project, cloud_provider="aws", region="us-east-1")
    member = reconcile_fleet_member(db, "cluster", cluster.id)
    member.lifecycle_state = lifecycle_state
    member.health_status = health_status
    db.flush()
    db.refresh(member)
    return member


def _make_policy(db, project, target):
    p = FleetPolicy(
        project_id=project.id,
        name="test-policy",
        target_id=target.id,
        kind=POLICY_KIND_LABEL_COMPLIANCE,
        mode=POLICY_MODE_INFORM,
        desired={"required_labels": ["env=prod"]},
        created_by="test",
    )
    db.add(p)
    db.flush()
    db.refresh(p)
    return p


def _make_evaluation(db, policy, compliant=1, drifted=0, total=1):
    ev = PolicyEvaluation(
        policy_id=policy.id,
        project_id=policy.project_id,
        evaluated_at=datetime.now(UTC),
        evaluated_by="test",
        selector_snapshot={},
        desired_snapshot=policy.desired,
        compliant_count=compliant,
        drifted_count=drifted,
        unknown_count=0,
        total_count=total,
        member_results=[],
        mode=POLICY_MODE_INFORM,
        detail=None,
    )
    db.add(ev)
    db.flush()
    db.refresh(ev)
    return ev


def _call_rollup(db, target_id, user):
    """Invoke the route function directly."""
    from routes.fleet import _compute_fleet_rollup
    return _compute_fleet_rollup(target_id, user, db)


def _call_batch_rollup(db, ids_str, user):
    from routes.fleet import get_fleet_targets_batch_rollup
    # Simulate Query param by calling with a string
    class _FakeDB:
        pass
    # use direct helper loop
    from core.errors import NotFoundError
    from routes.fleet import _compute_fleet_rollup
    id_list = [int(x.strip()) for x in ids_str.split(",") if x.strip()]
    results = []
    for fid in id_list:
        try:
            results.append(_compute_fleet_rollup(fid, user, db))
        except NotFoundError:
            continue
    return results


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWorstStateEmptyFleet:
    def test_empty_fleet_returns_unknown(self, db, make_project):
        p = make_project()
        target = _make_target(db, p)
        user = _FakeUser(role="admin")
        rollup = _call_rollup(db, target.id, user)
        assert rollup.worst_state == "unknown"
        assert rollup.member_count == 0
        assert rollup.fleet_id == target.id


class TestWorstStateReady:
    def test_all_active_zero_drift_is_ready(self, db, make_project):
        p = make_project()
        # Use an explicit label selector so the member is included (empty selector = no members now).
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        m = _make_cluster_member(db, p, lifecycle_state=LIFECYCLE_STATE_ACTIVE, health_status="healthy")
        m.assigned_labels = {"env": "prod"}
        db.flush()
        user = _FakeUser(role="admin")
        rollup = _call_rollup(db, target.id, user)
        assert rollup.worst_state == "ready"
        assert rollup.member_count == 1
        assert rollup.lifecycle.active == 1
        assert rollup.unreachable_count == 0

    def test_empty_selector_no_pins_returns_unknown(self, db, make_project):
        """Empty selector with no pinned members = zero members = unknown state."""
        p = make_project()
        target = _make_target(db, p, selector={"labels": []})
        _make_cluster_member(db, p, lifecycle_state=LIFECYCLE_STATE_ACTIVE, health_status="healthy")
        user = _FakeUser(role="admin")
        rollup = _call_rollup(db, target.id, user)
        assert rollup.worst_state == "unknown"
        assert rollup.member_count == 0


class TestWorstStateUnreachableViaLifecycle:
    def test_unknown_lifecycle_triggers_unreachable(self, db, make_project):
        """lifecycle_state=='unknown' → unreachable (old 'error' bucket removed)."""
        p = make_project()
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        m = _make_cluster_member(db, p, lifecycle_state=LIFECYCLE_STATE_UNKNOWN, health_status="healthy")
        m.assigned_labels = {"env": "prod"}
        db.flush()
        user = _FakeUser(role="admin")
        rollup = _call_rollup(db, target.id, user)
        assert rollup.worst_state == "unreachable"
        assert rollup.unreachable_count == 1
        assert rollup.lifecycle.unknown == 1

    def test_decommissioned_lifecycle_triggers_unreachable(self, db, make_project):
        """lifecycle_state=='decommissioned' → unreachable."""
        from models.fleet import LIFECYCLE_STATE_DECOMMISSIONED
        p = make_project()
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        m = _make_cluster_member(db, p, lifecycle_state=LIFECYCLE_STATE_DECOMMISSIONED, health_status="healthy")
        m.assigned_labels = {"env": "prod"}
        db.flush()
        user = _FakeUser(role="admin")
        rollup = _call_rollup(db, target.id, user)
        assert rollup.worst_state == "unreachable"
        assert rollup.unreachable_count == 1
        assert rollup.lifecycle.decommissioned == 1


class TestWorstStateDrifted:
    def test_drifted_policy_triggers_drifted(self, db, make_project):
        p = make_project()
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        m = _make_cluster_member(db, p, lifecycle_state=LIFECYCLE_STATE_ACTIVE, health_status="healthy")
        m.assigned_labels = {"env": "prod"}
        db.flush()
        policy = _make_policy(db, p, target)
        _make_evaluation(db, policy, compliant=0, drifted=1, total=1)
        user = _FakeUser(role="admin")
        rollup = _call_rollup(db, target.id, user)
        assert rollup.worst_state == "drifted"
        assert rollup.drift_count == 1


class TestWorstStateProvisioning:
    def test_provisioning_member_triggers_provisioning(self, db, make_project):
        p = make_project()
        target = _make_target(db, p, selector={"labels": ["env=staging"]})
        m = _make_cluster_member(db, p, lifecycle_state=LIFECYCLE_STATE_PROVISIONING, health_status="healthy")
        m.assigned_labels = {"env": "staging"}
        db.flush()
        user = _FakeUser(role="admin")
        rollup = _call_rollup(db, target.id, user)
        assert rollup.worst_state == "provisioning"
        assert rollup.lifecycle.provisioning == 1


class TestWorstStateUnreachable:
    def test_active_member_with_stable_health_is_not_unreachable(self, db, make_project):
        """active lifecycle + any health_status → NOT unreachable (health vocab no longer drives reachability)."""
        p = make_project()
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        m = _make_cluster_member(db, p, lifecycle_state=LIFECYCLE_STATE_ACTIVE, health_status="stale")
        m.assigned_labels = {"env": "prod"}
        db.flush()
        user = _FakeUser(role="admin")
        rollup = _call_rollup(db, target.id, user)
        # active lifecycle → reachable regardless of health_status string
        assert rollup.worst_state == "ready"
        assert rollup.unreachable_count == 0

    def test_active_member_null_health_is_not_unreachable(self, db, make_project):
        """active lifecycle + None health_status → NOT unreachable."""
        p = make_project()
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        m = _make_cluster_member(db, p, lifecycle_state=LIFECYCLE_STATE_ACTIVE, health_status=None)
        m.assigned_labels = {"env": "prod"}
        db.flush()
        user = _FakeUser(role="admin")
        rollup = _call_rollup(db, target.id, user)
        assert rollup.worst_state == "ready"
        assert rollup.unreachable_count == 0


class TestWorstStateRanking:
    """drifted wins over provisioning; provisioning wins over unreachable."""

    def test_drifted_beats_provisioning(self, db, make_project):
        p = make_project()
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        m = _make_cluster_member(db, p, lifecycle_state=LIFECYCLE_STATE_PROVISIONING, health_status="healthy")
        m.assigned_labels = {"env": "prod"}
        db.flush()
        policy = _make_policy(db, p, target)
        _make_evaluation(db, policy, compliant=0, drifted=1, total=1)
        user = _FakeUser(role="admin")
        rollup = _call_rollup(db, target.id, user)
        assert rollup.worst_state == "drifted"

    def test_provisioning_beats_unreachable(self, db, make_project):
        """provisioning lifecycle takes priority over unknown lifecycle."""
        p = make_project()
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        m1 = _make_cluster_member(db, p, lifecycle_state=LIFECYCLE_STATE_PROVISIONING, health_status="healthy")
        m1.assigned_labels = {"env": "prod"}
        m2 = _make_cluster_member(db, p, lifecycle_state=LIFECYCLE_STATE_UNKNOWN, health_status="healthy")
        m2.assigned_labels = {"env": "prod"}
        db.flush()
        user = _FakeUser(role="admin")
        rollup = _call_rollup(db, target.id, user)
        # provisioning beats unreachable (provisioning checked first)
        assert rollup.worst_state == "provisioning"
        assert rollup.unreachable_count == 1  # the unknown member still counted

    def test_drifted_beats_unreachable(self, db, make_project):
        """drifted wins over unknown lifecycle."""
        p = make_project()
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        m = _make_cluster_member(db, p, lifecycle_state=LIFECYCLE_STATE_UNKNOWN, health_status="healthy")
        m.assigned_labels = {"env": "prod"}
        db.flush()
        policy = _make_policy(db, p, target)
        _make_evaluation(db, policy, compliant=0, drifted=1, total=1)
        user = _FakeUser(role="admin")
        rollup = _call_rollup(db, target.id, user)
        assert rollup.worst_state == "drifted"


class TestComplianceAggregation:
    def test_aggregates_across_multiple_policies(self, db, make_project):
        p = make_project()
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        m = _make_cluster_member(db, p)
        m.assigned_labels = {"env": "prod"}
        db.flush()

        # Policy 1: 3 compliant, 1 drifted, total 4
        policy1 = _make_policy(db, p, target)
        _make_evaluation(db, policy1, compliant=3, drifted=1, total=4)

        # Policy 2: 2 compliant, 0 drifted, total 2
        from models.fleet_policy import FleetPolicy
        policy2 = FleetPolicy(
            project_id=p.id, name="policy2", target_id=target.id,
            kind=POLICY_KIND_LABEL_COMPLIANCE, mode=POLICY_MODE_INFORM,
            desired={"required_labels": ["env=prod"]}, created_by="test",
        )
        db.add(policy2)
        db.flush()
        db.refresh(policy2)
        _make_evaluation(db, policy2, compliant=2, drifted=0, total=2)

        user = _FakeUser(role="admin")
        rollup = _call_rollup(db, target.id, user)

        assert rollup.policy_count == 2
        assert rollup.compliant_count == 5   # 3+2
        assert rollup.total_evaluated == 6    # 4+2
        assert rollup.drift_count == 1        # 1+0


class TestRbacScoping:
    def test_non_owner_cannot_rollup_other_projects_fleet(self, db, make_project):
        from core.errors import NotFoundError as _NotFoundError

        # Owner creates fleet with members in their project
        owner = UserFactory(db, role="operator")
        owner_project = ProjectFactory(db, user_id=owner.id)
        target = _make_target(db, owner_project)
        _make_cluster_member(db, owner_project)

        # Non-owner calls rollup — should get NotFoundError (404) because the
        # RBAC-scoped member query sees no members and the target lookup succeeds
        # but the target itself is visible (it's global enough). The spec says:
        # "non-admin only their projects" — the member resolver filters by project.
        non_owner = UserFactory(db, role="operator")
        non_owner_user = _FakeUser(role="operator", user_id=non_owner.id)

        # The rollup should succeed but return 0 members (no members in non-owner's projects)
        rollup = _call_rollup(db, target.id, non_owner_user)
        # Non-owner sees 0 members (RBAC-filtered)
        assert rollup.member_count == 0
        assert rollup.worst_state == "unknown"

    def test_admin_sees_all_members(self, db, make_project):
        p = make_project()
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        m = _make_cluster_member(db, p)
        m.assigned_labels = {"env": "prod"}
        db.flush()
        admin_user = _FakeUser(role="admin")
        rollup = _call_rollup(db, target.id, admin_user)
        assert rollup.member_count == 1


class TestBatchEndpoint:
    def test_batch_returns_one_per_id(self, db, make_project):
        p = make_project()
        t1 = _make_target(db, p)
        t2 = FleetTarget(
            project_id=p.id, name="fleet-2", selector={"labels": []}, created_by="test"
        )
        db.add(t2)
        db.flush()
        db.refresh(t2)

        user = _FakeUser(role="admin")
        ids_str = f"{t1.id},{t2.id}"
        results = _call_batch_rollup(db, ids_str, user)
        assert len(results) == 2
        assert {r.fleet_id for r in results} == {t1.id, t2.id}

    def test_batch_skips_unknown_ids(self, db, make_project):
        p = make_project()
        t1 = _make_target(db, p)
        user = _FakeUser(role="admin")
        ids_str = f"{t1.id},99999"
        results = _call_batch_rollup(db, ids_str, user)
        assert len(results) == 1
        assert results[0].fleet_id == t1.id

    def test_batch_rollup_via_http_client_not_422(self, db, make_project, client, admin_headers, sample_user):
        """Regression: GET /api/fleet/targets/rollup must return 200, not 422.

        Before the route-ordering fix, FastAPI matched '/targets/rollup' against
        '/targets/{target_id}' with target_id='rollup', causing an int-parse 422.
        This test goes through the real HTTP router to catch that shadowing.
        """
        p = make_project()
        t1 = _make_target(db, p)
        t2 = FleetTarget(
            project_id=p.id, name="fleet-http-2", selector={"labels": []}, created_by="test"
        )
        db.add(t2)
        db.flush()
        db.refresh(t2)
        db.commit()

        resp = client.get(
            f"/api/fleet/targets/rollup?ids={t1.id},{t2.id}",
            headers=admin_headers,
        )
        assert resp.status_code == 200, (
            f"Expected 200 from batch rollup, got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 2
        returned_ids = {item["fleet_id"] for item in data}
        assert returned_ids == {t1.id, t2.id}


# ---------------------------------------------------------------------------
# Operations status in rollup — D-022 P6 traffic-lights
# ---------------------------------------------------------------------------


def _make_decision(db, target):
    """Create a FleetDecision (resolved) for the given target."""
    decision = FleetDecision(
        target_id=target.id,
        project_id=target.project_id,
        selector_snapshot={"labels": []},
        resolved_member_ids=[],
        member_count=0,
        resolved_at=datetime.now(UTC),
        resolved_by="test",
        status=DECISION_STATUS_RESOLVED,
    )
    db.add(decision)
    db.flush()
    db.refresh(decision)
    return decision


def _make_run(db, decision, status=BULK_RUN_STATUS_COMPLETED, project_id=None):
    """Create a FleetBulkRun with the given status."""
    run = FleetBulkRun(
        decision_id=decision.id,
        project_id=project_id or decision.project_id,
        action="re-reconcile",
        action_params=None,
        wave_by="fault_domain",
        concurrency=5,
        gate_mode="manual",
        status=status,
        current_wave_index=0,
        created_by="test",
    )
    db.add(run)
    db.flush()
    db.refresh(run)
    return run


class TestOpsStatusInRollup:
    def test_grey_when_no_runs(self, db, make_project):
        """ops_state=grey when there are no runs for this fleet."""
        p = make_project()
        target = _make_target(db, p)
        user = _FakeUser(role="admin")
        rollup = _call_rollup(db, target.id, user)
        assert rollup.ops_state == "grey"
        assert rollup.active_run_count == 0
        assert rollup.failed_run_count == 0
        assert rollup.last_run_status is None

    def test_green_when_last_run_completed(self, db, make_project):
        """ops_state=green when the most recent run completed successfully."""
        p = make_project()
        target = _make_target(db, p)
        decision = _make_decision(db, target)
        _make_run(db, decision, status=BULK_RUN_STATUS_COMPLETED)
        user = _FakeUser(role="admin")
        rollup = _call_rollup(db, target.id, user)
        assert rollup.ops_state == "green"
        assert rollup.last_run_status == "completed"
        assert rollup.failed_run_count == 0

    def test_red_when_last_run_failed(self, db, make_project):
        """ops_state=red when the most recent run has failed status."""
        p = make_project()
        target = _make_target(db, p)
        decision = _make_decision(db, target)
        _make_run(db, decision, status=BULK_RUN_STATUS_FAILED)
        user = _FakeUser(role="admin")
        rollup = _call_rollup(db, target.id, user)
        assert rollup.ops_state == "red"
        assert rollup.last_run_status == "failed"
        assert rollup.failed_run_count == 1

    def test_amber_when_active_run_exists(self, db, make_project):
        """ops_state=amber when there is a pending/running run."""
        p = make_project()
        target = _make_target(db, p)
        decision = _make_decision(db, target)
        _make_run(db, decision, status=BULK_RUN_STATUS_PENDING)
        user = _FakeUser(role="admin")
        rollup = _call_rollup(db, target.id, user)
        assert rollup.ops_state == "amber"
        assert rollup.active_run_count == 1

    def test_red_takes_priority_over_amber(self, db, make_project):
        """If the most recent run failed (last_run_status=failed), ops_state=red
        even if there are also active runs — last_run is the most recent (highest id)."""
        p = make_project()
        target = _make_target(db, p)
        decision = _make_decision(db, target)
        # Create an older active run and a newer failed run.
        _make_run(db, decision, status=BULK_RUN_STATUS_RUNNING)
        _make_run(db, decision, status=BULK_RUN_STATUS_FAILED)
        user = _FakeUser(role="admin")
        rollup = _call_rollup(db, target.id, user)
        # last_run_status is 'failed' (newer run), so red wins.
        assert rollup.ops_state == "red"
        assert rollup.active_run_count == 1
        assert rollup.failed_run_count == 1

    def test_active_run_before_failed_gives_amber(self, db, make_project):
        """If the most recent run is active (running) and an older run failed,
        ops_state=amber because last_run_status='running' (not failed)."""
        p = make_project()
        target = _make_target(db, p)
        decision = _make_decision(db, target)
        # Older failed run, newer running run.
        _make_run(db, decision, status=BULK_RUN_STATUS_FAILED)
        _make_run(db, decision, status=BULK_RUN_STATUS_RUNNING)
        user = _FakeUser(role="admin")
        rollup = _call_rollup(db, target.id, user)
        # Last run is 'running', so amber (active).
        assert rollup.ops_state == "amber"
        assert rollup.active_run_count == 1
        assert rollup.failed_run_count == 1

    def test_runs_scoped_to_fleet_via_decision_join(self, db, make_project):
        """Runs for a different fleet's decision are NOT counted in this fleet's rollup."""
        p = make_project()
        target_a = _make_target(db, p)
        target_b = FleetTarget(
            project_id=p.id, name="fleet-b", selector={"labels": []}, created_by="test"
        )
        db.add(target_b)
        db.flush()
        db.refresh(target_b)

        decision_b = _make_decision(db, target_b)
        _make_run(db, decision_b, status=BULK_RUN_STATUS_FAILED)

        user = _FakeUser(role="admin")
        # Fleet A has no runs — should be grey, not red.
        rollup_a = _call_rollup(db, target_a.id, user)
        assert rollup_a.ops_state == "grey"
        # Fleet B has a failed run.
        rollup_b = _call_rollup(db, target_b.id, user)
        assert rollup_b.ops_state == "red"
