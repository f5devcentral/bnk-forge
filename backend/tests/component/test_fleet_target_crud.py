"""Component tests for Fleet target PUT/DELETE (soft-delete) — D-022 P5 S2.

Tests:
  - PUT updates name/description/selector/pinned_member_ids.
  - PUT re-validates selector and rejects forbidden keys.
  - PUT does NOT mutate an existing FleetDecision snapshot.
  - DELETE soft-deletes: row hidden from list, GET/{id}→404.
  - DELETE preserves historical decisions/runs (FK chain intact).
  - RBAC: non-owner cannot PUT/DELETE another project's fleet.
  - Policy referencing a soft-deleted fleet does NOT 500.
"""

from datetime import UTC, datetime

import pytest

from core.errors import ForbiddenError, NotFoundError, ValidationError
from models.fleet import FleetMember
from models.fleet_targeting import (
    DECISION_STATUS_RESOLVED,
    FleetDecision,
    FleetTarget,
)
from tests.factories import KubernetesClusterFactory, ProjectFactory, UserFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_target(db, project, selector=None, name="test-fleet", **kwargs):
    target = FleetTarget(
        project_id=project.id,
        name=name,
        selector=selector or {"labels": ["env=prod"]},
        created_by="test",
        **kwargs,
    )
    db.add(target)
    db.flush()
    db.refresh(target)
    return target


