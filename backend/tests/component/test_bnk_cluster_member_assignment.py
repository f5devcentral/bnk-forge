"""Component tests for BNK multi-host cluster member assignment (ADR-424).

Covers the review findings:
  M1 — unique-index backstop: duplicate tmfifo IP raises IntegrityError -> ValidationError
  M2 — cross-project 404: host/DPU from another project cannot be attached
  M3 — destructive-defaults: custom pool CIDR not reset on re-call
  M3 — reconciliation: changing CP host doesn't leave two is_control_plane=True rows

All tests use TestClient (HTTP layer) against the shared SQLite test DB
so they exercise the full request path including auth middleware.
"""

import pytest

from models.bare_metal import BareMetalHost
from models.dpu import Dpu
from models.kubernetes import BnkClusterConfig, KubernetesCluster

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_project(db, name="test-project"):
    from models import Project
    n = _make_project._n = getattr(_make_project, "_n", 0) + 1
    p = Project(
        name=f"{name}-{n}",
        description="test",
        project_type="kubernetes",
        cloud_provider="on-prem",
        environment="dev",
        backend_type="local",
        color="#aabbcc",
        icon="cloud",
        is_active=True,
    )
    db.add(p)
    db.flush()
    return p


def _make_cluster(db, project, name="cluster"):
    _make_cluster._n = getattr(_make_cluster, "_n", 0) + 1
    c = KubernetesCluster(
        name=f"{name}-{_make_cluster._n}",
        context=f"ctx-{_make_cluster._n}",
        project_id=project.id,
        api_server=f"https://k8s-{_make_cluster._n}.example.com:6443",
        status="active",
    )
    db.add(c)
    db.flush()
    return c


def _make_host(db, project, host_ip=None, name=None):
    _make_host._n = getattr(_make_host, "_n", 0) + 1
    n = _make_host._n
    h = BareMetalHost(
        project_id=project.id,
        name=name or f"host-{n}",
        host_ip=host_ip or f"10.0.0.{n}",
    )
    db.add(h)
    db.flush()
    return h


def _make_dpu(db, project, host_node_ip=None, name=None):
    _make_dpu._n = getattr(_make_dpu, "_n", 0) + 1
    n = _make_dpu._n
    d = Dpu(
        project_id=project.id,
        name=name or f"dpu-{n}",
        access_mode="in-band",
        host_node_ip=host_node_ip or f"10.0.0.{n}",
        oob0_ipv4="dhcp",
    )
    db.add(d)
    db.flush()
    return d


# ---------------------------------------------------------------------------
# M1 — unique-index backstop
# ---------------------------------------------------------------------------

class TestIpamUniqueIndexBackstop:
    """Directly inserting a duplicate (cluster_id, dpu_tmfifo_ip) raises an error."""

    def test_duplicate_tmfifo_ip_raises_integrity_error(self, db):
        """Two DPUs with the same dpu_tmfifo_ip in the same cluster violate the index."""
        from sqlalchemy.exc import IntegrityError

        project = _make_project(db, "ipam-test")
        cluster = _make_cluster(db, project)

        dpu1 = _make_dpu(db, project, host_node_ip="10.1.0.1")
        dpu2 = _make_dpu(db, project, host_node_ip="10.1.0.2")

        # Manually assign the same IP to both DPUs — bypasses service-level lock.
        dpu1.kubernetes_cluster_id = cluster.id
        dpu1.dpu_tmfifo_ip = "192.168.100.2"
        dpu1.host_tmfifo_ip = "192.168.100.1"
        db.add(dpu1)
        db.flush()

        dpu2.kubernetes_cluster_id = cluster.id
        dpu2.dpu_tmfifo_ip = "192.168.100.2"  # duplicate
        dpu2.host_tmfifo_ip = "192.168.100.1"
        db.add(dpu2)

        with pytest.raises(IntegrityError):
            db.flush()

    def test_unique_ips_accepted(self, db):
        """Two DPUs with different tmfifo IPs in the same cluster are valid."""
        project = _make_project(db, "ipam-ok")
        cluster = _make_cluster(db, project)

        dpu1 = _make_dpu(db, project, host_node_ip="10.2.0.1")
        dpu2 = _make_dpu(db, project, host_node_ip="10.2.0.2")

        dpu1.kubernetes_cluster_id = cluster.id
        dpu1.dpu_tmfifo_ip = "192.168.100.2"
        dpu1.host_tmfifo_ip = "192.168.100.1"
        db.add(dpu1)
        db.flush()

        dpu2.kubernetes_cluster_id = cluster.id
        dpu2.dpu_tmfifo_ip = "192.168.100.6"  # next /30
        dpu2.host_tmfifo_ip = "192.168.100.5"
        db.add(dpu2)
        db.flush()  # no exception

        db.expire_all()
        assert dpu1.dpu_tmfifo_ip == "192.168.100.2"
        assert dpu2.dpu_tmfifo_ip == "192.168.100.6"


