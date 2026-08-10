"""
Component tests verifying that cluster register/update/refresh routes
enqueue a background scan after committing the cluster row.

Pattern mirrors test_project_module_service.py:685-721:
  patch scan_cluster_async, hit route via TestClient, assert .delay called.

These tests use the existing `client`, `admin_headers`, `sample_user`,
and `sample_project` fixtures from conftest.py.
"""

from unittest.mock import MagicMock, patch

import pytest

_SCAN_TASK = "tasks.cluster_scan_task.scan_cluster_async"


class TestClusterRouteEnqueuesScans:
    """Routes enqueue scan_cluster_async after committing a cluster row."""

    def test_add_cluster_enqueues_scan(self, client, admin_headers, sample_user, sample_project):
        """POST /api/projects/{id}/k8s/clusters enqueues a scan with the new cluster id."""
        mock_result = {
            "id": 7,
            "name": "ocp-cluster",
            "context": "ocp-context",
            "status": "pending",
            "project_id": sample_project.id,
        }

        with patch("routes.k8s.clusters.ClusterManagementService") as MockService, \
             patch(_SCAN_TASK) as mock_scan_task:
            MockService.return_value.create_cluster.return_value = mock_result

            response = client.post(
                f"/api/projects/{sample_project.id}/k8s/clusters",
                json={"name": "ocp-cluster", "context": "ocp-context", "kubeconfig": "apiVersion: v1"},
                headers=admin_headers,
            )

        assert response.status_code == 200, response.text
        mock_scan_task.delay.assert_called_once_with(7)

    def test_update_cluster_enqueues_scan(
        self, client, admin_headers, sample_user, sample_project, make_k8s_cluster
    ):
        """PUT /api/k8s/clusters/{id} enqueues a scan with the cluster id."""
        cluster = make_k8s_cluster(project=sample_project, name="update-cluster")

        mock_result = {
            "id": cluster.id,
            "name": "update-cluster",
            "context": "new-context",
            "status": "active",
        }

        with patch("routes.k8s.clusters.ClusterManagementService") as MockService, \
             patch(_SCAN_TASK) as mock_scan_task:
            MockService.return_value.update_cluster.return_value = mock_result

            response = client.put(
                f"/api/k8s/clusters/{cluster.id}",
                json={"name": "update-cluster", "context": "new-context"},
                headers=admin_headers,
            )

        assert response.status_code == 200, response.text
        mock_scan_task.delay.assert_called_once_with(cluster.id)

    def test_refresh_kubeconfig_enqueues_scan(
        self, client, admin_headers, sample_user, sample_project, make_k8s_cluster
    ):
        """POST /api/k8s/clusters/{id}/refresh-kubeconfig enqueues a scan."""
        cluster = make_k8s_cluster(project=sample_project, name="refresh-cluster")

        mock_result = {
            "message": "Refreshed",
            "cluster_id": cluster.id,
            "cluster_name": "refresh-cluster",
        }

        with patch("routes.k8s.clusters.ClusterManagementService") as MockService, \
             patch(_SCAN_TASK) as mock_scan_task:
            MockService.return_value.refresh_kubeconfig.return_value = mock_result

            response = client.post(
                f"/api/k8s/clusters/{cluster.id}/refresh-kubeconfig",
                headers=admin_headers,
            )

        assert response.status_code == 200, response.text
        mock_scan_task.delay.assert_called_once_with(cluster.id)
