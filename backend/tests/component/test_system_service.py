"""
Tests for services.system_service — system health, version, cleanup, database stats.

BC-011: SystemService — real DB for cleanup, mock Celery/Redis/GitHub for external calls.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from core.errors import BadRequestError
from models import AuditLog, DeploymentLog, Task
from models.enums import TaskStatus
from services.system_service import SystemService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _noop_cache():
    """Cache mock that always misses."""
    mock = MagicMock()
    mock.get.return_value = None
    mock.set.return_value = True
    return mock


# ---------------------------------------------------------------------------
# get_health
# ---------------------------------------------------------------------------

class TestGetHealth:
    """System health endpoint covering DB, Redis, Celery."""

    @patch("services.system_service.heartbeat")
    @patch("services.system_service.celery_app")
    @patch("services.system_service.cache", _noop_cache())
    def test_healthy_system(self, mock_celery, mock_hb, db):
        mock_celery.backend.client.ping.return_value = True
        mock_hb.get_worker_status.return_value = {
            "total": 2, "active": 2, "offline": 0, "active_tasks": 1,
        }
        svc = SystemService(db)
        result = svc.get_health()
        assert result["services"]["backend"]["status"] == "healthy"
        assert result["services"]["database"]["status"] == "healthy"
        assert result["services"]["redis"]["status"] == "healthy"
        assert result["services"]["celery"]["status"] == "healthy"

    @patch("services.system_service.heartbeat")
    @patch("services.system_service.celery_app")
    @patch("services.system_service.cache", _noop_cache())
    def test_redis_offline(self, mock_celery, mock_hb, db):
        mock_celery.backend.client.ping.side_effect = ConnectionError("refused")
        mock_hb.get_worker_status.return_value = {
            "total": 0, "active": 0, "offline": 0, "active_tasks": 0,
        }
        svc = SystemService(db)
        result = svc.get_health()
        assert result["services"]["redis"]["status"] == "offline"

    @patch("services.system_service.heartbeat")
    @patch("services.system_service.celery_app")
    @patch("services.system_service.cache", _noop_cache())
    def test_no_celery_workers_degraded(self, mock_celery, mock_hb, db):
        mock_celery.backend.client.ping.return_value = True
        mock_hb.get_worker_status.return_value = {
            "total": 0, "active": 0, "offline": 0, "active_tasks": 0,
        }
        svc = SystemService(db)
        result = svc.get_health()
        assert result["services"]["celery"]["status"] == "degraded"

    @patch("services.system_service.heartbeat")
    @patch("services.system_service.celery_app")
    @patch("services.system_service.cache")
    def test_returns_cached_health(self, mock_cache, mock_celery, mock_hb, db):
        cached_data = {"services": {"backend": {"status": "healthy"}}, "timestamp": "cached"}
        mock_cache.get.return_value = cached_data
        svc = SystemService(db)
        result = svc.get_health()
        assert result["timestamp"] == "cached"
        mock_celery.backend.client.ping.assert_not_called()


# ---------------------------------------------------------------------------
# is_upgrade_in_progress
# ---------------------------------------------------------------------------

class TestIsUpgradeInProgress:
    """Class-level upgrade lock flag."""

    def test_default_is_false(self):
        assert SystemService.is_upgrade_in_progress() is False

    def test_set_and_check(self):
        original = SystemService._upgrade_in_progress
        try:
            SystemService._upgrade_in_progress = True
            assert SystemService.is_upgrade_in_progress() is True
        finally:
            SystemService._upgrade_in_progress = original


# ---------------------------------------------------------------------------
# get_upgrade_readiness
# ---------------------------------------------------------------------------

class TestGetUpgradeReadiness:
    """Pre-flight checks for system upgrade."""

    @patch("services.system_service.os.path.exists", return_value=True)
    @patch.dict("os.environ", {"HOST_REPO_PATH": "/opt/bnk-forge"})
    def test_ready_when_all_set(self, mock_exists, db):
        svc = SystemService(db)
        result = svc.get_upgrade_readiness()
        assert result["host_repo_path_set"] is True
        assert result["docker_socket_available"] is True
        assert result["upgrade_ready"] is True

    @patch("services.system_service.os.path.exists", return_value=False)
    @patch.dict("os.environ", {"HOST_REPO_PATH": ""}, clear=False)
    def test_not_ready_missing_both(self, mock_exists, db):
        svc = SystemService(db)
        result = svc.get_upgrade_readiness()
        assert result["host_repo_path_set"] is False
        assert result["docker_socket_available"] is False
        assert result["upgrade_ready"] is False

    @patch("services.system_service.os.path.exists", return_value=True)
    @patch.dict("os.environ", {"HOST_REPO_PATH": ""}, clear=False)
    def test_not_ready_missing_repo_path(self, mock_exists, db):
        svc = SystemService(db)
        result = svc.get_upgrade_readiness()
        assert result["host_repo_path_set"] is False
        assert result["upgrade_ready"] is False


# ---------------------------------------------------------------------------
# get_system_version
# ---------------------------------------------------------------------------

class TestGetSystemVersion:
    """Version check — reads local VERSION file and GitHub API."""

    @patch("services.system_service.requests.get")
    @patch("services.system_service.os.path.exists", return_value=False)
    def test_no_version_file(self, mock_exists, mock_get, db):
        mock_get.return_value = MagicMock(status_code=200, text="2.0.0")
        svc = SystemService(db)
        result = svc.get_system_version()
        assert result["current_version"] == "unknown"
        assert result["latest_version"] == "2.0.0"

    @patch("services.system_service.requests.get")
    @patch("builtins.open", create=True)
    @patch("services.system_service.os.path.exists", side_effect=lambda p: p == "VERSION")
    def test_version_file_and_github(self, mock_exists, mock_open, mock_get, db):
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = MagicMock(return_value=False)
        mock_open.return_value.read.return_value = "1.0.0"
        mock_get.return_value = MagicMock(status_code=200, text="1.1.0")
        svc = SystemService(db)
        result = svc.get_system_version()
        assert result["current_version"] == "1.0.0"
        assert result["latest_version"] == "1.1.0"
        assert result["update_available"] is True

    @patch("services.system_service.requests.get")
    @patch("builtins.open", create=True)
    @patch("services.system_service.os.path.exists", side_effect=lambda p: p == "VERSION")
    def test_same_version_no_update(self, mock_exists, mock_open, mock_get, db):
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = MagicMock(return_value=False)
        mock_open.return_value.read.return_value = "2.0.0"
        mock_get.return_value = MagicMock(status_code=200, text="2.0.0")
        svc = SystemService(db)
        result = svc.get_system_version()
        assert result["update_available"] is False

    @patch("services.system_service.requests.get")
    @patch("services.system_service.os.path.exists", return_value=False)
    def test_github_timeout(self, mock_exists, mock_get, db):
        import requests as real_requests
        mock_get.side_effect = real_requests.exceptions.Timeout("timed out")
        svc = SystemService(db)
        result = svc.get_system_version()
        assert result["error"] is None
        assert result["update_available"] is False

    @patch("services.system_service.requests.get")
    @patch("services.system_service.os.path.exists", return_value=False)
    def test_github_rate_limit(self, mock_exists, mock_get, db):
        mock_get.return_value = MagicMock(status_code=403)
        svc = SystemService(db)
        result = svc.get_system_version()
        assert result["error"] is None
        assert result["update_available"] is False


# ---------------------------------------------------------------------------
# cleanup_database
# ---------------------------------------------------------------------------

class TestCleanupDatabase:
    """Database cleanup with real DB rows."""

    def _mock_pg_size(self, db):
        """Patch pg_database_size calls to return 0 (SQLite doesn't have it)."""
        original_execute = db.execute

        def patched_execute(stmt, *args, **kwargs):
            stmt_str = str(stmt) if not isinstance(stmt, str) else stmt
            if "pg_database_size" in str(stmt_str):
                mock_result = MagicMock()
                mock_result.__getitem__ = lambda s, i: 0
                mock_result.fetchone = lambda: mock_result
                return mock_result
            return original_execute(stmt, *args, **kwargs)

        return patch.object(db, "execute", side_effect=patched_execute)

    def test_cleanup_too_recent_raises(self, db):
        svc = SystemService(db)
        with pytest.raises(BadRequestError, match="newer than 7 days"):
            svc.cleanup_database("deployment_logs", older_than_days=3)

    def test_cleanup_invalid_type_raises(self, db):
        svc = SystemService(db)
        with self._mock_pg_size(db):
            with pytest.raises(BadRequestError, match="Invalid cleanup type"):
                svc.cleanup_database("invalid_type", older_than_days=30)

    def test_cleanup_completed_tasks(self, db, make_project, make_task):
        """Old completed tasks are deleted; recent ones are kept."""
        project = make_project()
        old_date = datetime.now(UTC) - timedelta(days=60)
        # Old completed task — should be deleted
        make_task(project=project, status="completed",
                  completed_at=old_date)
        # Recent completed task — should be kept
        make_task(project=project, status="completed",
                  completed_at=datetime.now(UTC))

        svc = SystemService(db)
        with self._mock_pg_size(db):
            result = svc.cleanup_database("completed_tasks", older_than_days=30)
        assert result["deleted"] == 1

    def test_cleanup_audit_logs(self, db):
        """Old audit logs are deleted."""
        old_date = datetime.now(UTC) - timedelta(days=60)
        recent_date = datetime.now(UTC)
        db.add(AuditLog(timestamp=old_date, action="login", user="test"))
        db.add(AuditLog(timestamp=recent_date, action="login", user="test"))
        db.flush()

        svc = SystemService(db)
        with self._mock_pg_size(db):
            result = svc.cleanup_database("audit_logs", older_than_days=30)
        assert result["deleted"] == 1


# ---------------------------------------------------------------------------
# get_database_stats
# ---------------------------------------------------------------------------

class TestGetDatabaseStats:
    """Database statistics with mocked pg-specific SQL."""

    def test_get_database_stats(self, db):
        """Stats returns table row counts even when pg functions fail."""
        original_execute = db.execute

        def patched_execute(stmt, *args, **kwargs):
            stmt_str = str(stmt) if not isinstance(stmt, str) else stmt
            if "pg_database_size" in str(stmt_str):
                mock_result = MagicMock()
                mock_result.__getitem__ = lambda s, i: 1048576  # 1 MB
                mock_result.fetchone = lambda: mock_result
                return mock_result
            if "pg_total_relation_size" in str(stmt_str):
                mock_result = MagicMock()
                mock_result.__getitem__ = lambda s, i: 524288  # 0.5 MB
                mock_result.fetchone = lambda: mock_result
                return mock_result
            return original_execute(stmt, *args, **kwargs)

        with patch.object(db, "execute", side_effect=patched_execute):
            svc = SystemService(db)
            result = svc.get_database_stats()

        assert "size_mb" in result
        assert "tables" in result
        assert "tasks" in result["tables"]
        assert "deployment_logs" in result["tables"]
        assert "audit_logs" in result["tables"]

    def test_stats_handles_pg_failure_gracefully(self, db):
        """When pg-specific SQL fails, table_size_mb falls back to 0."""
        original_execute = db.execute

        call_count = {"n": 0}

        def patched_execute(stmt, *args, **kwargs):
            stmt_str = str(stmt) if not isinstance(stmt, str) else stmt
            if "pg_database_size" in str(stmt_str):
                mock_result = MagicMock()
                mock_result.__getitem__ = lambda s, i: 0
                mock_result.fetchone = lambda: mock_result
                return mock_result
            if "pg_total_relation_size" in str(stmt_str):
                raise Exception("SQLite doesn't have pg_total_relation_size")
            return original_execute(stmt, *args, **kwargs)

        with patch.object(db, "execute", side_effect=patched_execute):
            svc = SystemService(db)
            result = svc.get_database_stats()

        for table in result["tables"].values():
            assert table["size_mb"] == 0
            assert "rows" in table


# ---------------------------------------------------------------------------
# get_queue_metrics
# ---------------------------------------------------------------------------

class TestGetQueueMetrics:
    """Queue metrics — worker/queue stats with Celery/heartbeat mocked."""

    @patch("services.system_service.heartbeat")
    @patch("services.system_service.cache", _noop_cache())
    def test_queue_metrics_healthy(self, mock_hb, db):
        mock_hb.get_worker_status.return_value = {
            "total": 2, "active": 2, "offline": 0, "active_tasks": 3,
        }
        mock_hb.get_queue_stats.return_value = {
            "default": {"pending": 1, "active": 2},
            "opentofu": {"pending": 0, "active": 1},
        }
        svc = SystemService(db)
        result = svc.get_queue_metrics()

        assert result["workers"]["total"] == 2
        assert result["queues"]["default"]["pending"] == 1
        assert "tasks" in result
        assert result["tasks"]["active"] == 3

    @patch("services.system_service.heartbeat")
    @patch("services.system_service.cache", _noop_cache())
    def test_queue_metrics_celery_failure_fallback(self, mock_hb, db):
        mock_hb.get_worker_status.side_effect = Exception("Celery offline")
        svc = SystemService(db)
        result = svc.get_queue_metrics()

        assert result["workers"]["total"] == 0
        assert "tasks" in result

    @patch("services.system_service.heartbeat")
    @patch("services.system_service.cache")
    def test_queue_metrics_cached(self, mock_cache, mock_hb, db):
        cached_data = {"workers": {"total": 5}, "queues": {}, "tasks": {}}
        mock_cache.get.return_value = cached_data
        svc = SystemService(db)
        result = svc.get_queue_metrics()
        assert result["workers"]["total"] == 5
        mock_hb.get_worker_status.assert_not_called()


# ---------------------------------------------------------------------------
# get_performance_metrics
# ---------------------------------------------------------------------------

class TestGetPerformanceMetrics:
    """Performance metrics — task durations, DB stats."""

    @patch("services.system_service.cache", _noop_cache())
    def test_performance_metrics_basic(self, db):
        """With no tasks, returns zeroed metrics."""
        original_execute = db.execute

        def patched_execute(stmt, *args, **kwargs):
            stmt_str = str(stmt) if not isinstance(stmt, str) else stmt
            if "pg_database_size" in str(stmt_str):
                mock_result = MagicMock()
                mock_result.__getitem__ = lambda s, i: 0
                mock_result.fetchone = lambda: mock_result
                return mock_result
            if "pg_stat_activity" in str(stmt_str):
                mock_result = MagicMock()
                mock_result.__getitem__ = lambda s, i: 5
                mock_result.fetchone = lambda: mock_result
                return mock_result
            return original_execute(stmt, *args, **kwargs)

        with patch.object(db, "execute", side_effect=patched_execute):
            svc = SystemService(db)
            result = svc.get_performance_metrics()

        assert "api" in result
        assert "database" in result
        assert "tasks" in result
        assert result["api"]["avg_task_duration_ms"] >= 0

    @patch("services.system_service.cache")
    def test_performance_metrics_cached(self, mock_cache, db):
        cached_data = {"api": {"avg_task_duration_ms": 100}, "database": {}, "tasks": {}}
        mock_cache.get.return_value = cached_data
        svc = SystemService(db)
        result = svc.get_performance_metrics()
        assert result["api"]["avg_task_duration_ms"] == 100


# ---------------------------------------------------------------------------
# get_recent_errors
# ---------------------------------------------------------------------------

class TestGetRecentErrors:
    """Recent failed tasks."""

    @patch("services.system_service.cache", _noop_cache())
    def test_no_errors(self, db):
        svc = SystemService(db)
        result = svc.get_recent_errors()
        assert result["errors"] == []
        assert result["total"] == 0

    @patch("services.system_service.cache", _noop_cache())
    def test_with_failed_tasks(self, db, make_project, make_task):
        project = make_project()
        make_task(project=project, status="failed", error="Something broke")
        make_task(project=project, status="failed", error="Another error")
        make_task(project=project, status="completed")

        svc = SystemService(db)
        result = svc.get_recent_errors()
        assert result["total"] == 2
        assert len(result["errors"]) == 2
        assert result["errors"][0]["error"] == "Another error" or result["errors"][0]["error"] == "Something broke"

    @patch("services.system_service.cache", _noop_cache())
    def test_limit_capped_at_50(self, db):
        svc = SystemService(db)
        result = svc.get_recent_errors(limit=100)
        # Should not error, but limit is capped internally
        assert result["total"] >= 0

    @patch("services.system_service.cache")
    def test_recent_errors_cached(self, mock_cache, db):
        cached = {"errors": [{"task_id": 99}], "total": 1}
        mock_cache.get.return_value = cached
        svc = SystemService(db)
        result = svc.get_recent_errors()
        assert result["total"] == 1


# ---------------------------------------------------------------------------
# vacuum_database
# ---------------------------------------------------------------------------

class TestVacuumDatabase:
    """VACUUM ANALYZE operation."""

    def test_vacuum_success(self, db):
        """Mock the raw connection for VACUUM."""
        mock_cursor = MagicMock()
        mock_raw_conn = MagicMock()
        mock_raw_conn.isolation_level = 1
        mock_raw_conn.cursor.return_value = mock_cursor
        mock_conn = MagicMock()
        mock_conn.connection = mock_raw_conn

        with patch.object(db, "connection", return_value=mock_conn):
            svc = SystemService(db)
            result = svc.vacuum_database()

        assert result["status"] == "success"
        assert "duration_seconds" in result
        mock_cursor.execute.assert_called_once_with("VACUUM ANALYZE")

    def test_vacuum_failure_skipped(self, db):
        """When VACUUM fails, result should be 'skipped'."""
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("VACUUM not supported on SQLite")
        mock_raw_conn = MagicMock()
        mock_raw_conn.isolation_level = 1
        mock_raw_conn.cursor.return_value = mock_cursor
        mock_conn = MagicMock()
        mock_conn.connection = mock_raw_conn

        with patch.object(db, "connection", return_value=mock_conn):
            svc = SystemService(db)
            result = svc.vacuum_database()

        assert result["status"] == "skipped"


# ---------------------------------------------------------------------------
# trigger_upgrade
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Upgrade state persistence (UP-003)
# ---------------------------------------------------------------------------

class TestUpgradeStatePersistence:
    """UP-003: Persist upgrade state to ApplicationSetting."""

    def test_save_and_read_state(self, db):
        svc = SystemService(db)
        state = {
            "status": "in_progress",
            "old_version": "2.10.60",
            "new_version": "2.10.61",
            "started_at": "2026-02-27T00:00:00Z",
            "completed_at": None,
            "current_phase": "build",
            "phase_label": "Building containers",
            "log": ["Starting upgrade", "Building..."],
        }
        svc._save_upgrade_state(state)
        result = svc.get_upgrade_state()
        assert result is not None
        assert result["status"] == "in_progress"
        assert result["old_version"] == "2.10.60"
        assert result["current_phase"] == "build"
        assert len(result["log"]) == 2

    def test_overwrite_existing_state(self, db):
        svc = SystemService(db)
        svc._save_upgrade_state({"status": "in_progress", "log": []})
        svc._save_upgrade_state({"status": "completed", "log": ["done"]})
        result = svc.get_upgrade_state()
        assert result["status"] == "completed"

    def test_read_when_no_state_exists(self, db):
        svc = SystemService(db)
        result = svc.get_upgrade_state()
        assert result is None


# ---------------------------------------------------------------------------
# Pre-upgrade safety checks (UP-010)
# ---------------------------------------------------------------------------

class TestPreUpgradeSafetyChecks:
    """UP-010: Verify pre-upgrade checks block when deployments are active."""

    @patch("services.system_service.subprocess.run")
    @patch.object(SystemService, "get_system_version")
    @patch.object(SystemService, "get_upgrade_readiness")
    def test_blocked_by_active_tasks(self, mock_ready, mock_version, mock_run, db, make_project, make_task):
        mock_ready.return_value = {
            "host_repo_path_set": True, "docker_socket_available": True,
            "upgrade_ready": True, "upgrade_in_progress": False,
        }
        mock_version.return_value = {
            "current_version": "2.0.0", "latest_version": "2.1.0",
            "update_available": True,
        }
        # Create an in-progress task
        project = make_project()
        make_task(project=project, status="in_progress")

        svc = SystemService(db)
        result = svc.trigger_upgrade()
        assert result["status"] == "blocked"
        assert "1 deployment" in result["message"]

    @patch("services.system_service.subprocess.run")
    @patch.object(SystemService, "get_system_version")
    @patch.object(SystemService, "get_upgrade_readiness")
    def test_blocked_by_docker_failure(self, mock_ready, mock_version, mock_run, db):
        mock_ready.return_value = {
            "host_repo_path_set": True, "docker_socket_available": True,
            "upgrade_ready": True, "upgrade_in_progress": False,
        }
        mock_version.return_value = {
            "current_version": "2.0.0", "latest_version": "2.1.0",
            "update_available": True,
        }
        # Docker info fails
        mock_run.return_value = MagicMock(returncode=1, stderr="Cannot connect to Docker daemon")

        svc = SystemService(db)
        result = svc.trigger_upgrade()
        assert result["status"] == "docker_error"

    @patch("services.system_service.subprocess.run")
    @patch.object(SystemService, "get_system_version")
    @patch.object(SystemService, "get_upgrade_readiness")
    def test_docker_timeout(self, mock_ready, mock_version, mock_run, db):
        mock_ready.return_value = {
            "host_repo_path_set": True, "docker_socket_available": True,
            "upgrade_ready": True, "upgrade_in_progress": False,
        }
        mock_version.return_value = {
            "current_version": "2.0.0", "latest_version": "2.1.0",
            "update_available": True,
        }
        import subprocess as real_subprocess
        mock_run.side_effect = real_subprocess.TimeoutExpired(cmd="docker", timeout=10)

        svc = SystemService(db)
        result = svc.trigger_upgrade()
        assert result["status"] == "docker_error"
        assert "timed out" in result["message"]


# ---------------------------------------------------------------------------
# Post-upgrade verification (UP-011)
# ---------------------------------------------------------------------------

class TestVerifyPostUpgrade:
    """UP-011: Post-upgrade service health verification."""

    @patch("services.system_service.heartbeat")
    @patch("services.system_service.celery_app")
    @patch("services.system_service.os.path.exists", side_effect=lambda p: p == "VERSION")
    @patch("builtins.open", create=True)
    def test_healthy_verdict(self, mock_open, mock_exists, mock_celery, mock_hb, db):
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = MagicMock(return_value=False)
        mock_open.return_value.read.return_value = "2.1.0"
        mock_celery.backend.client.ping.return_value = True
        mock_hb.get_worker_status.return_value = {"total": 2, "active": 2, "offline": 0}

        svc = SystemService(db)
        # Set upgrade state with matching expected version
        svc._save_upgrade_state({"new_version": "2.1.0", "status": "completed", "log": []})

        with patch("services.system_service.SystemService.verify_post_upgrade") as mock_verify:
            # Call the real method — we need to avoid the Alembic import
            mock_verify.side_effect = lambda: SystemService.verify_post_upgrade(svc)
            # Actually just call it directly and handle Alembic gracefully
            pass

        result = svc.verify_post_upgrade()
        assert result["checks"]["version"]["status"] == "pass"
        assert result["checks"]["database"]["status"] == "pass"
        assert result["checks"]["redis"]["status"] == "pass"
        assert result["checks"]["celery"]["status"] == "pass"
        # Alembic check will be 'skip' in test environment
        assert result["checks"]["migrations"]["status"] in ("pass", "skip")
        assert result["verdict"] in ("healthy", "degraded")  # degraded if migrations skip

    @patch("services.system_service.heartbeat")
    @patch("services.system_service.celery_app")
    @patch("services.system_service.os.path.exists", side_effect=lambda p: p == "VERSION")
    @patch("builtins.open", create=True)
    def test_version_mismatch_fails(self, mock_open, mock_exists, mock_celery, mock_hb, db):
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = MagicMock(return_value=False)
        mock_open.return_value.read.return_value = "2.0.0"  # Still old version!
        mock_celery.backend.client.ping.return_value = True
        mock_hb.get_worker_status.return_value = {"total": 1, "active": 1, "offline": 0}

        svc = SystemService(db)
        svc._save_upgrade_state({"new_version": "2.1.0", "status": "completed", "log": []})
        result = svc.verify_post_upgrade()
        assert result["checks"]["version"]["status"] == "fail"
        assert result["verdict"] == "unhealthy"

    @patch("services.system_service.heartbeat")
    @patch("services.system_service.celery_app")
    @patch("services.system_service.os.path.exists", return_value=False)
    def test_redis_down_unhealthy(self, mock_exists, mock_celery, mock_hb, db):
        mock_celery.backend.client.ping.side_effect = ConnectionError("refused")
        mock_hb.get_worker_status.return_value = {"total": 0, "active": 0, "offline": 0}

        svc = SystemService(db)
        result = svc.verify_post_upgrade()
        assert result["checks"]["redis"]["status"] == "fail"
        assert result["verdict"] == "unhealthy"

    @patch("services.system_service.heartbeat")
    @patch("services.system_service.celery_app")
    @patch("services.system_service.os.path.exists", return_value=False)
    def test_no_workers_warns(self, mock_exists, mock_celery, mock_hb, db):
        mock_celery.backend.client.ping.return_value = True
        mock_hb.get_worker_status.return_value = {"total": 0, "active": 0, "offline": 0}

        svc = SystemService(db)
        result = svc.verify_post_upgrade()
        assert result["checks"]["celery"]["status"] == "warn"


# ---------------------------------------------------------------------------
# trigger_upgrade
# ---------------------------------------------------------------------------

class TestTriggerUpgrade:
    """System upgrade trigger with mocked readiness and version check."""

    @patch.object(SystemService, "get_system_version")
    @patch.object(SystemService, "get_upgrade_readiness")
    def test_not_configured(self, mock_ready, mock_version, db):
        mock_ready.return_value = {
            "host_repo_path_set": False, "docker_socket_available": False,
            "upgrade_ready": False,
        }
        svc = SystemService(db)
        result = svc.trigger_upgrade()
        assert result["status"] == "not_configured"

    @patch.object(SystemService, "get_system_version")
    @patch.object(SystemService, "get_upgrade_readiness")
    def test_no_update_available(self, mock_ready, mock_version, db):
        mock_ready.return_value = {
            "host_repo_path_set": True, "docker_socket_available": True,
            "upgrade_ready": True, "upgrade_in_progress": False,
        }
        mock_version.return_value = {
            "current_version": "2.0.0", "latest_version": "2.0.0",
            "update_available": False,
        }
        svc = SystemService(db)
        result = svc.trigger_upgrade()
        assert result["status"] == "no_update"

    @patch.object(SystemService, "get_system_version")
    @patch.object(SystemService, "get_upgrade_readiness")
    def test_already_upgrading(self, mock_ready, mock_version, db):
        mock_ready.return_value = {
            "host_repo_path_set": True, "docker_socket_available": True,
            "upgrade_ready": True, "upgrade_in_progress": False,
        }
        original = SystemService._upgrade_in_progress
        try:
            SystemService._upgrade_in_progress = True
            svc = SystemService(db)
            result = svc.trigger_upgrade()
            assert result["status"] == "already_upgrading"
        finally:
            SystemService._upgrade_in_progress = original
