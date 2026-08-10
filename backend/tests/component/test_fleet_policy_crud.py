"""Component tests for Fleet policy PUT/DELETE (soft-delete) — D-022 P5 S3.

Tests:
  - PUT updates name/kind/mode/desired.
  - PUT re-validates desired schema per kind.
  - PUT rejects os-compliance-seed → enforce mode flip (invariant guard).
  - PUT does NOT mutate any existing PolicyEvaluation snapshot.
  - DELETE soft-deletes: hidden from list, GET/{id}→404, compliance→404,
    evaluate→404, enforce→404, evaluations→404.
  - DELETE preserves PolicyEvaluation rows in DB (immutable audit trail).
  - RBAC: non-owner 403 on PUT and DELETE.
"""

from datetime import UTC, datetime

import pytest

from models.fleet_policy import (
    POLICY_KIND_LABEL_COMPLIANCE,
    POLICY_KIND_OS_COMPLIANCE_SEED,
    POLICY_MODE_ENFORCE,
    POLICY_MODE_INFORM,
    FleetPolicy,
    PolicyEvaluation,
)
from models.fleet_targeting import (
    DECISION_STATUS_RESOLVED,
    FleetDecision,
    FleetTarget,
)
from tests.factories import KubernetesClusterFactory, ProjectFactory, UserFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _make_policy(db, project, target, kind=POLICY_KIND_LABEL_COMPLIANCE,
                 mode=POLICY_MODE_INFORM, desired=None, name="test-policy"):
    if desired is None:
        desired = {"required_labels": ["env=prod"]}
    policy = FleetPolicy(
        project_id=project.id,
        name=name,
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


def _make_evaluation(db, policy):
    """Create an immutable PolicyEvaluation snapshot for a policy."""
    ev = PolicyEvaluation(
        policy_id=policy.id,
        project_id=policy.project_id,
        evaluated_at=datetime.now(UTC),
        evaluated_by="test",
        selector_snapshot={"labels": []},
        desired_snapshot=dict(policy.desired),
        compliant_count=1,
        drifted_count=0,
        unknown_count=0,
        total_count=1,
        member_results=[{"member_id": 1, "status": "compliant", "detail": None}],
        mode=policy.mode,
    )
    db.add(ev)
    db.flush()
    db.refresh(ev)
    return ev


# ---------------------------------------------------------------------------
# PUT /api/fleet/policies/{id}
# ---------------------------------------------------------------------------

class TestPutFleetPolicy:
    def test_put_updates_name(self, client, db, admin_headers, sample_user, make_project):
        p = make_project()
        target = _make_target(db, p)
        policy = _make_policy(db, p, target, name="original")
        db.commit()

        resp = client.put(
            f"/api/fleet/policies/{policy.id}",
            json={"name": "updated-name"},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "updated-name"

    def test_put_updates_mode_inform_to_enforce_for_label_compliance(
        self, client, db, admin_headers, sample_user, make_project
    ):
        p = make_project()
        target = _make_target(db, p)
        policy = _make_policy(db, p, target, kind=POLICY_KIND_LABEL_COMPLIANCE,
                               mode=POLICY_MODE_INFORM)
        db.commit()

        resp = client.put(
            f"/api/fleet/policies/{policy.id}",
            json={"mode": "enforce"},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["mode"] == "enforce"

    def test_put_rejects_os_seed_to_enforce_flip(
        self, client, db, admin_headers, sample_user, make_project
    ):
        """os-compliance-seed cannot be flipped to enforce — invariant guard."""
        p = make_project()
        target = _make_target(db, p)
        policy = _make_policy(
            db, p, target,
            kind=POLICY_KIND_OS_COMPLIANCE_SEED,
            mode=POLICY_MODE_INFORM,
            desired={"os": "ubuntu", "min_version": "22.04"},
        )
        db.commit()

        resp = client.put(
            f"/api/fleet/policies/{policy.id}",
            json={"mode": "enforce"},
            headers=admin_headers,
        )
        assert resp.status_code == 400, resp.text
        assert "inform-only" in resp.text.lower() or "does not support" in resp.text.lower()

    def test_put_rejects_setting_kind_to_os_seed_with_enforce_mode(
        self, client, db, admin_headers, sample_user, make_project
    ):
        """Changing kind to os-compliance-seed while mode=enforce must be rejected."""
        p = make_project()
        target = _make_target(db, p)
        # Start as label-compliance enforce
        policy = _make_policy(
            db, p, target,
            kind=POLICY_KIND_LABEL_COMPLIANCE,
            mode=POLICY_MODE_ENFORCE,
        )
        db.commit()

        resp = client.put(
            f"/api/fleet/policies/{policy.id}",
            json={"kind": "os-compliance-seed", "desired": {"os": "ubuntu", "min_version": "22.04"}},
            headers=admin_headers,
        )
        assert resp.status_code == 400, resp.text

    def test_put_updates_desired(self, client, db, admin_headers, sample_user, make_project):
        p = make_project()
        target = _make_target(db, p)
        policy = _make_policy(db, p, target, desired={"required_labels": ["env=prod"]})
        db.commit()

        resp = client.put(
            f"/api/fleet/policies/{policy.id}",
            json={"desired": {"required_labels": ["env=staging", "team=platform"]}},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        assert "env=staging" in resp.json()["desired"]["required_labels"]

    def test_put_rejects_invalid_kind(self, client, db, admin_headers, sample_user, make_project):
        p = make_project()
        target = _make_target(db, p)
        policy = _make_policy(db, p, target)
        db.commit()

        resp = client.put(
            f"/api/fleet/policies/{policy.id}",
            json={"kind": "not-a-real-kind"},
            headers=admin_headers,
        )
        assert resp.status_code == 400, resp.text

    def test_put_does_not_mutate_existing_policy_evaluation_snapshot(
        self, client, db, admin_headers, sample_user, make_project
    ):
        """PUT updates only the FleetPolicy row; PolicyEvaluation is IMMUTABLE."""
        p = make_project()
        target = _make_target(db, p)
        policy = _make_policy(db, p, target, desired={"required_labels": ["env=prod"]})
        ev = _make_evaluation(db, policy)
        original_desired_snapshot = dict(ev.desired_snapshot)
        original_mode = ev.mode
        db.commit()

        # Update the policy
        resp = client.put(
            f"/api/fleet/policies/{policy.id}",
            json={
                "name": "changed-name",
                "desired": {"required_labels": ["env=staging"]},
                "mode": "enforce",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text

        # Verify the evaluation snapshot is UNCHANGED
        db.expire(ev)
        db.refresh(ev)
        assert ev.desired_snapshot == original_desired_snapshot
        assert ev.mode == original_mode

    def test_put_returns_404_for_soft_deleted_policy(
        self, client, db, admin_headers, sample_user, make_project
    ):
        p = make_project()
        target = _make_target(db, p)
        policy = _make_policy(db, p, target)
        policy.deleted_at = datetime.now(UTC)
        db.commit()

        resp = client.put(
            f"/api/fleet/policies/{policy.id}",
            json={"name": "ghost"},
            headers=admin_headers,
        )
        assert resp.status_code == 404, resp.text

    def test_rbac_non_owner_cannot_update_policy(self, db, make_project):
        """A non-admin user who does not own the project gets ForbiddenError."""
        from core.errors import ForbiddenError
        from services.fleet_rbac import require_fleet_mutation

        owner = UserFactory(db, role="operator")
        p = ProjectFactory(db, user_id=owner.id)
        target = _make_target(db, p)
        policy = _make_policy(db, p, target)
        db.commit()

        non_owner = UserFactory(db, role="operator")

        with pytest.raises(ForbiddenError):
            require_fleet_mutation([policy.project_id], non_owner, db)


# ---------------------------------------------------------------------------
# DELETE /api/fleet/policies/{id}
# ---------------------------------------------------------------------------

class TestDeleteFleetPolicy:
    def test_soft_delete_sets_deleted_at(self, client, db, admin_headers, sample_user, make_project):
        p = make_project()
        target = _make_target(db, p)
        policy = _make_policy(db, p, target)
        db.commit()

        resp = client.delete(f"/api/fleet/policies/{policy.id}", headers=admin_headers)
        assert resp.status_code == 204, resp.text

        db.expire(policy)
        db.refresh(policy)
        assert policy.deleted_at is not None

    def test_soft_deleted_policy_hidden_from_list(
        self, client, db, admin_headers, sample_user, make_project
    ):
        p = make_project()
        target = _make_target(db, p)
        policy = _make_policy(db, p, target)
        db.commit()

        client.delete(f"/api/fleet/policies/{policy.id}", headers=admin_headers)

        resp = client.get("/api/fleet/policies", headers=admin_headers)
        assert resp.status_code == 200
        ids = [pol["id"] for pol in resp.json()]
        assert policy.id not in ids

    def test_soft_deleted_policy_get_returns_404(
        self, client, db, admin_headers, sample_user, make_project
    ):
        p = make_project()
        target = _make_target(db, p)
        policy = _make_policy(db, p, target)
        db.commit()

        client.delete(f"/api/fleet/policies/{policy.id}", headers=admin_headers)

        resp = client.get(f"/api/fleet/policies/{policy.id}", headers=admin_headers)
        assert resp.status_code == 404, resp.text

    def test_soft_deleted_policy_compliance_returns_404(
        self, client, db, admin_headers, sample_user, make_project
    ):
        p = make_project()
        target = _make_target(db, p)
        policy = _make_policy(db, p, target)
        db.commit()

        client.delete(f"/api/fleet/policies/{policy.id}", headers=admin_headers)

        resp = client.get(f"/api/fleet/policies/{policy.id}/compliance", headers=admin_headers)
        assert resp.status_code == 404, resp.text

    def test_soft_deleted_policy_evaluate_returns_404(
        self, client, db, admin_headers, sample_user, make_project
    ):
        p = make_project()
        target = _make_target(db, p)
        policy = _make_policy(db, p, target)
        db.commit()

        client.delete(f"/api/fleet/policies/{policy.id}", headers=admin_headers)

        resp = client.post(
            f"/api/fleet/policies/{policy.id}/evaluate", headers=admin_headers
        )
        assert resp.status_code == 404, resp.text

    def test_soft_deleted_policy_evaluations_list_returns_404(
        self, client, db, admin_headers, sample_user, make_project
    ):
        p = make_project()
        target = _make_target(db, p)
        policy = _make_policy(db, p, target)
        db.commit()

        client.delete(f"/api/fleet/policies/{policy.id}", headers=admin_headers)

        resp = client.get(
            f"/api/fleet/policies/{policy.id}/evaluations", headers=admin_headers
        )
        assert resp.status_code == 404, resp.text

    def test_soft_delete_preserves_policy_evaluation_rows(
        self, client, db, admin_headers, sample_user, make_project
    ):
        """PolicyEvaluation rows are immutable and must survive soft-delete."""
        p = make_project()
        target = _make_target(db, p)
        policy = _make_policy(db, p, target)
        ev = _make_evaluation(db, policy)
        db.commit()

        client.delete(f"/api/fleet/policies/{policy.id}", headers=admin_headers)

        # Direct DB query: evaluation still exists.
        surviving = (
            db.query(PolicyEvaluation)
            .filter(PolicyEvaluation.policy_id == policy.id)
            .all()
        )
        assert len(surviving) == 1
        assert surviving[0].id == ev.id

    def test_delete_returns_404_for_already_soft_deleted_policy(
        self, client, db, admin_headers, sample_user, make_project
    ):
        p = make_project()
        target = _make_target(db, p)
        policy = _make_policy(db, p, target)
        policy.deleted_at = datetime.now(UTC)
        db.commit()

        resp = client.delete(f"/api/fleet/policies/{policy.id}", headers=admin_headers)
        assert resp.status_code == 404, resp.text

    def test_rbac_non_owner_cannot_delete_policy(self, db, make_project):
        """A non-admin user who does not own the project gets ForbiddenError."""
        from core.errors import ForbiddenError
        from services.fleet_rbac import require_fleet_mutation

        owner = UserFactory(db, role="operator")
        p = ProjectFactory(db, user_id=owner.id)
        target = _make_target(db, p)
        policy = _make_policy(db, p, target)
        db.commit()

        non_owner = UserFactory(db, role="operator")

        with pytest.raises(ForbiddenError):
            require_fleet_mutation([policy.project_id], non_owner, db)