# ---------------------------------------------------------------------------
# M2 — cross-project 404
# ---------------------------------------------------------------------------

class TestAssignMembersCrossProjectGuard:
    """Hosts / DPUs from another project cannot be attached to a cluster."""

    def test_host_from_other_project_returns_404(
        self, client, db, admin_headers, sample_user, make_project, make_k8s_cluster
    ):
        """POST /bnk-members with a host from a different project returns 404."""
        owner_project = make_project(name="owner-project")
        other_project = make_project(name="other-project")
        cluster = make_k8s_cluster(project=owner_project)

        # Host belongs to other_project, not owner_project
        foreign_host = _make_host(db, other_project, host_ip="10.99.0.1")
        db.commit()

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk-members",
            json={
                "control_plane_host_id": foreign_host.id,
                "host_ids": [foreign_host.id],
                "dpu_ids": [],
            },
            headers=admin_headers,
        )
        assert response.status_code == 404, response.text

    def test_dpu_from_other_project_returns_404(
        self, client, db, admin_headers, sample_user, make_project, make_k8s_cluster
    ):
        """POST /bnk-members with a DPU from a different project returns 404."""
        owner_project = make_project(name="owner-proj2")
        other_project = make_project(name="other-proj2")
        cluster = make_k8s_cluster(project=owner_project)

        # Host is in the right project
        host = _make_host(db, owner_project, host_ip="10.50.0.1")
        # DPU is in the wrong project
        foreign_dpu = _make_dpu(db, other_project, host_node_ip="10.50.0.1")
        db.commit()

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk-members",
            json={
                "control_plane_host_id": host.id,
                "host_ids": [host.id],
                "dpu_ids": [foreign_dpu.id],
            },
            headers=admin_headers,
        )
        assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# M3 — destructive defaults: custom pool CIDR not reset on re-call
# ---------------------------------------------------------------------------

class TestAssignMembersDoesNotResetCustomCidr:
    """Re-calling assign_members without tmfifo_pool_cidr must not reset a custom value."""

    def test_omitting_cidr_preserves_custom_value(
        self, client, db, admin_headers, sample_user, make_project, make_k8s_cluster
    ):
        """A custom pool CIDR set on the first call is preserved when omitted on the second."""
        project = make_project(name="cidr-preserve")
        cluster = make_k8s_cluster(project=project)
        host = _make_host(db, project, host_ip="10.60.0.1")
        db.commit()

        # First call: set a custom CIDR
        r1 = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk-members",
            json={
                "control_plane_host_id": host.id,
                "host_ids": [host.id],
                "dpu_ids": [],
                "tmfifo_pool_cidr": "10.100.0.0/24",
            },
            headers=admin_headers,
        )
        assert r1.status_code == 200, r1.text
        assert r1.json()["bnk_config"]["tmfifo_pool_cidr"] == "10.100.0.0/24"

        # Second call: omit tmfifo_pool_cidr (None default)
        r2 = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk-members",
            json={
                "control_plane_host_id": host.id,
                "host_ids": [host.id],
                "dpu_ids": [],
            },
            headers=admin_headers,
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["bnk_config"]["tmfifo_pool_cidr"] == "10.100.0.0/24", (
            "Custom pool CIDR was reset to default when tmfifo_pool_cidr was omitted"
        )


# ---------------------------------------------------------------------------
# M3 — reconciliation: CP host change doesn't leave two is_control_plane=True
# ---------------------------------------------------------------------------

