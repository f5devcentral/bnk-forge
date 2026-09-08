"""Component test: POST /bnk-config route must db.commit() so the row survives.

The underlying service method get_or_create_config() only calls db.flush().
Without an explicit db.commit() in the route handler, the row is lost when the
session is closed at the end of the request (production get_db closes without
auto-committing).

Three tests cover the fix:
1. Service-lifecycle test: flush-only loses data after session.close().
2. Service-lifecycle test: flush+commit preserves data after session.close().
3. Route-level spy test (via TestClient): asserts the route calls db.commit()
   and that the row is present in the DB after the request.  The spy FAILS if
   the route does not call db.commit().
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.kubernetes import BnkClusterConfig, KubernetesCluster
from services.bnk_cluster_service import BnkClusterService


def _isolated_engine():
    """Fresh in-memory SQLite with all tables — NOT the shared test pool."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return engine


class TestBnkClusterConfigPersistence:
    """BnkClusterService.get_or_create_config() only flushes; caller must commit."""

    # ------------------------------------------------------------------
    # Service-level lifecycle tests — isolated engine, no HTTP layer.
    # ------------------------------------------------------------------

    def test_flush_only_data_lost_after_session_close(self):
        """Without db.commit(), flushed data is gone once the session closes.

        This is the bug: the pre-fix route only called flush.  When production
        get_db closes the session at request end, the row vanishes.
        """
        engine = _isolated_engine()
        Session = sessionmaker(bind=engine)

        setup = Session()
        cluster = KubernetesCluster(
            name="c1", context="ctx1",
            api_server="https://k8s.example.com:6443", status="active",
        )
        setup.add(cluster)
        setup.commit()
        cluster_id = cluster.id
        setup.close()

        # Service flushes but no commit — then session closes (mimics production get_db).
        db1 = Session()
        BnkClusterService(db1).get_or_create_config(cluster_id=cluster_id)
        # intentionally no db1.commit()
        db1.close()

        db2 = Session()
        cfg = db2.query(BnkClusterConfig).filter_by(cluster_id=cluster_id).first()
        db2.close()
        assert cfg is None, "flush-only: row should be lost after session.close() without commit"

    def test_commit_data_survives_session_close(self):
        """With db.commit() after get_or_create_config(), data survives session close.

        This is the fix: the route now calls db.commit() right after the service,
        so the row is committed before get_db closes the session.
        """
        engine = _isolated_engine()
        Session = sessionmaker(bind=engine)

        setup = Session()
        cluster = KubernetesCluster(
            name="c2", context="ctx2",
            api_server="https://k8s.example.com:6443", status="active",
        )
        setup.add(cluster)
        setup.commit()
        cluster_id = cluster.id
        setup.close()

        # Service flushes, route commits, then session closes.
        db1 = Session()
        BnkClusterService(db1).get_or_create_config(
            cluster_id=cluster_id,
            tmfifo_pool_cidr="10.99.0.0/24",
            join_transport="rshim",
        )
        db1.commit()  # this is what configure_bnk_cluster now does
        db1.close()

        db2 = Session()
        cfg = db2.query(BnkClusterConfig).filter_by(cluster_id=cluster_id).first()
        db2.close()
        assert cfg is not None, (
            "BnkClusterConfig row missing after commit+close — route must call db.commit()."
        )
        assert cfg.tmfifo_pool_cidr == "10.99.0.0/24"
        assert cfg.join_transport == "rshim"

    # ------------------------------------------------------------------
    # Route-level test — exercises the HTTP path, spies on db.commit().
    # FAILS without db.commit() in configure_bnk_cluster; PASSES with it.
    # ------------------------------------------------------------------

    def test_configure_bnk_cluster_route_calls_commit_and_persists(
        self, client, db, admin_headers, sample_user, make_project, make_k8s_cluster
    ):
        """Route must call db.commit() and the row must be readable from the DB.

        A spy wraps db.commit so any call made during the request is counted.
        Without db.commit() in the route: commit_calls == 0 → assertion fails.
        With db.commit() in the route: commit_calls >= 1 → assertion passes.
        A follow-up DB query confirms the BnkClusterConfig row is present.
        """
        project = make_project()
        cluster = make_k8s_cluster(project=project)
        cluster_id = cluster.id

        # Spy: count how many times db.commit() is called during the route.
        real_commit = db.commit
        commit_calls: list[bool] = []

        def _spy_commit():
            commit_calls.append(True)
            return real_commit()

        db.commit = _spy_commit

        try:
            response = client.post(
                f"/api/k8s/clusters/{cluster_id}/bnk-config",
                json={"tmfifo_pool_cidr": "10.88.0.0/24", "join_transport": "rshim"},
                headers=admin_headers,
            )
        finally:
            db.commit = real_commit  # restore unconditionally

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["cluster_id"] == cluster_id
        assert body["tmfifo_pool_cidr"] == "10.88.0.0/24"
        assert body["join_transport"] == "rshim"

        assert commit_calls, (
            "configure_bnk_cluster did not call db.commit() — "
            "without commit the row is lost when get_db closes the session."
        )

        # Verify the row is present in the DB (identity-map safe: expire first).
        db.expire_all()
        cfg = db.query(BnkClusterConfig).filter_by(cluster_id=cluster_id).first()
        assert cfg is not None, "BnkClusterConfig row not found after route returned."
        assert cfg.tmfifo_pool_cidr == "10.88.0.0/24"


