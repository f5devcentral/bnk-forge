"""Component tests for POST /targets/{id}/run-selection — D-022 P6 Slice C.

Tests:
  - Happy path: intersected ids used to build Decision + BulkRun.
  - Out-of-fleet ids are silently dropped; only the intersection is used.
  - Empty intersection → 400 BadRequestError.
  - Action not in SAFE_ACTIONS → 400 BadRequestError.
  - RBAC: non-owner of a selected member's project → ForbiddenError (no run/dispatch).
"""
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from core.errors import BadRequestError, ForbiddenError, NotFoundError
from models.fleet import FleetMember
from models.fleet_targeting import (
    BULK_RUN_STATUS_PENDING,
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

def _make_cluster_member(db, project, assigned_labels=None, fault_domain=None):
    cluster = KubernetesClusterFactory(db, project=project, cloud_provider="aws", region="us-east-1")
    member = reconcile_fleet_member(db, "cluster", cluster.id)
    if assigned_labels:
        member.assigned_labels = assigned_labels
    if fault_domain:
        member.fault_domain = fault_domain
    db.flush()
    db.refresh(member)
    return member


def _make_target(db, project, selector=None, name="test-fleet", pinned_member_ids=None):
    target = FleetTarget(
        project_id=project.id,
        name=name,
        selector=selector or {"labels": ["env=prod"]},
        pinned_member_ids=pinned_member_ids or [],
        created_by="test",
    )
    db.add(target)
    db.flush()
    db.refresh(target)
    return target


class _FakeUser:
    def __init__(self, role="admin", user_id=1, username="testuser"):
        self.role = role
        self.id = user_id
        self.username = username


def _call_run_selection(db, target_id, body_dict, user):
    """Invoke the route handler directly (no HTTP layer)."""
    from routes.fleet import RunSelectionRequest, run_fleet_selection

    body = RunSelectionRequest(**body_dict)
    return run_fleet_selection(target_id=target_id, body=body, current_user=user, db=db)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestRunSelectionHappyPath:
    def test_creates_decision_and_run_over_intersected_ids(self, db, make_project):
        p = make_project()
        m1 = _make_cluster_member(db, p, assigned_labels={"env": "prod"})
        m2 = _make_cluster_member(db, p, assigned_labels={"env": "prod"})
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        db.commit()

        admin = _FakeUser(role="admin", user_id=1, username="admin")

        with patch("tasks.fleet_tasks.run_fleet_bulk_op") as mock_task:
            mock_task.delay.return_value.id = "celery-abc"
            result = _call_run_selection(db, target.id, {
                "member_ids": [m1.id, m2.id],
                "action": "re-reconcile",
            }, admin)

        # A BulkRun was persisted.
        assert result.action == "re-reconcile"
        assert result.status == BULK_RUN_STATUS_PENDING

        # Verify the decision was created with both members.
        decision = db.query(FleetDecision).filter(
            FleetDecision.id == result.decision_id
        ).first()
        assert decision is not None
        assert set(decision.resolved_member_ids) == {m1.id, m2.id}
        assert decision.status == DECISION_STATUS_RESOLVED

        # Celery was dispatched.
        assert mock_task.delay.called

    def test_run_is_scoped_to_target_project(self, db, make_project):
        p = make_project()
        m = _make_cluster_member(db, p, assigned_labels={"env": "prod"})
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        db.commit()

        admin = _FakeUser(role="admin", user_id=1, username="admin")

        with patch("tasks.fleet_tasks.run_fleet_bulk_op") as mock_task:
            mock_task.delay.return_value.id = "celery-xyz"
            result = _call_run_selection(db, target.id, {
                "member_ids": [m.id],
                "action": "set-labels",
            }, admin)

        assert result.project_id == target.project_id


# ---------------------------------------------------------------------------
# Out-of-fleet ids are silently dropped
# ---------------------------------------------------------------------------

class TestRunSelectionFilterOutOfFleet:
    def test_out_of_fleet_ids_dropped_only_intersection_used(self, db, make_project):
        p = make_project()
        m_in = _make_cluster_member(db, p, assigned_labels={"env": "prod"})
        m_out = _make_cluster_member(db, p, assigned_labels={"env": "staging"})
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        db.commit()

        admin = _FakeUser(role="admin", user_id=1, username="admin")

        with patch("tasks.fleet_tasks.run_fleet_bulk_op") as mock_task:
            mock_task.delay.return_value.id = "celery-filter"
            result = _call_run_selection(db, target.id, {
                # m_out is NOT in this fleet (wrong label), but is passed — must be dropped.
                "member_ids": [m_in.id, m_out.id],
                "action": "re-reconcile",
            }, admin)

        decision = db.query(FleetDecision).filter(
            FleetDecision.id == result.decision_id
        ).first()
        # Only the in-fleet member survives.
        assert m_in.id in decision.resolved_member_ids
        assert m_out.id not in decision.resolved_member_ids

    def test_nonexistent_id_treated_as_out_of_fleet(self, db, make_project):
        p = make_project()
        m = _make_cluster_member(db, p, assigned_labels={"env": "prod"})
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        db.commit()

        admin = _FakeUser(role="admin", user_id=1, username="admin")

        with patch("tasks.fleet_tasks.run_fleet_bulk_op") as mock_task:
            mock_task.delay.return_value.id = "celery-nonexistent"
            result = _call_run_selection(db, target.id, {
                "member_ids": [m.id, 999999],
                "action": "re-reconcile",
            }, admin)

        decision = db.query(FleetDecision).filter(
            FleetDecision.id == result.decision_id
        ).first()
        assert 999999 not in decision.resolved_member_ids
        assert m.id in decision.resolved_member_ids


# ---------------------------------------------------------------------------
# Empty intersection → 400
# ---------------------------------------------------------------------------

class TestRunSelectionEmptyIntersection:
    def test_all_ids_out_of_fleet_raises_400(self, db, make_project):
        p = make_project()
        m_out = _make_cluster_member(db, p, assigned_labels={"env": "staging"})
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        db.commit()

        admin = _FakeUser(role="admin", user_id=1, username="admin")

        with pytest.raises(BadRequestError):
            _call_run_selection(db, target.id, {
                "member_ids": [m_out.id],
                "action": "re-reconcile",
            }, admin)

    def test_empty_member_ids_list_raises_400(self, db, make_project):
        p = make_project()
        _make_cluster_member(db, p, assigned_labels={"env": "prod"})
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        db.commit()

        admin = _FakeUser(role="admin", user_id=1, username="admin")

        with pytest.raises(BadRequestError):
            _call_run_selection(db, target.id, {
                "member_ids": [],
                "action": "re-reconcile",
            }, admin)

    def test_no_run_created_when_empty_intersection(self, db, make_project):
        """Verify no DB write happens on empty intersection."""
        p = make_project()
        m_out = _make_cluster_member(db, p, assigned_labels={"env": "staging"})
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        db.commit()

        run_count_before = db.query(FleetBulkRun).count()
        admin = _FakeUser(role="admin", user_id=1, username="admin")

        with pytest.raises(BadRequestError):
            _call_run_selection(db, target.id, {
                "member_ids": [m_out.id],
                "action": "re-reconcile",
            }, admin)

        assert db.query(FleetBulkRun).count() == run_count_before


# ---------------------------------------------------------------------------
# Action not in SAFE_ACTIONS → 400
# ---------------------------------------------------------------------------

class TestRunSelectionActionAllowlist:
    def test_forbidden_action_raises_400(self, db, make_project):
        p = make_project()
        m = _make_cluster_member(db, p, assigned_labels={"env": "prod"})
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        db.commit()

        admin = _FakeUser(role="admin", user_id=1, username="admin")

        with pytest.raises(BadRequestError, match="not allowed"):
            _call_run_selection(db, target.id, {
                "member_ids": [m.id],
                "action": "destroy",
            }, admin)

    def test_forbidden_action_creates_no_run(self, db, make_project):
        p = make_project()
        m = _make_cluster_member(db, p, assigned_labels={"env": "prod"})
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        db.commit()

        run_count_before = db.query(FleetBulkRun).count()
        admin = _FakeUser(role="admin", user_id=1, username="admin")

        with pytest.raises(BadRequestError):
            _call_run_selection(db, target.id, {
                "member_ids": [m.id],
                "action": "rm -rf /",
            }, admin)

        assert db.query(FleetBulkRun).count() == run_count_before


# ---------------------------------------------------------------------------
# RBAC: non-owner of selected member's project → ForbiddenError
# ---------------------------------------------------------------------------

class TestRunSelectionRBAC:
    def test_non_owner_raises_forbidden(self, db, make_project):
        """operator who does not own the fleet's project is denied."""
        owner = UserFactory(db, role="operator")
        project = ProjectFactory(db, user_id=owner.id)
        m = _make_cluster_member(db, project, assigned_labels={"env": "prod"})
        target = _make_target(db, project, selector={"labels": ["env=prod"]})
        db.commit()

        interloper = UserFactory(db, role="operator")
        fake_interloper = _FakeUser(
            role="operator",
            user_id=interloper.id,
            username=interloper.username,
        )

        with pytest.raises(ForbiddenError):
            _call_run_selection(db, target.id, {
                "member_ids": [m.id],
                "action": "re-reconcile",
            }, fake_interloper)

    def test_non_owner_creates_no_run(self, db, make_project):
        """No run row is written and no Celery task is dispatched on RBAC denial."""
        owner = UserFactory(db, role="operator")
        project = ProjectFactory(db, user_id=owner.id)
        m = _make_cluster_member(db, project, assigned_labels={"env": "prod"})
        target = _make_target(db, project, selector={"labels": ["env=prod"]})
        db.commit()

        run_count_before = db.query(FleetBulkRun).count()
        interloper = UserFactory(db, role="operator")
        fake_interloper = _FakeUser(
            role="operator",
            user_id=interloper.id,
            username=interloper.username,
        )

        with patch("tasks.fleet_tasks.run_fleet_bulk_op") as mock_task:
            with pytest.raises(ForbiddenError):
                _call_run_selection(db, target.id, {
                    "member_ids": [m.id],
                    "action": "re-reconcile",
                }, fake_interloper)

        # Neither a run row nor a Celery dispatch.
        assert db.query(FleetBulkRun).count() == run_count_before
        assert not mock_task.delay.called

    def test_admin_bypasses_rbac(self, db, make_project):
        owner = UserFactory(db, role="operator")
        project = ProjectFactory(db, user_id=owner.id)
        m = _make_cluster_member(db, project, assigned_labels={"env": "prod"})
        target = _make_target(db, project, selector={"labels": ["env=prod"]})
        db.commit()

        admin = _FakeUser(role="admin", user_id=999, username="admin")

        with patch("tasks.fleet_tasks.run_fleet_bulk_op") as mock_task:
            mock_task.delay.return_value.id = "celery-rbac"
            result = _call_run_selection(db, target.id, {
                "member_ids": [m.id],
                "action": "re-reconcile",
            }, admin)

        assert result.status == BULK_RUN_STATUS_PENDING


# ---------------------------------------------------------------------------
# Fleet not found
# ---------------------------------------------------------------------------

class TestRunSelectionNotFound:
    def test_missing_fleet_raises_404(self, db):
        admin = _FakeUser(role="admin", user_id=1, username="admin")
        with pytest.raises(NotFoundError):
            _call_run_selection(db, 999999, {
                "member_ids": [1],
                "action": "re-reconcile",
            }, admin)