class TestAssignMembersCpHostReconciliation:
    """Changing the control-plane host must not leave two is_control_plane=True rows."""

    def test_changing_cp_host_clears_old_cp_flag(
        self, client, db, admin_headers, sample_user, make_project, make_k8s_cluster
    ):
        """After changing CP host, the old CP host must have is_control_plane=False."""
        project = make_project(name="cp-reconcile")
        cluster = make_k8s_cluster(project=project)
        host_a = _make_host(db, project, host_ip="10.70.0.1", name="host-a")
        host_b = _make_host(db, project, host_ip="10.70.0.2", name="host-b")
        db.commit()

        # First call: host_a is CP
        r1 = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk-members",
            json={
                "control_plane_host_id": host_a.id,
                "host_ids": [host_a.id, host_b.id],
                "dpu_ids": [],
            },
            headers=admin_headers,
        )
        assert r1.status_code == 200, r1.text

        db.expire_all()
        assert host_a.is_control_plane is True
        assert host_b.is_control_plane is False

        # Second call: host_b becomes CP
        r2 = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk-members",
            json={
                "control_plane_host_id": host_b.id,
                "host_ids": [host_a.id, host_b.id],
                "dpu_ids": [],
            },
            headers=admin_headers,
        )
        assert r2.status_code == 200, r2.text

        db.expire_all()
        assert host_b.is_control_plane is True, "host_b should now be CP"
        assert host_a.is_control_plane is False, "host_a must not still be CP after CP host change"

    def test_removing_cp_host_from_cluster_clears_cp_flag(
        self, client, db, admin_headers, sample_user, make_project, make_k8s_cluster
    ):
        """A host removed from the cluster must have is_control_plane cleared."""
        project = make_project(name="cp-remove")
        cluster = make_k8s_cluster(project=project)
        host_a = _make_host(db, project, host_ip="10.80.0.1", name="host-a2")
        host_b = _make_host(db, project, host_ip="10.80.0.2", name="host-b2")
        db.commit()

        # First call: host_a is CP, both hosts in cluster
        r1 = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk-members",
            json={
                "control_plane_host_id": host_a.id,
                "host_ids": [host_a.id, host_b.id],
                "dpu_ids": [],
            },
            headers=admin_headers,
        )
        assert r1.status_code == 200, r1.text

        # Second call: only host_b remains, host_b becomes CP
        r2 = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk-members",
            json={
                "control_plane_host_id": host_b.id,
                "host_ids": [host_b.id],
                "dpu_ids": [],
            },
            headers=admin_headers,
        )
        assert r2.status_code == 200, r2.text

        db.expire_all()
        assert host_a.kubernetes_cluster_id is None, "Removed host must be unassigned from cluster"
        assert host_a.is_control_plane is False, "Removed host must not remain is_control_plane"
        assert host_b.is_control_plane is True


# ---------------------------------------------------------------------------
# Fix 1 — /31 pool CIDR must yield 422, never 500
# ---------------------------------------------------------------------------

