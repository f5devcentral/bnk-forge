"""HTTP-level RBAC tests for fleet mutation routes — D-022 Phase 4.

Asserts that a non-owner operator is rejected (403) at the HTTP layer on:
  - PUT  /api/fleet/members/{id}/labels
  - POST /api/fleet/bulk-ops
  - POST /api/fleet/bulk-ops/{id}/resume
  - POST /api/fleet/policies/{id}/enforce

And that the project owner and admin are allowed (2xx) on the same routes.

These are TestClient (HTTP-level) tests — they catch route-shadowing / dependency-
bypass bugs that service-layer unit tests cannot catch.  Mirror of the existing
pattern in test_routes_operators_fleet.py.
"""

from datetime import UTC
from unittest.mock import patch

import pytest

from models.fleet import FleetMember
from models.fleet_policy import POLICY_MODE_ENFORCE, FleetPolicy
from models.fleet_targeting import (
    BULK_RUN_STATUS_PAUSED_GATE,
    BULK_RUN_STATUS_PENDING,
    DECISION_STATUS_RESOLVED,
    GATE_MODE_MANUAL,
    FleetBulkRun,
    FleetDecision,
    FleetTarget,
)
from services.auth_service import create_access_token
from tests.factories import ProjectFactory, UserFactory

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _token_for(user) -> dict:
    """Return Bearer Authorization headers minted for a real DB user."""
    token = create_access_token(data={"sub": user.username, "role": user.role})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def owner(db):
    """Operator who owns the fleet project."""
    return UserFactory(db, role="operator")


@pytest.fixture()
def non_owner(db):
    """Operator who does NOT own the fleet project."""
    return UserFactory(db, role="operator")


@pytest.fixture()
def admin(db):
    """Admin user who bypasses ownership checks."""
    return UserFactory(db, role="admin")


@pytest.fixture()
def owned_project(db, owner):
    """Project owned by `owner`."""
    return ProjectFactory(db, user_id=owner.id)


@pytest.fixture()
def fleet_member(db, owned_project):
    """FleetMember scoped to the owned project."""
    member = FleetMember(
        project_id=owned_project.id,
        member_type="cluster",
        member_id=1,
        name="test-cluster",
        assigned_labels={},
        discovered_labels={},
    )
    db.add(member)
    db.flush()
    return member


@pytest.fixture()
def fleet_target(db, owned_project):
    """FleetTarget linked to owned_project."""
    target = FleetTarget(
        project_id=owned_project.id,
        name="test-target",
        selector={"matchLabels": {}},
        created_by="fixture",
    )
    db.add(target)
    db.flush()
    return target


@pytest.fixture()
def resolved_decision(db, owned_project, fleet_member, fleet_target):
    """A FleetDecision in 'resolved' status containing fleet_member."""
    from datetime import datetime, timezone

    decision = FleetDecision(
        target_id=fleet_target.id,
        project_id=owned_project.id,
        selector_snapshot={},
        resolved_member_ids=[fleet_member.id],
        member_count=1,
        resolved_at=datetime.now(tz=UTC),
        resolved_by="fixture",
        status=DECISION_STATUS_RESOLVED,
    )
    db.add(decision)
    db.flush()
    return decision


@pytest.fixture()
def enforce_policy(db, owned_project, fleet_target):
    """A FleetPolicy in enforce mode with label-compliance kind."""
    policy = FleetPolicy(
        project_id=owned_project.id,
        name="test-enforce-policy",
        target_id=fleet_target.id,
        kind="label-compliance",
        mode=POLICY_MODE_ENFORCE,
        desired={"required_labels": ["env=prod"]},
        created_by="fixture",
    )
    db.add(policy)
    db.flush()
    return policy


# ---------------------------------------------------------------------------
# PUT /api/fleet/members/{id}/labels — label assignment
# ---------------------------------------------------------------------------


