"""
Integration tests for system routes — /api/system.

Covers: health (public), version, database stats, defaults, RBAC,
errors, database cleanup/vacuum, workspace management, container
management, defaults CRUD, and upgrade endpoints.
Uses FastAPI TestClient with real SQLite DB.
"""

from unittest.mock import MagicMock, patch

import pytest


class TestSystemHealth:
    """GET /api/system/health — public endpoint."""

    def test_health_no_auth(self, client, db):
        """Health endpoint works without authentication."""
        mock_health = {
            "services": {
                "backend": {"status": "healthy", "response_time_ms": 1.0},
                "database": {"status": "healthy", "response_time_ms": 2.0},
            },
            "timestamp": "2026-02-24T00:00:00",
        }
        with patch("routes.system.SystemService") as MockService:
            MockService.return_value.get_health.return_value = mock_health
            response = client.get("/api/system/health")

        assert response.status_code == 200
        data = response.json()
        assert "services" in data
        assert data["services"]["backend"]["status"] == "healthy"


class TestSystemAdmin:
    """Admin-only system endpoints."""

    def test_version_requires_auth(self, client):
        """GET /api/system/version requires admin auth."""
        response = client.get("/api/system/version")
        assert response.status_code == 401

    def test_version_viewer_denied(self, client, viewer_headers, all_test_users):
        """Viewer cannot access admin endpoints."""
        response = client.get("/api/system/version", headers=viewer_headers)
        assert response.status_code == 403

    def test_version_admin_allowed(self, client, admin_headers, sample_user):
        """Admin can access version endpoint."""
        with patch("routes.system.SystemService") as MockService:
            MockService.return_value.get_system_version.return_value = {
                "version": "2.10.49",
                "build": "test",
            }
            MockService.return_value.get_upgrade_readiness.return_value = {
                "ready": True,
            }
            response = client.get("/api/system/version", headers=admin_headers)

        assert response.status_code == 200

    def test_database_stats(self, client, admin_headers, sample_user):
        """Database stats endpoint returns statistics."""
        with patch("routes.system.SystemService") as MockService:
            MockService.return_value.get_database_stats.return_value = {
                "tables": 14,
                "total_rows": 100,
            }
            response = client.get("/api/system/database/stats", headers=admin_headers)

        assert response.status_code == 200

    def test_defaults_get(self, client, admin_headers, sample_user):
        """Get system defaults."""
        with patch("services.defaults_service.get_all_defaults") as mock_get:
            mock_get.return_value = {"aws_region": "us-east-1"}
            response = client.get("/api/system/defaults", headers=admin_headers)

        assert response.status_code == 200

    def test_queue_metrics(self, client, admin_headers, sample_user):
        """Queue metrics endpoint returns Celery stats."""
        with patch("routes.system.SystemService") as MockService:
            MockService.return_value.get_queue_metrics.return_value = {
                "active": 0,
                "reserved": 0,
                "scheduled": 0,
            }
            response = client.get("/api/system/queue-metrics", headers=admin_headers)

        assert response.status_code == 200

    def test_performance_metrics(self, client, admin_headers, sample_user):
        """Performance metrics endpoint returns stats."""
        with patch("routes.system.SystemService") as MockService:
            MockService.return_value.get_performance_metrics.return_value = {
                "avg_response_ms": 50,
                "p95_response_ms": 200,
            }
            response = client.get("/api/system/performance", headers=admin_headers)

        assert response.status_code == 200

    def test_bnk_consumption_requires_auth(self, client):
        """GET /api/system/bnk-consumption requires admin auth."""
        response = client.get("/api/system/bnk-consumption")
        assert response.status_code == 401

    def test_bnk_consumption_viewer_denied(self, client, viewer_headers, all_test_users):
        """Viewer cannot access BNK consumption endpoint."""
        response = client.get("/api/system/bnk-consumption", headers=viewer_headers)
        assert response.status_code == 403

    def test_bnk_consumption_admin_allowed(self, client, admin_headers, sample_user):
        """Admin can retrieve BNK consumption."""
        mock_consumption = {
            "timestamp": "2026-09-01T12:00:00Z",
            "fleet_summary": {
                "total_clusters": 2,
                "reachable_clusters": 2,
                "bnk_installed_clusters": 1,
                "total_bnk_pods": 5,
                "control_plane_pods": 2,
                "data_plane_pods": 3,
                "total_cpu_millicores": 1700,
                "total_memory_bytes": 3400000000,
                "dpf_detected_clusters": 0,
                "dpu_count": 0,
            },
            "clusters": [],
        }
        with patch("routes.system.SystemService") as MockService:
            MockService.return_value.get_bnk_consumption.return_value = mock_consumption
            response = client.get("/api/system/bnk-consumption", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["fleet_summary"]["total_clusters"] == 2
        assert data["timestamp"] == "2026-09-01T12:00:00Z"


# ============================================================================
# Recent Errors
# ============================================================================


class TestRecentErrors:
    """GET /api/system/errors."""

    def test_requires_auth(self, client):
        """Errors endpoint requires admin auth."""
        response = client.get("/api/system/errors")
        assert response.status_code == 401

    def test_viewer_denied(self, client, viewer_headers, all_test_users):
        """Viewer cannot access errors endpoint."""
        response = client.get("/api/system/errors", headers=viewer_headers)
        assert response.status_code == 403

    def test_returns_errors(self, client, admin_headers, sample_user):
        """Admin can retrieve recent errors."""
        mock_errors = {
            "errors": [
                {"id": 1, "message": "Task failed", "timestamp": "2026-01-01T00:00:00"},
            ],
            "count": 1,
        }
        with patch("routes.system.SystemService") as MockService:
            MockService.return_value.get_recent_errors.return_value = mock_errors
            response = client.get("/api/system/errors", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1

    def test_respects_limit_param(self, client, admin_headers, sample_user):
        """Limit query parameter is forwarded to service."""
        with patch("routes.system.SystemService") as MockService:
            MockService.return_value.get_recent_errors.return_value = {"errors": [], "count": 0}
            response = client.get("/api/system/errors?limit=5", headers=admin_headers)

        assert response.status_code == 200
        MockService.return_value.get_recent_errors.assert_called_once_with(limit=5)


# ============================================================================
# Database Cleanup & Vacuum
# ============================================================================


class TestDatabaseCleanup:
    """POST /api/system/database/cleanup."""

    def test_requires_auth(self, client):
        response = client.post("/api/system/database/cleanup", json={"cleanup_type": "tasks"})
        assert response.status_code == 401

    def test_viewer_denied(self, client, viewer_headers, all_test_users):
        response = client.post(
            "/api/system/database/cleanup",
            json={"cleanup_type": "tasks"},
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_cleanup_success(self, client, admin_headers, sample_user):
        """Admin can trigger database cleanup."""
        mock_result = {"deleted": 42, "cleanup_type": "tasks"}
        with patch("routes.system.SystemService") as MockService:
            MockService.return_value.cleanup_database.return_value = mock_result
            response = client.post(
                "/api/system/database/cleanup",
                json={"cleanup_type": "tasks", "older_than_days": 60},
                headers=admin_headers,
            )

        assert response.status_code == 200
        MockService.return_value.cleanup_database.assert_called_once_with("tasks", 60)

    def test_cleanup_default_days(self, client, admin_headers, sample_user):
        """Default older_than_days is 30."""
        with patch("routes.system.SystemService") as MockService:
            MockService.return_value.cleanup_database.return_value = {"deleted": 0}
            response = client.post(
                "/api/system/database/cleanup",
                json={"cleanup_type": "logs"},
                headers=admin_headers,
            )

        assert response.status_code == 200
        MockService.return_value.cleanup_database.assert_called_once_with("logs", 30)


class TestDatabaseVacuum:
    """POST /api/system/database/vacuum."""

    def test_requires_auth(self, client):
        response = client.post("/api/system/database/vacuum")
        assert response.status_code == 401

    def test_viewer_denied(self, client, viewer_headers, all_test_users):
        response = client.post("/api/system/database/vacuum", headers=viewer_headers)
        assert response.status_code == 403

    def test_vacuum_success(self, client, admin_headers, sample_user):
        """Admin can trigger database vacuum."""
        mock_result = {"status": "completed", "duration_ms": 120}
        with patch("routes.system.SystemService") as MockService:
            MockService.return_value.vacuum_database.return_value = mock_result
            response = client.post("/api/system/database/vacuum", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"


# ============================================================================
# Workspace Management
# ============================================================================


class TestWorkspaceStats:
    """GET /api/system/workspaces/stats."""

    def test_requires_auth(self, client):
        response = client.get("/api/system/workspaces/stats")
        assert response.status_code == 401

    def test_returns_stats(self, client, admin_headers, sample_user):
        """Admin can get workspace statistics."""
        mock_stats = {"total": 5, "active": 3, "orphaned": 2, "total_size_mb": 150}
        with patch("services.workspace_manager.WorkspaceManager") as MockWM:
            MockWM.return_value.get_workspace_stats.return_value = mock_stats
            response = client.get("/api/system/workspaces/stats", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["total"] == 5


class TestOrphanedWorkspaces:
    """GET /api/system/workspaces/orphaned."""

    def test_requires_auth(self, client):
        response = client.get("/api/system/workspaces/orphaned")
        assert response.status_code == 401

    def test_returns_orphaned_list(self, client, admin_headers, sample_user):
        """Admin can list orphaned workspaces."""
        mock_orphaned = ["/data/workspaces/old-1", "/data/workspaces/old-2"]
        with patch("services.workspace_manager.WorkspaceManager") as MockWM:
            MockWM.return_value.get_orphaned_workspaces.return_value = mock_orphaned
            response = client.get("/api/system/workspaces/orphaned", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["count"] == 2
        assert len(data["orphaned"]) == 2


class TestWorkspaceCleanup:
    """POST /api/system/workspaces/cleanup."""

    def test_requires_auth(self, client):
        response = client.post("/api/system/workspaces/cleanup")
        assert response.status_code == 401

    def test_cleanup_orphaned(self, client, admin_headers, sample_user):
        """Admin can clean up orphaned workspaces."""
        mock_orphaned = ["/data/workspaces/old-1"]
        with patch("services.workspace_manager.WorkspaceManager") as MockWM:
            MockWM.return_value.get_orphaned_workspaces.return_value = mock_orphaned
            MockWM.return_value.cleanup_orphaned_workspaces.return_value = 1
            response = client.post("/api/system/workspaces/cleanup", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["deleted_count"] == 1
        assert data["deleted_paths"] == mock_orphaned


# ============================================================================
# Workspace Lock Management
# ============================================================================


class TestWorkspaceLocks:
    """GET /api/system/workspaces/locks."""

    def test_requires_auth(self, client):
        response = client.get("/api/system/workspaces/locks")
        assert response.status_code == 401

    def test_list_locks(self, client, admin_headers, sample_user):
        """Admin can list all currently-held module locks."""
        mock_locks = [
            {
                "project_id": 1,
                "module_id": 2,
                "holding_task_id": 123,
                "heartbeat_at": "2026-01-01T00:00:00",
                "fence_token": 7,
            },
        ]
        with patch("services.module_lock.ModuleLockService") as mock_cls:
            mock_cls.return_value.list_held.return_value = mock_locks
            response = client.get("/api/system/workspaces/locks", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["count"] == 1
        assert data["locks"][0]["holding_task_id"] == 123


class TestCheckWorkspaceLock:
    """GET /api/system/workspaces/locks/{project_id}/{module_id}."""

    def test_requires_auth(self, client):
        response = client.get("/api/system/workspaces/locks/1/2")
        assert response.status_code == 401

    def test_check_locked(self, client, admin_headers, sample_user):
        """Check a locked module returns holder info."""
        holder = {
            "module_id": 2, "project_id": 1, "holding_task_id": 456,
            "heartbeat_at": "2026-01-01T00:00:00", "fence_token": 3,
        }
        with patch("services.module_lock.ModuleLockService") as mock_cls:
            mock_svc = mock_cls.return_value
            mock_svc.get_holder.return_value = holder
            mock_svc.is_held.return_value = True
            response = client.get("/api/system/workspaces/locks/1/2", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["is_locked"] is True
        assert data["holder"]["holding_task_id"] == 456

    def test_check_unlocked(self, client, admin_headers, sample_user):
        """Check an unlocked module."""
        with patch("services.module_lock.ModuleLockService") as mock_cls:
            mock_svc = mock_cls.return_value
            mock_svc.get_holder.return_value = None
            mock_svc.is_held.return_value = False
            response = client.get("/api/system/workspaces/locks/1/2", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["is_locked"] is False
        assert data["holder"] is None


class TestForceReleaseWorkspaceLock:
    """DELETE /api/system/workspaces/locks/{project_id}/{module_id}."""

    def test_requires_auth(self, client):
        response = client.delete("/api/system/workspaces/locks/1/2")
        assert response.status_code == 401

    def test_release_existing_lock(self, client, admin_headers, sample_user):
        """Admin can force-release an existing lock."""
        holder = {
            "module_id": 2, "project_id": 1, "holding_task_id": 789,
            "heartbeat_at": "2026-01-01T00:00:00", "fence_token": 4,
        }
        with patch("services.module_lock.ModuleLockService") as mock_cls:
            mock_svc = mock_cls.return_value
            mock_svc.get_holder.return_value = holder
            mock_svc.force_release.return_value = True
            response = client.delete("/api/system/workspaces/locks/1/2", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["released"] is True
        assert data["previous_holder"]["holding_task_id"] == 789

    def test_release_no_lock(self, client, admin_headers, sample_user):
        """Force-release when no lock exists returns released=False."""
        with patch("services.module_lock.ModuleLockService") as mock_cls:
            mock_cls.return_value.get_holder.return_value = None
            response = client.delete("/api/system/workspaces/locks/1/2", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["released"] is False


# ============================================================================
# Container Management
# ============================================================================


class TestContainerRestart:
    """POST /api/system/containers/restart."""

    def test_requires_auth(self, client):
        response = client.post("/api/system/containers/restart", json={})
        assert response.status_code == 401

    def test_viewer_denied(self, client, viewer_headers, all_test_users):
        response = client.post(
            "/api/system/containers/restart", json={}, headers=viewer_headers,
        )
        assert response.status_code == 403

    @patch("subprocess.run")
    def test_restart_specific_services(self, mock_run, client, admin_headers, sample_user):
        """Admin can restart specific services."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        response = client.post(
            "/api/system/containers/restart",
            json={"services": ["frontend"]},
            headers=admin_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["service"] == "frontend"
        assert data["results"][0]["status"] == "restarted"

    @patch("subprocess.run")
    def test_restart_defaults_to_all(self, mock_run, client, admin_headers, sample_user):
        """No services specified restarts the default set."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        response = client.post(
            "/api/system/containers/restart",
            json={},
            headers=admin_headers,
        )

        assert response.status_code == 200
        data = response.json()
        # Default: backend, celery-worker, celery-beat, frontend
        assert len(data["results"]) == 4

    def test_rejects_invalid_service(self, client, admin_headers, sample_user):
        """Invalid service names are rejected."""
        response = client.post(
            "/api/system/containers/restart",
            json={"services": ["hacker-service"]},
            headers=admin_headers,
        )
        assert response.status_code == 400

    def test_rejects_restricted_services(self, client, admin_headers, sample_user):
        """postgres and redis cannot be restarted via API."""
        response = client.post(
            "/api/system/containers/restart",
            json={"services": ["postgres"]},
            headers=admin_headers,
        )
        assert response.status_code == 400


class TestContainerStatus:
    """GET /api/system/containers/status."""

    def test_requires_auth(self, client):
        response = client.get("/api/system/containers/status")
        assert response.status_code == 401

    @patch("subprocess.run")
    def test_returns_container_list(self, mock_run, client, admin_headers, sample_user):
        """Admin can get container status."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="bnk-forge-v2-backend-1|Up 2 hours|running\nbnk-forge-v2-frontend-1|Up 2 hours|running\n",
        )
        response = client.get("/api/system/containers/status", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["containers"]) == 2
        assert data["containers"][0]["state"] == "running"


# ============================================================================
# Defaults Status & Updates
# ============================================================================


class TestDefaultsStatus:
    """GET /api/system/defaults/status."""

    def test_requires_auth(self, client):
        response = client.get("/api/system/defaults/status")
        assert response.status_code == 401

    def test_returns_status(self, client, admin_headers, sample_user):
        """Admin can check defaults configuration status."""
        mock_status = {"all_configured": True, "missing": []}
        with patch("services.defaults_service.check_required_configured") as mock_check:
            mock_check.return_value = mock_status
            response = client.get("/api/system/defaults/status", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["all_configured"] is True


class TestUpdateDefaults:
    """PUT /api/system/defaults."""

    def test_requires_auth(self, client):
        response = client.put("/api/system/defaults", json={"updates": {"key": "val"}})
        assert response.status_code == 401

    def test_update_batch(self, client, admin_headers, sample_user):
        """Admin can update multiple defaults at once."""
        with patch("services.defaults_service.set_defaults_batch") as mock_batch:
            mock_batch.return_value = {"aws_region": True, "default_env": True}
            response = client.put(
                "/api/system/defaults",
                json={"updates": {"aws_region": "us-west-2", "default_env": "staging"}},
                headers=admin_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"

    def test_update_batch_partial_failure(self, client, admin_headers, sample_user):
        """Partial failure returns status='partial' with failed keys."""
        with patch("services.defaults_service.set_defaults_batch") as mock_batch:
            mock_batch.return_value = {"aws_region": True, "bad_key": False}
            response = client.put(
                "/api/system/defaults",
                json={"updates": {"aws_region": "us-west-2", "bad_key": "x"}},
                headers=admin_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "partial"
        assert "bad_key" in data["failed"]


class TestUpdateSingleDefault:
    """PUT /api/system/defaults/{key}."""

    def test_requires_auth(self, client):
        response = client.put("/api/system/defaults/aws_region?value=us-east-1")
        assert response.status_code == 401

    def test_update_single_key(self, client, admin_headers, sample_user):
        """Admin can update a single default."""
        with patch("services.defaults_service.set_default") as mock_set:
            mock_set.return_value = True
            response = client.put(
                "/api/system/defaults/aws_region?value=eu-west-1",
                headers=admin_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["key"] == "aws_region"

    def test_invalid_key_returns_400(self, client, admin_headers, sample_user):
        """Invalid setting key returns 400."""
        with patch("services.defaults_service.set_default") as mock_set:
            mock_set.return_value = False
            response = client.put(
                "/api/system/defaults/nonexistent_key?value=whatever",
                headers=admin_headers,
            )

        assert response.status_code == 400


# ============================================================================
# Upgrade Endpoints
# ============================================================================


class TestUpgradeStatus:
    """GET /api/system/upgrade/status."""

    def test_requires_auth(self, client):
        response = client.get("/api/system/upgrade/status")
        assert response.status_code == 401

    def test_no_upgrade_state(self, client, admin_headers, sample_user):
        """Returns status='none' when no upgrade has been run."""
        with patch("routes.system.SystemService") as MockService:
            MockService.return_value.get_upgrade_state.return_value = None
            response = client.get("/api/system/upgrade/status", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "none"

    def test_completed_upgrade(self, client, admin_headers, sample_user):
        """Returns completed upgrade state from DB."""
        mock_state = {
            "status": "completed",
            "old_version": "2.10.70",
            "new_version": "2.10.75",
            "started_at": "2026-03-01T00:00:00",
            "completed_at": "2026-03-01T00:05:00",
            "current_phase": "complete",
            "phase_label": "Upgrade completed",
            "pre_upgrade_commit": "abc123",
            "log": ["Pulling latest...", "Build complete"],
        }
        with patch("routes.system.SystemService") as MockService:
            MockService.return_value.get_upgrade_state.return_value = mock_state
            response = client.get("/api/system/upgrade/status", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["old_version"] == "2.10.70"

    def test_interrupted_upgrade_detection(self, client, admin_headers, sample_user):
        """Detects interrupted upgrade when DB says in_progress but no class-level flag."""
        mock_state = {
            "status": "in_progress",
            "current_phase": "build",
            "phase_label": "Building containers...",
        }
        with patch("routes.system.SystemService") as MockService:
            MockService.return_value.get_upgrade_state.return_value = mock_state
            MockService.is_upgrade_in_progress.return_value = False
            response = client.get("/api/system/upgrade/status", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "interrupted"


class TestUpgradeVerify:
    """GET /api/system/upgrade/verify."""

    def test_requires_auth(self, client):
        response = client.get("/api/system/upgrade/verify")
        assert response.status_code == 401

    def test_verify_post_upgrade(self, client, admin_headers, sample_user):
        """Admin can run post-upgrade verification."""
        mock_result = {
            "healthy": True,
            "services": {"backend": "ok", "database": "ok"},
            "version_changed": True,
        }
        with patch("routes.system.SystemService") as MockService:
            MockService.return_value.verify_post_upgrade.return_value = mock_result
            response = client.get("/api/system/upgrade/verify", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["healthy"] is True


class TestTriggerUpgrade:
    """POST /api/system/upgrade."""

    def test_requires_auth(self, client):
        response = client.post("/api/system/upgrade")
        assert response.status_code == 401

    def test_viewer_denied(self, client, viewer_headers, all_test_users):
        response = client.post("/api/system/upgrade", headers=viewer_headers)
        assert response.status_code == 403

    def test_trigger_upgrade(self, client, admin_headers, sample_user):
        """Admin can trigger a system upgrade."""
        mock_result = {"status": "started", "message": "Upgrade initiated"}
        with patch("routes.system.SystemService") as MockService:
            MockService.return_value.trigger_upgrade.return_value = mock_result
            response = client.post("/api/system/upgrade", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"