class TestShortPoolCidrRejected:
    """A pool CIDR too short to fit a /30 must be rejected with 422, not 500."""

    def test_slash31_pool_cidr_returns_422_via_bnk_members(
        self, client, db, admin_headers, sample_user, make_project, make_k8s_cluster
    ):
        """POST /bnk-members with a /31 pool CIDR returns 422 Unprocessable Entity."""
        project = make_project(name="cidr-short-members")
        cluster = make_k8s_cluster(project=project)
        host = _make_host(db, project, host_ip="10.90.0.1")
        db.commit()

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk-members",
            json={
                "control_plane_host_id": host.id,
                "host_ids": [host.id],
                "dpu_ids": [],
                "tmfifo_pool_cidr": "192.168.100.0/31",
            },
            headers=admin_headers,
        )
        assert response.status_code == 422, (
            f"Expected 422 for /31 pool CIDR, got {response.status_code}: {response.text}"
        )

    def test_slash31_pool_cidr_returns_422_via_bnk_config(
        self, client, db, admin_headers, sample_user, make_project, make_k8s_cluster
    ):
        """POST /bnk-config with a /31 pool CIDR returns 422 Unprocessable Entity."""
        project = make_project(name="cidr-short-config")
        cluster = make_k8s_cluster(project=project)
        db.commit()

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk-config",
            json={"tmfifo_pool_cidr": "10.0.0.0/31"},
            headers=admin_headers,
        )
        assert response.status_code == 422, (
            f"Expected 422 for /31 pool CIDR, got {response.status_code}: {response.text}"
        )

    def test_slash32_pool_cidr_returns_422(
        self, client, db, admin_headers, sample_user, make_project, make_k8s_cluster
    ):
        """A /32 host address is also too short to fit a /30."""
        project = make_project(name="cidr-short-32")
        cluster = make_k8s_cluster(project=project)
        host = _make_host(db, project, host_ip="10.91.0.1")
        db.commit()

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk-members",
            json={
                "control_plane_host_id": host.id,
                "host_ids": [host.id],
                "dpu_ids": [],
                "tmfifo_pool_cidr": "192.168.100.0/32",
            },
            headers=admin_headers,
        )
        assert response.status_code == 422, (
            f"Expected 422 for /32 pool CIDR, got {response.status_code}: {response.text}"
        )

    def test_exactly_slash30_is_accepted(
        self, client, db, admin_headers, sample_user, make_project, make_k8s_cluster
    ):
        """A /30 pool CIDR is the minimum valid size and must be accepted."""
        project = make_project(name="cidr-exact-30")
        cluster = make_k8s_cluster(project=project)
        host = _make_host(db, project, host_ip="10.92.0.1")
        db.commit()

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk-members",
            json={
                "control_plane_host_id": host.id,
                "host_ids": [host.id],
                "dpu_ids": [],
                "tmfifo_pool_cidr": "192.168.200.0/30",
            },
            headers=admin_headers,
        )
        assert response.status_code == 200, (
            f"Expected 200 for /30 pool CIDR, got {response.status_code}: {response.text}"
        )


# ---------------------------------------------------------------------------
# Fix 3 — assign route persists after commit moved to route handler
# ---------------------------------------------------------------------------

class TestAssignMembersPersistence:
    """Membership changes must survive session close (route must commit)."""

    def test_assign_members_persists_host_after_commit(
        self, client, db, admin_headers, sample_user, make_project, make_k8s_cluster
    ):
        """POST /bnk-members must commit so host.kubernetes_cluster_id is readable
        in a subsequent DB query (simulating a GET after the POST).
        """
        project = make_project(name="persist-test")
        cluster = make_k8s_cluster(project=project)
        host = _make_host(db, project, host_ip="10.93.0.1")
        db.commit()

        # Spy: verify db.commit() is called by the route handler.
        real_commit = db.commit
        commit_calls: list[bool] = []

        def _spy_commit():
            commit_calls.append(True)
            return real_commit()

        db.commit = _spy_commit
        try:
            response = client.post(
                f"/api/k8s/clusters/{cluster.id}/bnk-members",
                json={
                    "control_plane_host_id": host.id,
                    "host_ids": [host.id],
                    "dpu_ids": [],
                },
                headers=admin_headers,
            )
        finally:
            db.commit = real_commit

        assert response.status_code == 200, response.text
        assert commit_calls, (
            "assign_bnk_cluster_members route did not call db.commit() — "
            "membership changes will be lost when get_db closes the session."
        )

        # Simulate a GET: expire the identity map and re-read from DB.
        db.expire_all()
        assert host.kubernetes_cluster_id == cluster.id, (
            "host.kubernetes_cluster_id not set after route committed — "
            "assign_members must flush+commit, not just flush."
        )


# ---------------------------------------------------------------------------
# Fix — DPU removal from dpu_ids releases tmfifo allocation
# ---------------------------------------------------------------------------