class TestFleetLabelUpdateRBAC:
    """HTTP-level RBAC tests for PUT /api/fleet/members/{id}/labels."""

    def test_non_owner_operator_is_rejected_403(
        self, client, db, owner, non_owner, fleet_member
    ):
        """Non-owner operator cannot assign labels — must get 403 at HTTP layer."""
        response = client.put(
            f"/api/fleet/members/{fleet_member.id}/labels",
            json={"labels": {"env": "prod"}},
            headers=_token_for(non_owner),
        )
        assert response.status_code == 403

    def test_owner_operator_is_allowed(
        self, client, db, owner, fleet_member
    ):
        """Project owner can assign labels — must get 200."""
        response = client.put(
            f"/api/fleet/members/{fleet_member.id}/labels",
            json={"labels": {"env": "prod"}},
            headers=_token_for(owner),
        )
        assert response.status_code == 200
        assert response.json()["assigned_labels"] == {"env": "prod"}

    def test_admin_is_allowed(
        self, client, db, admin, fleet_member
    ):
        """Admin bypasses ownership — must get 200."""
        response = client.put(
            f"/api/fleet/members/{fleet_member.id}/labels",
            json={"labels": {"team": "platform"}},
            headers=_token_for(admin),
        )
        assert response.status_code == 200

    def test_viewer_is_rejected_403(
        self, client, db, fleet_member
    ):
        """Viewer role is below operator — must get 403 (role gate, not ownership)."""
        viewer = UserFactory(db, role="viewer")
        response = client.put(
            f"/api/fleet/members/{fleet_member.id}/labels",
            json={"labels": {"env": "staging"}},
            headers=_token_for(viewer),
        )
        assert response.status_code == 403

    def test_unauthenticated_is_rejected_401(self, client, fleet_member):
        """No auth header returns 401."""
        response = client.put(
            f"/api/fleet/members/{fleet_member.id}/labels",
            json={"labels": {"env": "prod"}},
        )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/fleet/bulk-ops — bulk operation start
# ---------------------------------------------------------------------------


class TestFleetBulkOpsRBAC:
    """HTTP-level RBAC tests for POST /api/fleet/bulk-ops."""

    def test_non_owner_operator_is_rejected_403(
        self, client, db, non_owner, resolved_decision
    ):
        """Non-owner operator cannot start a bulk-op — must get 403 at HTTP layer."""
        response = client.post(
            "/api/fleet/bulk-ops",
            json={
                "decision_id": resolved_decision.id,
                "action": "re-reconcile",
                "concurrency": 1,
                "gate_mode": "manual",
            },
            headers=_token_for(non_owner),
        )
        assert response.status_code == 403

    def test_owner_operator_is_allowed(
        self, client, db, owner, resolved_decision
    ):
        """Project owner can start a bulk-op — must get 201."""
        # Patch the Celery task dispatch at its source module so we don't need a
        # real broker.  The route imports run_fleet_bulk_op lazily inside the
        # handler body, so we must patch tasks.fleet_tasks (the module that owns
        # the attribute), not routes.fleet.
        with patch("tasks.fleet_tasks.run_fleet_bulk_op") as mock_task:
            mock_task.delay.return_value.id = "fake-celery-id"
            response = client.post(
                "/api/fleet/bulk-ops",
                json={
                    "decision_id": resolved_decision.id,
                    "action": "re-reconcile",
                    "concurrency": 1,
                    "gate_mode": "manual",
                },
                headers=_token_for(owner),
            )
        assert response.status_code == 201

    def test_admin_is_allowed(
        self, client, db, admin, resolved_decision
    ):
        """Admin bypasses ownership — must get 201."""
        with patch("tasks.fleet_tasks.run_fleet_bulk_op") as mock_task:
            mock_task.delay.return_value.id = "fake-celery-id"
            response = client.post(
                "/api/fleet/bulk-ops",
                json={
                    "decision_id": resolved_decision.id,
                    "action": "re-reconcile",
                    "concurrency": 1,
                    "gate_mode": "manual",
                },
                headers=_token_for(admin),
            )
        assert response.status_code == 201

    def test_viewer_is_rejected_403(self, client, db, resolved_decision):
        """Viewer role returns 403 (role gate fires before ownership check)."""
        viewer = UserFactory(db, role="viewer")
        response = client.post(
            "/api/fleet/bulk-ops",
            json={
                "decision_id": resolved_decision.id,
                "action": "re-reconcile",
                "concurrency": 1,
                "gate_mode": "manual",
            },
            headers=_token_for(viewer),
        )
        assert response.status_code == 403

    def test_unauthenticated_is_rejected_401(self, client, resolved_decision):
        """No auth header returns 401."""
        response = client.post(
            "/api/fleet/bulk-ops",
            json={
                "decision_id": resolved_decision.id,
                "action": "re-reconcile",
                "concurrency": 1,
                "gate_mode": "manual",
            },
        )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/fleet/policies/{id}/enforce — policy enforcement
# ---------------------------------------------------------------------------


