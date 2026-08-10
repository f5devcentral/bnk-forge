"""Component tests for fleet RBAC + rollout-defaults — D-022 Phase 4.

Tests:
  - require_fleet_mutation: owner allowed, non-owner 403, admin bypass,
    null-project allowed (legacy), empty / all-None allowed.
  - resolve_rollout_defaults: override wins, Project-default used, system
    literal when NULL, conflicting projects → system literal.
  - Reconcile fold-in: lifecycle_state set on create AND update;
    assigned_labels never overwritten.
"""

import pytest

from core.errors import ForbiddenError
from models.bare_metal import BareMetalHost
from models.dpu import Dpu
from models.fleet import (
    LIFECYCLE_STATE_ACTIVE,
    LIFECYCLE_STATE_PROVISIONING,
    LIFECYCLE_STATE_UNKNOWN,
    FleetMember,
)
from models.fleet_targeting import GATE_MODE_MANUAL
from services.fleet_rbac import require_fleet_mutation, resolve_rollout_defaults
from services.fleet_reconcile_service import reconcile_fleet_member
from tests.factories import KubernetesClusterFactory, ProjectFactory, UserFactory

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def project_owner(db):
    owner = UserFactory(db, role="operator")
    project = ProjectFactory(db, user_id=owner.id)
    return owner, project


@pytest.fixture
def non_owner(db):
    return UserFactory(db, role="operator")


@pytest.fixture
def admin_user(db):
    return UserFactory(db, role="admin")


# ---------------------------------------------------------------------------
# require_fleet_mutation: RBAC gate
# ---------------------------------------------------------------------------

class TestRequireFleetMutation:
    def test_owner_is_allowed(self, db, project_owner):
        owner, project = project_owner
        # Should not raise.
        require_fleet_mutation([project.id], owner, db)

    def test_non_owner_gets_403(self, db, project_owner, non_owner):
        _, project = project_owner
        with pytest.raises(ForbiddenError):
            require_fleet_mutation([project.id], non_owner, db)

    def test_admin_bypasses_ownership(self, db, project_owner, admin_user):
        _, project = project_owner
        # Should not raise.
        require_fleet_mutation([project.id], admin_user, db)

    def test_null_project_id_allowed(self, db, non_owner):
        # None project_id → skip (legacy global member). Should not raise.
        require_fleet_mutation([None], non_owner, db)

    def test_empty_list_allowed(self, db, non_owner):
        # Empty project_ids → nothing to check. Should not raise.
        require_fleet_mutation([], non_owner, db)

    def test_all_none_list_allowed(self, db, non_owner):
        # All None → effectively empty. Should not raise.
        require_fleet_mutation([None, None], non_owner, db)

    def test_missing_project_skipped(self, db, non_owner):
        # project_id that doesn't exist → treat as null-owner (skip). Should not raise.
        require_fleet_mutation([99999], non_owner, db)

    def test_multi_project_all_owned_allowed(self, db, admin_user):
        p1 = ProjectFactory(db, user_id=admin_user.id)
        p2 = ProjectFactory(db, user_id=admin_user.id)
        # admin user owns both. Should not raise.
        require_fleet_mutation([p1.id, p2.id], admin_user, db)

    def test_multi_project_one_unowned_raises(self, db, non_owner):
        owner = UserFactory(db, role="operator")
        owned_project = ProjectFactory(db, user_id=owner.id)
        unowned_project = ProjectFactory(db, user_id=UserFactory(db, role="operator").id)
        # non_owner doesn't own unowned_project.
        with pytest.raises(ForbiddenError):
            require_fleet_mutation([owned_project.id, unowned_project.id], non_owner, db)

    def test_deduplication_does_not_double_check(self, db, project_owner):
        owner, project = project_owner
        # Duplicate project_id in list should not cause issues.
        require_fleet_mutation([project.id, project.id], owner, db)


# ---------------------------------------------------------------------------
# resolve_rollout_defaults: default inheritance
# ---------------------------------------------------------------------------