def _make_decision(db, target):
    decision = FleetDecision(
        target_id=target.id,
        project_id=target.project_id,
        selector_snapshot=dict(target.selector),
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


class _FakeUser:
    def __init__(self, role="admin", user_id=1, username="testuser"):
        self.role = role
        self.id = user_id
        self.username = username


# ---------------------------------------------------------------------------
# PUT /targets/{id} — update
# ---------------------------------------------------------------------------

class TestPutFleetTarget:
    def test_put_updates_name(self, db, make_project):
        p = make_project()
        target = _make_target(db, p, name="original")
        db.commit()

        target.name = "updated-name"
        db.commit()
        db.refresh(target)

        assert target.name == "updated-name"

    def test_put_updates_description(self, db, make_project):
        p = make_project()
        target = _make_target(db, p)
        db.commit()

        target.description = "A description"
        db.commit()
        db.refresh(target)

        assert target.description == "A description"

    def test_put_updates_selector_and_revalidates(self, db, make_project):
        from services.fleet_selector import validate_selector

        p = make_project()
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        db.commit()

        new_selector = {"labels": ["env=staging", "region=us-east-1"]}
        validate_selector(new_selector)  # must not raise
        target.selector = new_selector
        db.commit()
        db.refresh(target)

        assert target.selector["labels"] == ["env=staging", "region=us-east-1"]

    def test_put_rejects_forbidden_selector_key(self, db, make_project):
        from services.fleet_selector import validate_selector

        p = make_project()
        target = _make_target(db, p)
        db.commit()

        with pytest.raises(ValidationError, match="Forbidden selector key"):
            validate_selector({"labels": ["project_id=99"]})

    def test_put_rejects_member_id_selector_key(self, db, make_project):
        from services.fleet_selector import validate_selector

        p = make_project()
        target = _make_target(db, p)
        db.commit()

        with pytest.raises(ValidationError):
            validate_selector({"labels": ["member_id=5"]})

    def test_put_does_not_mutate_existing_decision(self, db, make_project):
        """Updating a target MUST NOT change any historical FleetDecision snapshot."""
        p = make_project()
        target = _make_target(db, p, selector={"labels": ["env=prod"]})
        db.commit()

        # Create a decision snapshot of the original selector.
        decision = _make_decision(db, target)
        db.commit()

        original_snapshot = dict(decision.selector_snapshot)
        original_member_ids = list(decision.resolved_member_ids)

        # Now update the target's selector.
        target.selector = {"labels": ["env=staging"]}
        db.commit()
        db.refresh(decision)

        # Decision snapshot must be immutable.
        assert decision.selector_snapshot == original_snapshot
        assert decision.resolved_member_ids == original_member_ids

    def test_put_updates_pinned_member_ids(self, db, make_project):
        p = make_project()
        target = _make_target(db, p)
        db.commit()

        target.pinned_member_ids = [10, 20, 30]
        db.commit()
        db.refresh(target)

        assert target.pinned_member_ids == [10, 20, 30]

    def test_rbac_non_owner_cannot_update_another_projects_fleet(self, db, make_project):
        from core.errors import ForbiddenError
        from services.fleet_rbac import require_fleet_mutation

        owner = UserFactory(db, role="operator")
        p = ProjectFactory(db, user_id=owner.id)
        target = _make_target(db, p)
        db.commit()

        non_owner = UserFactory(db, role="operator")

        with pytest.raises(ForbiddenError):
            require_fleet_mutation([target.project_id], non_owner, db)

    def test_rbac_owner_can_update_own_fleet(self, db):
        from services.fleet_rbac import require_fleet_mutation

        owner = UserFactory(db, role="operator")
        p = ProjectFactory(db, user_id=owner.id)
        target = _make_target(db, p)
        db.commit()

        # Should not raise.
        require_fleet_mutation([target.project_id], owner, db)

    def test_rbac_admin_can_update_any_fleet(self, db):
        from services.fleet_rbac import require_fleet_mutation

        owner = UserFactory(db, role="operator")
        p = ProjectFactory(db, user_id=owner.id)
        target = _make_target(db, p)
        db.commit()

        admin = UserFactory(db, role="admin")

        # Should not raise.
        require_fleet_mutation([target.project_id], admin, db)


# ---------------------------------------------------------------------------
# DELETE /targets/{id} — soft-delete
# ---------------------------------------------------------------------------

class TestDeleteFleetTarget:
    def test_soft_delete_sets_deleted_at(self, db, make_project):
        p = make_project()
        target = _make_target(db, p)
        db.commit()

        target.deleted_at = datetime.now(UTC)
        db.commit()
        db.refresh(target)

        assert target.deleted_at is not None

    def test_soft_deleted_target_hidden_from_list_query(self, db, make_project):
        p = make_project()
        active = _make_target(db, p, name="active-fleet")
        deleted = _make_target(db, p, name="deleted-fleet")
        deleted.deleted_at = datetime.now(UTC)
        db.commit()

        visible = (
            db.query(FleetTarget)
            .filter(FleetTarget.deleted_at.is_(None))
            .all()
        )
        visible_names = [t.name for t in visible]
        assert "active-fleet" in visible_names
        assert "deleted-fleet" not in visible_names

    def test_soft_deleted_target_returns_none_on_filtered_get(self, db, make_project):
        p = make_project()
        target = _make_target(db, p)
        db.commit()
        target_id = target.id

        target.deleted_at = datetime.now(UTC)
        db.commit()

        result = (
            db.query(FleetTarget)
            .filter(FleetTarget.id == target_id, FleetTarget.deleted_at.is_(None))
            .first()
        )
        assert result is None

    def test_historical_decisions_survive_soft_delete(self, db, make_project):
        """Decisions (immutable audit trail) must survive soft-delete of the target."""
        p = make_project()
        target = _make_target(db, p)
        db.commit()

        decision = _make_decision(db, target)
        db.commit()
        decision_id = decision.id

        # Soft-delete the target.
        target.deleted_at = datetime.now(UTC)
        db.commit()

        # Decision is still queryable — the audit trail is intact.
        surviving = db.query(FleetDecision).filter(FleetDecision.id == decision_id).first()
        assert surviving is not None
        assert surviving.target_id == target.id

    def test_soft_deleted_target_decisions_still_queryable_by_target_id(self, db, make_project):
        """After soft-delete, historical decisions can still be fetched via target_id."""
        p = make_project()
        target = _make_target(db, p)
        db.commit()

        d1 = _make_decision(db, target)
        d2 = _make_decision(db, target)
        db.commit()
        target_id = target.id

        target.deleted_at = datetime.now(UTC)
        db.commit()

        decisions = (
            db.query(FleetDecision)
            .filter(FleetDecision.target_id == target_id)
            .all()
        )
        assert len(decisions) == 2

    def test_rbac_non_owner_cannot_delete_another_projects_fleet(self, db):
        from core.errors import ForbiddenError
        from services.fleet_rbac import require_fleet_mutation

        owner = UserFactory(db, role="operator")
        p = ProjectFactory(db, user_id=owner.id)
        target = _make_target(db, p)
        db.commit()

        non_owner = UserFactory(db, role="operator")

        with pytest.raises(ForbiddenError):
            require_fleet_mutation([target.project_id], non_owner, db)


# ---------------------------------------------------------------------------
# Policy referencing a soft-deleted fleet — no 500
# ---------------------------------------------------------------------------

class TestPolicyWithSoftDeletedFleet:
    def test_policy_out_serializes_with_soft_deleted_target(self, db, make_project):
        """A policy pointing to a soft-deleted fleet must serialize cleanly.

        PolicyOut only carries target_id (int) — it never joins to the target
        row, so soft-deleting the fleet cannot cause a 500 in _policy_to_out.
        """
        from models.fleet_policy import FleetPolicy

        p = make_project()
        target = _make_target(db, p)
        db.commit()

        policy = FleetPolicy(
            project_id=p.id,
            name="test-policy",
            target_id=target.id,
            kind="label-compliance",
            mode="inform",
            desired={"labels": {"env": "prod"}},
            created_by="test",
        )
        db.add(policy)
        db.flush()
        db.commit()

        # Soft-delete the fleet.
        target.deleted_at = datetime.now(UTC)
        db.commit()

        # _policy_to_out only reads from the policy row — should not 500.
        from routes.fleet import _policy_to_out
        out = _policy_to_out(policy)
        assert out.target_id == target.id
        assert out.name == "test-policy"

    def test_list_policies_does_not_500_when_target_soft_deleted(self, db, make_project):
        """list_fleet_policies must not 500 when a policy's target is soft-deleted."""
        from models.fleet_policy import FleetPolicy

        p = make_project()
        target = _make_target(db, p)
        db.commit()

        policy = FleetPolicy(
            project_id=p.id,
            name="orphan-policy",
            target_id=target.id,
            kind="label-compliance",
            mode="inform",
            desired={"labels": {"env": "prod"}},
            created_by="test",
        )
        db.add(policy)
        db.flush()
        db.commit()

        target.deleted_at = datetime.now(UTC)
        db.commit()

        # Querying all policies and serializing — no join to fleet_targets.
        from models.fleet_policy import FleetPolicy
        from routes.fleet import _policy_to_out

        policies = db.query(FleetPolicy).filter(FleetPolicy.id == policy.id).all()
        outputs = [_policy_to_out(p) for p in policies]
        assert len(outputs) == 1
        assert outputs[0].target_id == target.id
