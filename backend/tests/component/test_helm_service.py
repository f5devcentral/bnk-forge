"""
BC-020: Component tests for HelmService — Helm chart and release management.

Tests cover: list_releases, get_release, install_chart, upgrade_release,
rollback_release, uninstall_release, get_history, get_values.
All subprocess/kubeconfig calls are mocked.
"""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from services.helm_service import HelmService

# ── Helpers ──────────────────────────────────────────────────────────

def _mock_cluster():
    """Build a fake KubernetesCluster-like object."""
    cluster = MagicMock()
    cluster.id = 1
    cluster.name = "test-cluster"
    cluster.kubeconfig = "apiVersion: v1\nclusters: []"
    return cluster


def _mock_kubeconfig():
    """Return a mock tempfile for kubeconfig."""
    kf = MagicMock()
    kf.name = "/tmp/fake-kubeconfig"
    return kf


def _mock_run_result(exit_code=0, stdout="", stderr=""):
    return {"exit_code": exit_code, "stdout": stdout, "stderr": stderr}


# ── List Releases ────────────────────────────────────────────────────

class TestListReleases:
    @patch.object(HelmService, "_run_helm_command")
    @patch.object(HelmService, "get_cluster")
    def test_list_releases_success(self, mock_get, mock_run, db):
        releases_json = json.dumps([
            {"name": "nginx", "namespace": "default", "status": "deployed"},
        ])
        mock_get.return_value = _mock_cluster()
        mock_run.return_value = _mock_run_result(stdout=releases_json)

        svc = HelmService(db)
        result = svc.list_releases(cluster_id=1)

        assert len(result) == 1
        assert result[0]["name"] == "nginx"

    @patch.object(HelmService, "_run_helm_command")
    @patch.object(HelmService, "get_cluster")
    def test_list_releases_empty(self, mock_get, mock_run, db):
        mock_get.return_value = _mock_cluster()
        mock_run.return_value = _mock_run_result(stdout="")

        svc = HelmService(db)
        result = svc.list_releases(cluster_id=1)
        assert result == []

    @patch.object(HelmService, "_run_helm_command")
    @patch.object(HelmService, "get_cluster")
    def test_list_releases_failure_raises(self, mock_get, mock_run, db):
        mock_get.return_value = _mock_cluster()
        mock_run.return_value = _mock_run_result(exit_code=1, stderr="connection refused")

        svc = HelmService(db)
        with pytest.raises(RuntimeError, match="Failed to list releases"):
            svc.list_releases(cluster_id=1)

    @patch.object(HelmService, "_run_helm_command")
    @patch.object(HelmService, "get_cluster")
    def test_list_releases_bad_json_returns_empty(self, mock_get, mock_run, db):
        mock_get.return_value = _mock_cluster()
        mock_run.return_value = _mock_run_result(stdout="not-json")

        svc = HelmService(db)
        result = svc.list_releases(cluster_id=1)
        assert result == []


# ── Install Chart ────────────────────────────────────────────────────

class TestInstallChart:
    @patch.object(HelmService, "_run_helm_command")
    @patch.object(HelmService, "get_cluster")
    def test_install_success(self, mock_get, mock_run, db):
        install_result = json.dumps({"name": "myapp", "info": {"status": "deployed"}})
        mock_get.return_value = _mock_cluster()
        mock_run.return_value = _mock_run_result(stdout=install_result)

        svc = HelmService(db)
        result = svc.install_chart(1, "myapp", "bitnami/nginx", namespace="default")

        assert result["name"] == "myapp"

    @patch.object(HelmService, "_run_helm_command")
    @patch.object(HelmService, "get_cluster")
    def test_install_duplicate_name_raises_value_error(self, mock_get, mock_run, db):
        mock_get.return_value = _mock_cluster()
        mock_run.return_value = _mock_run_result(
            exit_code=1,
            stderr="cannot re-use a name that is still in use",
        )

        svc = HelmService(db)
        with pytest.raises(ValueError, match="Release name already in use"):
            svc.install_chart(1, "myapp", "bitnami/nginx")

    @patch.object(HelmService, "_run_helm_command")
    @patch.object(HelmService, "get_cluster")
    def test_install_chart_not_found_raises(self, mock_get, mock_run, db):
        mock_get.return_value = _mock_cluster()
        mock_run.return_value = _mock_run_result(
            exit_code=1,
            stderr="chart not found in repo",
        )

        svc = HelmService(db)
        with pytest.raises(ValueError, match="Chart not found"):
            svc.install_chart(1, "myapp", "bitnami/nonexistent")


# ── Upgrade Release ──────────────────────────────────────────────────

class TestUpgradeRelease:
    @patch.object(HelmService, "_run_helm_command")
    @patch.object(HelmService, "get_cluster")
    def test_upgrade_success(self, mock_get, mock_run, db):
        upgrade_json = json.dumps({"name": "myapp", "info": {"status": "deployed"}})
        mock_get.return_value = _mock_cluster()
        mock_run.return_value = _mock_run_result(stdout=upgrade_json)

        svc = HelmService(db)
        result = svc.upgrade_release(1, "myapp", chart="bitnami/nginx")
        assert result["name"] == "myapp"

    @patch.object(HelmService, "_run_helm_command")
    @patch.object(HelmService, "get_cluster")
    def test_upgrade_failure_raises(self, mock_get, mock_run, db):
        mock_get.return_value = _mock_cluster()
        mock_run.return_value = _mock_run_result(exit_code=1, stderr="upgrade failed")

        svc = HelmService(db)
        with pytest.raises(RuntimeError, match="Failed to upgrade"):
            svc.upgrade_release(1, "myapp", chart="bitnami/nginx")


