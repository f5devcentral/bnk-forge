"""
Integration tests for TMM Debug Sidecar routes — /api/k8s/clusters/{cid}/tmm-debug/*.

Covers: list pods, exec raw command, tmctl, configview, configview UUIDs,
bdt_cli, authentication checks.

Uses FastAPI TestClient with real SQLite DB. TMM debug service functions
and KubernetesService are mocked (no live cluster).
"""

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_k8s_service():
    """Return a MagicMock KubernetesService that resolves cluster + kubeconfig."""
    mock_svc = MagicMock()
    mock_svc.get_cluster.return_value = MagicMock(id=1, name="test-cluster")
    mock_svc.load_kubeconfig.return_value = MagicMock()  # api_client
    return mock_svc


# ---------------------------------------------------------------------------
# GET /api/k8s/clusters/{cluster_id}/tmm-debug/pods
# ---------------------------------------------------------------------------


class TestListTMMDebugPods:
    """GET /api/k8s/clusters/{cluster_id}/tmm-debug/pods."""

    def test_requires_auth(self, client, sample_project, make_k8s_cluster):
        """Unauthenticated request returns 401."""
        cluster = make_k8s_cluster(project=sample_project, name="tmm-noauth")
        response = client.get(f"/api/k8s/clusters/{cluster.id}/tmm-debug/pods")
        assert response.status_code == 401

    @patch("routes.k8s.tmm_debug.list_tmm_debug_pods")
    @patch("routes.k8s.tmm_debug.KubernetesService")
    def test_returns_pod_list(
        self, mock_k8s_cls, mock_list_pods,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Viewer can list TMM pods with debug sidecar info."""
        cluster = make_k8s_cluster(project=sample_project, name="tmm-pods-cluster")
        mock_k8s_cls.return_value = _mock_k8s_service()
        mock_list_pods.return_value = [
            {"name": "f5-tmm-abc", "namespace": "f5-bnk", "has_debug": True, "status": "Running"},
            {"name": "f5-tmm-xyz", "namespace": "f5-bnk", "has_debug": False, "status": "Running"},
        ]

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/tmm-debug/pods",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        assert data["debug_available"] == 1
        assert len(data["pods"]) == 2
        assert data["pods"][0]["has_debug"] is True


# ---------------------------------------------------------------------------
# POST /api/k8s/clusters/{cluster_id}/tmm-debug/exec
# ---------------------------------------------------------------------------


class TestTMMDebugExec:
    """POST /api/k8s/clusters/{cluster_id}/tmm-debug/exec."""

    def test_requires_auth(self, client, sample_project, make_k8s_cluster):
        cluster = make_k8s_cluster(project=sample_project, name="tmm-exec-noauth")
        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/tmm-debug/exec",
            json={"pod_name": "f5-tmm-abc", "command": "ls"},
        )
        assert response.status_code == 401

    @patch("routes.k8s.tmm_debug.exec_debug_command")
    @patch("routes.k8s.tmm_debug.KubernetesService")
    def test_exec_raw_command(
        self, mock_k8s_cls, mock_exec,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Viewer can exec a raw command in the debug sidecar."""
        cluster = make_k8s_cluster(project=sample_project, name="tmm-exec-cluster")
        mock_k8s_cls.return_value = _mock_k8s_service()
        mock_exec.return_value = {
            "stdout": "bin  dev  etc  proc",
            "stderr": "",
            "exit_code": 0,
        }

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/tmm-debug/exec",
            json={
                "pod_name": "f5-tmm-abc",
                "namespace": "f5-bnk",
                "command": "ls /",
                "timeout": 15,
            },
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 0
        assert "bin" in data["stdout"]

        # Verify command was split and passed correctly
        mock_exec.assert_called_once()
        call_args = mock_exec.call_args
        assert call_args[0][1] == "f5-tmm-abc"  # pod_name
        assert call_args[0][2] == "f5-bnk"  # namespace
        assert call_args[0][3] == ["ls", "/"]  # command_parts
        assert call_args[0][4] == 15  # timeout

    @patch("routes.k8s.tmm_debug.exec_debug_command")
    @patch("routes.k8s.tmm_debug.KubernetesService")
    def test_exec_default_namespace(
        self, mock_k8s_cls, mock_exec,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Default namespace is f5-bnk when not specified."""
        cluster = make_k8s_cluster(project=sample_project, name="tmm-exec-ns-cluster")
        mock_k8s_cls.return_value = _mock_k8s_service()
        mock_exec.return_value = {"stdout": "", "stderr": "", "exit_code": 0}

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/tmm-debug/exec",
            json={"pod_name": "f5-tmm-abc", "command": "whoami"},
            headers=viewer_headers,
        )
        assert response.status_code == 200
        call_args = mock_exec.call_args
        assert call_args[0][2] == "f5-bnk"

    @patch("routes.k8s.tmm_debug.exec_debug_command")
    @patch("routes.k8s.tmm_debug.KubernetesService")
    def test_exec_resolves_cluster_default_namespace(
        self, mock_k8s_cls, mock_exec,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Namespace resolves from the cluster's default_namespace (e.g. direct-helm
        installs where the tenant namespace isn't 'f5-bnk')."""
        cluster = make_k8s_cluster(
            project=sample_project, name="tmm-exec-defns-cluster", default_namespace="bnk-app1",
        )
        mock_svc = _mock_k8s_service()
        mock_svc.get_cluster.return_value = cluster
        mock_k8s_cls.return_value = mock_svc
        mock_exec.return_value = {"stdout": "", "stderr": "", "exit_code": 0}

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/tmm-debug/exec",
            json={"pod_name": "f5-tmm-abc", "command": "whoami"},
            headers=viewer_headers,
        )
        assert response.status_code == 200
        call_args = mock_exec.call_args
        assert call_args[0][2] == "bnk-app1"


# ---------------------------------------------------------------------------
# POST /api/k8s/clusters/{cluster_id}/tmm-debug/tmctl
# ---------------------------------------------------------------------------


class TestTMMDebugTmctl:
    """POST /api/k8s/clusters/{cluster_id}/tmm-debug/tmctl."""

    def test_requires_auth(self, client, sample_project, make_k8s_cluster):
        cluster = make_k8s_cluster(project=sample_project, name="tmm-tmctl-noauth")
        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/tmm-debug/tmctl",
            json={"pod_name": "f5-tmm-abc", "table": "virtual_server_stat"},
        )
        assert response.status_code == 401

    @patch("routes.k8s.tmm_debug.exec_tmctl")
    @patch("routes.k8s.tmm_debug.KubernetesService")
    def test_tmctl_query(
        self, mock_k8s_cls, mock_tmctl,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Viewer can execute a structured tmctl query."""
        cluster = make_k8s_cluster(project=sample_project, name="tmm-tmctl-cluster")
        mock_k8s_cls.return_value = _mock_k8s_service()
        mock_tmctl.return_value = {
            "columns": ["name", "clientside.bits_in", "clientside.bits_out"],
            "rows": [
                {"name": "/Common/vs1", "clientside.bits_in": "1024", "clientside.bits_out": "2048"},
            ],
            "raw_output": "name  clientside.bits_in  clientside.bits_out\n/Common/vs1  1024  2048",
        }

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/tmm-debug/tmctl",
            json={
                "pod_name": "f5-tmm-abc",
                "table": "virtual_server_stat",
                "columns": ["name", "clientside.bits_in", "clientside.bits_out"],
                "width": 300,
            },
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["columns"]) == 3
        assert len(data["rows"]) == 1
        mock_tmctl.assert_called_once()
        call_args = mock_tmctl.call_args[0]
        assert call_args[3] == "virtual_server_stat"  # table
        assert call_args[4] == ["name", "clientside.bits_in", "clientside.bits_out"]  # columns
        assert call_args[5] == 300  # width


# ---------------------------------------------------------------------------
# POST /api/k8s/clusters/{cluster_id}/tmm-debug/configview
# ---------------------------------------------------------------------------


class TestTMMDebugConfigview:
    """POST /api/k8s/clusters/{cluster_id}/tmm-debug/configview."""

    def test_requires_auth(self, client, sample_project, make_k8s_cluster):
        cluster = make_k8s_cluster(project=sample_project, name="tmm-cv-noauth")
        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/tmm-debug/configview",
            json={"pod_name": "f5-tmm-abc", "uuid": "abc-123"},
        )
        assert response.status_code == 401

    @patch("routes.k8s.tmm_debug.exec_configview")
    @patch("routes.k8s.tmm_debug.KubernetesService")
    def test_configview_uuid(
        self, mock_k8s_cls, mock_configview,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Viewer can inspect a CR config by UUID."""
        cluster = make_k8s_cluster(project=sample_project, name="tmm-cv-cluster")
        mock_k8s_cls.return_value = _mock_k8s_service()
        mock_configview.return_value = {
            "uuid": "abc-123",
            "config": {"kind": "HTTPRoute", "name": "web-route"},
            "raw_output": "configview uuid abc-123\n{...}",
        }

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/tmm-debug/configview",
            json={"pod_name": "f5-tmm-abc", "uuid": "abc-123"},
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["uuid"] == "abc-123"
        mock_configview.assert_called_once()
        call_args = mock_configview.call_args[0]
        assert call_args[1] == "f5-tmm-abc"
        assert call_args[3] == "abc-123"


# ---------------------------------------------------------------------------
# POST /api/k8s/clusters/{cluster_id}/tmm-debug/configview/uuids
# ---------------------------------------------------------------------------


class TestTMMDebugConfigviewUuids:
    """POST /api/k8s/clusters/{cluster_id}/tmm-debug/configview/uuids."""

    def test_requires_auth(self, client, sample_project, make_k8s_cluster):
        cluster = make_k8s_cluster(project=sample_project, name="tmm-uuids-noauth")
        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/tmm-debug/configview/uuids",
            json={"pod_name": "f5-tmm-abc"},
        )
        assert response.status_code == 401

    @patch("routes.k8s.tmm_debug.discover_configview_uuids")
    @patch("routes.k8s.tmm_debug.KubernetesService")
    def test_discover_uuids(
        self, mock_k8s_cls, mock_discover,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Viewer can discover available configview UUIDs."""
        cluster = make_k8s_cluster(project=sample_project, name="tmm-uuids-cluster")
        mock_k8s_cls.return_value = _mock_k8s_service()
        mock_discover.return_value = {
            "uuids": ["abc-123", "def-456", "ghi-789"],
            "count": 3,
        }

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/tmm-debug/configview/uuids",
            json={"pod_name": "f5-tmm-abc", "namespace": "f5-bnk"},
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 3
        assert len(data["uuids"]) == 3
        mock_discover.assert_called_once()


# ---------------------------------------------------------------------------
# POST /api/k8s/clusters/{cluster_id}/tmm-debug/bdt
# ---------------------------------------------------------------------------


class TestTMMDebugBdt:
    """POST /api/k8s/clusters/{cluster_id}/tmm-debug/bdt."""

    def test_requires_auth(self, client, sample_project, make_k8s_cluster):
        cluster = make_k8s_cluster(project=sample_project, name="tmm-bdt-noauth")
        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/tmm-debug/bdt",
            json={"pod_name": "f5-tmm-abc", "subcommand": "arp"},
        )
        assert response.status_code == 401

    @patch("routes.k8s.tmm_debug.exec_bdt_cli")
    @patch("routes.k8s.tmm_debug.KubernetesService")
    def test_bdt_arp(
        self, mock_k8s_cls, mock_bdt,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Viewer can execute bdt_cli arp subcommand."""
        cluster = make_k8s_cluster(project=sample_project, name="tmm-bdt-cluster")
        mock_k8s_cls.return_value = _mock_k8s_service()
        mock_bdt.return_value = {
            "output": "10.1.1.1  00:0c:29:xx:xx:xx  internal_vlan",
            "exit_code": 0,
        }

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/tmm-debug/bdt",
            json={"pod_name": "f5-tmm-abc", "subcommand": "arp"},
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 0
        assert "10.1.1.1" in data["output"]
        mock_bdt.assert_called_once()
        call_args = mock_bdt.call_args[0]
        assert call_args[1] == "f5-tmm-abc"
        assert call_args[3] == "arp"

    @patch("routes.k8s.tmm_debug.exec_bdt_cli")
    @patch("routes.k8s.tmm_debug.KubernetesService")
    def test_bdt_connection_list(
        self, mock_k8s_cls, mock_bdt,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Viewer can execute bdt_cli 'connection list' subcommand."""
        cluster = make_k8s_cluster(project=sample_project, name="tmm-bdt-conn-cluster")
        mock_k8s_cls.return_value = _mock_k8s_service()
        mock_bdt.return_value = {
            "output": "Total connections: 42",
            "exit_code": 0,
        }

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/tmm-debug/bdt",
            json={"pod_name": "f5-tmm-abc", "subcommand": "connection list"},
            headers=viewer_headers,
        )
        assert response.status_code == 200
        mock_bdt.assert_called_once()
        assert mock_bdt.call_args[0][3] == "connection list"


# ---------------------------------------------------------------------------
# Bulk auth check
# ---------------------------------------------------------------------------


class TestTMMDebugAllEndpointsRequireAuth:
    """All TMM debug endpoints reject unauthenticated requests."""

    def test_all_endpoints_require_auth(self, client, sample_project, make_k8s_cluster):
        cluster = make_k8s_cluster(project=sample_project, name="tmm-bulk-noauth")
        cid = cluster.id

        endpoints = [
            ("GET", f"/api/k8s/clusters/{cid}/tmm-debug/pods"),
            ("POST", f"/api/k8s/clusters/{cid}/tmm-debug/exec"),
            ("POST", f"/api/k8s/clusters/{cid}/tmm-debug/tmctl"),
            ("POST", f"/api/k8s/clusters/{cid}/tmm-debug/configview"),
            ("POST", f"/api/k8s/clusters/{cid}/tmm-debug/configview/uuids"),
            ("POST", f"/api/k8s/clusters/{cid}/tmm-debug/bdt"),
        ]
        body = {"pod_name": "f5-tmm-abc", "command": "ls", "table": "t", "uuid": "u", "subcommand": "arp"}

        for method, url in endpoints:
            if method == "GET":
                resp = client.get(url)
            else:
                resp = client.post(url, json=body)
            assert resp.status_code == 401, f"{method} {url} should require auth"
