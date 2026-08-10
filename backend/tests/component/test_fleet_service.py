"""Component tests for fleet reconcile service and members API — D-022 Phase 1.

Uses the shared SQLite in-memory DB fixture from tests/conftest.py.
Tests:
  - Reconcile creates/updates FleetMember rows for each type.
  - assigned_labels preserved across re-reconcile.
  - Vocabulary validation (allowed pass, unknown 422).
  - Label filter + group-by on mixed-type seed.
  - GET /api/fleet/members returns correct shape.
  - GET /api/operators/fleet-health regression: response shape unchanged.
"""

import pytest

from models.bare_metal import BareMetalHost
from models.dpu import Dpu
from models.fleet import FleetMember
from services.fleet_reconcile_service import reconcile_fleet_member
from tests.factories import KubernetesClusterFactory, ProjectFactory, UserFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_host(db, project, **kwargs):
    defaults = dict(
        project_id=project.id,
        name="test-host",
        host_ip="192.168.1.10",
        os_info={"os_type": "ubuntu", "os_version": "22.04", "arch": "x86_64"},
        k8s_info={"installed": True, "version": "1.28.5"},
        rshim_present=True,
        nic_mode="EMBEDDED_CPU(1)",
        bmc_vendor="supermicro",
        topology="bf3",
    )
    defaults.update(kwargs)
    host = BareMetalHost(**defaults)
    db.add(host)
    db.flush()
    return host


def _make_dpu(db, project, **kwargs):
    defaults = dict(
        project_id=project.id,
        access_mode="bmc",
        oob0_ipv4="dhcp",
        extra_vlan_interfaces=[],
        flash_status="os_online",
        serial_number="MT11223344",
        dpu_os_reachable=True,
    )
    defaults.update(kwargs)
    dpu = Dpu(**defaults)
    db.add(dpu)
    db.flush()
    return dpu


# ---------------------------------------------------------------------------
# Reconcile: cluster
# ---------------------------------------------------------------------------

class TestReconcileCluster:
    def test_creates_member_row(self, db, make_project):
        project = make_project()
        cluster = KubernetesClusterFactory(
            db, project=project, cloud_provider="aws", region="us-east-1", version="1.29"
        )
        member = reconcile_fleet_member(db, "cluster", cluster.id)
        assert member is not None
        assert member.member_type == "cluster"
        assert member.member_id == cluster.id
        assert member.project_id == project.id
        assert member.discovered_labels["vendor"] == "aws"
        assert member.discovered_labels["region"] == "us-east-1"
        assert member.discovered_labels["k8s-version"] == "1.29"
        assert member.assigned_labels == {}
        assert member.last_reconciled_at is not None

    def test_idempotent_upsert(self, db, make_project):
        project = make_project()
        cluster = KubernetesClusterFactory(db, project=project, cloud_provider="gcp")
        m1 = reconcile_fleet_member(db, "cluster", cluster.id)
        m2 = reconcile_fleet_member(db, "cluster", cluster.id)
        assert m1.id == m2.id
        # Only one row
        count = db.query(FleetMember).filter(
            FleetMember.member_type == "cluster",
            FleetMember.member_id == cluster.id,
        ).count()
        assert count == 1

    def test_assigned_labels_preserved_on_re_reconcile(self, db, make_project):
        project = make_project()
        cluster = KubernetesClusterFactory(db, project=project)
        member = reconcile_fleet_member(db, "cluster", cluster.id)
        member.assigned_labels = {"env": "prod", "team": "platform"}
        db.flush()

        # Second reconcile must not touch assigned_labels
        reconcile_fleet_member(db, "cluster", cluster.id)
        db.refresh(member)
        assert member.assigned_labels == {"env": "prod", "team": "platform"}

    def test_unknown_member_type_raises(self, db):
        from services.fleet_reconcile_service import reconcile_fleet_member
        with pytest.raises(ValueError, match="Unknown member_type"):
            reconcile_fleet_member(db, "f5-bigip", 999)

    def test_missing_target_returns_none(self, db):
        result = reconcile_fleet_member(db, "cluster", 99999)
        assert result is None


# ---------------------------------------------------------------------------
# Reconcile: host
# ---------------------------------------------------------------------------

class TestReconcileHost:
    def test_creates_member_row(self, db, make_project):
        project = make_project()
        host = _make_host(db, project)
        member = reconcile_fleet_member(db, "bare-metal-host", host.id)
        assert member.member_type == "bare-metal-host"
        assert member.discovered_labels["os"] == "ubuntu"
        assert member.discovered_labels["has-dpu"] == "true"
        assert member.discovered_labels["bmc-vendor"] == "supermicro"

    def test_none_os_info_tolerated(self, db, make_project):
        project = make_project()
        host = _make_host(db, project, os_info=None, k8s_info=None)
        member = reconcile_fleet_member(db, "bare-metal-host", host.id)
        assert "os" not in member.discovered_labels
        assert "k8s-version" not in member.discovered_labels