class TestFleetPolicyEnforceRBAC:
    """HTTP-level RBAC tests for POST /api/fleet/policies/{id}/enforce."""

    def test_non_owner_operator_is_rejected_403(
        self, client, db, non_owner, enforce_policy, resolved_decision
    ):
        """Non-owner operator cannot enforce a policy — must get 403 at HTTP layer.

        resolved_decision is included so the RBAC gate has project_ids to check
        (it scans the most recent resolved decision for the policy's target).
        """
        response = client.post(
            f"/api/fleet/policies/{enforce_policy.id}/enforce",
            headers=_token_for(non_owner),
        )
        assert response.status_code == 403

    def test_owner_operator_is_allowed(
        self, client, db, owner, fleet_member, enforce_policy, resolved_decision
    ):
        """Project owner can enforce a policy — must get 201."""
        # fleet_policy_service imports run_fleet_bulk_op lazily inside enforce_policy;
        # patch at its source module.
        with patch("tasks.fleet_tasks.run_fleet_bulk_op") as mock_task:
            mock_task.delay.return_value.id = "fake-celery-id"
            response = client.post(
                f"/api/fleet/policies/{enforce_policy.id}/enforce",
                headers=_token_for(owner),
            )
        # 201 on success; 400 if there's no drift (policy evaluates compliant for
        # the bare member with no assigned labels matching required_labels) — both
        # indicate the RBAC gate passed and we reached the business logic layer.
        assert response.status_code in {201, 400}

    def test_admin_is_allowed(
        self, client, db, admin, fleet_member, enforce_policy, resolved_decision
    ):
        """Admin bypasses ownership — must reach business logic (201 or 400, not 403)."""
        with patch("tasks.fleet_tasks.run_fleet_bulk_op") as mock_task:
            mock_task.delay.return_value.id = "fake-celery-id"
            response = client.post(
                f"/api/fleet/policies/{enforce_policy.id}/enforce",
                headers=_token_for(admin),
            )
        assert response.status_code in {201, 400}

    def test_viewer_is_rejected_403(self, client, db, enforce_policy):
        """Viewer role returns 403 (role gate fires before ownership check)."""
        viewer = UserFactory(db, role="viewer")
        response = client.post(
            f"/api/fleet/policies/{enforce_policy.id}/enforce",
            headers=_token_for(viewer),
        )
        assert response.status_code == 403

    def test_unauthenticated_is_rejected_401(self, client, enforce_policy):
        """No auth header returns 401."""
        response = client.post(
            f"/api/fleet/policies/{enforce_policy.id}/enforce",
        )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/fleet/bulk-ops/{id}/resume — resume paused bulk run
# ---------------------------------------------------------------------------


@pytest.fixture()
def paused_bulk_run(db, owned_project, resolved_decision):
    """A FleetBulkRun in 'paused_gate' status, linked to resolved_decision."""
    run = FleetBulkRun(
        decision_id=resolved_decision.id,
        project_id=owned_project.id,
        action="re-reconcile",
        action_params={},
        wave_by="fault_domain",
        concurrency=1,
        gate_mode=GATE_MODE_MANUAL,
        status=BULK_RUN_STATUS_PAUSED_GATE,
        current_wave_index=0,
        created_by="fixture",
    )
    db.add(run)
    db.flush()
    return run


class TestFleetBulkOpResumeRBAC:
    """HTTP-level RBAC tests for POST /api/fleet/bulk-ops/{id}/resume."""

    def test_non_owner_operator_is_rejected_403(
        self, client, db, non_owner, paused_bulk_run
    ):
        """Non-owner operator cannot resume a bulk-op — must get 403 at HTTP layer."""
        response = client.post(
            f"/api/fleet/bulk-ops/{paused_bulk_run.id}/resume",
            headers=_token_for(non_owner),
        )
        assert response.status_code == 403

    def test_owner_operator_is_allowed(
        self, client, db, owner, paused_bulk_run
    ):
        """Project owner can resume a bulk-op — must get 200."""
        with patch("tasks.fleet_tasks.resume_fleet_bulk_op") as mock_task:
            mock_task.delay.return_value.id = "fake-celery-id"
            response = client.post(
                f"/api/fleet/bulk-ops/{paused_bulk_run.id}/resume",
                headers=_token_for(owner),
            )
        assert response.status_code == 200

    def test_admin_is_allowed(
        self, client, db, admin, paused_bulk_run
    ):
        """Admin bypasses ownership — must get 200."""
        with patch("tasks.fleet_tasks.resume_fleet_bulk_op") as mock_task:
            mock_task.delay.return_value.id = "fake-celery-id"
            response = client.post(
                f"/api/fleet/bulk-ops/{paused_bulk_run.id}/resume",
                headers=_token_for(admin),
            )
        assert response.status_code == 200

    def test_viewer_is_rejected_403(self, client, db, paused_bulk_run):
        """Viewer role returns 403 (role gate fires before ownership check)."""
        viewer = UserFactory(db, role="viewer")
        response = client.post(
            f"/api/fleet/bulk-ops/{paused_bulk_run.id}/resume",
            headers=_token_for(viewer),
        )
        assert response.status_code == 403

    def test_unauthenticated_is_rejected_401(self, client, paused_bulk_run):
        """No auth header returns 401."""
        response = client.post(
            f"/api/fleet/bulk-ops/{paused_bulk_run.id}/resume",
        )
        assert response.status_code == 401
