"""Component tests — /api/benchmarks/agent-hosts CRUD (Slice 1).

Note on auth: JWT middleware resolves the user from DB, so tests that call
authenticated routes must include `sample_user` / `sample_operator_user` /
`sample_viewer_user` to create the "testadmin"/"testoperator"/"testviewer"
rows that the JWT tokens (in admin_headers etc.) reference.
"""
from unittest.mock import MagicMock, patch

import pytest

from models.ssh_credential import SSHCredential
from tests.factories import ProjectFactory, _next_seq


def _cred(db) -> SSHCredential:
    """Minimal SSHCredential for FK tests (avoid real SSH)."""
    n = _next_seq("ssh_cred")
    c = SSHCredential(name=f"tc-{n}", host=f"10.99.0.{n}", port=22, username="forge", auth_type="key")
    db.add(c)
    db.flush()
    return c


@pytest.mark.component
class TestAgentHostCreate:
    """POST /api/benchmarks/agent-hosts."""

    def test_create_returns_201(self, client, admin_headers, sample_user, db):
        project = ProjectFactory(db, user_id=sample_user.id)
        cred = _cred(db)
        db.commit()

        resp = client.post(
            "/api/benchmarks/agent-hosts",
            json={
                "name": "remote-host-01",
                "project_id": project.id,
                "host_ip": "10.0.1.100",
                "ssh_credential_id": cred.id,
                "ssh_port": 22,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "remote-host-01"
        assert body["managed"] is True
        assert body["provision_status"] == "unprovisioned"
        assert body["host_ip"] == "10.0.1.100"
        assert body["project_id"] == project.id
        assert body["ssh_credential_id"] == cred.id

    def test_create_with_jumphost_chain(self, client, admin_headers, sample_user, db):
        project = ProjectFactory(db, user_id=sample_user.id)
        cred = _cred(db)
        db.commit()

        resp = client.post(
            "/api/benchmarks/agent-hosts",
            json={
                "name": "remote-host-jump-01",
                "project_id": project.id,
                "host_ip": "10.0.1.200",
                "ssh_credential_id": cred.id,
                "ssh_port": 2222,
                "jumphost_chain": [{"ssh_credential_id": cred.id}],
            },
            headers=admin_headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["ssh_port"] == 2222
        assert body["jumphost_chain"] == [{"ssh_credential_id": cred.id}]

    def test_duplicate_name_returns_400(self, client, admin_headers, sample_user, db):
        project = ProjectFactory(db, user_id=sample_user.id)
        cred = _cred(db)
        db.commit()

        payload = {"name": "dup-host-01", "project_id": project.id, "host_ip": "10.0.1.101", "ssh_credential_id": cred.id}
        resp1 = client.post("/api/benchmarks/agent-hosts", json=payload, headers=admin_headers)
        assert resp1.status_code == 201, resp1.text
        resp2 = client.post("/api/benchmarks/agent-hosts", json=payload, headers=admin_headers)
        assert resp2.status_code == 400
        assert "AGENT_HOST_EXISTS" in resp2.text

    def test_nonexistent_project_returns_404(self, client, admin_headers, sample_user, db):
        cred = _cred(db)
        db.commit()
        resp = client.post(
            "/api/benchmarks/agent-hosts",
            json={"name": "orphan-host", "project_id": 99999, "host_ip": "1.2.3.4", "ssh_credential_id": cred.id},
            headers=admin_headers,
        )
        assert resp.status_code == 404

    def test_viewer_cannot_create(self, client, viewer_headers, sample_viewer_user, db):
        project = ProjectFactory(db)
        cred = _cred(db)
        db.commit()
        resp = client.post(
            "/api/benchmarks/agent-hosts",
            json={"name": "viewer-host", "project_id": project.id, "host_ip": "1.2.3.4", "ssh_credential_id": cred.id},
            headers=viewer_headers,
        )
        assert resp.status_code == 403


@pytest.mark.component
class TestAgentHostList:
    """GET /api/benchmarks/agent-hosts."""

    def test_list_managed_hosts(self, client, admin_headers, sample_user, db):
        project = ProjectFactory(db, user_id=sample_user.id)
        cred = _cred(db)
        db.commit()

        client.post(
            "/api/benchmarks/agent-hosts",
            json={"name": "list-host-01", "project_id": project.id, "host_ip": "10.0.2.1", "ssh_credential_id": cred.id},
            headers=admin_headers,
        )

        resp = client.get(f"/api/benchmarks/agent-hosts?project_id={project.id}", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        names = [h["name"] for h in resp.json()]
        assert "list-host-01" in names

    def test_list_without_project_filter_returns_all_managed(self, client, admin_headers, sample_user):
        resp = client.get("/api/benchmarks/agent-hosts", headers=admin_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_viewer_can_list(self, client, viewer_headers, sample_viewer_user):
        resp = client.get("/api/benchmarks/agent-hosts", headers=viewer_headers)
        assert resp.status_code == 200


@pytest.mark.component
class TestAgentHostDetail:
    """GET /api/benchmarks/agent-hosts/{host_id}."""

    def test_get_returns_200(self, client, admin_headers, sample_user, db):
        project = ProjectFactory(db, user_id=sample_user.id)
        cred = _cred(db)
        db.commit()

        create_resp = client.post(
            "/api/benchmarks/agent-hosts",
            json={"name": "detail-host-01", "project_id": project.id, "host_ip": "10.0.3.1", "ssh_credential_id": cred.id},
            headers=admin_headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        host_id = create_resp.json()["id"]

        resp = client.get(f"/api/benchmarks/agent-hosts/{host_id}", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["id"] == host_id

    def test_get_nonexistent_returns_404(self, client, admin_headers, sample_user):
        resp = client.get("/api/benchmarks/agent-hosts/99999", headers=admin_headers)
        assert resp.status_code == 404


@pytest.mark.component
class TestAgentHostDelete:
    """DELETE /api/benchmarks/agent-hosts/{host_id}."""

    def test_delete_returns_204(self, client, admin_headers, sample_user, db):
        project = ProjectFactory(db, user_id=sample_user.id)
        cred = _cred(db)
        db.commit()

        create_resp = client.post(
            "/api/benchmarks/agent-hosts",
            json={"name": "delete-host-01", "project_id": project.id, "host_ip": "10.0.4.1", "ssh_credential_id": cred.id},
            headers=admin_headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        host_id = create_resp.json()["id"]

        resp = client.delete(f"/api/benchmarks/agent-hosts/{host_id}", headers=admin_headers)
        assert resp.status_code == 204

        resp2 = client.get(f"/api/benchmarks/agent-hosts/{host_id}", headers=admin_headers)
        assert resp2.status_code == 404

    def test_delete_provisioned_dispatches_cleanup_to_celery(
        self, client, admin_headers, sample_user, db
    ):
        """A provisioned host's SSH teardown is dispatched async, never run in-request.

        Regression for mwiget review: a synchronous systemctl-over-SSH in the
        request thread stalls the DELETE when the host is unreachable.
        """
        from models.benchmark import BenchmarkAgent

        project = ProjectFactory(db, user_id=sample_user.id)
        cred = _cred(db)
        db.commit()

        create_resp = client.post(
            "/api/benchmarks/agent-hosts",
            json={"name": "prov-host-01", "project_id": project.id, "host_ip": "10.0.6.1", "ssh_credential_id": cred.id},
            headers=admin_headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        host_id = create_resp.json()["id"]

        # Mark it provisioned so the delete path triggers cleanup dispatch
        agent = db.query(BenchmarkAgent).filter_by(id=host_id).first()
        agent.provision_status = "provisioned"
        db.commit()

        with patch(
            "tasks.benchmark_agent_tasks.cleanup_benchmark_agent_host.delay"
        ) as mock_delay:
            mock_delay.return_value = MagicMock(id="cleanup-task-1")
            resp = client.delete(f"/api/benchmarks/agent-hosts/{host_id}", headers=admin_headers)

        assert resp.status_code == 204
        # Row is gone immediately (not blocked on SSH)
        assert db.query(BenchmarkAgent).filter_by(id=host_id).first() is None
        # Best-effort cleanup was dispatched to Celery with the captured params
        mock_delay.assert_called_once()
        args = mock_delay.call_args.args
        assert args[0] == cred.id          # ssh_credential_id
        assert args[1] == "10.0.6.1"       # host_ip

    def test_delete_unprovisioned_skips_cleanup_dispatch(
        self, client, admin_headers, sample_user, db
    ):
        """An unprovisioned host has nothing to tear down — no Celery dispatch."""
        project = ProjectFactory(db, user_id=sample_user.id)
        cred = _cred(db)
        db.commit()

        create_resp = client.post(
            "/api/benchmarks/agent-hosts",
            json={"name": "unprov-host-01", "project_id": project.id, "host_ip": "10.0.7.1", "ssh_credential_id": cred.id},
            headers=admin_headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        host_id = create_resp.json()["id"]

        with patch(
            "tasks.benchmark_agent_tasks.cleanup_benchmark_agent_host.delay"
        ) as mock_delay:
            resp = client.delete(f"/api/benchmarks/agent-hosts/{host_id}", headers=admin_headers)

        assert resp.status_code == 204
        mock_delay.assert_not_called()

    def test_delete_nonexistent_returns_404(self, client, admin_headers, sample_user):
        resp = client.delete("/api/benchmarks/agent-hosts/99999", headers=admin_headers)
        assert resp.status_code == 404

    def test_viewer_cannot_delete(self, client, viewer_headers, admin_headers, sample_user, sample_viewer_user, db):
        project = ProjectFactory(db, user_id=sample_user.id)
        cred = _cred(db)
        db.commit()

        create_resp = client.post(
            "/api/benchmarks/agent-hosts",
            json={"name": "nodelete-host-01", "project_id": project.id, "host_ip": "10.0.5.1", "ssh_credential_id": cred.id},
            headers=admin_headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        host_id = create_resp.json()["id"]

        resp = client.delete(f"/api/benchmarks/agent-hosts/{host_id}", headers=viewer_headers)
        assert resp.status_code == 403


@pytest.mark.component
class TestAgentHostNameValidation:
    """Charset hardening for BenchmarkAgentHostCreate.name (Item 3).

    The name is interpolated into a shell command on the provision nohup
    fallback (`$(cat /etc/forge/agent.env | xargs)`), so spaces / shell
    metacharacters must be rejected at the validation boundary.
    """

    @pytest.mark.parametrize(
        "bad_name",
        [
            "host with spaces",
            "host;rm -rf /",
            "host$(whoami)",
            "host`id`",
            "host&&echo",
            "host|cat",
            'host"quote',
        ],
    )
    def test_invalid_agent_name_rejected(self, client, admin_headers, sample_user, db, bad_name):
        project = ProjectFactory(db, user_id=sample_user.id)
        cred = _cred(db)
        db.commit()
        resp = client.post(
            "/api/benchmarks/agent-hosts",
            json={"name": bad_name, "project_id": project.id, "host_ip": "10.0.9.1", "ssh_credential_id": cred.id},
            headers=admin_headers,
        )
        assert resp.status_code == 422, resp.text

    @pytest.mark.parametrize("good_name", ["loadgen-01", "host_A.b-2", "Agent.Host-3"])
    def test_valid_agent_name_accepted(self, client, admin_headers, sample_user, db, good_name):
        project = ProjectFactory(db, user_id=sample_user.id)
        cred = _cred(db)
        db.commit()
        resp = client.post(
            "/api/benchmarks/agent-hosts",
            json={"name": good_name, "project_id": project.id, "host_ip": "10.0.9.2", "ssh_credential_id": cred.id},
            headers=admin_headers,
        )
        assert resp.status_code == 201, resp.text


@pytest.mark.component
class TestBenchmarkAgentDeleteRBAC:
    """DELETE /api/benchmarks/agents/{agent_id} — role gate (Item 1).

    Regression for mwiget review: the test-client agent deregister route had
    no role gate, so any authenticated viewer could deregister any agent.
    Now require_operator + project ownership (for project-scoped agents).
    """

    def _make_agent(self, db, project_id=None):
        from models.benchmark import BenchmarkAgent

        n = _next_seq("rbac_agent")
        agent = BenchmarkAgent(name=f"rbac-agent-{n}", project_id=project_id)
        db.add(agent)
        db.flush()
        return agent

    def test_viewer_cannot_deregister_agent(self, client, viewer_headers, sample_viewer_user, db):
        agent = self._make_agent(db)
        db.commit()
        resp = client.delete(f"/api/benchmarks/agents/{agent.id}", headers=viewer_headers)
        assert resp.status_code == 403

    def test_operator_can_deregister_global_agent(self, client, operator_headers, sample_operator_user, db):
        agent = self._make_agent(db)
        db.commit()
        resp = client.delete(f"/api/benchmarks/agents/{agent.id}", headers=operator_headers)
        assert resp.status_code == 204

    def test_operator_cannot_deregister_other_users_project_agent(
        self, client, operator_headers, sample_operator_user, sample_user, db
    ):
        """Operator must own the project for a project-scoped agent.

        The project is owned by another (admin) user, so the operator — who is
        neither admin nor the owner — gets 403 via _check_project_access.
        """
        project = ProjectFactory(db, user_id=sample_user.id)  # owned by testadmin
        agent = self._make_agent(db, project_id=project.id)
        db.commit()
        resp = client.delete(f"/api/benchmarks/agents/{agent.id}", headers=operator_headers)
        assert resp.status_code == 403