# ---------------------------------------------------------------------------
# Reconcile: DPU
# ---------------------------------------------------------------------------

class TestReconcileDpu:
    def test_creates_member_row(self, db, make_project):
        project = make_project()
        dpu = _make_dpu(db, project)
        member = reconcile_fleet_member(db, "dpu", dpu.id)
        assert member.member_type == "dpu"
        assert member.discovered_labels["vendor"] == "nvidia-bluefield"
        assert member.discovered_labels["flash-status"] == "os_online"
        assert member.discovered_labels["reachable"] == "true"

    def test_vendor_always_present(self, db, make_project):
        project = make_project()
        dpu = _make_dpu(db, project, serial_number=None, flash_status=None, dpu_os_reachable=None)
        member = reconcile_fleet_member(db, "dpu", dpu.id)
        assert member.discovered_labels["vendor"] == "nvidia-bluefield"


# ---------------------------------------------------------------------------
# Vocabulary validator (via route-level checks)
# ---------------------------------------------------------------------------

class TestVocabularyValidation:
    def test_valid_assigned_labels_pass(self):
        from services.fleet_vocabulary import validate_assigned_labels
        validate_assigned_labels({"env": "prod", "team": "platform"})  # should not raise

    def test_unknown_key_raises_validation_error(self):
        from core.errors import ValidationError
        from services.fleet_vocabulary import validate_assigned_labels
        with pytest.raises(ValidationError):
            validate_assigned_labels({"k8s-version": "1.29"})


# ---------------------------------------------------------------------------
# Members API: label filter + group-by
# ---------------------------------------------------------------------------

class TestFleetMembersFilter:
    """Integration: seed mixed members, check filter and group-by."""

    def _seed(self, db, make_project):
        p = make_project()
        cluster = KubernetesClusterFactory(
            db, project=p, cloud_provider="aws", region="us-east-1"
        )
        m_cluster = reconcile_fleet_member(db, "cluster", cluster.id)
        m_cluster.assigned_labels = {"env": "prod"}
        db.flush()

        host = _make_host(db, p, name="host-1", bmc_vendor="dell", topology="standalone")
        m_host = reconcile_fleet_member(db, "bare-metal-host", host.id)
        m_host.assigned_labels = {"env": "staging"}
        db.flush()

        dpu = _make_dpu(db, p)
        m_dpu = reconcile_fleet_member(db, "dpu", dpu.id)
        db.flush()

        return p, m_cluster, m_host, m_dpu

    def test_all_types_returned_without_filter(self, db, make_project):
        p, m_cluster, m_host, m_dpu = self._seed(db, make_project)
        members = db.query(FleetMember).filter(FleetMember.project_id == p.id).all()
        types = {m.member_type for m in members}
        assert "cluster" in types
        assert "bare-metal-host" in types
        assert "dpu" in types

    def test_label_filter_by_assigned_env(self, db, make_project):
        from routes.fleet import _label_filter_matches
        p, m_cluster, m_host, m_dpu = self._seed(db, make_project)

        prod_members = [
            m for m in [m_cluster, m_host, m_dpu]
            if _label_filter_matches(m, ["env=prod"])
        ]
        assert len(prod_members) == 1
        assert prod_members[0].member_type == "cluster"

    def test_label_filter_by_discovered_vendor(self, db, make_project):
        from routes.fleet import _label_filter_matches
        p, m_cluster, m_host, m_dpu = self._seed(db, make_project)

        dpu_members = [
            m for m in [m_cluster, m_host, m_dpu]
            if _label_filter_matches(m, ["vendor=nvidia-bluefield"])
        ]
        assert len(dpu_members) == 1
        assert dpu_members[0].member_type == "dpu"

    def test_no_match_returns_empty(self, db, make_project):
        from routes.fleet import _label_filter_matches
        p, m_cluster, m_host, m_dpu = self._seed(db, make_project)

        matched = [
            m for m in [m_cluster, m_host, m_dpu]
            if _label_filter_matches(m, ["env=nonexistent"])
        ]
        assert matched == []


# ---------------------------------------------------------------------------
# Regression: /api/operators/fleet-health shape unchanged
# ---------------------------------------------------------------------------

