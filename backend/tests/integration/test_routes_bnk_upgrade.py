"""
Integration tests for BNK upgrade routes — /api/k8s/clusters/{id}/bnk/upgrade/*.

Covers: available versions, current version, create plan, execute, rollback,
        cancel, history, detail, RBAC enforcement.
Uses FastAPI TestClient with real SQLite DB. BnkUpgradeService + Celery tasks mocked.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from models import BnkUpgrade


def _make_upgrade(db, cluster, **overrides):
    """Helper to create a BnkUpgrade record directly in the DB."""
    defaults = {
        "cluster_id": cluster.id,
        "project_id": None,
        "from_version": "1.6.0",
        "to_version": "1.7.0",
        "status": "ready",
        "pre_check_passed": True,
        "pre_checks": [{"name": "health", "status": "pass"}],
        "plan": [{"step": 1, "action": "backup", "label": "Backup config"}],
        "total_steps": 3,
        "current_step": 0,
        "rollback_available": False,
        "triggered_by": "testadmin",
    }
    defaults.update(overrides)
    upgrade = BnkUpgrade(**defaults)
    db.add(upgrade)
    db.commit()
    db.refresh(upgrade)
    return upgrade


class TestGetAvailableVersions:
    """GET /api/k8s/clusters/{cluster_id}/bnk/upgrade/versions."""

    @patch("services.bnk_upgrade_service.BnkUpgradeService")
    def test_get_versions(
        self, mock_svc_cls, client, viewer_headers, all_test_users,
        sample_project, make_k8s_cluster,
    ):
        """Viewer can list available BNK versions for a cluster."""
        cluster = make_k8s_cluster(project=sample_project, name="ver-cluster")

        mock_svc = MagicMock()
        mock_svc.get_available_versions.return_value = (
            [
                {"version": "1.7.0", "label": "BNK 1.7.0"},
                {"version": "1.8.0", "label": "BNK 1.8.0"},
            ],
            None,
        )
        mock_svc.get_cluster_bnk_version.return_value = {"flo_version": "1.6.0"}
        mock_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/bnk/upgrade/versions",
            headers=viewer_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["current_version"] == "1.6.0"
        assert len(data["available_versions"]) == 2
        assert data["registry_available"] is True


class TestGetCurrentVersion:
    """GET /api/k8s/clusters/{cluster_id}/bnk/upgrade/current."""

    @patch("services.bnk_upgrade_service.BnkUpgradeService")
    def test_get_current_version(
        self, mock_svc_cls, client, viewer_headers, all_test_users,
        sample_project, make_k8s_cluster,
    ):
        """Viewer can get the current BNK version for a cluster."""
        cluster = make_k8s_cluster(project=sample_project, name="cur-ver-cluster")

        mock_svc = MagicMock()
        mock_svc.get_cluster_bnk_version.return_value = {
            "status": "healthy",
            "flo_version": "1.6.0",
            "health": "ok",
        }
        mock_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/bnk/upgrade/current",
            headers=viewer_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["flo_version"] == "1.6.0"
        assert data["status"] == "healthy"


class TestCreateUpgradePlan:
    """POST /api/k8s/clusters/{cluster_id}/bnk/upgrade/plan."""

    @patch("services.bnk_upgrade_service.BnkUpgradeService")
    def test_create_plan(
        self, mock_svc_cls, client, operator_headers, all_test_users,
        sample_project, make_k8s_cluster, db,
    ):
        """Operator can create an upgrade plan."""
        cluster = make_k8s_cluster(project=sample_project, name="plan-cluster")

        mock_upgrade = MagicMock()
        mock_upgrade.id = 1
        mock_upgrade.cluster_id = cluster.id
        mock_upgrade.project_id = None
        mock_upgrade.from_version = "1.6.0"
        mock_upgrade.to_version = "1.7.0"
        mock_upgrade.status = "ready"
        mock_upgrade.pre_check_passed = True
        mock_upgrade.pre_checks = [{"name": "health", "status": "pass"}]
        mock_upgrade.plan = [{"step": 1, "action": "backup"}]
        mock_upgrade.total_steps = 3
        mock_upgrade.current_step = 0
        mock_upgrade.step_results = []
        mock_upgrade.pre_health = None
        mock_upgrade.post_health = None
        mock_upgrade.health_gate_passed = None
        mock_upgrade.rollback_available = False
        mock_upgrade.error_message = None
        mock_upgrade.error_step = None
        mock_upgrade.triggered_by = "testoperator"
        mock_upgrade.approved_by = None
        mock_upgrade.started_at = None
        mock_upgrade.completed_at = None
        mock_upgrade.duration_seconds = None
        mock_upgrade.celery_task_id = None
        mock_upgrade.created_at = datetime.now(UTC)

        mock_svc = MagicMock()
        mock_svc.create_upgrade.return_value = mock_upgrade
        mock_svc_cls.return_value = mock_svc

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk/upgrade/plan",
            json={"target_version": "1.7.0"},
            headers=operator_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["to_version"] == "1.7.0"
        assert data["status"] == "ready"
        assert data["pre_check_passed"] is True

    def test_viewer_cannot_create_plan(
        self, client, viewer_headers, all_test_users,
        sample_project, make_k8s_cluster,
    ):
        """Viewer cannot create an upgrade plan — returns 403."""
        cluster = make_k8s_cluster(project=sample_project, name="viewer-plan-cluster")

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk/upgrade/plan",
            json={"target_version": "1.7.0"},
            headers=viewer_headers,
        )
        assert response.status_code == 403


class TestExecuteUpgrade:
    """POST /api/k8s/clusters/{cluster_id}/bnk/upgrade/{upgrade_id}/execute."""

    @patch("tasks.bnk_upgrade_tasks.execute_upgrade_task")
    def test_execute_ready_upgrade(
        self, mock_task, client, operator_headers, all_test_users,
        sample_project, make_k8s_cluster, db,
    ):
        """Operator can execute a ready upgrade plan."""
        cluster = make_k8s_cluster(project=sample_project, name="exec-cluster")
        upgrade = _make_upgrade(db, cluster, status="ready")

        mock_task.delay.return_value = MagicMock(id="celery-task-abc")

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk/upgrade/{upgrade.id}/execute",
            headers=operator_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "in_progress"
        assert data["celery_task_id"] == "celery-task-abc"
        mock_task.delay.assert_called_once_with(upgrade.id)

    @patch("tasks.bnk_upgrade_tasks.execute_upgrade_task")
    def test_execute_non_ready_upgrade_rejected(
        self, mock_task, client, operator_headers, all_test_users,
        sample_project, make_k8s_cluster, db,
    ):
        """Cannot execute an upgrade that isn't in 'ready' status — returns 400."""
        cluster = make_k8s_cluster(project=sample_project, name="exec-fail-cluster")
        upgrade = _make_upgrade(db, cluster, status="planning")

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk/upgrade/{upgrade.id}/execute",
            headers=operator_headers,
        )
        assert response.status_code == 400


