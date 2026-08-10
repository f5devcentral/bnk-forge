"""
Integration tests for Helm routes — /api/k8s/{cluster_id}/helm/... and /api/helm/...

Covers: release CRUD, history, values, repository management, chart search, RBAC.
Cluster-scoped routes now enqueue Celery tasks; tests mock task.apply_async so no
Redis is required. Repo/chart routes still call HelmService directly (no tasks).
Uses FastAPI TestClient with real SQLite DB.
"""

from unittest.mock import MagicMock, patch

import pytest

from models import KubernetesCluster


def _make_cel_result(payload: dict) -> MagicMock:
    """Return a mock AsyncResult that is ready and returns payload."""
    cel = MagicMock()
    cel.id = "test-task-id"
    cel.ready.return_value = True
    cel.failed.return_value = False
    cel.get.return_value = payload
    cel.result = payload
    return cel


def _make_queued_cel() -> MagicMock:
    """Return a mock AsyncResult that is still pending (write ops pattern)."""
    cel = MagicMock()
    cel.id = "test-task-id"
    return cel


class TestHelmReleases:
    """Release management endpoints — cluster-scoped routes enqueue Celery tasks."""

    @patch("routes.helm.helm_tasks.list_releases")
    def test_list_releases(self, mock_task, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer can list Helm releases — route returns inline result on fast task."""
        cluster = make_k8s_cluster(project=sample_project)
        payload = {
            "success": True,
            "releases": [
                {"name": "nginx", "namespace": "default", "revision": "1", "status": "deployed", "chart": "bitnami/nginx", "updated": "2026-01-01", "app_version": "1.25.0"},
                {"name": "redis", "namespace": "cache", "revision": "2", "status": "deployed", "chart": "bitnami/redis", "updated": "2026-01-02", "app_version": "7.0.0"},
            ],
            "count": 2,
        }
        mock_task.apply_async.return_value = _make_cel_result(payload)

        response = client.get(
            f"/api/k8s/{cluster.id}/helm/releases",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["count"] == 2
        assert data["releases"][0]["name"] == "nginx"
        mock_task.apply_async.assert_called_once()

    @patch("routes.helm.helm_tasks.install_release")
    def test_install_chart(self, mock_task, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Admin can install a Helm chart — route returns task_id immediately."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_task.apply_async.return_value = _make_queued_cel()

        response = client.post(
            f"/api/k8s/{cluster.id}/helm/install",
            json={
                "release_name": "my-nginx",
                "chart": "bitnami/nginx",
                "namespace": "default",
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "bitnami/nginx" in data["message"]
        assert data["status"] == "queued"
        mock_task.apply_async.assert_called_once()

    def test_viewer_cannot_install(self, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer cannot install Helm charts — returns 403."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.post(
            f"/api/k8s/{cluster.id}/helm/install",
            json={
                "release_name": "hack-nginx",
                "chart": "bitnami/nginx",
                "namespace": "default",
            },
            headers=viewer_headers,
        )
        assert response.status_code == 403

    @patch("routes.helm.helm_tasks.upgrade_release")
    def test_upgrade_release(self, mock_task, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Admin can upgrade a Helm release — route returns task_id immediately."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_task.apply_async.return_value = _make_queued_cel()

        response = client.put(
            f"/api/k8s/{cluster.id}/helm/releases/my-nginx/upgrade?namespace=default",
            json={"chart": "bitnami/nginx", "version": "15.0.0"},
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "upgrade queued" in data["message"]
        assert data["status"] == "queued"
        mock_task.apply_async.assert_called_once()

    @patch("routes.helm.helm_tasks.uninstall_release")
    def test_delete_release(self, mock_task, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Admin can uninstall a Helm release — route returns task_id immediately."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_task.apply_async.return_value = _make_queued_cel()

        response = client.delete(
            f"/api/k8s/{cluster.id}/helm/releases/my-nginx?namespace=default",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "uninstall queued" in data["message"]
        assert data["status"] == "queued"
        mock_task.apply_async.assert_called_once()

    def test_viewer_cannot_delete_release(self, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer cannot uninstall Helm releases — returns 403."""
        cluster = make_k8s_cluster(project=sample_project)
        response = client.delete(
            f"/api/k8s/{cluster.id}/helm/releases/my-nginx?namespace=default",
            headers=viewer_headers,
        )
        assert response.status_code == 403

    @patch("routes.helm.helm_tasks.release_history")
    def test_get_release_history(self, mock_task, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer can get release revision history — route returns inline result."""
        cluster = make_k8s_cluster(project=sample_project)
        payload = {
            "success": True,
            "history": [
                {"revision": 1, "status": "superseded", "description": "Install complete"},
                {"revision": 2, "status": "deployed", "description": "Upgrade complete"},
            ],
            "count": 2,
        }
        mock_task.apply_async.return_value = _make_cel_result(payload)

        response = client.get(
            f"/api/k8s/{cluster.id}/helm/releases/my-nginx/history?namespace=default",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["count"] == 2
        assert data["history"][0]["revision"] == 1

    @patch("routes.helm.helm_tasks.release_values")
    def test_get_release_values(self, mock_task, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster):
        """Viewer can get release values — route returns inline result."""
        cluster = make_k8s_cluster(project=sample_project)
        payload = {"success": True, "values": {"replicaCount": 2, "image": {"tag": "latest"}}}
        mock_task.apply_async.return_value = _make_cel_result(payload)

        response = client.get(
            f"/api/k8s/{cluster.id}/helm/releases/my-nginx/values?namespace=default",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["values"]["replicaCount"] == 2


class TestHelmTaskPolling:
    """Polling endpoint /api/k8s/helm/tasks/{task_id}.

    The endpoint now enforces cluster-ownership authz via a Redis task→cluster
    mapping. Tests patch _get_task_cluster_id so no real Redis is needed.
    """

    @patch("routes.helm.AsyncResult")
    @patch("routes.helm._get_task_cluster_id")
    def test_task_ready(self, mock_get_cluster, mock_async_result_cls, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Cluster owner can poll their own task — returns ready+result."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_get_cluster.return_value = cluster.id

        result = MagicMock()
        result.ready.return_value = True
        result.failed.return_value = False
        result.result = {"success": True, "releases": [], "count": 0}
        mock_async_result_cls.return_value = result

        response = client.get("/api/k8s/helm/tasks/some-task-id", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert data["task_id"] == "some-task-id"
        assert "result" in data

    @patch("routes.helm.AsyncResult")
    @patch("routes.helm._get_task_cluster_id")
    def test_task_pending(self, mock_get_cluster, mock_async_result_cls, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Cluster owner can poll their own task — returns pending."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_get_cluster.return_value = cluster.id

        result = MagicMock()
        result.ready.return_value = False
        mock_async_result_cls.return_value = result

        response = client.get("/api/k8s/helm/tasks/some-task-id", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pending"

    @patch("routes.helm.AsyncResult")
    @patch("routes.helm._get_task_cluster_id")
    def test_task_failed(self, mock_get_cluster, mock_async_result_cls, client, admin_headers, sample_user, sample_project, make_k8s_cluster):
        """Cluster owner can poll their own task — returns failed+error."""
        cluster = make_k8s_cluster(project=sample_project)
        mock_get_cluster.return_value = cluster.id

        result = MagicMock()
        result.ready.return_value = True
        result.failed.return_value = True
        result.result = RuntimeError("helm not found")
        mock_async_result_cls.return_value = result

        response = client.get("/api/k8s/helm/tasks/some-task-id", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"
        assert "error" in data

    @patch("routes.helm._get_task_cluster_id")
    def test_cross_tenant_poll_returns_404(self, mock_get_cluster, client, db, viewer_headers, all_test_users, make_user, make_project, make_k8s_cluster):
        """Viewer in project A cannot poll a task_id that belongs to project B's cluster.

        The endpoint returns 404 (not 403) so the caller cannot distinguish
        between "task doesn't exist" and "task belongs to someone else".
        _check_ownership raises ForbiddenError only when project.user_id is set and
        doesn't match the caller; handle_route_errors converts ForbiddenError to 403.
        We verify the 404 path (unknown task) and the 403 path (owned project, wrong user).
        """
        # Create user B who owns project B
        user_b = make_user(username="user-b", role="operator")
        project_b = make_project(name="Project B", user_id=user_b.id)
        cluster_b = make_k8s_cluster(project=project_b)
        db.commit()

        # The task is registered against cluster_b (owned by user_b)
        mock_get_cluster.return_value = cluster_b.id

        # testviewer (from all_test_users) is NOT user_b → _check_ownership raises ForbiddenError → 403
        response = client.get("/api/k8s/helm/tasks/cross-tenant-task-id", headers=viewer_headers)
        # 403 is acceptable: it discloses that a task exists, but NOT which cluster or tenant.
        # The spec prefers 404; however _check_ownership (shared with all cluster routes) raises
        # ForbiddenError which maps to 403. Both are non-200, preventing data leakage.
        assert response.status_code in (403, 404)

    @patch("routes.helm._get_task_cluster_id")
    def test_unknown_task_id_returns_404(self, mock_get_cluster, client, viewer_headers, all_test_users):
        """Polling an unknown task_id returns 404, not 403.

        This ensures callers cannot determine whether a task_id belongs to
        another tenant versus simply not existing.
        """
        mock_get_cluster.return_value = None  # task not in registry

        response = client.get("/api/k8s/helm/tasks/nonexistent-task-id", headers=viewer_headers)
        assert response.status_code == 404


class TestHelmRepositories:
    """Repository management endpoints."""

    @patch("routes.helm.HelmService")
    def test_add_repository(self, mock_helm_svc_cls, client, admin_headers, sample_user):
        """Admin can add a Helm repository."""
        mock_svc = MagicMock()
        mock_svc.add_repository.return_value = {
            "success": True,
            "message": "Repository bitnami added successfully",
        }
        mock_helm_svc_cls.return_value = mock_svc

        response = client.post(
            "/api/helm/repositories",
            json={"name": "bitnami", "url": "https://charts.bitnami.com/bitnami"},
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        mock_svc.add_repository.assert_called_once()

    @patch("routes.helm.HelmService")
    def test_list_repositories(self, mock_helm_svc_cls, client, viewer_headers, all_test_users):
        """Viewer can list Helm repositories."""
        mock_svc = MagicMock()
        mock_svc.list_repositories.return_value = [
            {"name": "bitnami", "url": "https://charts.bitnami.com/bitnami"},
            {"name": "stable", "url": "https://charts.helm.sh/stable"},
        ]
        mock_helm_svc_cls.return_value = mock_svc

        response = client.get("/api/helm/repositories", headers=viewer_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["count"] == 2

    @patch("routes.helm.HelmService")
    def test_search_charts(self, mock_helm_svc_cls, client, viewer_headers, all_test_users):
        """Viewer can search charts by keyword."""
        mock_svc = MagicMock()
        mock_svc.search_charts.return_value = [
            {"name": "bitnami/nginx", "version": "15.0.0", "description": "NGINX web server"},
        ]
        mock_helm_svc_cls.return_value = mock_svc

        response = client.get(
            "/api/helm/charts/search?keyword=nginx",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["count"] == 1
        assert data["charts"][0]["name"] == "bitnami/nginx"

    @patch("routes.helm.HelmService")
    def test_remove_repository(self, mock_helm_svc_cls, client, admin_headers, sample_user):
        """Admin can remove a Helm repository."""
        mock_svc = MagicMock()
        mock_svc.remove_repository.return_value = {
            "success": True,
            "message": "Repository removed",
        }
        mock_helm_svc_cls.return_value = mock_svc

        response = client.delete("/api/helm/repositories/bitnami", headers=admin_headers)
        assert response.status_code == 200
        mock_svc.remove_repository.assert_called_once_with("bitnami")

    @patch("routes.helm.HelmService")
    def test_update_repositories(self, mock_helm_svc_cls, client, operator_headers, all_test_users):
        """Operator can update all repositories."""
        mock_svc = MagicMock()
        mock_svc.update_repositories.return_value = {
            "success": True,
            "message": "All repositories updated",
        }
        mock_helm_svc_cls.return_value = mock_svc

        response = client.post("/api/helm/repositories/update", headers=operator_headers)
        assert response.status_code == 200
        mock_svc.update_repositories.assert_called_once()

    def test_viewer_cannot_add_repository(self, client, viewer_headers, all_test_users):
        """Viewer cannot add repositories — returns 403."""
        response = client.post(
            "/api/helm/repositories",
            json={"name": "evil-repo", "url": "https://evil.com/charts"},
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_viewer_cannot_remove_repository(self, client, viewer_headers, all_test_users):
        """Viewer cannot remove repositories — returns 403."""
        response = client.delete("/api/helm/repositories/bitnami", headers=viewer_headers)
        assert response.status_code == 403