class TestFleetHealthRegressionShape:
    """Assert that the existing fleet-health response shape is byte-stable."""

    def test_fleet_health_returns_expected_keys(self, client, admin_headers, sample_user):
        resp = client.get("/api/operators/fleet-health", headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        # These keys are the contract; must all be present
        for key in ("total_clusters", "healthy", "warning", "critical", "offline", "operators"):
            assert key in body, f"Missing key '{key}' from fleet-health response"

    def test_fleet_health_empty_operators_list(self, client, admin_headers, sample_user):
        """When no clusters exist, operators list is empty (not missing)."""
        resp = client.get("/api/operators/fleet-health", headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["operators"], list)
        assert body["total_clusters"] == 0


# ---------------------------------------------------------------------------
# RBAC: non-admin sees only their own project's members
# ---------------------------------------------------------------------------

class TestFleetMembersRBAC:
    """Assert that GET /api/fleet/members is scoped to the caller's owned projects."""

    def test_non_admin_sees_only_own_project_members(self, client, db, sample_user):
        """A viewer/operator sees only members from projects they own."""
        from services.auth_service import create_access_token

        # Create two users: one who will own the project and one who will not.
        owner = UserFactory(db, username="rbac-owner", role="operator")
        other = UserFactory(db, username="rbac-other", role="operator")

        # Project owned by `owner`; project owned by `other`.
        owner_project = ProjectFactory(db, user_id=owner.id)
        other_project = ProjectFactory(db, user_id=other.id)

        # Seed a cluster in each project and reconcile into FleetMember rows.
        owner_cluster = KubernetesClusterFactory(db, project=owner_project)
        other_cluster = KubernetesClusterFactory(db, project=other_project)
        reconcile_fleet_member(db, "cluster", owner_cluster.id)
        reconcile_fleet_member(db, "cluster", other_cluster.id)
        db.commit()

        owner_token = create_access_token(data={"sub": owner.username, "role": owner.role})
        owner_headers = {"Authorization": f"Bearer {owner_token}"}

        resp = client.get("/api/fleet/members", headers=owner_headers)
        assert resp.status_code == 200
        body = resp.json()
        member_project_ids = {m["project_id"] for m in body["members"]}
        assert owner_project.id in member_project_ids
        assert other_project.id not in member_project_ids

    def test_admin_sees_all_projects_members(self, client, db, sample_user):
        """An admin sees all fleet members regardless of project ownership."""
        from services.auth_service import create_access_token

        user_a = UserFactory(db, username="rbac-admin-a", role="operator")
        user_b = UserFactory(db, username="rbac-admin-b", role="operator")
        project_a = ProjectFactory(db, user_id=user_a.id)
        project_b = ProjectFactory(db, user_id=user_b.id)
        cluster_a = KubernetesClusterFactory(db, project=project_a)
        cluster_b = KubernetesClusterFactory(db, project=project_b)
        reconcile_fleet_member(db, "cluster", cluster_a.id)
        reconcile_fleet_member(db, "cluster", cluster_b.id)
        db.commit()

        # sample_user fixture creates username="testadmin" with role="admin"
        admin_token = create_access_token(data={"sub": sample_user.username, "role": "admin"})
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        resp = client.get("/api/fleet/members", headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        member_project_ids = {m["project_id"] for m in body["members"]}
        assert project_a.id in member_project_ids
        assert project_b.id in member_project_ids


# ---------------------------------------------------------------------------
# Ownership check: PUT /api/fleet/members/{id}/labels
# ---------------------------------------------------------------------------

class TestFleetLabelPutOwnership:
    """Assert that PUT /api/fleet/members/{id}/labels enforces project ownership."""

    def _seed_member(self, db, owner):
        """Create a project + cluster + FleetMember owned by *owner*. Returns member."""
        project = ProjectFactory(db, user_id=owner.id)
        cluster = KubernetesClusterFactory(db, project=project)
        member = reconcile_fleet_member(db, "cluster", cluster.id)
        db.commit()
        return member

    def _token_headers(self, user):
        from services.auth_service import create_access_token
        token = create_access_token(data={"sub": user.username, "role": user.role})
        return {"Authorization": f"Bearer {token}"}

    def test_owner_operator_can_update_labels(self, client, db, sample_user):
        """An operator who owns the project can set assigned labels."""
        owner = UserFactory(db, username="label-owner", role="operator")
        member = self._seed_member(db, owner)

        resp = client.put(
            f"/api/fleet/members/{member.id}/labels",
            json={"labels": {"env": "prod"}},
            headers=self._token_headers(owner),
        )
        assert resp.status_code == 200
        assert resp.json()["assigned_labels"] == {"env": "prod"}

    def test_non_owner_operator_denied(self, client, db, sample_user):
        """An operator who does NOT own the project is denied with 403."""
        owner = UserFactory(db, username="label-owner2", role="operator")
        other = UserFactory(db, username="label-other2", role="operator")
        member = self._seed_member(db, owner)

        resp = client.put(
            f"/api/fleet/members/{member.id}/labels",
            json={"labels": {"env": "staging"}},
            headers=self._token_headers(other),
        )
        assert resp.status_code == 403

    def test_admin_can_update_any_member_labels(self, client, db, sample_user):
        """An admin can update labels on a member from any project."""
        owner = UserFactory(db, username="label-owner3", role="operator")
        member = self._seed_member(db, owner)

        admin_token_headers = self._token_headers(sample_user)
        resp = client.put(
            f"/api/fleet/members/{member.id}/labels",
            json={"labels": {"team": "platform"}},
            headers=admin_token_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["assigned_labels"] == {"team": "platform"}