class TestConfigureBnkClusterValidation:
    """#3 — CP host authz + join_transport enum validation on POST /bnk-config."""

    def test_control_plane_host_from_other_project_returns_404(
        self, client, db, admin_headers, sample_user, make_project, make_k8s_cluster
    ):
        """A CP host FK pointing at another project's host must 404, not silently persist."""
        from models.bare_metal import BareMetalHost

        owner_project = make_project(name="cfg-owner")
        other_project = make_project(name="cfg-other")
        cluster = make_k8s_cluster(project=owner_project)

        foreign_host = BareMetalHost(
            project_id=other_project.id, name="foreign", host_ip="10.130.0.1",
        )
        db.add(foreign_host)
        db.commit()

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk-config",
            json={"control_plane_host_id": foreign_host.id},
            headers=admin_headers,
        )
        assert response.status_code == 404, response.text

    def test_garbage_join_transport_returns_422(
        self, client, db, admin_headers, sample_user, make_project, make_k8s_cluster
    ):
        """join_transport is Literal['rshim','mgmt'] — anything else is a 422."""
        project = make_project(name="cfg-jt")
        cluster = make_k8s_cluster(project=project)

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk-config",
            json={"join_transport": "garbage"},
            headers=admin_headers,
        )
        assert response.status_code == 422, response.text

    def test_bnk_config_moving_cp_host_syncs_is_control_plane(
        self, client, db, admin_headers, sample_user, make_project, make_k8s_cluster
    ):
        """POST /bnk-config moving the CP from H1→H2 must keep is_control_plane in sync
        with control_plane_host_id (ADR-424 cold audit B).

        Without the fix, cfg.control_plane_host_id can point to H2 while H1 still
        carries is_control_plane=True, giving kubeadm-init two conflicting targets.
        """
        from models.bare_metal import BareMetalHost
        from services.bnk_cluster_service import BnkClusterService

        project = make_project(name="cp-sync")
        cluster = make_k8s_cluster(project=project)

        h1 = BareMetalHost(project_id=project.id, name="cp-h1", host_ip="10.140.0.1")
        h2 = BareMetalHost(project_id=project.id, name="cp-h2", host_ip="10.140.0.2")
        db.add_all([h1, h2])
        db.commit()

        # First call: establish H1 as CP and member.
        svc = BnkClusterService(db)
        svc.assign_members(
            cluster_id=cluster.id,
            control_plane_host_id=h1.id,
            host_ids=[h1.id, h2.id],
            dpu_ids=[],
        )
        db.commit()

        db.expire_all()
        assert h1.is_control_plane is True
        assert h2.is_control_plane is False

        # Now move CP to H2 via POST /bnk-config (no member re-send).
        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk-config",
            json={"control_plane_host_id": h2.id},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["control_plane_host_id"] == h2.id

        # is_control_plane flags must track the new assignment.
        db.expire_all()
        assert h1.is_control_plane is False, "H1 must lose is_control_plane after CP moves to H2"
        assert h2.is_control_plane is True, "H2 must gain is_control_plane after CP moves to H2"