class TestRollbackUpgrade:
    """POST /api/k8s/clusters/{cluster_id}/bnk/upgrade/{upgrade_id}/rollback."""

    @patch("tasks.bnk_upgrade_tasks.rollback_upgrade_task")
    def test_rollback_failed_upgrade(
        self, mock_task, client, operator_headers, all_test_users,
        sample_project, make_k8s_cluster, db,
    ):
        """Operator can rollback a failed upgrade."""
        cluster = make_k8s_cluster(project=sample_project, name="rollback-cluster")
        upgrade = _make_upgrade(
            db, cluster, status="failed", rollback_available=True,
        )

        mock_task.delay.return_value = MagicMock(id="celery-rollback-abc")

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk/upgrade/{upgrade.id}/rollback",
            headers=operator_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "rolling_back"
        mock_task.delay.assert_called_once_with(upgrade.id)

    @patch("tasks.bnk_upgrade_tasks.rollback_upgrade_task")
    def test_rollback_without_availability_rejected(
        self, mock_task, client, operator_headers, all_test_users,
        sample_project, make_k8s_cluster, db,
    ):
        """Cannot rollback if rollback_available is False — returns 400."""
        cluster = make_k8s_cluster(project=sample_project, name="no-rb-cluster")
        upgrade = _make_upgrade(
            db, cluster, status="failed", rollback_available=False,
        )

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk/upgrade/{upgrade.id}/rollback",
            headers=operator_headers,
        )
        assert response.status_code == 400