class TestResolveRolloutDefaults:
    def test_system_literal_when_no_projects(self, db):
        concurrency, gate_mode = resolve_rollout_defaults(db, [])
        assert concurrency == 5
        assert gate_mode == GATE_MODE_MANUAL

    def test_system_literal_when_all_null_project_ids(self, db):
        concurrency, gate_mode = resolve_rollout_defaults(db, [None, None])
        assert concurrency == 5
        assert gate_mode == GATE_MODE_MANUAL

    def test_system_literal_when_no_defaults_set(self, db):
        p = ProjectFactory(db)  # fleet_default_* = NULL
        concurrency, gate_mode = resolve_rollout_defaults(db, [p.id])
        assert concurrency == 5
        assert gate_mode == GATE_MODE_MANUAL

    def test_project_default_concurrency_used(self, db):
        p = ProjectFactory(db, fleet_default_concurrency=10)
        concurrency, gate_mode = resolve_rollout_defaults(db, [p.id])
        assert concurrency == 10
        assert gate_mode == GATE_MODE_MANUAL  # not set, falls to literal

    def test_project_default_gate_mode_used(self, db):
        p = ProjectFactory(db, fleet_default_gate_mode="health_stable")
        concurrency, gate_mode = resolve_rollout_defaults(db, [p.id])
        assert concurrency == 5  # not set, falls to literal
        assert gate_mode == "health_stable"

    def test_both_project_defaults_used(self, db):
        p = ProjectFactory(db, fleet_default_concurrency=20, fleet_default_gate_mode="health_stable")
        concurrency, gate_mode = resolve_rollout_defaults(db, [p.id])
        assert concurrency == 20
        assert gate_mode == "health_stable"

    def test_conflicting_concurrency_across_projects_falls_to_literal(self, db):
        p1 = ProjectFactory(db, fleet_default_concurrency=10)
        p2 = ProjectFactory(db, fleet_default_concurrency=20)
        concurrency, _ = resolve_rollout_defaults(db, [p1.id, p2.id])
        assert concurrency == 5  # conflict → system literal

    def test_conflicting_gate_mode_across_projects_falls_to_literal(self, db):
        p1 = ProjectFactory(db, fleet_default_gate_mode="health_stable")
        p2 = ProjectFactory(db, fleet_default_gate_mode="manual")
        _, gate_mode = resolve_rollout_defaults(db, [p1.id, p2.id])
        assert gate_mode == GATE_MODE_MANUAL  # conflict → system literal

    def test_agreeing_projects_use_shared_value(self, db):
        # Two projects with same default → use it (no conflict).
        p1 = ProjectFactory(db, fleet_default_concurrency=15)
        p2 = ProjectFactory(db, fleet_default_concurrency=15)
        concurrency, _ = resolve_rollout_defaults(db, [p1.id, p2.id])
        assert concurrency == 15

    def test_none_project_id_skipped_in_mixed_list(self, db):
        p = ProjectFactory(db, fleet_default_concurrency=8)
        concurrency, _ = resolve_rollout_defaults(db, [None, p.id])
        assert concurrency == 8  # None skipped, only p contributes

    def test_missing_project_skipped(self, db):
        concurrency, gate_mode = resolve_rollout_defaults(db, [99999])
        assert concurrency == 5
        assert gate_mode == GATE_MODE_MANUAL


# ---------------------------------------------------------------------------
# Reconcile fold-in: lifecycle_state set on create AND update
# ---------------------------------------------------------------------------

class TestReconcileLifecycleFoldIn:
    def test_lifecycle_set_on_create_cluster(self, db):
        project = ProjectFactory(db)
        cluster = KubernetesClusterFactory(db, project=project, status="active")
        member = reconcile_fleet_member(db, "cluster", cluster.id)
        assert member.lifecycle_state == LIFECYCLE_STATE_ACTIVE

    def test_lifecycle_set_on_update_cluster(self, db):
        project = ProjectFactory(db)
        cluster = KubernetesClusterFactory(db, project=project, status="active")
        # Create
        reconcile_fleet_member(db, "cluster", cluster.id)
        # Update: cluster status changes
        cluster.status = "connecting"
        db.flush()
        member = reconcile_fleet_member(db, "cluster", cluster.id)
        assert member.lifecycle_state == LIFECYCLE_STATE_PROVISIONING

    def test_lifecycle_set_on_create_dpu(self, db):
        project = ProjectFactory(db)
        dpu = Dpu(
            project_id=project.id, access_mode="bmc", oob0_ipv4="dhcp",
            extra_vlan_interfaces=[], flash_status="uploading",
        )
        db.add(dpu)
        db.flush()
        member = reconcile_fleet_member(db, "dpu", dpu.id)
        assert member.lifecycle_state == LIFECYCLE_STATE_PROVISIONING

    def test_lifecycle_null_for_never_reconciled(self, db):
        project = ProjectFactory(db)
        cluster = KubernetesClusterFactory(db, project=project, status="active")
        # Create member without reconcile.
        fm = FleetMember(
            member_type="cluster",
            member_id=cluster.id,
            project_id=project.id,
            name="test",
            assigned_labels={},
        )
        db.add(fm)
        db.flush()
        # lifecycle_state should be NULL until reconcile runs.
        assert fm.lifecycle_state is None

    def test_lifecycle_unknown_for_error_status_cluster(self, db):
        project = ProjectFactory(db)
        cluster = KubernetesClusterFactory(db, project=project, status="error")
        member = reconcile_fleet_member(db, "cluster", cluster.id)
        assert member.lifecycle_state == LIFECYCLE_STATE_UNKNOWN

    def test_assigned_labels_never_overwritten(self, db):
        project = ProjectFactory(db)
        cluster = KubernetesClusterFactory(db, project=project, status="active")
        member = reconcile_fleet_member(db, "cluster", cluster.id)
        # Assign some labels.
        member.assigned_labels = {"team": "platform"}
        db.flush()
        # Re-reconcile must not touch assigned_labels.
        member2 = reconcile_fleet_member(db, "cluster", cluster.id)
        assert member2.assigned_labels == {"team": "platform"}

    def test_lifecycle_set_on_create_host(self, db):
        project = ProjectFactory(db)
        host = BareMetalHost(
            project_id=project.id,
            name="test-host",
            host_ip="10.0.0.1",
            last_discovery_status="pending",
        )
        db.add(host)
        db.flush()
        member = reconcile_fleet_member(db, "bare-metal-host", host.id)
        assert member.lifecycle_state == LIFECYCLE_STATE_PROVISIONING