class TestRemovingDpuFromDpuIdsReleasesTmfifo:
    """A DPU absent from dpu_ids on a re-call must have its /30 released even
    when its owner host remains in the cluster."""

    def test_removing_dpu_from_dpu_ids_releases_tmfifo_allocation(
        self, client, db, admin_headers, sample_user, make_project, make_k8s_cluster
    ):
        """Assign a cluster with one DPU, re-assign with that DPU absent, assert
        its kubernetes_cluster_id and dpu_tmfifo_ip are cleared."""
        project = make_project(name="dpu-deselect")
        cluster = make_k8s_cluster(project=project)
        host = _make_host(db, project, host_ip="10.95.0.1")
        dpu = _make_dpu(db, project, host_node_ip="10.95.0.1")
        db.commit()

        # First call: host + DPU both assigned.
        r1 = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk-members",
            json={
                "control_plane_host_id": host.id,
                "host_ids": [host.id],
                "dpu_ids": [dpu.id],
            },
            headers=admin_headers,
        )
        assert r1.status_code == 200, r1.text

        db.expire_all()
        assert dpu.kubernetes_cluster_id == cluster.id, "DPU should be in cluster after first call"
        assert dpu.dpu_tmfifo_ip is not None, "DPU should have a tmfifo IP after first call"

        # Second call: host still present, but DPU removed from dpu_ids.
        r2 = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk-members",
            json={
                "control_plane_host_id": host.id,
                "host_ids": [host.id],
                "dpu_ids": [],
            },
            headers=admin_headers,
        )
        assert r2.status_code == 200, r2.text

        db.expire_all()
        assert dpu.kubernetes_cluster_id is None, (
            "DPU must be removed from cluster when absent from dpu_ids, even if its host stays"
        )
        assert dpu.dpu_tmfifo_ip is None, (
            "DPU tmfifo IP must be released when DPU is removed from dpu_ids"
        )
        # Host must remain in cluster.
        assert host.kubernetes_cluster_id == cluster.id, "Host must still be in the cluster"
        assert host.is_control_plane is True


# ---------------------------------------------------------------------------
# #4 — cross-cluster steal guard: a member owned by another cluster in the
# same project is always rejected (409). The former reassign=True bypass was
# removed (ADR-424 cold audit C) — the guard is now unconditional.
# ---------------------------------------------------------------------------

class TestAssignMembersCrossClusterGuard:
    """Opening the dialog on cluster B must not silently re-home cluster A's members."""

    def test_host_in_other_cluster_returns_409(
        self, client, db, admin_headers, sample_user, make_project, make_k8s_cluster
    ):
        project = make_project(name="xcluster-host")
        cluster_a = make_k8s_cluster(project=project)
        cluster_b = make_k8s_cluster(project=project)
        host = _make_host(db, project, host_ip="10.120.0.1")
        db.commit()

        # Assign host to cluster A.
        r1 = client.post(
            f"/api/k8s/clusters/{cluster_a.id}/bnk-members",
            json={"control_plane_host_id": host.id, "host_ids": [host.id], "dpu_ids": []},
            headers=admin_headers,
        )
        assert r1.status_code == 200, r1.text

        # Attempt to move it to cluster B → always 409 (no bypass).
        r2 = client.post(
            f"/api/k8s/clusters/{cluster_b.id}/bnk-members",
            json={"control_plane_host_id": host.id, "host_ids": [host.id], "dpu_ids": []},
            headers=admin_headers,
        )
        assert r2.status_code == 409, r2.text

        # Host must still belong to cluster A (unchanged).
        db.expire_all()
        assert host.kubernetes_cluster_id == cluster_a.id


# ---------------------------------------------------------------------------
# ADR-424 finding B — auto-registration delete path releases tmfifo IPs
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# B6 — bulk_cluster_membership + serialize_cluster bnk_config branch
# ---------------------------------------------------------------------------