class TestCancelUpgrade:
    """POST /api/k8s/clusters/{cluster_id}/bnk/upgrade/{upgrade_id}/cancel."""

    @patch("services.bnk_upgrade_service.BnkUpgradeService")
    def test_cancel_upgrade(
        self, mock_svc_cls, client, operator_headers, all_test_users,
        sample_project, make_k8s_cluster, db,
    ):
        """Operator can cancel a pending upgrade."""
        cluster = make_k8s_cluster(project=sample_project, name="cancel-cluster")

        mock_upgrade = MagicMock()
        mock_upgrade.id = 99
        mock_upgrade.cluster_id = cluster.id
        mock_upgrade.project_id = None
        mock_upgrade.from_version = "1.6.0"
        mock_upgrade.to_version = "1.7.0"
        mock_upgrade.status = "cancelled"
        mock_upgrade.pre_check_passed = True
        mock_upgrade.pre_checks = []
        mock_upgrade.plan = []
        mock_upgrade.total_steps = 0
        mock_upgrade.current_step = 0
        mock_upgrade.step_results = []
        mock_upgrade.pre_health = None
        mock_upgrade.post_health = None
        mock_upgrade.health_gate_passed = None
        mock_upgrade.rollback_available = False
        mock_upgrade.error_message = None
        mock_upgrade.error_step = None
        mock_upgrade.triggered_by = "testoperator"
        mock_upgrade.approved_by = None
        mock_upgrade.started_at = None
        mock_upgrade.completed_at = None
        mock_upgrade.duration_seconds = None
        mock_upgrade.celery_task_id = None
        mock_upgrade.created_at = datetime.now(UTC)

        mock_svc = MagicMock()
        mock_svc.cancel_upgrade.return_value = mock_upgrade
        mock_svc_cls.return_value = mock_svc

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk/upgrade/{99}/cancel",
            headers=operator_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "cancelled"


class TestUpgradeHistory:
    """GET /api/k8s/clusters/{cluster_id}/bnk/upgrade/history."""

    @patch("services.bnk_upgrade_service.BnkUpgradeService")
    def test_list_upgrade_history(
        self, mock_svc_cls, client, viewer_headers, all_test_users,
        sample_project, make_k8s_cluster,
    ):
        """Viewer can list upgrade history for a cluster."""
        cluster = make_k8s_cluster(project=sample_project, name="hist-cluster")

        mock_u = MagicMock()
        mock_u.id = 1
        mock_u.cluster_id = cluster.id
        mock_u.project_id = None
        mock_u.from_version = "1.5.0"
        mock_u.to_version = "1.6.0"
        mock_u.status = "completed"
        mock_u.pre_check_passed = True
        mock_u.pre_checks = []
        mock_u.plan = []
        mock_u.total_steps = 3
        mock_u.current_step = 3
        mock_u.step_results = []
        mock_u.pre_health = None
        mock_u.post_health = None
        mock_u.health_gate_passed = True
        mock_u.rollback_available = False
        mock_u.error_message = None
        mock_u.error_step = None
        mock_u.triggered_by = "admin"
        mock_u.approved_by = "admin"
        mock_u.started_at = datetime.now(UTC)
        mock_u.completed_at = datetime.now(UTC)
        mock_u.duration_seconds = 120.5
        mock_u.celery_task_id = "celery-done-123"
        mock_u.created_at = datetime.now(UTC)

        mock_svc = MagicMock()
        mock_svc.list_upgrades.return_value = [mock_u]
        mock_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/bnk/upgrade/history",
            headers=viewer_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 1
        assert data["upgrades"][0]["status"] == "completed"


