"""Integration tests for project-scoped discovery trigger/read routes."""

from unittest.mock import patch

import pytest
from paramiko.ssh_exception import AuthenticationException, NoValidConnectionsError

from core.encryption import encrypt_value
from models import DiscoveryJob, Project, User
from models.ssh_credential import SSHCredential
from services.auth_service import hash_password
from services.discovery_service import DiscoveryService


@pytest.fixture(autouse=True)
def _run_discovery_inline(monkeypatch, db):
    """Replace background-thread execution with inline execution against the test session.

    The production runner uses a fresh SessionLocal() bound to the production engine,
    which is the wrong database in tests. Inline execution lets us reuse the test
    session so HTTP-level assertions can read the post-run state directly.
    """

    def fake_start(job_id: int) -> None:
        job = db.query(DiscoveryJob).filter(DiscoveryJob.id == job_id).first()
        if not job:
            return
        DiscoveryService(db)._run_discovery_job(job)

    monkeypatch.setattr(DiscoveryService, "start_background_execution", staticmethod(fake_start))


def _create_project_with_users(db):
    admin = User(
        username="testadmin",
        email="testadmin@bnk-forge.test",
        hashed_password=hash_password("testpassword123"),
        role="admin",
        is_active=True,
        must_change_password=False,
    )
    viewer = User(
        username="testviewer",
        email="testviewer@bnk-forge.test",
        hashed_password=hash_password("testpassword123"),
        role="viewer",
        is_active=True,
        must_change_password=False,
    )
    operator = User(
        username="testoperator",
        email="testoperator@bnk-forge.test",
        hashed_password=hash_password("testpassword123"),
        role="operator",
        is_active=True,
        must_change_password=False,
    )
    project = Project(
        name="Discovery Route Project",
        description="discovery route integration tests",
        project_type="kubernetes",
        cloud_provider="on-prem",
        environment="dev",
        backend_type="local",
    )
    db.add_all([admin, viewer, operator])
    db.flush()
    project.user_id = admin.id
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