class TestBulkClusterMembership:
    """bulk_cluster_membership must bucket hosts and DPUs correctly by cluster_id.

    Exercises the path that list_all_clusters / list_project_clusters take:
    bnk_config present → membership fetched in bulk → serialize_cluster renders
    host_ids / dpu_ids without N+1 queries and without cross-cluster leakage.
    """

    def test_hosts_and_dpus_bucketed_by_cluster(self, db):
        """host_ids and dpu_ids are sorted per cluster with no cross-cluster leakage."""
        from services.bnk_cluster_service import BnkClusterService

        project = _make_project(db, "bulk-membership")
        cluster_a = _make_cluster(db, project)
        cluster_b = _make_cluster(db, project)

        host_a = _make_host(db, project, host_ip="10.10.0.1")
        host_b = _make_host(db, project, host_ip="10.10.0.2")
        dpu_a = _make_dpu(db, project, host_node_ip="10.10.0.1")

        host_a.kubernetes_cluster_id = cluster_a.id
        host_b.kubernetes_cluster_id = cluster_b.id
        dpu_a.kubernetes_cluster_id = cluster_a.id
        db.flush()
        db.commit()

        result = BnkClusterService(db).bulk_cluster_membership([cluster_a.id, cluster_b.id])

        # Cluster A: one host, one DPU.
        host_ids_a, dpu_ids_a = result[cluster_a.id]
        assert host_ids_a == [host_a.id], "cluster A must have exactly host_a"
        assert dpu_ids_a == [dpu_a.id], "cluster A must have exactly dpu_a"

        # Cluster B: one host, no DPUs.
        host_ids_b, dpu_ids_b = result[cluster_b.id]
        assert host_ids_b == [host_b.id], "cluster B must have exactly host_b"
        assert dpu_ids_b == [], "cluster B must have no DPUs"

        # No cross-cluster contamination.
        assert host_b.id not in host_ids_a, "host_b must not appear in cluster A"
        assert host_a.id not in host_ids_b, "host_a must not appear in cluster B"

    def test_serialize_cluster_with_bnk_config_and_members(
        self, client, db, admin_headers, sample_user, make_project, make_k8s_cluster
    ):
        """GET /api/k8s/clusters renders bnk_config.host_ids / dpu_ids correctly
        for a cluster that has a BnkClusterConfig with members assigned.

        Verifies _serialize_bnk_config branch (bnk_config present) and that
        host IDs do not leak into the dpu_ids bucket (B6).

        Two hosts and two DPUs ensure that IDs are distinct across the tables
        (SQLite auto-increments per-table from 1, so IDs can coincide with a
        single host + single DPU; using two of each forces divergence so the
        cross-bucket assertion is meaningful).
        """
        project = make_project(name="serialize-bnk")
        cluster = make_k8s_cluster(project=project)
        # Create two hosts and two DPUs to guarantee distinct integer IDs.
        host1 = _make_host(db, project, host_ip="10.11.0.1", name="h-serialize-1")
        host2 = _make_host(db, project, host_ip="10.11.0.2", name="h-serialize-2")
        dpu1 = _make_dpu(db, project, host_node_ip="10.11.0.1", name="dpu-serialize-1")
        dpu2 = _make_dpu(db, project, host_node_ip="10.11.0.2", name="dpu-serialize-2")
        db.commit()

        # Assign both hosts and both DPUs so membership is non-trivial.
        r = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk-members",
            json={
                "control_plane_host_id": host1.id,
                "host_ids": [host1.id, host2.id],
                "dpu_ids": [dpu1.id, dpu2.id],
            },
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text

        # Fetch the cluster list — exercises bulk_cluster_membership + serialize_cluster.
        resp = client.get("/api/k8s/clusters", headers=admin_headers)
        assert resp.status_code == 200, resp.text

        clusters = resp.json()["clusters"]
        target = next((c for c in clusters if c["id"] == cluster.id), None)
        assert target is not None, f"cluster {cluster.id} not in response"

        bnk = target.get("bnk_config")
        assert bnk is not None, "bnk_config must be present for a BNK cluster"

        assert sorted(bnk["host_ids"]) == sorted([host1.id, host2.id]), (
            f"host_ids must contain the two assigned hosts, got {bnk['host_ids']}"
        )
        assert sorted(bnk["dpu_ids"]) == sorted([dpu1.id, dpu2.id]), (
            f"dpu_ids must contain the two assigned DPUs, got {bnk['dpu_ids']}"
        )
        # Cross-bucket guard: dpu_ids must be exactly the DPU set — no extras.
        assert len(bnk["dpu_ids"]) == 2, (
            f"dpu_ids must have exactly 2 entries (no host leakage), got {bnk['dpu_ids']}"
        )
        assert len(bnk["host_ids"]) == 2, (
            f"host_ids must have exactly 2 entries (no DPU leakage), got {bnk['host_ids']}"
        )


# ---------------------------------------------------------------------------
# B6 — _require_cp_member=True BadRequestError path
# ---------------------------------------------------------------------------