# ── Rollback Release ─────────────────────────────────────────────────

class TestRollbackRelease:
    @patch.object(HelmService, "_run_helm_command")
    @patch.object(HelmService, "get_cluster")
    def test_rollback_success(self, mock_get, mock_run, db):
        mock_get.return_value = _mock_cluster()
        mock_run.return_value = _mock_run_result(stdout="Rollback was a success!")

        svc = HelmService(db)
        result = svc.rollback_release(1, "myapp", revision=2)
        assert result["success"] is True
        assert result["revision"] == 2

    @patch.object(HelmService, "_run_helm_command")
    @patch.object(HelmService, "get_cluster")
    def test_rollback_failure_raises(self, mock_get, mock_run, db):
        mock_get.return_value = _mock_cluster()
        mock_run.return_value = _mock_run_result(exit_code=1, stderr="rollback error")

        svc = HelmService(db)
        with pytest.raises(RuntimeError, match="Failed to rollback"):
            svc.rollback_release(1, "myapp")


# ── Uninstall Release ────────────────────────────────────────────────

class TestUninstallRelease:
    @patch.object(HelmService, "_run_helm_command")
    @patch.object(HelmService, "get_cluster")
    def test_uninstall_success(self, mock_get, mock_run, db):
        mock_get.return_value = _mock_cluster()
        mock_run.return_value = _mock_run_result(stdout="release 'myapp' uninstalled")

        svc = HelmService(db)
        result = svc.uninstall_release(1, "myapp")
        assert result["success"] is True

    @patch.object(HelmService, "_run_helm_command")
    @patch.object(HelmService, "get_cluster")
    def test_uninstall_failure_raises(self, mock_get, mock_run, db):
        mock_get.return_value = _mock_cluster()
        mock_run.return_value = _mock_run_result(exit_code=1, stderr="not found")

        svc = HelmService(db)
        with pytest.raises(RuntimeError, match="Failed to uninstall"):
            svc.uninstall_release(1, "myapp")


# ── Get History ──────────────────────────────────────────────────────

class TestGetHistory:
    @patch.object(HelmService, "_run_helm_command")
    @patch.object(HelmService, "get_cluster")
    def test_history_success(self, mock_get, mock_run, db):
        history_json = json.dumps([
            {"revision": 1, "status": "deployed"},
            {"revision": 2, "status": "superseded"},
        ])
        mock_get.return_value = _mock_cluster()
        mock_run.return_value = _mock_run_result(stdout=history_json)

        svc = HelmService(db)
        result = svc.get_history(1, "myapp")
        assert len(result) == 2


# ── Get Values ───────────────────────────────────────────────────────

class TestGetValues:
    @patch.object(HelmService, "_run_helm_command")
    @patch.object(HelmService, "get_cluster")
    def test_get_values_success(self, mock_get, mock_run, db):
        values_json = json.dumps({"replicaCount": 3, "image": {"tag": "latest"}})
        mock_get.return_value = _mock_cluster()
        mock_run.return_value = _mock_run_result(stdout=values_json)

        svc = HelmService(db)
        result = svc.get_values(1, "myapp")
        assert result["replicaCount"] == 3

    @patch.object(HelmService, "_run_helm_command")
    @patch.object(HelmService, "get_cluster")
    def test_get_values_empty(self, mock_get, mock_run, db):
        mock_get.return_value = _mock_cluster()
        mock_run.return_value = _mock_run_result(stdout="")

        svc = HelmService(db)
        result = svc.get_values(1, "myapp")
        assert result == {}


# ── _run_helm_command internal ───────────────────────────────────────

class TestRunHelmCommand:
    @patch("services.helm_service.os.path.exists", return_value=True)
    @patch("services.helm_service.os.unlink")
    @patch("services.helm_service.subprocess.run")
    @patch.object(HelmService, "_prepare_kubeconfig")
    def test_timeout_returns_exit_124(self, mock_prep, mock_run, mock_unlink, mock_exists, db):
        mock_prep.return_value = _mock_kubeconfig()
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="helm", timeout=300)

        svc = HelmService(db)
        result = svc._run_helm_command(_mock_cluster(), ["list"])
        assert result["exit_code"] == 124
        assert "timed out" in result["stderr"]

    @patch("services.helm_service.os.path.exists", return_value=True)
    @patch("services.helm_service.os.unlink")
    @patch("services.helm_service.subprocess.run")
    @patch.object(HelmService, "_prepare_kubeconfig")
    def test_generic_exception(self, mock_prep, mock_run, mock_unlink, mock_exists, db):
        mock_prep.return_value = _mock_kubeconfig()
        mock_run.side_effect = OSError("helm binary not found")

        svc = HelmService(db)
        result = svc._run_helm_command(_mock_cluster(), ["list"])
        assert result["exit_code"] == 1
        assert "helm binary not found" in result["stderr"]
