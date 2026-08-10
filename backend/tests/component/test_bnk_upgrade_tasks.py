"""
BC-C3: Component tests for BNK upgrade Celery tasks.

Tests execute_upgrade_task and rollback_upgrade_task with mocked DB and services.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from models import BnkUpgrade, KubernetesCluster, Project

_MOD = "tasks.bnk_upgrade_tasks"


# ── Helpers ──────────────────────────────────────────────────────────

def _make_upgrade_env(db, status="pending"):
    """Create project + cluster + upgrade for testing."""
    project = Project(
        name="Upgrade Project", project_type="kubernetes",
        cloud_provider="on-prem", environment="dev",
        backend_type="local", color="#000", icon="cloud",
        is_active=True,
    )
    db.add(project)
    db.flush()

    cluster = KubernetesCluster(
        name="upgrade-cluster", project_id=project.id,
        context="upgrade-ctx",
    )
    db.add(cluster)
    db.flush()

    upgrade = BnkUpgrade(
        status=status,
        cluster_id=cluster.id,
        project_id=project.id,
        from_version="1.0.0",
        to_version="2.0.0",
        created_at=datetime.utcnow(),
    )
    db.add(upgrade)
    db.commit()
    return project, cluster, upgrade


def _make_upgrade_result(status="completed"):
    """Build a mock UpgradeResult."""
    result = MagicMock()
    result.status = status
    result.from_version = "1.0.0"
    result.to_version = "2.0.0"
    result.duration_seconds = 120.0
    result.error_message = None if status == "completed" else "Step failed"
    result.error_step = None if status == "completed" else "apply-crd"
    result.cluster_id = 1
    result.project_id = 1
    return result


# ── execute_upgrade_task ─────────────────────────────────────────────

class TestExecuteUpgradeTask:
    """tasks.bnk_upgrade_tasks.execute_upgrade_task."""

    @patch("services.alert_service.fire_alert")
    @patch("services.bnk_upgrade_service.BnkUpgradeService")
    @patch(f"{_MOD}.get_db_context")
    def test_execute_success(self, mock_db_ctx, mock_svc_cls, mock_alert, db):
        """Successful upgrade returns success."""
        project, cluster, upgrade = _make_upgrade_env(db)

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

        mock_svc = MagicMock()
        mock_svc.execute_upgrade.return_value = _make_upgrade_result("completed")
        mock_svc_cls.return_value = mock_svc

        from tasks.bnk_upgrade_tasks import execute_upgrade_task
        result = execute_upgrade_task(upgrade.id)

        assert result["success"] is True
        assert result["status"] == "completed"

    @patch("services.alert_service.fire_alert")
    @patch("services.bnk_upgrade_service.BnkUpgradeService")
    @patch(f"{_MOD}.get_db_context")
    def test_execute_failure(self, mock_db_ctx, mock_svc_cls, mock_alert, db):
        """Failed upgrade returns failure with error."""
        project, cluster, upgrade = _make_upgrade_env(db)

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

        mock_svc = MagicMock()
        mock_svc.execute_upgrade.return_value = _make_upgrade_result("failed")
        mock_svc_cls.return_value = mock_svc

        from tasks.bnk_upgrade_tasks import execute_upgrade_task
        result = execute_upgrade_task(upgrade.id)

        assert result["success"] is False
        assert result["status"] == "failed"

    @patch(f"{_MOD}.get_db_context")
    def test_execute_upgrade_not_found(self, mock_db_ctx, db):
        """Missing upgrade returns error dict."""
        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

        from tasks.bnk_upgrade_tasks import execute_upgrade_task
        result = execute_upgrade_task(99999)

        assert result["success"] is False
        assert "not found" in result["error"].lower()


# ── rollback_upgrade_task ────────────────────────────────────────────

class TestRollbackUpgradeTask:
    """tasks.bnk_upgrade_tasks.rollback_upgrade_task."""

    @patch("services.alert_service.fire_alert")
    @patch("services.bnk_upgrade_service.BnkUpgradeService")
    @patch(f"{_MOD}.get_db_context")
    def test_rollback_success(self, mock_db_ctx, mock_svc_cls, mock_alert, db):
        """Successful rollback returns rolled_back status."""
        project, cluster, upgrade = _make_upgrade_env(db, status="failed")

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

        rollback_result = MagicMock()
        rollback_result.status = "rolled_back"
        rollback_result.from_version = "1.0.0"
        rollback_result.to_version = "2.0.0"
        rollback_result.cluster_id = cluster.id
        rollback_result.project_id = project.id

        mock_svc = MagicMock()
        mock_svc.rollback.return_value = rollback_result
        mock_svc_cls.return_value = mock_svc

        from tasks.bnk_upgrade_tasks import rollback_upgrade_task
        result = rollback_upgrade_task(upgrade.id)

        assert result["success"] is True
        assert result["status"] == "rolled_back"
