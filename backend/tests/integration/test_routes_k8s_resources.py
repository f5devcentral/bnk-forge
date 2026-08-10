"""
Integration tests for Kubernetes resource routes — /api/k8s/clusters/{id}/resources/...

Covers: list, create, update, delete, patch resources; labels/annotations;
pod logs/restart/containers; deployment scale/rollout; node cordon/uncordon/drain;
events; describe; metrics (top pods/nodes); RBAC enforcement.

ALL endpoints delegate to KubernetesService, so every test mocks the service.
Uses FastAPI TestClient with real SQLite DB.
"""

from unittest.mock import MagicMock, patch

import pytest

from models import KubernetesCluster


class TestResourceList:
    """GET /api/k8s/clusters/{cluster_id}/resources/{resource_type}."""

    @patch("routes.k8s.resources.KubernetesService")
    def test_list_resources(self, mock_k8s_svc_cls, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer can list Kubernetes resources."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.get_resources.return_value = [
            {"name": "nginx-pod", "namespace": "default", "status": "Running"},
        ]
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/resources/pod?namespace=default",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["resource_type"] == "pod"
        assert data["resources"][0]["name"] == "nginx-pod"

    @patch("routes.k8s.resources.KubernetesService")
    def test_list_resources_empty(self, mock_k8s_svc_cls, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Listing resources with no results returns empty list."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.get_resources.return_value = []
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/resources/deployment",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0
        assert data["resources"] == []


class TestResourceCreate:
    """POST /api/k8s/clusters/{cluster_id}/resources/{resource_type}."""

    @patch("routes.k8s.resources.KubernetesService")
    def test_create_resource(self, mock_k8s_svc_cls, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Admin can create a Kubernetes resource."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.create_resource.return_value = {
            "success": True,
            "message": "Resource created",
            "resource": {"name": "test-svc", "kind": "Service"},
        }
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/resources/service",
            json={
                "resource_yaml": "apiVersion: v1\nkind: Service\nmetadata:\n  name: test-svc",
                "namespace": "default",
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        mock_svc.create_resource.assert_called_once()

    def test_viewer_cannot_create(self, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer cannot create resources — returns 403."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/resources/service",
            json={
                "resource_yaml": "apiVersion: v1\nkind: Service",
                "namespace": "default",
            },
            headers=viewer_headers,
        )
        assert response.status_code == 403


class TestResourceDelete:
    """DELETE /api/k8s/clusters/{cluster_id}/resources/{resource_type}/{resource_name}."""

    @patch("routes.k8s.resources.KubernetesService")
    def test_delete_resource(self, mock_k8s_svc_cls, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Admin can delete a Kubernetes resource."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.delete_resource.return_value = {
            "success": True,
            "message": "Resource deleted",
        }
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.delete(
            f"/api/k8s/clusters/{cluster.id}/resources/service/test-svc?namespace=default",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        mock_svc.delete_resource.assert_called_once()

    def test_viewer_cannot_delete(self, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer cannot delete resources — returns 403."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.delete(
            f"/api/k8s/clusters/{cluster.id}/resources/pod/nginx-pod?namespace=default",
            headers=viewer_headers,
        )
        assert response.status_code == 403


class TestPodLogs:
    """GET /api/k8s/clusters/{cluster_id}/pods/{pod_name}/logs."""

    @patch("routes.k8s.resources.KubernetesService")
    def test_get_pod_logs(self, mock_k8s_svc_cls, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer can read pod logs."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.get_pod_logs.return_value = "2024-01-01 INFO Starting application...\n2024-01-01 INFO Ready."
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/pods/my-pod/logs?namespace=default&tail_lines=50",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "logs" in data
        assert data["pod_name"] == "my-pod"
        assert data["namespace"] == "default"
        mock_svc.get_pod_logs.assert_called_once()


class TestDeploymentScale:
    """POST /api/k8s/clusters/{cluster_id}/deployments/{name}/scale."""

    @patch("routes.k8s.resources.KubernetesService")
    def test_scale_deployment(self, mock_k8s_svc_cls, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Admin can scale a deployment."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.scale_deployment.return_value = {
            "success": True,
            "message": "Deployment scaled to 5 replicas",
            "replicas": 5,
        }
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/deployments/nginx/scale",
            json={"replicas": 5, "namespace": "default"},
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["replicas"] == 5
        mock_svc.scale_deployment.assert_called_once()

    def test_viewer_cannot_scale(self, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer cannot scale deployments — returns 403."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/deployments/nginx/scale",
            json={"replicas": 10, "namespace": "default"},
            headers=viewer_headers,
        )
        assert response.status_code == 403


# ============================================================================
# Resource Update (PUT)
# ============================================================================

class TestResourceUpdate:
    """PUT /api/k8s/clusters/{cluster_id}/resources/{resource_type}/{resource_name}."""

    @patch("routes.k8s.resources.KubernetesService")
    def test_update_resource(self, mock_k8s_svc_cls, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Admin can update an existing Kubernetes resource."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.update_resource.return_value = {
            "success": True,
            "message": "Resource updated",
            "resource": {"name": "nginx-svc", "kind": "Service"},
        }
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.put(
            f"/api/k8s/clusters/{cluster.id}/resources/service/nginx-svc",
            json={
                "resource_yaml": "apiVersion: v1\nkind: Service\nmetadata:\n  name: nginx-svc",
                "namespace": "default",
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        mock_svc.update_resource.assert_called_once()

    @patch("routes.k8s.resources.KubernetesService")
    def test_update_resource_dry_run(self, mock_k8s_svc_cls, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Dry-run update returns preview without applying."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.update_resource.return_value = {
            "success": True,
            "message": "Dry-run: resource would be updated",
            "resource": {"name": "nginx-svc", "kind": "Service"},
        }
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.put(
            f"/api/k8s/clusters/{cluster.id}/resources/service/nginx-svc",
            json={
                "resource_yaml": "apiVersion: v1\nkind: Service\nmetadata:\n  name: nginx-svc",
                "namespace": "default",
                "dry_run": True,
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        mock_svc.update_resource.assert_called_once()

    def test_update_unauthenticated(self, client, sample_project, make_k8s_cluster):
        """Unauthenticated request returns 401."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.put(
            f"/api/k8s/clusters/{cluster.id}/resources/service/nginx-svc",
            json={"resource_yaml": "apiVersion: v1", "namespace": "default"},
        )
        assert response.status_code == 401

    def test_viewer_cannot_update(self, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer cannot update resources — returns 403."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.put(
            f"/api/k8s/clusters/{cluster.id}/resources/service/nginx-svc",
            json={
                "resource_yaml": "apiVersion: v1\nkind: Service",
                "namespace": "default",
            },
            headers=viewer_headers,
        )
        assert response.status_code == 403


# ============================================================================
# Resource Patch (PATCH)
# ============================================================================

class TestResourcePatch:
    """PATCH /api/k8s/clusters/{cluster_id}/resources/{resource_type}/{resource_name}."""

    @patch("routes.k8s.resources.KubernetesService")
    def test_patch_resource_strategic(self, mock_k8s_svc_cls, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Admin can strategic-merge-patch a resource (default patch type)."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.patch_resource.return_value = {
            "success": True,
            "message": "Resource patched",
            "resource": {"name": "nginx-deploy", "kind": "Deployment"},
        }
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.patch(
            f"/api/k8s/clusters/{cluster.id}/resources/deployment/nginx-deploy",
            json={
                "patch_data": {"spec": {"replicas": 3}},
                "namespace": "default",
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        mock_svc.patch_resource.assert_called_once()

    @patch("routes.k8s.resources.KubernetesService")
    def test_patch_resource_merge(self, mock_k8s_svc_cls, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Admin can use JSON merge patch type."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.patch_resource.return_value = {
            "success": True,
            "message": "Resource patched",
        }
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.patch(
            f"/api/k8s/clusters/{cluster.id}/resources/service/my-svc",
            json={
                "patch_data": {"metadata": {"labels": {"env": "prod"}}},
                "namespace": "default",
                "patch_type": "merge",
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        mock_svc.patch_resource.assert_called_once()

    def test_patch_unauthenticated(self, client, sample_project, make_k8s_cluster):
        """Unauthenticated request returns 401."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.patch(
            f"/api/k8s/clusters/{cluster.id}/resources/deployment/nginx",
            json={"patch_data": {"spec": {"replicas": 1}}, "namespace": "default"},
        )
        assert response.status_code == 401

    def test_viewer_cannot_patch(self, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer cannot patch resources — returns 403."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.patch(
            f"/api/k8s/clusters/{cluster.id}/resources/deployment/nginx",
            json={"patch_data": {"spec": {"replicas": 1}}, "namespace": "default"},
            headers=viewer_headers,
        )
        assert response.status_code == 403


# ============================================================================
# Label Resource
# ============================================================================

class TestLabelResource:
    """POST /api/k8s/clusters/{cluster_id}/resources/{resource_type}/{resource_name}/label."""

    @patch("routes.k8s.resources.KubernetesService")
    def test_label_resource(self, mock_k8s_svc_cls, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Admin can add labels to a resource."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.label_resource.return_value = {
            "success": True,
            "message": "Labels applied",
        }
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/resources/pod/nginx-pod/label",
            json={
                "labels": {"env": "production", "team": "platform"},
                "namespace": "default",
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        mock_svc.label_resource.assert_called_once()

    @patch("routes.k8s.resources.KubernetesService")
    def test_label_resource_with_overwrite(self, mock_k8s_svc_cls, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Admin can overwrite existing labels."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.label_resource.return_value = {"success": True, "message": "Labels applied"}
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/resources/pod/nginx-pod/label",
            json={
                "labels": {"env": "staging"},
                "namespace": "default",
                "overwrite": True,
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        mock_svc.label_resource.assert_called_once()

    def test_label_unauthenticated(self, client, sample_project, make_k8s_cluster):
        """Unauthenticated request returns 401."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/resources/pod/nginx-pod/label",
            json={"labels": {"env": "test"}, "namespace": "default"},
        )
        assert response.status_code == 401

    def test_viewer_cannot_label(self, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer cannot label resources — returns 403."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/resources/pod/nginx-pod/label",
            json={"labels": {"env": "test"}, "namespace": "default"},
            headers=viewer_headers,
        )
        assert response.status_code == 403


# ============================================================================
# Annotate Resource
# ============================================================================

class TestAnnotateResource:
    """POST /api/k8s/clusters/{cluster_id}/resources/{resource_type}/{resource_name}/annotate."""

    @patch("routes.k8s.resources.KubernetesService")
    def test_annotate_resource(self, mock_k8s_svc_cls, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Admin can add annotations to a resource."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.annotate_resource.return_value = {
            "success": True,
            "message": "Annotations applied",
        }
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/resources/deployment/nginx/annotate",
            json={
                "annotations": {"description": "Main web server", "owner": "platform-team"},
                "namespace": "default",
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        mock_svc.annotate_resource.assert_called_once()

    @patch("routes.k8s.resources.KubernetesService")
    def test_annotate_resource_with_overwrite(self, mock_k8s_svc_cls, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Admin can overwrite existing annotations."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.annotate_resource.return_value = {"success": True, "message": "Annotations applied"}
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/resources/deployment/nginx/annotate",
            json={
                "annotations": {"description": "Updated"},
                "namespace": "default",
                "overwrite": True,
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        mock_svc.annotate_resource.assert_called_once()

    def test_annotate_unauthenticated(self, client, sample_project, make_k8s_cluster):
        """Unauthenticated request returns 401."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/resources/deployment/nginx/annotate",
            json={"annotations": {"foo": "bar"}, "namespace": "default"},
        )
        assert response.status_code == 401

    def test_viewer_cannot_annotate(self, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer cannot annotate resources — returns 403."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/resources/deployment/nginx/annotate",
            json={"annotations": {"foo": "bar"}, "namespace": "default"},
            headers=viewer_headers,
        )
        assert response.status_code == 403


# ============================================================================
# Pod Restart
# ============================================================================

class TestPodRestart:
    """POST /api/k8s/clusters/{cluster_id}/pods/{pod_name}/restart."""

    @patch("routes.k8s.resources.KubernetesService")
    def test_restart_pod(self, mock_k8s_svc_cls, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Admin can restart (delete) a pod."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.restart_pod.return_value = {
            "success": True,
            "message": "Pod nginx-pod deleted for restart",
        }
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/pods/nginx-pod/restart?namespace=default",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        mock_svc.restart_pod.assert_called_once()

    def test_restart_pod_unauthenticated(self, client, sample_project, make_k8s_cluster):
        """Unauthenticated request returns 401."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/pods/nginx-pod/restart?namespace=default",
        )
        assert response.status_code == 401

    def test_viewer_cannot_restart_pod(self, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer cannot restart pods — returns 403."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/pods/nginx-pod/restart?namespace=default",
            headers=viewer_headers,
        )
        assert response.status_code == 403


# ============================================================================
# Pod Containers
# ============================================================================

class TestPodContainers:
    """GET /api/k8s/clusters/{cluster_id}/pods/{pod_name}/containers."""

    @patch("routes.k8s.resources.KubernetesService")
    def test_list_pod_containers(self, mock_k8s_svc_cls, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer can list containers in a pod."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.get_pod_containers.return_value = {
            "pod_name": "nginx-pod",
            "namespace": "default",
            "containers": [
                {"name": "nginx", "image": "nginx:1.25", "state": "running"},
                {"name": "sidecar", "image": "fluentbit:2.1", "state": "running"},
            ],
            "init_containers": [
                {"name": "init-config", "image": "busybox:1.36", "state": "terminated"},
            ],
        }
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/pods/nginx-pod/containers?namespace=default",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["pod_name"] == "nginx-pod"
        assert len(data["containers"]) == 2
        assert len(data["init_containers"]) == 1
        mock_svc.get_pod_containers.assert_called_once()

    def test_containers_unauthenticated(self, client, sample_project, make_k8s_cluster):
        """Unauthenticated request returns 401."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/pods/nginx-pod/containers?namespace=default",
        )
        assert response.status_code == 401


# ============================================================================
# Describe Resource
# ============================================================================

class TestDescribeResource:
    """GET /api/k8s/clusters/{cluster_id}/resources/{resource_type}/{resource_name}/describe."""

    @patch("routes.k8s.resources.KubernetesService")
    def test_describe_resource(self, mock_k8s_svc_cls, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer can describe a resource."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.describe_resource.return_value = {
            "name": "nginx-deploy",
            "kind": "Deployment",
            "namespace": "default",
            "status": {"replicas": 3, "availableReplicas": 3},
            "conditions": [{"type": "Available", "status": "True"}],
            "events": [{"type": "Normal", "reason": "ScalingReplicaSet", "message": "Scaled up"}],
        }
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/resources/deployment/nginx-deploy/describe?namespace=default",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "nginx-deploy"
        assert data["kind"] == "Deployment"
        assert "conditions" in data
        assert "events" in data
        mock_svc.describe_resource.assert_called_once()

    @patch("routes.k8s.resources.KubernetesService")
    def test_describe_cluster_scoped_resource(self, mock_k8s_svc_cls, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Describe works without namespace for cluster-scoped resources."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.describe_resource.return_value = {
            "name": "worker-1",
            "kind": "Node",
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/resources/node/worker-1/describe",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "worker-1"
        mock_svc.describe_resource.assert_called_once()

    def test_describe_unauthenticated(self, client, sample_project, make_k8s_cluster):
        """Unauthenticated request returns 401."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/resources/deployment/nginx/describe?namespace=default",
        )
        assert response.status_code == 401


# ============================================================================
# Cluster Events
# ============================================================================

class TestClusterEvents:
    """GET /api/k8s/clusters/{cluster_id}/events."""

    @patch("routes.k8s.resources.KubernetesService")
    def test_get_events(self, mock_k8s_svc_cls, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer can get cluster events."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.get_events.return_value = [
            {
                "type": "Normal",
                "reason": "Scheduled",
                "message": "Successfully assigned default/nginx to node-1",
                "namespace": "default",
                "involved_object": {"kind": "Pod", "name": "nginx"},
            },
            {
                "type": "Warning",
                "reason": "BackOff",
                "message": "Back-off restarting failed container",
                "namespace": "default",
                "involved_object": {"kind": "Pod", "name": "crasher"},
            },
        ]
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/events?namespace=default",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        assert len(data["events"]) == 2
        mock_svc.get_events.assert_called_once()

    @patch("routes.k8s.resources.KubernetesService")
    def test_get_events_filtered_by_type(self, mock_k8s_svc_cls, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Events can be filtered by event_type query param."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.get_events.return_value = [
            {"type": "Warning", "reason": "BackOff", "message": "crash"},
        ]
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/events?event_type=Warning",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        mock_svc.get_events.assert_called_once()

    @patch("routes.k8s.resources.KubernetesService")
    def test_get_events_empty(self, mock_k8s_svc_cls, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Empty events list returns count 0."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.get_events.return_value = []
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/events",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0
        assert data["events"] == []

    def test_events_unauthenticated(self, client, sample_project, make_k8s_cluster):
        """Unauthenticated request returns 401."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.get(f"/api/k8s/clusters/{cluster.id}/events")
        assert response.status_code == 401


# ============================================================================
# Pod Metrics (kubectl top pods)
# ============================================================================

class TestPodMetrics:
    """GET /api/k8s/clusters/{cluster_id}/top/pods."""

    @patch("routes.k8s.resources.KubernetesService")
    def test_get_pod_metrics(self, mock_k8s_svc_cls, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer can get pod resource usage metrics."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.get_pod_metrics.return_value = {
            "available": True,
            "metrics": [
                {"name": "nginx-abc123", "namespace": "default", "cpu_millicores": 10, "memory_bytes": 67108864},
                {"name": "redis-xyz789", "namespace": "default", "cpu_millicores": 25, "memory_bytes": 134217728},
            ],
        }
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/top/pods?namespace=default",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["available"] is True
        assert len(data["metrics"]) == 2
        mock_svc.get_pod_metrics.assert_called_once()

    @patch("routes.k8s.resources.KubernetesService")
    def test_get_pod_metrics_sorted(self, mock_k8s_svc_cls, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Pod metrics can be sorted by a field."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.get_pod_metrics.return_value = {"available": True, "metrics": []}
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/top/pods?sort_by=cpu",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        mock_svc.get_pod_metrics.assert_called_once()

    def test_pod_metrics_unauthenticated(self, client, sample_project, make_k8s_cluster):
        """Unauthenticated request returns 401."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.get(f"/api/k8s/clusters/{cluster.id}/top/pods")
        assert response.status_code == 401


# ============================================================================
# Node Metrics (kubectl top nodes)
# ============================================================================

class TestNodeMetrics:
    """GET /api/k8s/clusters/{cluster_id}/top/nodes."""

    @patch("routes.k8s.resources.KubernetesService")
    def test_get_node_metrics(self, mock_k8s_svc_cls, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer can get node resource usage metrics."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.get_node_metrics.return_value = {
            "available": True,
            "metrics": [
                {"name": "node-1", "cpu_millicores": 500, "cpu_percent": 25.0, "memory_bytes": 4294967296, "memory_percent": 50.0},
                {"name": "node-2", "cpu_millicores": 250, "cpu_percent": 12.0, "memory_bytes": 2147483648, "memory_percent": 25.0},
            ],
        }
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/top/nodes",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["available"] is True
        assert len(data["metrics"]) == 2
        mock_svc.get_node_metrics.assert_called_once()

    @patch("routes.k8s.resources.KubernetesService")
    def test_get_node_metrics_sorted(self, mock_k8s_svc_cls, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Node metrics can be sorted by a field."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.get_node_metrics.return_value = {"available": True, "metrics": []}
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/top/nodes?sort_by=memory",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        mock_svc.get_node_metrics.assert_called_once()

    def test_node_metrics_unauthenticated(self, client, sample_project, make_k8s_cluster):
        """Unauthenticated request returns 401."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.get(f"/api/k8s/clusters/{cluster.id}/top/nodes")
        assert response.status_code == 401


# ============================================================================
# Rollout History
# ============================================================================

class TestRolloutHistory:
    """GET /api/k8s/clusters/{cluster_id}/deployments/{name}/rollout/history."""

    @patch("routes.k8s.resources.KubernetesService")
    def test_get_rollout_history(self, mock_k8s_svc_cls, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer can get deployment rollout history."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.get_rollout_history.return_value = [
            {"revision": 1, "change_cause": "Initial deploy"},
            {"revision": 2, "change_cause": "Image update to nginx:1.26"},
            {"revision": 3, "change_cause": "Config change"},
        ]
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/deployments/nginx/rollout/history?namespace=default",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["deployment_name"] == "nginx"
        assert data["namespace"] == "default"
        assert len(data["history"]) == 3
        mock_svc.get_rollout_history.assert_called_once()

    def test_rollout_history_unauthenticated(self, client, sample_project, make_k8s_cluster):
        """Unauthenticated request returns 401."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/deployments/nginx/rollout/history?namespace=default",
        )
        assert response.status_code == 401


# ============================================================================
# Rollout Status
# ============================================================================

class TestRolloutStatus:
    """GET /api/k8s/clusters/{cluster_id}/deployments/{name}/rollout/status."""

    @patch("routes.k8s.resources.KubernetesService")
    def test_get_rollout_status(self, mock_k8s_svc_cls, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer can get deployment rollout status."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.get_rollout_status.return_value = {
            "deployment_name": "nginx",
            "namespace": "default",
            "replicas": 3,
            "ready_replicas": 3,
            "updated_replicas": 3,
            "available_replicas": 3,
            "conditions": [
                {"type": "Available", "status": "True", "reason": "MinimumReplicasAvailable"},
                {"type": "Progressing", "status": "True", "reason": "NewReplicaSetAvailable"},
            ],
        }
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/deployments/nginx/rollout/status?namespace=default",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["deployment_name"] == "nginx"
        assert data["ready_replicas"] == 3
        assert "conditions" in data
        mock_svc.get_rollout_status.assert_called_once()

    def test_rollout_status_unauthenticated(self, client, sample_project, make_k8s_cluster):
        """Unauthenticated request returns 401."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/deployments/nginx/rollout/status?namespace=default",
        )
        assert response.status_code == 401


# ============================================================================
# Rollout Undo
# ============================================================================

class TestRolloutUndo:
    """POST /api/k8s/clusters/{cluster_id}/deployments/{name}/rollout/undo."""

    @patch("routes.k8s.resources.KubernetesService")
    def test_rollout_undo(self, mock_k8s_svc_cls, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Admin can rollback a deployment to the previous revision."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.rollout_undo.return_value = {
            "success": True,
            "message": "Deployment nginx rolled back",
        }
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/deployments/nginx/rollout/undo?namespace=default",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        mock_svc.rollout_undo.assert_called_once()

    @patch("routes.k8s.resources.KubernetesService")
    def test_rollout_undo_to_revision(self, mock_k8s_svc_cls, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Admin can rollback to a specific revision."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.rollout_undo.return_value = {
            "success": True,
            "message": "Deployment nginx rolled back to revision 2",
        }
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/deployments/nginx/rollout/undo?namespace=default&revision=2",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        mock_svc.rollout_undo.assert_called_once()

    def test_rollout_undo_unauthenticated(self, client, sample_project, make_k8s_cluster):
        """Unauthenticated request returns 401."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/deployments/nginx/rollout/undo?namespace=default",
        )
        assert response.status_code == 401

    def test_viewer_cannot_rollout_undo(self, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer cannot rollback deployments — returns 403."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/deployments/nginx/rollout/undo?namespace=default",
            headers=viewer_headers,
        )
        assert response.status_code == 403


# ============================================================================
# Rollout Restart
# ============================================================================

class TestRolloutRestart:
    """POST /api/k8s/clusters/{cluster_id}/deployments/{name}/rollout/restart."""

    @patch("routes.k8s.resources.KubernetesService")
    def test_rollout_restart(self, mock_k8s_svc_cls, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Admin can trigger a rolling restart of a deployment."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.rollout_restart.return_value = {
            "success": True,
            "message": "Deployment nginx restarted",
        }
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/deployments/nginx/rollout/restart?namespace=default",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        mock_svc.rollout_restart.assert_called_once()

    def test_rollout_restart_unauthenticated(self, client, sample_project, make_k8s_cluster):
        """Unauthenticated request returns 401."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/deployments/nginx/rollout/restart?namespace=default",
        )
        assert response.status_code == 401

    def test_viewer_cannot_rollout_restart(self, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer cannot restart deployments — returns 403."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/deployments/nginx/rollout/restart?namespace=default",
            headers=viewer_headers,
        )
        assert response.status_code == 403


# ============================================================================
# Node Cordon
# ============================================================================

class TestNodeCordon:
    """POST /api/k8s/clusters/{cluster_id}/nodes/{node_name}/cordon."""

    @patch("routes.k8s.resources.KubernetesService")
    def test_cordon_node(self, mock_k8s_svc_cls, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Admin can cordon a node."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.cordon_node.return_value = {
            "success": True,
            "message": "Node worker-1 cordoned",
        }
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/nodes/worker-1/cordon",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        mock_svc.cordon_node.assert_called_once()

    def test_cordon_unauthenticated(self, client, sample_project, make_k8s_cluster):
        """Unauthenticated request returns 401."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.post(f"/api/k8s/clusters/{cluster.id}/nodes/worker-1/cordon")
        assert response.status_code == 401

    def test_viewer_cannot_cordon(self, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer cannot cordon nodes — returns 403."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/nodes/worker-1/cordon",
            headers=viewer_headers,
        )
        assert response.status_code == 403


# ============================================================================
# Node Uncordon
# ============================================================================

class TestNodeUncordon:
    """POST /api/k8s/clusters/{cluster_id}/nodes/{node_name}/uncordon."""

    @patch("routes.k8s.resources.KubernetesService")
    def test_uncordon_node(self, mock_k8s_svc_cls, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Admin can uncordon a node."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.uncordon_node.return_value = {
            "success": True,
            "message": "Node worker-1 uncordoned",
        }
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/nodes/worker-1/uncordon",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        mock_svc.uncordon_node.assert_called_once()

    def test_uncordon_unauthenticated(self, client, sample_project, make_k8s_cluster):
        """Unauthenticated request returns 401."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.post(f"/api/k8s/clusters/{cluster.id}/nodes/worker-1/uncordon")
        assert response.status_code == 401

    def test_viewer_cannot_uncordon(self, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer cannot uncordon nodes — returns 403."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/nodes/worker-1/uncordon",
            headers=viewer_headers,
        )
        assert response.status_code == 403


# ============================================================================
# Node Drain
# ============================================================================

class TestNodeDrain:
    """POST /api/k8s/clusters/{cluster_id}/nodes/{node_name}/drain."""

    @patch("routes.k8s.resources.KubernetesService")
    def test_drain_node_defaults(self, mock_k8s_svc_cls, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Admin can drain a node with default options."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.drain_node.return_value = {
            "success": True,
            "message": "Node worker-1 drained",
            "evicted_pods": ["nginx-abc123", "redis-xyz789"],
        }
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/nodes/worker-1/drain",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        mock_svc.drain_node.assert_called_once()

    @patch("routes.k8s.resources.KubernetesService")
    def test_drain_node_with_options(self, mock_k8s_svc_cls, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Admin can drain a node with custom options."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = MagicMock()
        mock_svc.drain_node.return_value = {
            "success": True,
            "message": "Node worker-1 drained (force)",
        }
        mock_k8s_svc_cls.return_value = mock_svc

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/nodes/worker-1/drain"
            "?ignore_daemonsets=true&delete_emptydir_data=true&force=true&grace_period=30",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        mock_svc.drain_node.assert_called_once()
        # Verify the options were passed through
        call_kwargs = mock_svc.drain_node.call_args
        assert call_kwargs.kwargs.get("force") is True or call_kwargs[1].get("force") is True

    def test_drain_unauthenticated(self, client, sample_project, make_k8s_cluster):
        """Unauthenticated request returns 401."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.post(f"/api/k8s/clusters/{cluster.id}/nodes/worker-1/drain")
        assert response.status_code == 401

    def test_viewer_cannot_drain(self, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer cannot drain nodes — returns 403."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/nodes/worker-1/drain",
            headers=viewer_headers,
        )
        assert response.status_code == 403
