"""Component tests for fleet policy + compliance — D-022 Phase 3.

Tests (with DB):

Key safety invariants tested:
  - inform-no-mutation: evaluate_policy writes ZERO FleetMember / FleetBulkRun rows.
  - enforce-through-gated-executor (no bypass): enforce_policy's only mutation path
    is via FleetBulkRun + run_fleet_bulk_op.delay. Creates a resolved Decision over
    exactly the drifted ids. action must be in SAFE_ACTIONS.
    _dispatch_action_for_member is the sole assigned_labels writer (tested via the
    executor path — we verify no direct FleetMember.assigned_labels writes in P3 code).
  - os-seed enforce rejected: enforce on os-compliance-seed → 400.
  - drift detection per kind: label-compliance, os-compliance-seed.
  - D-017 unknown: never-reconciled / missing OS facts → unknown (never compliant).
  - rollup counts correct.
  - RBAC: non-admin sees only own-project members.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from models.bare_metal import BareMetalHost
from models.fleet import FleetMember
from models.fleet_policy import (
    COMPLIANCE_STATUS_COMPLIANT,
    COMPLIANCE_STATUS_DRIFTED,
    COMPLIANCE_STATUS_UNKNOWN,
    KIND_SUPPORTS_ENFORCE,
    POLICY_KIND_LABEL_COMPLIANCE,
    POLICY_KIND_OS_COMPLIANCE_SEED,
    POLICY_MODE_ENFORCE,
    POLICY_MODE_INFORM,
    FleetPolicy,
    PolicyEvaluation,
)
from models.fleet_targeting import (
    BULK_RUN_STATUS_PENDING,
    DECISION_STATUS_RESOLVED,
    SAFE_ACTIONS,
    FleetBulkRun,
    FleetDecision,
    FleetTarget,
)
from services.fleet_reconcile_service import reconcile_fleet_member
from tests.factories import KubernetesClusterFactory, ProjectFactory, UserFactory  # noqa: F401

# ---------------------------------------------------------------------------
# Test helpers / fixtures
# ---------------------------------------------------------------------------

class _FakeUser:
    def __init__(self, role="admin", user_id=1, username="testuser"):
        self.role = role
        self.id = user_id
        self.username = username


def _make_cluster_member(db, project, *, assigned_labels=None, discovered_labels=None,
                          reconciled=True):
    cluster = KubernetesClusterFactory(
        db, project=project, cloud_provider="aws", region="us-east-1"
    )
    member = reconcile_fleet_member(db, "cluster", cluster.id)
    if member is None:
        raise RuntimeError("reconcile_fleet_member returned None")
    if assigned_labels is not None:
        member.assigned_labels = assigned_labels
    if discovered_labels is not None:
        member.discovered_labels = discovered_labels
    if not reconciled:
        member.last_reconciled_at = None
    db.flush()
    db.refresh(member)
    return member


def _make_host_member(db, project, *, os_info=None, reconciled=True):
    defaults = dict(
        project_id=project.id,
        name="test-host",
        host_ip="10.0.0.99",
        os_info=os_info or {"os_type": "ubuntu", "os_version": "22.04", "arch": "x86_64"},
    )
    host = BareMetalHost(**defaults)
    db.add(host)
    db.flush()
    member = reconcile_fleet_member(db, "bare-metal-host", host.id)
    if member is None:
        raise RuntimeError("reconcile returned None for host")
    if not reconciled:
        member.last_reconciled_at = None
    db.flush()
    db.refresh(member)
    return member


def _make_target(db, project, selector=None):
    selector = selector or {"labels": []}
    target = FleetTarget(
        project_id=project.id,
        name="test-target",
        selector=selector,
        created_by="test",
    )
    db.add(target)
    db.flush()
    db.refresh(target)
    return target


def _make_policy(db, project, target, kind, mode, desired):
    policy = FleetPolicy(
        project_id=project.id,
        name=f"policy-{kind}-{mode}",
        target_id=target.id,
        kind=kind,
        mode=mode,
        desired=desired,
        created_by="test",
    )
    db.add(policy)
    db.flush()
    db.refresh(policy)
    return policy


# ---------------------------------------------------------------------------
# decision_from_member_ids
# ---------------------------------------------------------------------------

class TestDecisionFromMemberIds:
    def test_creates_decision_over_subset(self, db, make_project):
        from services.fleet_selector import decision_from_member_ids

        p = make_project()
        m1 = _make_cluster_member(db, p)
        m2 = _make_cluster_member(db, p)
        target = _make_target(db, p)

        decision = decision_from_member_ids(db, target, [m1.id], resolved_by="test")
        db.flush()

        assert decision.status == DECISION_STATUS_RESOLVED
        assert decision.resolved_member_ids == [m1.id]
        assert m2.id not in decision.resolved_member_ids
        assert decision.member_count == 1
        assert decision.target_id == target.id

    def test_supersedes_prior_resolved_decision(self, db, make_project):
        from models.fleet_targeting import DECISION_STATUS_SUPERSEDED
        from services.fleet_selector import decision_from_member_ids

        p = make_project()
        m = _make_cluster_member(db, p)
        target = _make_target(db, p)

        d1 = decision_from_member_ids(db, target, [m.id], resolved_by="u")
        db.flush()
        assert d1.status == DECISION_STATUS_RESOLVED

        d2 = decision_from_member_ids(db, target, [m.id], resolved_by="u")
        db.flush()
        db.refresh(d1)

        assert d1.status == DECISION_STATUS_SUPERSEDED
        assert d2.status == DECISION_STATUS_RESOLVED

    def test_empty_member_ids_creates_empty_decision(self, db, make_project):
        from services.fleet_selector import decision_from_member_ids

        p = make_project()
        target = _make_target(db, p)
        decision = decision_from_member_ids(db, target, [], resolved_by="u")
        db.flush()

        assert decision.member_count == 0
        assert decision.resolved_member_ids == []


# ---------------------------------------------------------------------------
# Evaluator — label-compliance
# ---------------------------------------------------------------------------

class TestEvaluateLabelCompliance:
    def test_compliant_member(self, db, make_project):
        from services.fleet_policy_service import evaluate_policy

        p = make_project()
        _make_cluster_member(db, p, assigned_labels={"env": "prod"})
        target = _make_target(db, p, selector={"labels": []})
        policy = _make_policy(
            db, p, target,
            POLICY_KIND_LABEL_COMPLIANCE, POLICY_MODE_INFORM,
            {"required_labels": ["env=prod"]},
        )
        user = _FakeUser()

        result, evaluation = evaluate_policy(db, policy, current_user=user)
        db.flush()

        assert len(result.compliant) == 1
        assert len(result.drifted) == 0
        assert evaluation.compliant_count == 1

    def test_drifted_member_missing_label(self, db, make_project):
        from services.fleet_policy_service import evaluate_policy

        p = make_project()
        _make_cluster_member(db, p, assigned_labels={})  # no "env" label
        target = _make_target(db, p, selector={"labels": []})
        policy = _make_policy(
            db, p, target,
            POLICY_KIND_LABEL_COMPLIANCE, POLICY_MODE_INFORM,
            {"required_labels": ["env=prod"]},
        )
        user = _FakeUser()

        result, evaluation = evaluate_policy(db, policy, current_user=user)
        db.flush()

        assert len(result.drifted) == 1
        assert len(result.compliant) == 0
        assert result.drifted[0].detail is not None
        assert "env=prod" in result.drifted[0].detail
        # desired_labels must contain the required merge.
        assert result.drifted[0].desired_labels is not None
        assert result.drifted[0].desired_labels.get("env") == "prod"

    def test_unknown_never_reconciled_member(self, db, make_project):
        """D-017: never-reconciled member is unknown, never compliant."""
        from services.fleet_policy_service import evaluate_policy

        p = make_project()
        m = _make_cluster_member(db, p, assigned_labels={"env": "prod"}, reconciled=False)
        assert m.last_reconciled_at is None

        target = _make_target(db, p, selector={"labels": []})
        policy = _make_policy(
            db, p, target,
            POLICY_KIND_LABEL_COMPLIANCE, POLICY_MODE_INFORM,
            {"required_labels": ["env=prod"]},
        )
        user = _FakeUser()

        result, evaluation = evaluate_policy(db, policy, current_user=user)
        db.flush()

        assert len(result.unknown) == 1
        assert len(result.compliant) == 0
        assert len(result.drifted) == 0
        assert evaluation.unknown_count == 1

    def test_rollup_counts_correct(self, db, make_project):
        from services.fleet_policy_service import evaluate_policy

        p = make_project()
        _make_cluster_member(db, p, assigned_labels={"env": "prod"})  # compliant
        _make_cluster_member(db, p, assigned_labels={"env": "staging"})  # drifted
        _make_cluster_member(db, p, assigned_labels={"env": "prod"}, reconciled=False)  # unknown

        target = _make_target(db, p, selector={"labels": []})
        policy = _make_policy(
            db, p, target,
            POLICY_KIND_LABEL_COMPLIANCE, POLICY_MODE_INFORM,
            {"required_labels": ["env=prod"]},
        )
        user = _FakeUser()

        result, evaluation = evaluate_policy(db, policy, current_user=user)
        db.flush()

        assert evaluation.compliant_count == 1
        assert evaluation.drifted_count == 1
        assert evaluation.unknown_count == 1
        assert evaluation.total_count == 3

    def test_inform_no_mutation_zero_fleet_member_writes(self, db, make_project):
        """GATE: inform-no-mutation — evaluate_policy writes ZERO FleetMember rows."""
        from services.fleet_policy_service import evaluate_policy

        p = make_project()
        m = _make_cluster_member(db, p, assigned_labels={})
        original_labels = dict(m.assigned_labels or {})

        target = _make_target(db, p, selector={"labels": []})
        policy = _make_policy(
            db, p, target,
            POLICY_KIND_LABEL_COMPLIANCE, POLICY_MODE_INFORM,
            {"required_labels": ["env=prod"]},
        )
        user = _FakeUser()

        # Before: no bulk runs
        runs_before = db.query(FleetBulkRun).count()
        decisions_before = db.query(FleetDecision).count()

        evaluate_policy(db, policy, current_user=user)
        db.flush()

        # After: ZERO new bulk runs and ZERO new decisions (inform path).
        runs_after = db.query(FleetBulkRun).count()
        decisions_after = db.query(FleetDecision).count()

        assert runs_after == runs_before, "evaluate_policy must not create FleetBulkRun rows"
        assert decisions_after == decisions_before, "evaluate_policy must not create FleetDecision rows"

        # FleetMember labels must be unchanged.
        db.refresh(m)
        assert m.assigned_labels == original_labels


# ---------------------------------------------------------------------------
# Evaluator — os-compliance-seed
# ---------------------------------------------------------------------------

class TestEvaluateOsComplianceSeed:
    def test_compliant_os_match(self, db, make_project):
        from services.fleet_policy_service import evaluate_policy

        p = make_project()
        m = _make_cluster_member(db, p, discovered_labels={"os": "ubuntu", "os-version": "22.04"})
        # Ensure reconciled.
        m.last_reconciled_at = datetime.now(UTC)
        db.flush()

        target = _make_target(db, p, selector={"labels": []})
        policy = _make_policy(
            db, p, target,
            POLICY_KIND_OS_COMPLIANCE_SEED, POLICY_MODE_INFORM,
            {"os": "ubuntu", "min_version": "22.04"},
        )
        user = _FakeUser()

        result, _ = evaluate_policy(db, policy, current_user=user)
        db.flush()

        assert len(result.compliant) == 1

    def test_drifted_os_version_below_floor(self, db, make_project):
        from services.fleet_policy_service import evaluate_policy

        p = make_project()
        m = _make_cluster_member(db, p, discovered_labels={"os": "ubuntu", "os-version": "20.04"})
        m.last_reconciled_at = datetime.now(UTC)
        db.flush()

        target = _make_target(db, p, selector={"labels": []})
        policy = _make_policy(
            db, p, target,
            POLICY_KIND_OS_COMPLIANCE_SEED, POLICY_MODE_INFORM,
            {"os": "ubuntu", "min_version": "22.04"},
        )
        user = _FakeUser()

        result, _ = evaluate_policy(db, policy, current_user=user)
        db.flush()

        assert len(result.drifted) == 1
        assert "below minimum" in result.drifted[0].detail

    def test_drifted_os_mismatch(self, db, make_project):
        from services.fleet_policy_service import evaluate_policy

        p = make_project()
        m = _make_cluster_member(db, p, discovered_labels={"os": "centos", "os-version": "8"})
        m.last_reconciled_at = datetime.now(UTC)
        db.flush()

        target = _make_target(db, p, selector={"labels": []})
        policy = _make_policy(
            db, p, target,
            POLICY_KIND_OS_COMPLIANCE_SEED, POLICY_MODE_INFORM,
            {"os": "ubuntu", "min_version": "22.04"},
        )
        user = _FakeUser()

        result, _ = evaluate_policy(db, policy, current_user=user)
        db.flush()

        assert len(result.drifted) == 1
        assert "OS mismatch" in result.drifted[0].detail

    def test_unknown_missing_os_facts(self, db, make_project):
        """D-017: absent OS facts in discovered_labels → unknown."""
        from services.fleet_policy_service import evaluate_policy

        p = make_project()
        m = _make_cluster_member(db, p, discovered_labels={})  # no os/os-version
        m.last_reconciled_at = datetime.now(UTC)
        db.flush()

        target = _make_target(db, p, selector={"labels": []})
        policy = _make_policy(
            db, p, target,
            POLICY_KIND_OS_COMPLIANCE_SEED, POLICY_MODE_INFORM,
            {"os": "ubuntu", "min_version": "22.04"},
        )
        user = _FakeUser()

        result, _ = evaluate_policy(db, policy, current_user=user)
        db.flush()

        assert len(result.unknown) == 1
        assert len(result.compliant) == 0

    def test_os_seed_enforce_rejected_400(self, db, make_project):
        """GATE: os-seed enforce rejected — KIND_SUPPORTS_ENFORCE[os-compliance-seed]=False."""
        from core.errors import BadRequestError
        from services.fleet_policy_service import enforce_policy

        assert KIND_SUPPORTS_ENFORCE[POLICY_KIND_OS_COMPLIANCE_SEED] is False

        p = make_project()
        _make_cluster_member(db, p, discovered_labels={"os": "centos", "os-version": "7"})
        target = _make_target(db, p, selector={"labels": []})
        policy = _make_policy(
            db, p, target,
            POLICY_KIND_OS_COMPLIANCE_SEED, POLICY_MODE_ENFORCE,
            {"os": "ubuntu", "min_version": "22.04"},
        )
        user = _FakeUser()

        with pytest.raises(BadRequestError) as exc_info:
            enforce_policy(db, policy, current_user=user)

        assert "inform-only" in str(exc_info.value).lower() or "os-compliance" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Enforce path — safety invariants
# ---------------------------------------------------------------------------

class TestEnforcePolicy:
    def _mock_celery_task(self):
        """Return a mock celery task result."""
        mock_task = MagicMock()
        mock_task.id = "fake-celery-id"
        return mock_task

    def test_enforce_creates_decision_over_drifted_only(self, db, make_project):
        """GATE: enforce builds Decision over exactly the drifted member ids."""
        from services.fleet_policy_service import enforce_policy

        p = make_project()
        compliant = _make_cluster_member(db, p, assigned_labels={"env": "prod"})
        drifted = _make_cluster_member(db, p, assigned_labels={"env": "staging"})

        target = _make_target(db, p, selector={"labels": []})
        policy = _make_policy(
            db, p, target,
            POLICY_KIND_LABEL_COMPLIANCE, POLICY_MODE_ENFORCE,
            {"required_labels": ["env=prod"]},
        )
        user = _FakeUser()

        mock_task = self._mock_celery_task()
        with patch("tasks.fleet_tasks.run_fleet_bulk_op") as mock_celery:
            mock_celery.delay.return_value = mock_task
            result, evaluation, run = enforce_policy(db, policy, current_user=user)
            db.flush()

        # Decision over DRIFTED only.
        assert run is not None
        decision = db.query(FleetDecision).filter(FleetDecision.id == run.decision_id).first()
        assert decision is not None
        assert drifted.id in decision.resolved_member_ids
        assert compliant.id not in decision.resolved_member_ids

    def test_enforce_creates_bulk_run_with_safe_action(self, db, make_project):
        """GATE: enforce creates a FleetBulkRun with action in SAFE_ACTIONS."""
        from services.fleet_policy_service import enforce_policy

        p = make_project()
        _make_cluster_member(db, p, assigned_labels={"env": "staging"})

        target = _make_target(db, p, selector={"labels": []})
        policy = _make_policy(
            db, p, target,
            POLICY_KIND_LABEL_COMPLIANCE, POLICY_MODE_ENFORCE,
            {"required_labels": ["env=prod"]},
        )
        user = _FakeUser()

        mock_task = self._mock_celery_task()
        with patch("tasks.fleet_tasks.run_fleet_bulk_op") as mock_celery:
            mock_celery.delay.return_value = mock_task
            result, evaluation, run = enforce_policy(db, policy, current_user=user)
            db.flush()

        assert run is not None
        assert run.action in SAFE_ACTIONS
        assert run.action == "set-labels"
        assert run.status == BULK_RUN_STATUS_PENDING

    def test_enforce_dispatches_via_celery_not_direct(self, db, make_project):
        """GATE: enforce-through-gated-executor — executor dispatched via Celery."""
        from services.fleet_policy_service import enforce_policy

        p = make_project()
        _make_cluster_member(db, p, assigned_labels={})

        target = _make_target(db, p, selector={"labels": []})
        policy = _make_policy(
            db, p, target,
            POLICY_KIND_LABEL_COMPLIANCE, POLICY_MODE_ENFORCE,
            {"required_labels": ["env=prod"]},
        )
        user = _FakeUser()

        mock_task = self._mock_celery_task()
        with patch("tasks.fleet_tasks.run_fleet_bulk_op") as mock_celery:
            mock_celery.delay.return_value = mock_task
            _, _, run = enforce_policy(db, policy, current_user=user)
            db.flush()

        # run_fleet_bulk_op.delay must have been called with the run id.
        mock_celery.delay.assert_called_once_with(run.id)

    def test_enforce_no_direct_fleet_member_mutation(self, db, make_project):
        """GATE: P3 code never directly writes FleetMember.assigned_labels."""
        from services.fleet_policy_service import enforce_policy

        p = make_project()
        m = _make_cluster_member(db, p, assigned_labels={})
        original_labels = dict(m.assigned_labels or {})

        target = _make_target(db, p, selector={"labels": []})
        policy = _make_policy(
            db, p, target,
            POLICY_KIND_LABEL_COMPLIANCE, POLICY_MODE_ENFORCE,
            {"required_labels": ["env=prod"]},
        )
        user = _FakeUser()

        mock_task = self._mock_celery_task()
        with patch("tasks.fleet_tasks.run_fleet_bulk_op") as mock_celery:
            mock_celery.delay.return_value = mock_task
            enforce_policy(db, policy, current_user=user)
            db.flush()

        # FleetMember.assigned_labels must NOT be changed by P3 code.
        db.refresh(m)
        assert m.assigned_labels == original_labels, (
            "enforce_policy must NOT directly mutate FleetMember.assigned_labels; "
            "mutation must go through _dispatch_action_for_member via executor."
        )

    def test_enforce_empty_drift_noop(self, db, make_project):
        """enforce on a fully-compliant fleet → no run created."""
        from services.fleet_policy_service import enforce_policy

        p = make_project()
        _make_cluster_member(db, p, assigned_labels={"env": "prod"})

        target = _make_target(db, p, selector={"labels": []})
        policy = _make_policy(
            db, p, target,
            POLICY_KIND_LABEL_COMPLIANCE, POLICY_MODE_ENFORCE,
            {"required_labels": ["env=prod"]},
        )
        user = _FakeUser()

        with patch("tasks.fleet_tasks.run_fleet_bulk_op") as mock_celery:
            result, evaluation, run = enforce_policy(db, policy, current_user=user)
            db.flush()

        assert run is None
        mock_celery.delay.assert_not_called()
        assert evaluation.enforced_run_id is None

    def test_enforce_mode_inform_raises(self, db, make_project):
        """enforce_policy with mode=inform must raise BadRequestError."""
        from core.errors import BadRequestError
        from services.fleet_policy_service import enforce_policy

        p = make_project()
        _make_cluster_member(db, p, assigned_labels={})
        target = _make_target(db, p, selector={"labels": []})
        policy = _make_policy(
            db, p, target,
            POLICY_KIND_LABEL_COMPLIANCE, POLICY_MODE_INFORM,  # inform, not enforce
            {"required_labels": ["env=prod"]},
        )
        user = _FakeUser()

        with pytest.raises(BadRequestError):
            enforce_policy(db, policy, current_user=user)

    def test_enforce_persists_evaluation_with_run_id(self, db, make_project):
        """Evaluation record has enforced_run_id set after enforce."""
        from services.fleet_policy_service import enforce_policy

        p = make_project()
        _make_cluster_member(db, p, assigned_labels={})

        target = _make_target(db, p, selector={"labels": []})
        policy = _make_policy(
            db, p, target,
            POLICY_KIND_LABEL_COMPLIANCE, POLICY_MODE_ENFORCE,
            {"required_labels": ["env=prod"]},
        )
        user = _FakeUser()

        mock_task = self._mock_celery_task()
        with patch("tasks.fleet_tasks.run_fleet_bulk_op") as mock_celery:
            mock_celery.delay.return_value = mock_task
            _, evaluation, run = enforce_policy(db, policy, current_user=user)
            db.flush()

        assert run is not None
        assert evaluation.enforced_run_id == run.id
        assert evaluation.mode == POLICY_MODE_ENFORCE


# ---------------------------------------------------------------------------
# PolicyEvaluation persistence
# ---------------------------------------------------------------------------

class TestPolicyEvaluationPersistence:
    def test_evaluation_is_immutable_snapshot(self, db, make_project):
        """evaluate_policy creates PolicyEvaluation with correct snapshots."""
        from services.fleet_policy_service import evaluate_policy

        p = make_project()
        _make_cluster_member(db, p, assigned_labels={"env": "prod"})
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        desired = {"required_labels": ["env=prod"]}
        policy = _make_policy(
            db, p, target,
            POLICY_KIND_LABEL_COMPLIANCE, POLICY_MODE_INFORM, desired,
        )
        user = _FakeUser(username="alice")

        _, evaluation = evaluate_policy(db, policy, current_user=user)
        db.flush()

        assert evaluation.policy_id == policy.id
        assert evaluation.desired_snapshot == desired
        assert evaluation.evaluated_by == "alice"
        assert evaluation.mode == POLICY_MODE_INFORM
        assert evaluation.enforced_run_id is None
        assert isinstance(evaluation.member_results, list)

    def test_member_results_contain_per_member_status(self, db, make_project):
        from services.fleet_policy_service import evaluate_policy

        p = make_project()
        m = _make_cluster_member(db, p, assigned_labels={})
        target = _make_target(db, p, selector={"labels": []})
        policy = _make_policy(
            db, p, target,
            POLICY_KIND_LABEL_COMPLIANCE, POLICY_MODE_INFORM,
            {"required_labels": ["env=prod"]},
        )
        user = _FakeUser()

        _, evaluation = evaluate_policy(db, policy, current_user=user)
        db.flush()

        assert len(evaluation.member_results) == 1
        row = evaluation.member_results[0]
        assert row["member_id"] == m.id
        assert row["status"] == COMPLIANCE_STATUS_DRIFTED


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------

class TestPolicyRBAC:
    def test_non_admin_sees_only_own_project_members(self, db, make_project, make_user):
        """Non-admin user only sees members from their own projects."""
        from services.fleet_policy_service import evaluate_policy

        # Create a real user to satisfy the FK.
        user_obj = make_user(role="operator")

        p1 = make_project(user_id=user_obj.id)
        p2 = make_project()  # owned by nobody / different user

        # Member in p1 (user's project) — drifted.
        _make_cluster_member(db, p1, assigned_labels={})
        # Member in p2 (other project) — compliant.
        _make_cluster_member(db, p2, assigned_labels={"env": "prod"})

        target = _make_target(db, p1, selector={"labels": []})
        policy = _make_policy(
            db, p1, target,
            POLICY_KIND_LABEL_COMPLIANCE, POLICY_MODE_INFORM,
            {"required_labels": ["env=prod"]},
        )
        db.flush()

        user = _FakeUser(role="operator", user_id=user_obj.id, username=user_obj.username)
        result, evaluation = evaluate_policy(db, policy, current_user=user)
        db.flush()

        # Only members in user's own project (p1) should be visible.
        all_seen_ids = [r.member_id for r in result.compliant + result.drifted + result.unknown]
        # Must NOT include p2 member.
        p2_members = db.query(FleetMember).filter(FleetMember.project_id == p2.id).all()
        for pm in p2_members:
            assert pm.id not in all_seen_ids


# ---------------------------------------------------------------------------
# API routes — Phase 3 endpoints
# ---------------------------------------------------------------------------

class TestFleetPolicyRoutes:
    def test_create_policy_201(self, client, db, admin_headers, sample_user, make_project):
        p = make_project()
        target = _make_target(db, p)
        db.commit()

        resp = client.post(
            "/api/fleet/policies",
            json={
                "name": "test-pol",
                "target_id": target.id,
                "kind": "label-compliance",
                "mode": "inform",
                "desired": {"required_labels": ["env=prod"]},
            },
            headers=admin_headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["kind"] == "label-compliance"
        assert data["mode"] == "inform"
        assert data["target_id"] == target.id

    def test_list_policies(self, client, db, admin_headers, sample_user, make_project):
        p = make_project()
        target = _make_target(db, p)
        db.commit()

        client.post(
            "/api/fleet/policies",
            json={
                "name": "p1",
                "target_id": target.id,
                "kind": "label-compliance",
                "mode": "inform",
                "desired": {"required_labels": ["env=prod"]},
            },
            headers=admin_headers,
        )

        resp = client.get("/api/fleet/policies", headers=admin_headers)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_evaluate_policy_201(self, client, db, admin_headers, sample_user, make_project):
        p = make_project()
        _make_cluster_member(db, p, assigned_labels={"env": "prod"})
        target = _make_target(db, p, selector={"labels": []})
        db.commit()

        policy_resp = client.post(
            "/api/fleet/policies",
            json={
                "name": "eval-test",
                "target_id": target.id,
                "kind": "label-compliance",
                "mode": "inform",
                "desired": {"required_labels": ["env=prod"]},
            },
            headers=admin_headers,
        )
        assert policy_resp.status_code == 201
        policy_id = policy_resp.json()["id"]

        resp = client.post(f"/api/fleet/policies/{policy_id}/evaluate", headers=admin_headers)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["compliant_count"] == 1
        assert data["drifted_count"] == 0

    def test_enforce_os_seed_returns_400(self, client, db, admin_headers, sample_user, make_project):
        """GATE: os-seed enforce → 400 via API."""
        p = make_project()
        target = _make_target(db, p)
        db.commit()

        policy_resp = client.post(
            "/api/fleet/policies",
            json={
                "name": "os-pol",
                "target_id": target.id,
                "kind": "os-compliance-seed",
                "mode": "enforce",
                "desired": {"os": "ubuntu", "min_version": "22.04"},
            },
            headers=admin_headers,
        )
        assert policy_resp.status_code == 201
        policy_id = policy_resp.json()["id"]

        resp = client.post(f"/api/fleet/policies/{policy_id}/enforce", headers=admin_headers)
        assert resp.status_code == 400, resp.text
        assert "inform-only" in resp.text.lower() or "os-compliance" in resp.text.lower()

    def test_compliance_rollup_no_evaluations(self, client, db, admin_headers, sample_user, make_project):
        p = make_project()
        target = _make_target(db, p)
        db.commit()

        policy_resp = client.post(
            "/api/fleet/policies",
            json={
                "name": "rollup-test",
                "target_id": target.id,
                "kind": "label-compliance",
                "mode": "inform",
                "desired": {"required_labels": ["env=prod"]},
            },
            headers=admin_headers,
        )
        policy_id = policy_resp.json()["id"]

        resp = client.get(f"/api/fleet/policies/{policy_id}/compliance", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["compliant_count"] == 0
        assert data["drifted_count"] == 0
        assert data["last_evaluated_at"] is None

    def test_compliance_rollup_after_evaluate(self, client, db, admin_headers, sample_user, make_project):
        p = make_project()
        _make_cluster_member(db, p, assigned_labels={})  # drifted
        target = _make_target(db, p, selector={"labels": []})
        db.commit()

        policy_resp = client.post(
            "/api/fleet/policies",
            json={
                "name": "rollup-eval-test",
                "target_id": target.id,
                "kind": "label-compliance",
                "mode": "inform",
                "desired": {"required_labels": ["env=prod"]},
            },
            headers=admin_headers,
        )
        policy_id = policy_resp.json()["id"]

        # Evaluate first.
        client.post(f"/api/fleet/policies/{policy_id}/evaluate", headers=admin_headers)

        resp = client.get(f"/api/fleet/policies/{policy_id}/compliance", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["drifted_count"] == 1
        assert len(data["drifted_members"]) == 1
        assert data["last_evaluated_at"] is not None

    def test_viewer_can_read_policy(self, client, db, admin_headers, viewer_headers,
                                     sample_user, sample_viewer_user, make_project):
        p = make_project()
        target = _make_target(db, p)
        db.commit()

        client.post(
            "/api/fleet/policies",
            json={
                "name": "viewer-test-pol",
                "target_id": target.id,
                "kind": "label-compliance",
                "mode": "inform",
                "desired": {"required_labels": ["env=prod"]},
            },
            headers=admin_headers,
        )

        resp = client.get("/api/fleet/policies", headers=viewer_headers)
        assert resp.status_code == 200

    def test_viewer_cannot_create_policy(self, client, db, viewer_headers,
                                          sample_viewer_user, make_project):
        p = make_project()
        target = _make_target(db, p)
        db.commit()

        resp = client.post(
            "/api/fleet/policies",
            json={
                "name": "no-perm",
                "target_id": target.id,
                "kind": "label-compliance",
                "mode": "inform",
                "desired": {"required_labels": ["env=prod"]},
            },
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    def test_create_policy_invalid_kind_400(self, client, db, admin_headers, sample_user, make_project):
        p = make_project()
        target = _make_target(db, p)
        db.commit()

        resp = client.post(
            "/api/fleet/policies",
            json={
                "name": "bad-kind",
                "target_id": target.id,
                "kind": "unknown-kind",
                "mode": "inform",
                "desired": {},
            },
            headers=admin_headers,
        )
        assert resp.status_code == 400

    def test_policy_not_found_404(self, client, admin_headers, sample_user):
        resp = client.get("/api/fleet/policies/99999", headers=admin_headers)
        assert resp.status_code == 404

    def test_create_label_compliance_policy_non_vocab_key_400(
        self, client, db, admin_headers, sample_user, make_project
    ):
        """FIX 3: creating a label-compliance policy with a non-vocabulary required
        key (e.g. os=ubuntu) must be rejected at creation with a 4xx."""
        p = make_project()
        target = _make_target(db, p)
        db.commit()

        resp = client.post(
            "/api/fleet/policies",
            json={
                "name": "bad-vocab-pol",
                "target_id": target.id,
                "kind": "label-compliance",
                "mode": "inform",
                "desired": {"required_labels": ["os=ubuntu"]},  # 'os' is discovered, not assigned
            },
            headers=admin_headers,
        )
        assert resp.status_code in (400, 422), resp.text


class TestEnforcePolicyFixes:
    """Tests for FIX 1 (required-only labels) and FIX 2 (commit-before-dispatch)."""

    def _mock_celery_task(self):
        mock_task = MagicMock()
        mock_task.id = "fake-celery-id"
        return mock_task

    def test_enforce_action_params_contains_only_required_labels(self, db, make_project):
        """FIX 1: two drifted members with DIFFERENT pre-existing assigned labels
        under one label-compliance policy → FleetBulkRun.action_params['labels']
        contains ONLY the required keys, not the union of members' existing labels."""
        from services.fleet_policy_service import enforce_policy

        p = make_project()
        # Member A has a pre-existing label 'team=alpha' (not in required set).
        _make_cluster_member(db, p, assigned_labels={"team": "alpha", "env": "staging"})
        # Member B has a pre-existing label 'owner=bob' (not in required set).
        _make_cluster_member(db, p, assigned_labels={"owner": "bob", "env": "staging"})

        target = _make_target(db, p, selector={"labels": []})
        # Required: only env=prod.
        policy = _make_policy(
            db, p, target,
            POLICY_KIND_LABEL_COMPLIANCE, POLICY_MODE_ENFORCE,
            {"required_labels": ["env=prod"]},
        )
        user = _FakeUser()

        mock_task = self._mock_celery_task()
        with patch("tasks.fleet_tasks.run_fleet_bulk_op") as mock_celery:
            mock_celery.delay.return_value = mock_task
            _, _, run = enforce_policy(db, policy, current_user=user)
            db.flush()

        assert run is not None
        labels = run.action_params["labels"]
        # Must contain the required key.
        assert labels.get("env") == "prod"
        # Must NOT contain labels from individual members' pre-existing assigned sets.
        assert "team" not in labels, (
            "action_params['labels'] must not include member A's pre-existing 'team' label"
        )
        assert "owner" not in labels, (
            "action_params['labels'] must not include member B's pre-existing 'owner' label"
        )
        # Exactly the required keys only.
        assert set(labels.keys()) == {"env"}

    def test_enforce_calls_db_commit_before_dispatch(self, db, make_project):
        """FIX 2: enforce_policy calls db.commit() before run_fleet_bulk_op.delay
        so the Celery worker's own session can find the FleetBulkRun."""
        from unittest.mock import call, patch

        from services.fleet_policy_service import enforce_policy

        p = make_project()
        _make_cluster_member(db, p, assigned_labels={"env": "staging"})

        target = _make_target(db, p, selector={"labels": []})
        policy = _make_policy(
            db, p, target,
            POLICY_KIND_LABEL_COMPLIANCE, POLICY_MODE_ENFORCE,
            {"required_labels": ["env=prod"]},
        )
        user = _FakeUser()

        call_order: list[str] = []

        original_commit = db.commit

        def _recording_commit():
            call_order.append("commit")
            return original_commit()

        mock_task = MagicMock()
        mock_task.id = "fake-celery-id"

        db.commit = _recording_commit  # type: ignore[method-assign]
        try:
            with patch("tasks.fleet_tasks.run_fleet_bulk_op") as mock_celery:
                def _recording_delay(run_id):
                    call_order.append("delay")
                    return mock_task

                mock_celery.delay.side_effect = _recording_delay
                _, _, run = enforce_policy(db, policy, current_user=user)
                db.flush()
        finally:
            db.commit = original_commit  # type: ignore[method-assign]

        assert run is not None
        # commit must appear before delay in the call order.
        assert "commit" in call_order, "enforce_policy must call db.commit() before dispatch"
        assert "delay" in call_order, "enforce_policy must call run_fleet_bulk_op.delay()"
        commit_idx = call_order.index("commit")
        delay_idx = call_order.index("delay")
        assert commit_idx < delay_idx, (
            f"db.commit() (pos {commit_idx}) must be called before "
            f"run_fleet_bulk_op.delay() (pos {delay_idx})"
        )