class TestGetUpgradeDetail:
    """GET /api/k8s/clusters/{cluster_id}/bnk/upgrade/{upgrade_id}."""

    @patch("services.bnk_upgrade_service.BnkUpgradeService")
    def test_get_upgrade_detail(
        self, mock_svc_cls, client, viewer_headers, all_test_users,
        sample_project, make_k8s_cluster,
    ):
        """Viewer can get upgrade detail."""
        cluster = make_k8s_cluster(project=sample_project, name="detail-cluster")

        mock_u = MagicMock()
        mock_u.id = 5
        mock_u.cluster_id = cluster.id
        mock_u.project_id = None
        mock_u.from_version = "1.6.0"
        mock_u.to_version = "1.7.0"
        mock_u.status = "in_progress"
        mock_u.pre_check_passed = True
        mock_u.pre_checks = []
        mock_u.plan = [{"step": 1, "action": "backup"}]
        mock_u.total_steps = 3
        mock_u.current_step = 1
        mock_u.step_results = [{"step": 1, "status": "done"}]
        mock_u.pre_health = {"status": "ok"}
        mock_u.post_health = None
        mock_u.health_gate_passed = None
        mock_u.rollback_available = True
        mock_u.error_message = None
        mock_u.error_step = None
        mock_u.triggered_by = "admin"
        mock_u.approved_by = "admin"
        mock_u.started_at = datetime.now(UTC)
        mock_u.completed_at = None
        mock_u.duration_seconds = None
        mock_u.celery_task_id = "celery-running-456"
        mock_u.created_at = datetime.now(UTC)

        mock_svc = MagicMock()
        mock_svc.get_upgrade.return_value = mock_u
        mock_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/bnk/upgrade/5",
            headers=viewer_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["id"] == 5
        assert data["status"] == "in_progress"
        assert data["rollback_available"] is True

    @patch("services.bnk_upgrade_service.BnkUpgradeService")
    def test_get_upgrade_not_found(
        self, mock_svc_cls, client, viewer_headers, all_test_users,
        sample_project, make_k8s_cluster,
    ):
        """Nonexistent upgrade returns 404."""
        cluster = make_k8s_cluster(project=sample_project, name="nf-cluster")

        mock_svc = MagicMock()
        mock_svc.get_upgrade.return_value = None
        mock_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/bnk/upgrade/99999",
            headers=viewer_headers,
        )
        assert response.status_code == 404


class TestBnkUpgradeRBAC:
    """RBAC enforcement for BNK upgrade endpoints."""

    def test_viewer_cannot_execute(
        self, client, viewer_headers, all_test_users,
        sample_project, make_k8s_cluster, db,
    ):
        """Viewer cannot execute an upgrade — returns 403."""
        cluster = make_k8s_cluster(project=sample_project, name="rbac-exec-cluster")

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk/upgrade/1/execute",
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_viewer_cannot_rollback(
        self, client, viewer_headers, all_test_users,
        sample_project, make_k8s_cluster,
    ):
        """Viewer cannot rollback an upgrade — returns 403."""
        cluster = make_k8s_cluster(project=sample_project, name="rbac-rb-cluster")

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/bnk/upgrade/1/rollback",
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_unauthenticated_cannot_list_versions(
        self, client, sample_project, make_k8s_cluster, db,
    ):
        """Unauthenticated request to list versions returns 401."""
        cluster = make_k8s_cluster(project=sample_project, name="unauth-cluster")

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/bnk/upgrade/versions",
        )
        assert response.status_code == 401