class TestDiscoveryRoutes:
    @patch("services.discovery_service._probe_node_ssh")
    def test_list_discovery_jobs_returns_recent_summary_without_nodes(
        self,
        mock_probe,
        client,
        db,
        admin_headers,
        viewer_headers,
    ):
        project = _create_project_with_users(db)

        mock_probe.return_value = {
            "hostname": "node-a.example",
            "os_type": "ubuntu",
            "os_version": "24.04",
            "os_pretty_name": "Ubuntu 24.04 LTS",
            "kernel_version": "6.8.0",
            "architecture": "x86_64",
            "is_dpu_host": False,
            "is_dpu_node": False,
            "dpu_count": 0,
            "dpu_details": [],
            "k8s_installed": False,
            "k8s_running": False,
            "k8s_version": None,
            "k8s_distribution": None,
            "k8s_role": None,
            "container_runtime": None,
            "network_interfaces": [],
        }

        for ip in ("10.0.0.12", "10.0.0.13"):
            create_response = client.post(
                f"/api/projects/{project.id}/discovery",
                json={
                    "ssh_username": "ubuntu",
                    "ssh_password": "secret",
                    "nodes": [{"ip_address": ip}],
                },
                headers=admin_headers,
            )
            assert create_response.status_code == 200

        list_response = client.get(
            f"/api/projects/{project.id}/discovery?limit=1",
            headers=viewer_headers,
        )
        assert list_response.status_code == 200
        payload = list_response.json()

        assert "jobs" in payload
        assert len(payload["jobs"]) == 1
        assert payload["jobs"][0]["total_nodes"] == 1
        assert payload["jobs"][0]["nodes"] == []

    @patch("services.discovery_service._probe_node_ssh")
    def test_trigger_and_get_discovery_job_success(self, mock_probe, client, db, admin_headers, viewer_headers):
        project = _create_project_with_users(db)

        mock_probe.return_value = {
            "hostname": "node-a.example",
            "os_type": "ubuntu",
            "os_version": "24.04",
            "os_pretty_name": "Ubuntu 24.04 LTS",
            "kernel_version": "6.8.0",
            "architecture": "x86_64",
            "is_dpu_host": False,
            "is_dpu_node": False,
            "dpu_count": 0,
            "dpu_details": [],
            "k8s_installed": True,
            "k8s_running": True,
            "k8s_version": "v1.30.1",
            "k8s_distribution": "unknown",
            "k8s_role": "unknown",
            "container_runtime": "containerd",
            "network_interfaces": [{"name": "eth0", "state": "UP", "mac": "aa:bb:cc:dd:ee:ff"}],
        }

        create_response = client.post(
            f"/api/projects/{project.id}/discovery",
            json={
                "ssh_username": "ubuntu",
                "ssh_password": "super-secret",
                "nodes": [{"ip_address": "10.0.0.10"}],
            },
            headers=admin_headers,
        )
        assert create_response.status_code == 200
        payload = create_response.json()["job"]

        assert payload["project_id"] == project.id
        assert payload["status"] == "completed"
        assert payload["completed_nodes"] == 1
        assert payload["failed_nodes"] == 0
        assert payload["nodes"][0]["status"] == "completed"
        assert payload["nodes"][0]["hostname"] == "node-a.example"

        assert "ssh_password" not in payload
        assert "ssh_password_encrypted" not in payload
        assert "ssh_key_encrypted" not in payload
        assert "ssh_username" not in payload
        assert "discovery_log" not in payload["nodes"][0]
        assert "ssh_password_encrypted" not in payload["nodes"][0]

        get_response = client.get(
            f"/api/projects/{project.id}/discovery/{payload['id']}",
            headers=viewer_headers,
        )
        assert get_response.status_code == 200
        get_payload = get_response.json()
        assert get_payload["id"] == payload["id"]
        assert get_payload["nodes"][0]["hostname"] == "node-a.example"

    def test_trigger_discovery_missing_credential_is_truthful(self, client, db, admin_headers):
        project = _create_project_with_users(db)

        response = client.post(
            f"/api/projects/{project.id}/discovery",
            json={
                "ssh_username": "ubuntu",
                "nodes": [{"ip_address": "10.0.0.20"}],
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        payload = response.json()["job"]

        assert payload["status"] == "failed"
        assert payload["failed_nodes"] == 1
        assert payload["nodes"][0]["status"] == "failed"
        assert "No SSH credential secret available" in payload["nodes"][0]["error_message"]

    def test_trigger_discovery_unknown_shared_ssh_credential_returns_bad_request(self, client, db, admin_headers):
        project = _create_project_with_users(db)

        response = client.post(
            f"/api/projects/{project.id}/discovery",
            json={
                "ssh_credential_id": 999999,
                "nodes": [{"ip_address": "10.0.0.21"}],
            },
            headers=admin_headers,
        )

        assert response.status_code == 400
        assert "SSH credential 999999 not found" in response.json()["error"]["message"]

    def test_trigger_discovery_unknown_node_ssh_credential_returns_bad_request(self, client, db, admin_headers):
        project = _create_project_with_users(db)

        response = client.post(
            f"/api/projects/{project.id}/discovery",
            json={
                "ssh_username": "ubuntu",
                "ssh_password": "secret",
                "nodes": [{"ip_address": "10.0.0.22", "ssh_credential_id": 999998}],
            },
            headers=admin_headers,
        )

        assert response.status_code == 400
        assert "SSH credential 999998 not found" in response.json()["error"]["message"]

    @patch("services.discovery_service._probe_node_ssh", side_effect=AuthenticationException("auth failed"))
    def test_trigger_discovery_bad_auth_is_truthful(self, _mock_probe, client, db, admin_headers):
        project = _create_project_with_users(db)

        response = client.post(
            f"/api/projects/{project.id}/discovery",
            json={
                "ssh_username": "ubuntu",
                "ssh_password": "wrong-password",
                "nodes": [{"ip_address": "10.0.0.30"}],
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        payload = response.json()["job"]
        assert payload["nodes"][0]["status"] == "failed"
        assert "SSH authentication failed" in payload["nodes"][0]["error_message"]

    @patch(
        "services.discovery_service._probe_node_ssh",
        side_effect=NoValidConnectionsError(errors={("10.0.0.40", 22): OSError("connection refused")}),
    )
    def test_trigger_discovery_unreachable_host_is_truthful(self, _mock_probe, client, db, admin_headers):
        project = _create_project_with_users(db)

        response = client.post(
            f"/api/projects/{project.id}/discovery",
            json={
                "ssh_username": "ubuntu",
                "ssh_password": "secret",
                "nodes": [{"ip_address": "10.0.0.40"}],
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        payload = response.json()["job"]
        assert payload["nodes"][0]["status"] == "failed"
        assert "host unreachable" in payload["nodes"][0]["error_message"].lower()

    @patch("services.discovery_service._probe_node_ssh")
    def test_trigger_discovery_malformed_output_is_truthful(self, mock_probe, client, db, admin_headers):
        project = _create_project_with_users(db)
        mock_probe.return_value = {"hostname": "bad-node", "network_interfaces": "not-a-list"}

        response = client.post(
            f"/api/projects/{project.id}/discovery",
            json={
                "ssh_username": "ubuntu",
                "ssh_password": "secret",
                "nodes": [{"ip_address": "10.0.0.50"}],
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        payload = response.json()["job"]
        assert payload["nodes"][0]["status"] == "failed"
        assert "Malformed discovery output" in payload["nodes"][0]["error_message"]

    def test_get_discovery_job_enforces_project_scope(self, client, db, admin_headers):
        project_a = _create_project_with_users(db)
        project_b = Project(
            name="Discovery Route Project B",
            description="scope test",
            project_type="kubernetes",
            cloud_provider="on-prem",
            environment="dev",
            backend_type="local",
        )
        db.add(project_b)
        db.commit()
        db.refresh(project_b)

        response = client.post(
            f"/api/projects/{project_a.id}/discovery",
            json={
                "ssh_username": "ubuntu",
                "nodes": [{"ip_address": "10.0.0.60"}],
            },
            headers=admin_headers,
        )
        job_id = response.json()["job"]["id"]

        wrong_project_get = client.get(
            f"/api/projects/{project_b.id}/discovery/{job_id}",
            headers=admin_headers,
        )
        assert wrong_project_get.status_code == 404

        persisted = db.query(DiscoveryJob).filter(DiscoveryJob.id == job_id).first()
        assert persisted is not None
        assert persisted.project_id == project_a.id

    def test_trigger_discovery_requires_project_owner(self, client, db, operator_headers):
        project = _create_project_with_users(db)

        response = client.post(
            f"/api/projects/{project.id}/discovery",
            json={
                "ssh_username": "ubuntu",
                "ssh_password": "secret",
                "nodes": [{"ip_address": "10.0.0.70"}],
            },
            headers=operator_headers,
        )

        assert response.status_code == 403

    @patch("services.discovery_service._probe_node_ssh")
    def test_trigger_discovery_passes_decrypted_key_passphrase(self, mock_probe, client, db, admin_headers):
        project = _create_project_with_users(db)
        ssh_credential = SSHCredential(
            name="discovery-passphrase-key",
            description="credential with passphrase protected key",
            host="10.0.0.80",
            port=22,
            username="ubuntu",
            auth_type="key",
            private_key_encrypted=encrypt_value("-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----"),
            key_passphrase_encrypted=encrypt_value("key-passphrase"),
        )
        db.add(ssh_credential)
        db.commit()
        db.refresh(ssh_credential)

        mock_probe.return_value = {
            "hostname": "node-passphrase",
            "os_type": "ubuntu",
            "os_version": "24.04",
            "os_pretty_name": "Ubuntu 24.04 LTS",
            "kernel_version": "6.8.0",
            "architecture": "x86_64",
            "is_dpu_host": False,
            "is_dpu_node": False,
            "dpu_count": 0,
            "dpu_details": [],
            "k8s_installed": False,
            "k8s_running": False,
            "k8s_version": None,
            "k8s_distribution": None,
            "k8s_role": None,
            "container_runtime": None,
            "network_interfaces": [],
        }

        response = client.post(
            f"/api/projects/{project.id}/discovery",
            json={
                "ssh_credential_id": ssh_credential.id,
                "nodes": [{"ip_address": "10.0.0.80"}],
            },
            headers=admin_headers,
        )

        assert response.status_code == 200
        assert mock_probe.call_count == 1
        kwargs = mock_probe.call_args.kwargs
        assert kwargs["key_passphrase"] == "key-passphrase"

    @patch("services.discovery_service._run_connectivity_for_source")
    @patch("services.discovery_service._probe_node_ssh")
    def test_trigger_discovery_runs_connectivity_phase_with_classified_ifaces(
        self,
        mock_probe,
        mock_connectivity,
        client,
        db,
        admin_headers,
    ):
        project = _create_project_with_users(db)

        # Probe returns a successful node with classified built-in + bluefield interfaces.
        def probe_side_effect(host, **_kwargs):
            last = host.split(".")[-1]
            return {
                "hostname": f"node-{last}",
                "os_type": "ubuntu",
                "os_version": "24.04",
                "os_pretty_name": "Ubuntu 24.04 LTS",
                "kernel_version": "6.8.0",
                "architecture": "x86_64",
                "is_dpu_host": True,
                "is_dpu_node": False,
                "dpu_count": 1,
                "dpu_details": [{"pci_address": "0000:01:00.0", "description": "BlueField-3"}],
                "k8s_installed": False,
                "k8s_running": False,
                "k8s_version": None,
                "k8s_distribution": None,
                "k8s_role": None,
                "container_runtime": None,
                "network_interfaces": [
                    {
                        "name": "eno1", "state": "UP", "kind": "builtin", "pci": "0000:00:1f.6",
                        "addresses": [{"family": "inet", "address": f"10.0.0.{last}", "prefix": 24}],
                    },
                    {
                        "name": "enp1s0f0np0", "state": "UP", "kind": "bluefield", "pci": "0000:01:00.0",
                        "addresses": [{"family": "inet", "address": f"192.168.100.{last}", "prefix": 24}],
                    },
                ],
            }
        mock_probe.side_effect = probe_side_effect

        # Connectivity worker returns a fixed result per source.
        def connectivity_side_effect(*, src_node_id, runnable_groups, **_kwargs):
            out: dict = {group: [] for group in runnable_groups}
            for group, endpoints in runnable_groups.items():
                src_eps = [ep for ep in endpoints if ep["node_id"] == src_node_id]
                dst_eps = [ep for ep in endpoints if ep["node_id"] != src_node_id]
                for src in src_eps:
                    for dst in dst_eps:
                        for addr in dst["addresses"]:
                            out[group].append({
                                "src_node_id": src_node_id,
                                "src_iface": src["interface"],
                                "dst_node_id": dst["node_id"],
                                "dst_iface": dst["interface"],
                                "family": addr["family"],
                                "dst_address": addr["address"],
                                "reachable": True,
                                "rtt_ms": 0.42,
                                "error": None,
                            })
            return out
        mock_connectivity.side_effect = connectivity_side_effect

        response = client.post(
            f"/api/projects/{project.id}/discovery",
            json={
                "ssh_username": "ubuntu",
                "ssh_password": "secret",
                "nodes": [{"ip_address": "10.0.0.41"}, {"ip_address": "10.0.0.42"}],
            },
            headers=admin_headers,
        )

        assert response.status_code == 200
        payload = response.json()["job"]
        assert payload["status"] == "completed"
        assert payload["connectivity_status"] == "completed"
        matrix = payload["connectivity_matrix"]
        assert "groups" in matrix
        assert set(matrix["groups"].keys()) == {"builtin", "bluefield"}

        for group_key in ("builtin", "bluefield"):
            group = matrix["groups"][group_key]
            assert len(group["endpoints"]) == 2
            # 2 sources × 1 target × 1 address = 2 results per group (each direction).
            assert len(group["results"]) == 2
            assert all(r["reachable"] is True for r in group["results"])

    @patch("services.discovery_service.DiscoveryService._resolve_shared_credentials", side_effect=RuntimeError("boom"))
    def test_trigger_discovery_unexpected_job_error_is_not_left_in_progress(
        self,
        _mock_resolve_shared_credentials,
        client,
        db,
        admin_headers,
    ):
        project = _create_project_with_users(db)

        response = client.post(
            f"/api/projects/{project.id}/discovery",
            json={
                "ssh_username": "ubuntu",
                "ssh_password": "secret",
                "nodes": [{"ip_address": "10.0.0.90"}],
            },
            headers=admin_headers,
        )

        assert response.status_code == 200
        payload = response.json()["job"]
        assert payload["status"] == "failed"
        assert payload["completed_nodes"] == 0
        assert payload["failed_nodes"] == 1
        assert payload["completed_at"] is not None
        assert "Discovery execution failed" in payload["error_message"]
        assert payload["nodes"][0]["status"] == "failed"
        assert "failed before node execution" in payload["nodes"][0]["error_message"]
