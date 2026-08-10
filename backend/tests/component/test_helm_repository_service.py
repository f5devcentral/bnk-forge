"""
BC-C53: Component tests for HelmRepositoryMixin.

Tests add, remove, update, list, browse, and search repository operations.
All subprocess calls are mocked.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from services.helm_repository_service import HelmRepositoryMixin

# ── Helpers ──────────────────────────────────────────────────────────

def _make_service():
    class Host(HelmRepositoryMixin):
        pass

    host = Host()
    host._validate_arg = MagicMock()
    return host


def _mock_run(returncode=0, stdout="", stderr=""):
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


# ── Add Repository ───────────────────────────────────────────────────

class TestAddRepository:
    @patch("subprocess.run")
    def test_add_repo_success(self, mock_run):
        mock_run.return_value = _mock_run(stdout='"bitnami" has been added')
        svc = _make_service()
        # Patch update_repositories since it's called after add
        svc.update_repositories = MagicMock(return_value={"success": True})

        result = svc.add_repository("bitnami", "https://charts.bitnami.com/bitnami")
        assert result["success"] is True
        assert result["name"] == "bitnami"
        svc.update_repositories.assert_called_once()

    @patch("subprocess.run")
    def test_add_repo_with_auth(self, mock_run):
        mock_run.return_value = _mock_run(stdout="added")
        svc = _make_service()
        svc.update_repositories = MagicMock(return_value={"success": True})

        result = svc.add_repository("private", "https://repo.example.com",
                                     username="user", password="pass")
        assert result["success"] is True
        # Check that credentials were passed to subprocess
        call_args = mock_run.call_args[0][0]
        assert "--username" in call_args
        assert "--password" in call_args

    @patch("subprocess.run")
    def test_add_repo_failure(self, mock_run):
        mock_run.return_value = _mock_run(returncode=1, stderr="already exists")
        svc = _make_service()

        with pytest.raises(RuntimeError, match="Failed to add repository"):
            svc.add_repository("bitnami", "https://charts.bitnami.com/bitnami")


# ── Update Repositories ─────────────────────────────────────────────

class TestUpdateRepositories:
    @patch("subprocess.run")
    def test_update_success(self, mock_run):
        mock_run.return_value = _mock_run(stdout="Successfully got an update")
        svc = _make_service()
        result = svc.update_repositories()
        assert result["success"] is True

    @patch("subprocess.run")
    def test_update_failure(self, mock_run):
        mock_run.return_value = _mock_run(returncode=1, stderr="network error")
        svc = _make_service()
        result = svc.update_repositories()
        assert result["success"] is False


# ── Remove Repository ────────────────────────────────────────────────

class TestRemoveRepository:
    @patch("subprocess.run")
    def test_remove_success(self, mock_run):
        mock_run.return_value = _mock_run(stdout='"bitnami" removed')
        svc = _make_service()
        result = svc.remove_repository("bitnami")
        assert result["success"] is True

    @patch("subprocess.run")
    def test_remove_failure(self, mock_run):
        mock_run.return_value = _mock_run(returncode=1, stderr="not found")
        svc = _make_service()

        with pytest.raises(Exception, match="Failed to remove repository"):
            svc.remove_repository("nonexistent")


# ── List Repositories ────────────────────────────────────────────────

class TestListRepositories:
    @patch("subprocess.run")
    def test_list_repos(self, mock_run):
        repos = [{"name": "bitnami", "url": "https://charts.bitnami.com/bitnami"}]
        mock_run.return_value = _mock_run(stdout=json.dumps(repos))
        svc = _make_service()
        result = svc.list_repositories()
        assert len(result) == 1
        assert result[0]["name"] == "bitnami"

    @patch("subprocess.run")
    def test_list_repos_empty(self, mock_run):
        mock_run.return_value = _mock_run(stdout="")
        svc = _make_service()
        result = svc.list_repositories()
        assert result == []

    @patch("subprocess.run")
    def test_list_repos_failure_returns_empty(self, mock_run):
        mock_run.return_value = _mock_run(returncode=1, stderr="error")
        svc = _make_service()
        result = svc.list_repositories()
        assert result == []

    @patch("subprocess.run")
    def test_list_repos_bad_json_returns_empty(self, mock_run):
        mock_run.return_value = _mock_run(stdout="{bad json")
        svc = _make_service()
        result = svc.list_repositories()
        assert result == []


# ── Browse Charts ────────────────────────────────────────────────────

class TestBrowseCharts:
    @patch("subprocess.run")
    def test_browse_all(self, mock_run):
        charts = [{"name": "bitnami/nginx"}, {"name": "bitnami/redis"}]
        mock_run.return_value = _mock_run(stdout=json.dumps(charts))
        svc = _make_service()
        result = svc.browse_charts()
        assert len(result) == 2

    @patch("subprocess.run")
    def test_browse_specific_repo(self, mock_run):
        charts = [{"name": "bitnami/nginx"}]
        mock_run.return_value = _mock_run(stdout=json.dumps(charts))
        svc = _make_service()
        result = svc.browse_charts(repository="bitnami")
        assert len(result) == 1

    @patch("subprocess.run")
    def test_browse_respects_max_results(self, mock_run):
        charts = [{"name": f"chart-{i}"} for i in range(100)]
        mock_run.return_value = _mock_run(stdout=json.dumps(charts))
        svc = _make_service()
        result = svc.browse_charts(max_results=5)
        assert len(result) == 5


# ── Search Charts ────────────────────────────────────────────────────

class TestSearchCharts:
    @patch("subprocess.run")
    def test_search_success(self, mock_run):
        charts = [{"name": "bitnami/nginx", "version": "1.0"}]
        mock_run.return_value = _mock_run(stdout=json.dumps(charts))
        svc = _make_service()
        result = svc.search_charts("nginx")
        assert len(result) == 1

    @patch("subprocess.run")
    def test_search_failure_returns_empty(self, mock_run):
        mock_run.return_value = _mock_run(returncode=1, stderr="error")
        svc = _make_service()
        result = svc.search_charts("nginx")
        assert result == []

    @patch("subprocess.run")
    def test_search_with_repository(self, mock_run):
        mock_run.return_value = _mock_run(stdout="[]")
        svc = _make_service()
        result = svc.search_charts("nginx", repository="bitnami")
        assert result == []
        # Verify the command used bitnami/nginx
        call_args = mock_run.call_args[0][0]
        assert "bitnami/nginx" in call_args
