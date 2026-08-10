"""
Integration tests for Kubernetes cluster routes — /api/k8s/clusters and /api/projects/{pid}/k8s/clusters.

Covers: CRUD, test connection, RBAC enforcement, not-found handling.
Uses FastAPI TestClient with real SQLite DB. External services are mocked.
"""

from unittest.mock import MagicMock, patch

import pytest

from models import KubernetesCluster


class TestClusterCreate:
    """POST /api/projects/{pid}/k8s/clusters."""

    @patch("routes.k8s.clusters.ClusterManagementService")
    def test_create_cluster(self, mock_svc_cls, client, admin_headers, sample_user, sample_project):
        """Admin can create a cluster via the service."""
        mock_svc = MagicMock()
        mock_svc.create_cluster.return_value = {
            "id": 1,
            "name": "test-cluster",
            "cloud_provider": "aws",
            "region": "us-east-1",
            "context": "test-ctx",
            "default_namespace": "default",
        }
        mock_svc_cls.return_value = mock_svc

        response = client.post(
            f"/api/projects/{sample_project.id}/k8s/clusters",
            json={
                "name": "test-cluster",
                "kubeconfig": "YXBpVmVyc2lvbjogdjEK",  # base64 dummy
                "cloud_provider": "aws",
                "region": "us-east-1",
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "test-cluster"
        assert data["cloud_provider"] == "aws"
        mock_svc.create_cluster.assert_called_once()

    @patch("routes.k8s.clusters.ClusterManagementService")
    def test_create_cluster_operator_allowed(self, mock_svc_cls, client, operator_headers, all_test_users, sample_project):
        """Operator can create clusters."""
        mock_svc = MagicMock()
        mock_svc.create_cluster.return_value = {"id": 2, "name": "op-cluster"}
        mock_svc_cls.return_value = mock_svc

        response = client.post(
            f"/api/projects/{sample_project.id}/k8s/clusters",
            json={
                "name": "op-cluster",
                "kubeconfig": "YXBpVmVyc2lvbjogdjEK",
            },
            headers=operator_headers,
        )
        assert response.status_code == 200

    def test_viewer_cannot_create(self, client, viewer_headers, all_test_users, sample_project):
        """Viewer cannot create clusters — returns 403."""
        response = client.post(
            f"/api/projects/{sample_project.id}/k8s/clusters",
            json={
                "name": "viewer-cluster",
                "kubeconfig": "YXBpVmVyc2lvbjogdjEK",
            },
            headers=viewer_headers,
        )
        assert response.status_code == 403


class TestClusterList:
    """GET /api/k8s/clusters."""

    @patch("routes.k8s.clusters.ClusterManagementService")
    def test_list_clusters(self, mock_svc_cls, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer can list all clusters."""
        cluster = make_k8s_cluster(project=sample_project, name="list-cluster-1")
        mock_svc = MagicMock()
        mock_svc.list_all_clusters.return_value = {
            "clusters": [
                {"id": cluster.id, "name": cluster.name, "status": "active"},
            ],
            "count": 1,
        }
        mock_svc_cls.return_value = mock_svc

        response = client.get("/api/k8s/clusters", headers=viewer_headers)
        assert response.status_code == 200
        data = response.json()
        assert "clusters" in data
        assert isinstance(data["clusters"], list)
        assert len(data["clusters"]) >= 1

    def test_list_clusters_unauthenticated(self, client):
        """Unauthenticated request returns 401."""
        response = client.get("/api/k8s/clusters")
        assert response.status_code == 401


class TestClusterDetail:
    """GET /api/k8s/clusters/{id}."""

    @patch("routes.k8s.clusters.ClusterManagementService")
    def test_get_cluster_details(self, mock_svc_cls, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer can get cluster details."""
        cluster = make_k8s_cluster(project=sample_project, name="detail-cluster")
        mock_svc = MagicMock()
        mock_svc.get_cluster_details.return_value = {
            "id": cluster.id,
            "name": cluster.name,
            "cloud_provider": None,
            "region": None,
            "status": "active",
        }
        mock_svc_cls.return_value = mock_svc

        response = client.get(f"/api/k8s/clusters/{cluster.id}", headers=viewer_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == cluster.id
        assert data["name"] == "detail-cluster"

    @patch("routes.k8s.clusters.ClusterManagementService")
    def test_cluster_not_found(self, mock_svc_cls, client, admin_headers, sample_user):
        """GET nonexistent cluster returns 404."""
        from core.errors import NotFoundError

        mock_svc = MagicMock()
        mock_svc.get_cluster_details.side_effect = NotFoundError("cluster", 99999)
        mock_svc_cls.return_value = mock_svc

        response = client.get("/api/k8s/clusters/99999", headers=admin_headers)
        assert response.status_code == 404


class TestClusterUpdate:
    """PUT /api/k8s/clusters/{id}."""

    @patch("routes.k8s.clusters.ClusterManagementService")
    def test_update_cluster(self, mock_svc_cls, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Admin can update cluster configuration."""
        cluster = make_k8s_cluster(project=sample_project, name="update-cluster")
        mock_svc = MagicMock()
        mock_svc.update_cluster.return_value = {
            "id": cluster.id,
            "name": "updated-cluster",
            "cloud_provider": "azure",
        }
        mock_svc_cls.return_value = mock_svc

        response = client.put(
            f"/api/k8s/clusters/{cluster.id}",
            json={"name": "updated-cluster", "cloud_provider": "azure"},
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "updated-cluster"
        mock_svc.update_cluster.assert_called_once()

    def test_viewer_cannot_update(self, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer cannot update clusters — returns 403."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.put(
            f"/api/k8s/clusters/{cluster.id}",
            json={"name": "hacked"},
            headers=viewer_headers,
        )
        assert response.status_code == 403


class TestClusterDelete:
    """DELETE /api/k8s/clusters/{id}."""

    @patch("routes.k8s.clusters.ClusterManagementService")
    def test_delete_cluster(self, mock_svc_cls, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Admin can delete a cluster."""
        cluster = make_k8s_cluster(project=sample_project, name="delete-cluster")
        mock_svc = MagicMock()
        mock_svc.delete_cluster.return_value = {"success": True, "message": "Cluster deleted"}
        mock_svc_cls.return_value = mock_svc

        response = client.delete(f"/api/k8s/clusters/{cluster.id}", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        mock_svc.delete_cluster.assert_called_once_with(cluster.id)

    def test_viewer_cannot_delete(self, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer cannot delete clusters — returns 403."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.delete(f"/api/k8s/clusters/{cluster.id}", headers=viewer_headers)
        assert response.status_code == 403


class TestClusterTestConnection:
    """POST /api/k8s/clusters/{id}/test."""

    @patch("routes.k8s.clusters.KubernetesService")
    def test_test_connection(self, mock_k8s_svc_cls, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Operator/admin can test cluster connection."""
        cluster = make_k8s_cluster(project=sample_project, name="conn-test-cluster")
        mock_svc = MagicMock()
        mock_svc.test_connection.return_value = {
            "success": True,
            "message": "Connection successful",
            "server_version": "v1.28.0",
        }
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.post(f"/api/k8s/clusters/{cluster.id}/test", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        mock_svc.test_connection.assert_called_once_with(cluster.id)

    def test_viewer_cannot_test_connection(self, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer cannot test cluster connection — returns 403."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.post(f"/api/k8s/clusters/{cluster.id}/test", headers=viewer_headers)
        assert response.status_code == 403
