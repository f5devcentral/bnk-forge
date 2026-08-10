"""Unit tests for helm_tasks — mock HelmService, assert call args and return shape."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_db_context():
    with patch("tasks.helm_tasks.get_db_context") as m:
        db = MagicMock()
        m.return_value.__enter__ = MagicMock(return_value=db)
        m.return_value.__exit__ = MagicMock(return_value=False)
        yield db


@pytest.fixture
def mock_helm_service(mock_db_context):
    with patch("tasks.helm_tasks.HelmService") as cls:
        svc = MagicMock()
        cls.return_value = svc
        yield svc


# ── Read tasks ───────────────────────────────────────────────────────────────


class TestListReleases:
    def test_list_releases_callsServiceWithCorrectArgs(self, mock_helm_service):
        mock_helm_service.list_releases.return_value = [{"name": "nginx"}]
        from tasks.helm_tasks import list_releases
        result = list_releases(cluster_id=1, namespace="default", all_namespaces=False)
        mock_helm_service.list_releases.assert_called_once_with(
            cluster_id=1, namespace="default", all_namespaces=False
        )
        assert result["success"] is True
        assert result["count"] == 1
        assert result["releases"][0]["name"] == "nginx"

    def test_list_releases_returnsErrorOnRuntimeError(self, mock_helm_service):
        mock_helm_service.list_releases.side_effect = RuntimeError("aws not found")
        from tasks.helm_tasks import list_releases
        result = list_releases(cluster_id=1, namespace=None, all_namespaces=True)
        assert result["success"] is False
        assert "aws not found" in result["error"]

    def test_list_releases_returnsErrorOnTimeout(self, mock_helm_service):
        mock_helm_service.list_releases.side_effect = subprocess.TimeoutExpired(cmd="helm", timeout=300)
        from tasks.helm_tasks import list_releases
        result = list_releases(cluster_id=1, namespace=None, all_namespaces=False)
        assert result["success"] is False

    def test_list_releases_returnsErrorOnUnexpectedException(self, mock_helm_service):
        mock_helm_service.list_releases.side_effect = Exception("unexpected")
        from tasks.helm_tasks import list_releases
        result = list_releases(cluster_id=1, namespace=None, all_namespaces=False)
        assert result["success"] is False
        assert "unexpected" in result["error"]


class TestGetRelease:
    def test_get_release_callsServiceWithCorrectArgs(self, mock_helm_service):
        mock_helm_service.get_release.return_value = {"name": "nginx", "status": "deployed"}
        from tasks.helm_tasks import get_release
        result = get_release(cluster_id=2, release_name="nginx", namespace="default")
        mock_helm_service.get_release.assert_called_once_with(
            cluster_id=2, release_name="nginx", namespace="default"
        )
        assert result["success"] is True
        assert result["release"]["name"] == "nginx"

    def test_get_release_returnsErrorOnFailure(self, mock_helm_service):
        mock_helm_service.get_release.side_effect = RuntimeError("release not found")
        from tasks.helm_tasks import get_release
        result = get_release(cluster_id=2, release_name="nginx", namespace="default")
        assert result["success"] is False
        assert "release not found" in result["error"]


class TestReleaseHistory:
    def test_release_history_callsServiceWithCorrectArgs(self, mock_helm_service):
        mock_helm_service.get_history.return_value = [{"revision": 1}, {"revision": 2}]
        from tasks.helm_tasks import release_history
        result = release_history(cluster_id=1, release_name="nginx", namespace="default", max_revisions=10)
        mock_helm_service.get_history.assert_called_once_with(
            cluster_id=1, release_name="nginx", namespace="default", max_revisions=10
        )
        assert result["success"] is True
        assert result["count"] == 2

    def test_release_history_returnsErrorOnFailure(self, mock_helm_service):
        mock_helm_service.get_history.side_effect = RuntimeError("error")
        from tasks.helm_tasks import release_history
        result = release_history(cluster_id=1, release_name="nginx", namespace="default", max_revisions=10)
        assert result["success"] is False


class TestReleaseValues:
    def test_release_values_callsServiceWithCorrectArgs(self, mock_helm_service):
        mock_helm_service.get_values.return_value = {"replicaCount": 2}
        from tasks.helm_tasks import release_values
        result = release_values(cluster_id=1, release_name="nginx", namespace="default", all_values=True)
        mock_helm_service.get_values.assert_called_once_with(
            cluster_id=1, release_name="nginx", namespace="default", all_values=True
        )
        assert result["success"] is True
        assert result["values"]["replicaCount"] == 2

    def test_release_values_returnsErrorOnFailure(self, mock_helm_service):
        mock_helm_service.get_values.side_effect = RuntimeError("error")
        from tasks.helm_tasks import release_values
        result = release_values(cluster_id=1, release_name="nginx", namespace="default", all_values=False)
        assert result["success"] is False


class TestReleaseManifest:
    def test_release_manifest_callsServiceWithCorrectArgs(self, mock_helm_service):
        mock_helm_service.get_manifest.return_value = "apiVersion: apps/v1\n..."
        from tasks.helm_tasks import release_manifest
        result = release_manifest(cluster_id=1, release_name="nginx", namespace="default", revision=None)
        mock_helm_service.get_manifest.assert_called_once_with(
            cluster_id=1, release_name="nginx", namespace="default", revision=None
        )
        assert result["success"] is True
        assert "apiVersion" in result["manifest"]

    def test_release_manifest_withRevision(self, mock_helm_service):
        mock_helm_service.get_manifest.return_value = "---"
        from tasks.helm_tasks import release_manifest
        result = release_manifest(cluster_id=1, release_name="nginx", namespace="default", revision=3)
        mock_helm_service.get_manifest.assert_called_once_with(
            cluster_id=1, release_name="nginx", namespace="default", revision=3
        )
        assert result["success"] is True


class TestReleaseCompare:
    def test_release_compare_callsServiceWithCorrectArgs(self, mock_helm_service):
        mock_helm_service.compare_revisions.return_value = {"success": True, "revision1": {}, "revision2": {}}
        from tasks.helm_tasks import release_compare
        result = release_compare(
            cluster_id=1, release_name="nginx", revision1=1, revision2=2, namespace="default"
        )
        mock_helm_service.compare_revisions.assert_called_once_with(
            cluster_id=1, release_name="nginx", revision1=1, revision2=2, namespace="default"
        )
        assert result["success"] is True


# ── Write tasks ──────────────────────────────────────────────────────────────


class TestInstallRelease:
    def test_install_release_callsServiceWithCorrectArgs(self, mock_helm_service):
        mock_helm_service.install_chart.return_value = {"name": "my-nginx", "status": "deployed"}
        from tasks.helm_tasks import install_release
        result = install_release(
            cluster_id=1, release_name="my-nginx", chart="bitnami/nginx",
            namespace="default", values=None, version=None,
            create_namespace=True, wait=True, timeout="5m",
        )
        mock_helm_service.install_chart.assert_called_once_with(
            cluster_id=1, release_name="my-nginx", chart="bitnami/nginx",
            namespace="default", values=None, version=None,
            create_namespace=True, wait=True, timeout="5m",
        )
        assert result["success"] is True
        assert "my-nginx" in result["message"]

    def test_install_release_returnsErrorOnRuntimeError(self, mock_helm_service):
        mock_helm_service.install_chart.side_effect = RuntimeError("chart not found")
        from tasks.helm_tasks import install_release
        result = install_release(
            cluster_id=1, release_name="nginx", chart="bad/chart",
            namespace="default", values=None, version=None,
            create_namespace=True, wait=True, timeout="5m",
        )
        assert result["success"] is False
        assert "chart not found" in result["error"]

    def test_install_release_returnsErrorOnValueError(self, mock_helm_service):
        mock_helm_service.install_chart.side_effect = ValueError("release name in use")
        from tasks.helm_tasks import install_release
        result = install_release(
            cluster_id=1, release_name="nginx", chart="bitnami/nginx",
            namespace="default", values=None, version=None,
            create_namespace=True, wait=True, timeout="5m",
        )
        assert result["success"] is False


class TestUpgradeRelease:
    def test_upgrade_release_callsServiceWithCorrectArgs(self, mock_helm_service):
        mock_helm_service.upgrade_release.return_value = {"name": "nginx", "revision": 2}
        from tasks.helm_tasks import upgrade_release
        result = upgrade_release(
            cluster_id=1, release_name="nginx", chart="bitnami/nginx",
            namespace="default", values=None, version="15.0.0",
            install=True, wait=True, timeout="5m",
        )
        mock_helm_service.upgrade_release.assert_called_once_with(
            cluster_id=1, release_name="nginx", chart="bitnami/nginx",
            namespace="default", values=None, version="15.0.0",
            install=True, wait=True, timeout="5m",
        )
        assert result["success"] is True
        assert "nginx" in result["message"]

    def test_upgrade_release_returnsErrorOnFailure(self, mock_helm_service):
        mock_helm_service.upgrade_release.side_effect = RuntimeError("upgrade failed")
        from tasks.helm_tasks import upgrade_release
        result = upgrade_release(
            cluster_id=1, release_name="nginx", chart=None,
            namespace="default", values=None, version=None,
            install=True, wait=True, timeout="5m",
        )
        assert result["success"] is False


class TestRollbackRelease:
    def test_rollback_release_callsServiceWithCorrectArgs(self, mock_helm_service):
        mock_helm_service.rollback_release.return_value = {
            "success": True, "release": "nginx", "revision": 1
        }
        from tasks.helm_tasks import rollback_release
        result = rollback_release(
            cluster_id=1, release_name="nginx", revision=1,
            namespace="default", wait=True, timeout="5m",
        )
        mock_helm_service.rollback_release.assert_called_once_with(
            cluster_id=1, release_name="nginx", revision=1,
            namespace="default", wait=True, timeout="5m",
        )
        assert result["success"] is True
        assert "nginx" in result["message"]

    def test_rollback_release_returnsErrorOnFailure(self, mock_helm_service):
        mock_helm_service.rollback_release.side_effect = RuntimeError("rollback failed")
        from tasks.helm_tasks import rollback_release
        result = rollback_release(
            cluster_id=1, release_name="nginx", revision=None,
            namespace="default", wait=True, timeout="5m",
        )
        assert result["success"] is False


class TestUninstallRelease:
    def test_uninstall_release_callsServiceWithCorrectArgs(self, mock_helm_service):
        mock_helm_service.uninstall_release.return_value = {"success": True}
        from tasks.helm_tasks import uninstall_release
        result = uninstall_release(
            cluster_id=1, release_name="nginx", namespace="default",
            keep_history=False, wait=True, timeout="5m",
        )
        mock_helm_service.uninstall_release.assert_called_once_with(
            cluster_id=1, release_name="nginx", namespace="default",
            keep_history=False, wait=True, timeout="5m",
        )
        assert result["success"] is True
        assert "nginx" in result["message"]

    def test_uninstall_release_returnsErrorOnFailure(self, mock_helm_service):
        mock_helm_service.uninstall_release.side_effect = RuntimeError("release not found")
        from tasks.helm_tasks import uninstall_release
        result = uninstall_release(
            cluster_id=1, release_name="nginx", namespace="default",
            keep_history=False, wait=True, timeout="5m",
        )
        assert result["success"] is False


class TestTestRelease:
    def test_test_release_callsServiceWithCorrectArgs(self, mock_helm_service):
        mock_helm_service.test_release.return_value = {
            "success": True, "exit_code": 0, "output": "ok", "error": ""
        }
        from tasks.helm_tasks import test_release
        result = test_release(
            cluster_id=1, release_name="nginx", namespace="default", timeout="5m"
        )
        mock_helm_service.test_release.assert_called_once_with(
            cluster_id=1, release_name="nginx", namespace="default", timeout="5m"
        )
        assert result["success"] is True

    def test_test_release_returnsErrorOnFailure(self, mock_helm_service):
        mock_helm_service.test_release.side_effect = RuntimeError("test failed")
        from tasks.helm_tasks import test_release
        result = test_release(
            cluster_id=1, release_name="nginx", namespace="default", timeout="5m"
        )
        assert result["success"] is False
