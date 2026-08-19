"""
Integration tests for drift detection routes — /api/projects/{pid}/drift/* and /api/drift/*.

Covers: get/update settings, enable/disable drift, global summary, drift checks,
trigger checks, recent drifted, project summary, stats, cluster drift status, RBAC.
Uses FastAPI TestClient with real SQLite DB. DriftService is mocked.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest


def _make_drift_settings(**overrides):
    """Build a mock DriftSettingsResponse-compatible dict."""
    base = {
        "id": 1,
        "project_id": 1,
        "enabled": False,
        "schedule_type": "cron",
        "schedule_value": "0 2 * * *",
        "check_all_modules": True,
        "module_ids": None,
        "notify_on_drift": True,
        "notification_channels": None,
        "notification_config": None,
        "ignore_insignificant_changes": False,
        "ignore_patterns": None,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    base.update(overrides)
    return base


def _make_drift_summary(**overrides):
    """Build a mock DriftSummaryResponse-compatible dict."""
    base = {
        "total_checks": 10,
        "drift_detected_count": 2,
        "no_drift_count": 7,
        "failed_count": 1,
        "last_check_at": datetime.now(UTC).isoformat(),
        "projects_with_drift": 1,
        "modules_with_drift": 2,
    }
    base.update(overrides)
    return base


def _make_drift_check(**overrides):
    """Build a mock DriftCheckResponse-compatible dict."""
    now = datetime.now(UTC).isoformat()
    base = {
        "id": 1,
        "project_id": 1,
        "module_id": 1,
        "module_name": "test-vpc",
        "schedule_enabled": True,
        "schedule_cron": "0 2 * * *",
        "last_check_at": now,
        "next_check_at": now,
        "drift_detected": False,
        "drift_summary": None,
        "drift_details": None,
        "task_id": None,
        "status": "completed",
        "error_message": None,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return base


class TestGetDriftSettings:
    """GET /api/projects/{project_id}/drift/settings."""

    @patch("routes.drift.DriftService")
    def test_get_drift_settings(
        self, mock_svc_cls, client, viewer_headers, all_test_users, sample_project,
    ):
        """Viewer can get drift settings for a project."""
        mock_svc = MagicMock()
        mock_svc.get_settings.return_value = _make_drift_settings(
            project_id=sample_project.id,
        )
        mock_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/projects/{sample_project.id}/drift/settings",
            headers=viewer_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["project_id"] == sample_project.id
        assert data["schedule_type"] == "cron"
        mock_svc.get_settings.assert_called_once_with(sample_project.id)


class TestUpdateDriftSettings:
    """PUT /api/projects/{project_id}/drift/settings."""

    @patch("routes.drift.DriftService")
    def test_update_drift_settings(
        self, mock_svc_cls, client, operator_headers, all_test_users, sample_project,
    ):
        """Operator can update drift settings."""
        mock_svc = MagicMock()
        mock_svc.update_settings.return_value = _make_drift_settings(
            project_id=sample_project.id,
            enabled=True,
            schedule_type="interval",
            schedule_value="30m",
        )
        mock_svc_cls.return_value = mock_svc

        response = client.put(
            f"/api/projects/{sample_project.id}/drift/settings",
            json={
                "enabled": True,
                "schedule_type": "interval",
                "schedule_value": "30m",
            },
            headers=operator_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["enabled"] is True
        assert data["schedule_type"] == "interval"
        mock_svc.update_settings.assert_called_once()


class TestEnableDrift:
    """POST /api/projects/{project_id}/drift/enable."""

    @patch("routes.drift.DriftService")
    def test_enable_drift(
        self, mock_svc_cls, client, operator_headers, all_test_users, sample_project,
    ):
        """Operator can enable drift detection for a project."""
        mock_svc = MagicMock()
        mock_svc.enable_drift.return_value = _make_drift_settings(
            project_id=sample_project.id, enabled=True,
        )
        mock_svc_cls.return_value = mock_svc

        response = client.post(
            f"/api/projects/{sample_project.id}/drift/enable",
            headers=operator_headers,
        )
        assert response.status_code == 200
        assert response.json()["enabled"] is True
        mock_svc.enable_drift.assert_called_once_with(sample_project.id)


class TestDisableDrift:
    """POST /api/projects/{project_id}/drift/disable."""

    @patch("routes.drift.DriftService")
    def test_disable_drift(
        self, mock_svc_cls, client, operator_headers, all_test_users, sample_project,
    ):
        """Operator can disable drift detection for a project."""
        mock_svc = MagicMock()
        mock_svc.disable_drift.return_value = _make_drift_settings(
            project_id=sample_project.id, enabled=False,
        )
        mock_svc_cls.return_value = mock_svc

        response = client.post(
            f"/api/projects/{sample_project.id}/drift/disable",
            headers=operator_headers,
        )
        assert response.status_code == 200
        assert response.json()["enabled"] is False
        mock_svc.disable_drift.assert_called_once_with(sample_project.id)


class TestGetDriftSummary:
    """GET /api/drift/summary."""

    @patch("routes.drift.DriftService")
    def test_get_drift_summary(
        self, mock_svc_cls, client, viewer_headers, all_test_users,
    ):
        """Viewer can get global drift summary."""
        mock_svc = MagicMock()
        mock_svc.get_global_summary.return_value = _make_drift_summary()
        mock_svc_cls.return_value = mock_svc

        response = client.get("/api/drift/summary", headers=viewer_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["total_checks"] == 10
        assert data["drift_detected_count"] == 2
        assert data["projects_with_drift"] == 1
        mock_svc.get_global_summary.assert_called_once()


class TestDriftRBAC:
    """RBAC enforcement for drift endpoints."""

    def test_viewer_cannot_enable(self, client, viewer_headers, all_test_users, sample_project):
        """Viewer cannot enable drift detection — returns 403."""
        response = client.post(
            f"/api/projects/{sample_project.id}/drift/enable",
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_viewer_cannot_disable(self, client, viewer_headers, all_test_users, sample_project):
        """Viewer cannot disable drift detection — returns 403."""
        response = client.post(
            f"/api/projects/{sample_project.id}/drift/disable",
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_viewer_cannot_update_settings(self, client, viewer_headers, all_test_users, sample_project):
        """Viewer cannot update drift settings — returns 403."""
        response = client.put(
            f"/api/projects/{sample_project.id}/drift/settings",
            json={"enabled": True},
            headers=viewer_headers,
        )
        assert response.status_code == 403


# ============================================================================
# Drift Checks (history)
# ============================================================================


class TestGetDriftChecks:
    """GET /api/projects/{project_id}/drift/checks."""

    def test_requires_auth(self, client, sample_project):
        """Drift checks require authentication."""
        response = client.get(f"/api/projects/{sample_project.id}/drift/checks")
        assert response.status_code == 401

    @patch("routes.drift.DriftService")
    def test_returns_checks(
        self, mock_svc_cls, client, viewer_headers, all_test_users, sample_project,
    ):
        """Viewer can retrieve drift check history for a project."""
        checks = [
            _make_drift_check(id=1, project_id=sample_project.id),
            _make_drift_check(id=2, project_id=sample_project.id, drift_detected=True, status="drift_detected"),
        ]
        mock_svc = MagicMock()
        mock_svc.get_checks.return_value = checks
        mock_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/projects/{sample_project.id}/drift/checks",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    @patch("routes.drift.DriftService")
    def test_filters_by_module_id(
        self, mock_svc_cls, client, viewer_headers, all_test_users, sample_project,
    ):
        """module_id query param is forwarded to service."""
        mock_svc = MagicMock()
        mock_svc.get_checks.return_value = []
        mock_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/projects/{sample_project.id}/drift/checks?module_id=5",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        mock_svc.get_checks.assert_called_once_with(
            sample_project.id, module_id=5, status=None, limit=50, offset=0,
        )

    @patch("routes.drift.DriftService")
    def test_filters_by_status(
        self, mock_svc_cls, client, viewer_headers, all_test_users, sample_project,
    ):
        """status query param is forwarded to service."""
        mock_svc = MagicMock()
        mock_svc.get_checks.return_value = []
        mock_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/projects/{sample_project.id}/drift/checks?status=drift_detected&limit=10&offset=5",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        mock_svc.get_checks.assert_called_once_with(
            sample_project.id, module_id=None, status="drift_detected", limit=10, offset=5,
        )


class TestGetDriftCheckDetails:
    """GET /api/drift/checks/{check_id}."""

    def test_requires_auth(self, client):
        response = client.get("/api/drift/checks/1")
        assert response.status_code == 401

    @patch("routes.drift.DriftService")
    def test_returns_check_detail(
        self, mock_svc_cls, client, viewer_headers, all_test_users,
    ):
        """Viewer can retrieve a specific drift check by ID."""
        check = _make_drift_check(
            id=42,
            drift_detected=True,
            drift_summary="3 resources changed",
            drift_details={"added": 1, "changed": 2, "removed": 0},
        )
        mock_svc = MagicMock()
        mock_svc.get_check_details.return_value = check
        mock_svc_cls.return_value = mock_svc

        response = client.get("/api/drift/checks/42", headers=viewer_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 42
        assert data["drift_detected"] is True
        assert data["drift_summary"] == "3 resources changed"
        mock_svc.get_check_details.assert_called_once_with(42)


# ============================================================================
# Trigger Drift Check
# ============================================================================


class TestTriggerProjectDriftCheck:
    """POST /api/projects/{project_id}/drift/check-now."""

    def test_requires_auth(self, client, sample_project):
        response = client.post(
            f"/api/projects/{sample_project.id}/drift/check-now",
            json={},
        )
        assert response.status_code == 401

    def test_viewer_denied(self, client, viewer_headers, all_test_users, sample_project):
        """Viewer cannot trigger drift checks — requires project_owner (operator+)."""
        response = client.post(
            f"/api/projects/{sample_project.id}/drift/check-now",
            json={},
            headers=viewer_headers,
        )
        assert response.status_code == 403

    @patch("routes.drift.DriftService")
    def test_trigger_all_modules(
        self, mock_svc_cls, client, operator_headers, all_test_users, sample_project,
    ):
        """Operator can trigger drift check for all project modules."""
        mock_svc = MagicMock()
        mock_svc.trigger_project_check.return_value = {
            "status": "triggered",
            "modules_checked": 3,
        }
        mock_svc_cls.return_value = mock_svc

        response = client.post(
            f"/api/projects/{sample_project.id}/drift/check-now",
            json={},
            headers=operator_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "triggered"
        mock_svc.trigger_project_check.assert_called_once_with(
            sample_project.id, module_ids=None,
        )

    @patch("routes.drift.DriftService")
    def test_trigger_specific_modules(
        self, mock_svc_cls, client, operator_headers, all_test_users, sample_project,
    ):
        """Operator can trigger drift check for specific module IDs."""
        mock_svc = MagicMock()
        mock_svc.trigger_project_check.return_value = {"status": "triggered", "modules_checked": 2}
        mock_svc_cls.return_value = mock_svc

        response = client.post(
            f"/api/projects/{sample_project.id}/drift/check-now",
            json={"module_ids": [1, 3]},
            headers=operator_headers,
        )
        assert response.status_code == 200
        mock_svc.trigger_project_check.assert_called_once_with(
            sample_project.id, module_ids=[1, 3],
        )


class TestTriggerModuleDriftCheck:
    """POST /api/project-modules/{module_id}/drift/check-now."""

    def test_requires_auth(self, client):
        response = client.post("/api/project-modules/1/drift/check-now")
        assert response.status_code == 401

    def test_viewer_denied(self, client, viewer_headers, sample_viewer_user, sample_module):
        """Viewer cannot trigger module drift check — requires module_owner."""
        mod = sample_module["module"]
        response = client.post(
            f"/api/project-modules/{mod.id}/drift/check-now",
            headers=viewer_headers,
        )
        assert response.status_code == 403

    @patch("routes.drift.DriftService")
    def test_trigger_module_check(
        self, mock_svc_cls, client, admin_headers, sample_module,
    ):
        """Admin (project owner) can trigger drift check for a specific module."""
        mod = sample_module["module"]
        mock_svc = MagicMock()
        mock_svc.trigger_module_check.return_value = {
            "status": "triggered",
            "module_id": mod.id,
        }
        mock_svc_cls.return_value = mock_svc

        response = client.post(
            f"/api/project-modules/{mod.id}/drift/check-now",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "triggered"
        mock_svc.trigger_module_check.assert_called_once_with(mod.id)


# ============================================================================
# Dashboard Endpoints
# ============================================================================


class TestRecentDrifted:
    """GET /api/drift/recent."""

    def test_requires_auth(self, client):
        response = client.get("/api/drift/recent")
        assert response.status_code == 401

    @patch("routes.drift.DriftService")
    def test_returns_recent_drifted(
        self, mock_svc_cls, client, viewer_headers, all_test_users,
    ):
        """Viewer can retrieve recently drifted checks."""
        checks = [
            _make_drift_check(id=10, drift_detected=True),
            _make_drift_check(id=11, drift_detected=True),
        ]
        mock_svc = MagicMock()
        mock_svc.get_recent_drifted.return_value = checks
        mock_svc_cls.return_value = mock_svc

        response = client.get("/api/drift/recent", headers=viewer_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        mock_svc.get_recent_drifted.assert_called_once_with(limit=10)

    @patch("routes.drift.DriftService")
    def test_respects_limit_param(
        self, mock_svc_cls, client, viewer_headers, all_test_users,
    ):
        """Limit query param is forwarded to service."""
        mock_svc = MagicMock()
        mock_svc.get_recent_drifted.return_value = []
        mock_svc_cls.return_value = mock_svc

        response = client.get("/api/drift/recent?limit=5", headers=viewer_headers)
        assert response.status_code == 200
        mock_svc.get_recent_drifted.assert_called_once_with(limit=5)


class TestProjectDriftSummary:
    """GET /api/projects/{project_id}/drift/summary."""

    def test_requires_auth(self, client, sample_project):
        response = client.get(f"/api/projects/{sample_project.id}/drift/summary")
        assert response.status_code == 401

    @patch("routes.drift.DriftService")
    def test_returns_project_summary(
        self, mock_svc_cls, client, viewer_headers, all_test_users, sample_project,
    ):
        """Viewer can get drift summary for a specific project."""
        summary = _make_drift_summary(total_checks=5, drift_detected_count=1)
        mock_svc = MagicMock()
        mock_svc.get_project_summary.return_value = summary
        mock_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/projects/{sample_project.id}/drift/summary",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total_checks"] == 5
        assert data["drift_detected_count"] == 1
        mock_svc.get_project_summary.assert_called_once_with(sample_project.id)


class TestDriftStats:
    """GET /api/drift/stats."""

    def test_requires_auth(self, client):
        response = client.get("/api/drift/stats")
        assert response.status_code == 401

    @patch("routes.drift.DriftService")
    def test_returns_stats(
        self, mock_svc_cls, client, viewer_headers, all_test_users,
    ):
        """Viewer can get drift statistics."""
        mock_svc = MagicMock()
        mock_svc.get_stats.return_value = {
            "total_checks": 100,
            "drift_rate": 0.15,
            "avg_check_duration_s": 12.3,
        }
        mock_svc_cls.return_value = mock_svc

        response = client.get("/api/drift/stats", headers=viewer_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total_checks"] == 100
        mock_svc.get_stats.assert_called_once_with(project_id=None, days=None)

    @patch("routes.drift.DriftService")
    def test_filters_by_project_and_days(
        self, mock_svc_cls, client, viewer_headers, all_test_users,
    ):
        """project_id and days query params are forwarded."""
        mock_svc = MagicMock()
        mock_svc.get_stats.return_value = {"total_checks": 10}
        mock_svc_cls.return_value = mock_svc

        response = client.get(
            "/api/drift/stats?project_id=3&days=7",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        mock_svc.get_stats.assert_called_once_with(project_id=3, days=7)


class TestClusterDriftStatus:
    """GET /api/clusters/{cluster_id}/drift/status."""

    def test_requires_auth(self, client, sample_project, make_k8s_cluster):
        cluster = make_k8s_cluster(project=sample_project, name="drift-cluster")
        response = client.get(f"/api/clusters/{cluster.id}/drift/status")
        assert response.status_code == 401

    @patch("routes.drift.DriftService")
    def test_returns_cluster_drift_status(
        self, mock_svc_cls, client, viewer_headers, all_test_users,
        sample_project, make_k8s_cluster,
    ):
        """Viewer can get drift status for modules deployed to a cluster."""
        cluster = make_k8s_cluster(project=sample_project, name="drift-status-cluster")
        mock_svc = MagicMock()
        # Return value must match ClusterDriftStatusResponse (response_model filters strictly).
        mock_svc.get_cluster_drift_status.return_value = {
            "cluster_id": cluster.id,
            "project_id": sample_project.id,
            "drift_enabled": False,
            "total_modules": 1,
            "modules_with_drift": 0,
            "modules_ok": 0,
            "modules_unchecked": 1,
            "overall_status": "unchecked",
            "module_statuses": [
                {
                    "module_id": 1,
                    "module_name": "test-module",
                    "module_path": "modules/test",
                    "engine_type": "python",
                    "status": "unchecked",
                    "drift_detected": False,
                    "drift_summary": None,
                    "drift_details": None,
                    "last_check_at": None,
                    "check_id": None,
                }
            ],
            "release_drift": {
                "status": "not_forge_deployed",
                "deployed_release_id": None,
                "running_release_id": None,
            },
        }
        mock_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/clusters/{cluster.id}/drift/status",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["cluster_id"] == cluster.id
        assert data["total_modules"] == 1
        assert len(data["module_statuses"]) == 1
        assert data["release_drift"]["status"] == "not_forge_deployed"
        mock_svc.get_cluster_drift_status.assert_called_once_with(cluster.id)
