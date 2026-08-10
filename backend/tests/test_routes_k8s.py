"""
Integration tests for Kubernetes cluster routes.

Tests:
  12. test_list_clusters — GET /api/k8s/clusters (may return empty)
  13. test_register_cluster — POST /api/projects/{id}/k8s/clusters with kubeconfig
"""

from unittest.mock import MagicMock, patch

import pytest


class TestK8sRoutes:
    """Route-level integration tests for /api/k8s."""

    def test_list_clusters(self, client, admin_headers, sample_user):
        """GET /api/k8s/clusters returns a list (possibly empty)."""
        with patch("routes.k8s.clusters.ClusterManagementService") as MockService:
            MockService.return_value.list_all_clusters.return_value = {
                "clusters": [],
                "total": 0,
            }
            response = client.get("/api/k8s/clusters", headers=admin_headers)

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

        data = response.json()
        assert "clusters" in data
        assert isinstance(data["clusters"], list)

    def test_register_cluster(
        self, client, admin_headers, sample_user, sample_project
    ):
        """POST /api/projects/{id}/k8s/clusters registers a new cluster."""
        cluster_payload = {
            "name": "test-cluster",
            "context": "test-k8s-context",
            "kubeconfig": "apiVersion: v1\nclusters: []\nkind: Config",
        }

        mock_result = {
            "id": 1,
            "name": "test-cluster",
            "context": "test-k8s-context",
            "status": "pending",
            "project_id": sample_project.id,
        }

        with patch("routes.k8s.clusters.ClusterManagementService") as MockService:
            MockService.return_value.create_cluster.return_value = mock_result
            response = client.post(
                f"/api/projects/{sample_project.id}/k8s/clusters",
                json=cluster_payload,
                headers=admin_headers,
            )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

        data = response.json()
        assert data["name"] == "test-cluster"
        assert data["id"] == 1
