"""
Integration tests for SSH tunnel routes — /api/k8s/tunnels and /api/k8s/clusters/{id}/tunnel.

Covers: list tunnels, tunnel status, RBAC for open/close.
Uses FastAPI TestClient with real SQLite DB. SSH tunnel manager is mocked.
"""

from unittest.mock import MagicMock, patch

import pytest


class TestListTunnels:
    """GET /api/k8s/tunnels."""

    @patch("services.ssh_tunnel_manager.get_tunnel_manager")
    def test_list_tunnels(self, mock_get_mgr, client, viewer_headers, all_test_users):
        """Viewer can list all active tunnels (empty list)."""
        mock_mgr = MagicMock()
        mock_mgr.get_all_tunnel_statuses.return_value = []
        mock_get_mgr.return_value = mock_mgr

        response = client.get("/api/k8s/tunnels", headers=viewer_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["tunnels"] == []
        mock_mgr.get_all_tunnel_statuses.assert_called_once()

    @patch("services.ssh_tunnel_manager.get_tunnel_manager")
    def test_list_tunnels_with_active(self, mock_get_mgr, client, viewer_headers, all_test_users):
        """Viewer sees active tunnels when present."""
        mock_mgr = MagicMock()
        mock_mgr.get_all_tunnel_statuses.return_value = [
            {"cluster_id": 1, "local_port": 16443, "healthy": True},
        ]
        mock_get_mgr.return_value = mock_mgr

        response = client.get("/api/k8s/tunnels", headers=viewer_headers)
        assert response.status_code == 200
        assert len(response.json()["tunnels"]) == 1


class TestGetTunnelStatus:
    """GET /api/k8s/clusters/{cluster_id}/tunnel."""

    @patch("services.ssh_tunnel_manager.get_tunnel_manager")
    def test_get_tunnel_status(
        self, mock_get_mgr, client, viewer_headers, all_test_users,
        sample_project, make_k8s_cluster,
    ):
        """Viewer can get tunnel status for a specific cluster."""
        cluster = make_k8s_cluster(project=sample_project, name="tunnel-status-cluster")

        mock_mgr = MagicMock()
        mock_mgr.get_tunnel_status.return_value = {
            "cluster_id": cluster.id,
            "local_port": 16443,
            "healthy": True,
        }
        mock_get_mgr.return_value = mock_mgr

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/tunnel",
            headers=viewer_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["cluster_id"] == cluster.id
        assert data["tunnel_active"] is True
        assert data["tunnel"]["local_port"] == 16443


class TestTunnelRBAC:
    """RBAC enforcement for tunnel open/close endpoints."""

    def test_viewer_cannot_open(
        self, client, viewer_headers, all_test_users,
        sample_project, make_k8s_cluster,
    ):
        """Viewer cannot open a tunnel — returns 403."""
        cluster = make_k8s_cluster(project=sample_project, name="viewer-open-cluster")

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/tunnel/open",
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_viewer_cannot_close(
        self, client, viewer_headers, all_test_users,
        sample_project, make_k8s_cluster,
    ):
        """Viewer cannot close a tunnel — returns 403."""
        cluster = make_k8s_cluster(project=sample_project, name="viewer-close-cluster")

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/tunnel/close",
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_unauthenticated_cannot_list(self, client):
        """Unauthenticated request to list tunnels returns 401."""
        response = client.get("/api/k8s/tunnels")
        assert response.status_code == 401