class TestRequireCpMemberBadRequestError:
    """POST /bnk-config must reject a control_plane_host_id that exists in the
    project but is NOT yet a member of the cluster (ADR-424 minor)."""

    def test_cp_host_not_a_member_returns_400(
        self, client, db, admin_headers, sample_user, make_project, make_k8s_cluster
    ):
        """A host that belongs to the project but is not in the cluster must
        return 400 with a 'not a member' message when set as control_plane_host_id
        via POST /bnk-config (which uses _require_cp_member=True)."""
        project = make_project(name="cp-not-member")
        cluster = make_k8s_cluster(project=project)
        # Host exists in the project but has never been assigned to this cluster.
        host = _make_host(db, project, host_ip="10.12.0.1")
        db.commit()

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk-config",
            json={"control_plane_host_id": host.id},
            headers=admin_headers,
        )
        assert response.status_code == 400, (
            f"Expected 400 when CP host is not a cluster member, got {response.status_code}: {response.text}"
        )
        assert "not a member" in response.text.lower(), (
            f"Expected 'not a member' in error message, got: {response.text}"
        )

    def test_cp_host_that_is_member_accepted(
        self, client, db, admin_headers, sample_user, make_project, make_k8s_cluster
    ):
        """A host that IS a cluster member can be set as control_plane_host_id via
        POST /bnk-config without error."""
        project = make_project(name="cp-is-member")
        cluster = make_k8s_cluster(project=project)
        host = _make_host(db, project, host_ip="10.13.0.1")
        db.commit()

        # First assign the host to the cluster.
        r1 = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk-members",
            json={
                "control_plane_host_id": host.id,
                "host_ids": [host.id],
                "dpu_ids": [],
            },
            headers=admin_headers,
        )
        assert r1.status_code == 200, r1.text

        # Now set it as CP via bnk-config — should succeed.
        r2 = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk-config",
            json={"control_plane_host_id": host.id},
            headers=admin_headers,
        )
        assert r2.status_code == 200, (
            f"Expected 200 when CP host is a member, got {r2.status_code}: {r2.text}"
        )


class TestAutoRegistrationDeleteReleasesTmfifo:
    """maybe_unregister_container_cluster must clear DPU tmfifo IPs via the
    before_delete listener — previously this path bypassed the release loop
    that only existed in ClusterManagementService.delete_cluster (ADR-424 B).
    """

    def test_auto_unregister_releases_dpu_tmfifo_ips(self, db):
        """Deleting a cluster via maybe_unregister_container_cluster releases
        all bound DPU tmfifo allocations (host_tmfifo_ip and dpu_tmfifo_ip are
        cleared; kubernetes_cluster_id is set to NULL)."""
        from types import SimpleNamespace

        from services.cluster_auto_registration_service import maybe_unregister_container_cluster

        project = _make_project(db, "auto-reg-tmfifo")

        # Use a SimpleNamespace stub — maybe_unregister_container_cluster only
        # reads module.id and module.project_id, so no real DB row needed.
        module_stub = SimpleNamespace(id=9999, project_id=project.id)

        # Simulate an auto-registered cluster: cluster with source_module_id in meta_data.
        cluster = _make_cluster(db, project, name="auto-cluster")
        cluster.meta_data = {"source_module_id": module_stub.id}
        db.flush()

        # Bind a DPU with tmfifo allocations.
        dpu = _make_dpu(db, project, host_node_ip="10.200.0.1")
        dpu.kubernetes_cluster_id = cluster.id
        dpu.host_tmfifo_ip = "192.168.100.1"
        dpu.dpu_tmfifo_ip = "192.168.100.2"
        db.add(dpu)
        db.commit()

        assert dpu.kubernetes_cluster_id == cluster.id
        assert dpu.host_tmfifo_ip is not None
        assert dpu.dpu_tmfifo_ip is not None

        # Unregister the cluster (simulates module destroy).
        result = maybe_unregister_container_cluster(db, module_stub)
        db.flush()

        assert result is True, "Cluster should have been found and unregistered"

        db.expire_all()
        assert dpu.kubernetes_cluster_id is None, (
            "DPU kubernetes_cluster_id must be cleared by the before_delete listener"
        )
        assert dpu.host_tmfifo_ip is None, (
            "host_tmfifo_ip must be released when cluster is auto-unregistered (ADR-424 B)"
        )
        assert dpu.dpu_tmfifo_ip is None, (
            "dpu_tmfifo_ip must be released when cluster is auto-unregistered (ADR-424 B)"
        )
