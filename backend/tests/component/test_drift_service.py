"""
BC-022: Component tests for DriftService — drift settings, checks, summaries.

Uses real SQLite DB. Mocks Celery task dispatch and cache.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from core.errors import BadRequestError, NotFoundError
from models import DriftCheck, DriftSettings, Project, ProjectModule
from services.drift_service import DriftService

# ── Helpers ──────────────────────────────────────────────────────────

def _make_project(db, name="Drift Project"):
    project = Project(
        name=name, project_type="kubernetes", cloud_provider="on-prem",
        environment="dev", backend_type="local", color="#000", icon="cloud",
        is_active=True,
    )
    db.add(project)
    db.flush()
    return project


def _make_module(db, project, name="test-module", status="applied"):
    from models import ModuleLibrary
    lib = ModuleLibrary(
        name=name, category="kubernetes", path=f"modules/{name}",
        provider="test", description="Test module", git_source="https://github.com/test/test.git",
        is_official=True, is_active=True,
    )
    db.add(lib)
    db.flush()
    pm = ProjectModule(
        project_id=project.id, module_library_id=lib.id,
        path_in_project=f"modules/{name}", status=status,
        variables={}, enabled=True,
    )
    db.add(pm)
    db.flush()
    return pm


# ── Settings ─────────────────────────────────────────────────────────

class TestDriftSettings:
    def test_get_settings_creates_defaults(self, db):
        project = _make_project(db)
        svc = DriftService(db)
        settings = svc.get_settings(project.id)

        assert settings.project_id == project.id
        assert settings.enabled is False
        assert settings.schedule_type == "interval"

    def test_get_settings_returns_existing(self, db):
        project = _make_project(db)
        svc = DriftService(db)
        svc.get_settings(project.id)  # Create defaults
        settings = svc.get_settings(project.id)  # Retrieve existing
        assert settings.enabled is False

    def test_get_settings_nonexistent_project_raises(self, db):
        svc = DriftService(db)
        with pytest.raises(NotFoundError):
            svc.get_settings(99999)

    def test_update_settings(self, db):
        project = _make_project(db)
        svc = DriftService(db)
        settings = svc.update_settings(project.id, {
            "enabled": True,
            "schedule_type": "cron",
            "schedule_value": "0 * * * *",
        })
        assert settings.enabled is True
        assert settings.schedule_type == "cron"

    def test_enable_drift(self, db):
        project = _make_project(db)
        svc = DriftService(db)
        settings = svc.enable_drift(project.id)
        assert settings.enabled is True

    def test_disable_drift(self, db):
        project = _make_project(db)
        svc = DriftService(db)
        svc.enable_drift(project.id)
        settings = svc.disable_drift(project.id)
        assert settings.enabled is False

    def test_enable_drift_creates_settings_if_missing(self, db):
        project = _make_project(db)
        svc = DriftService(db)
        settings = svc.enable_drift(project.id)
        assert settings.project_id == project.id
        assert settings.enabled is True


# ── Trigger Checks ───────────────────────────────────────────────────

class TestTriggerChecks:
    @patch("services.drift_service.DriftService.trigger_project_check.__wrapped__" if hasattr(DriftService.trigger_project_check, "__wrapped__") else "tasks.drift_tasks.run_drift_check")
    def test_trigger_project_check(self, mock_task, db):
        mock_task.delay = MagicMock()
        project = _make_project(db)
        mod = _make_module(db, project, status="applied")

        svc = DriftService(db)

        with patch("tasks.drift_tasks.run_drift_check") as mock_drift:
            mock_drift.delay = MagicMock()
            result = svc.trigger_project_check(project.id)

        assert "drift_checks" in result
        assert len(result["drift_checks"]) == 1

    def test_trigger_project_check_no_modules_raises(self, db):
        project = _make_project(db)
        svc = DriftService(db)
        with pytest.raises(BadRequestError, match="No deployed modules"):
            svc.trigger_project_check(project.id)

    @patch("tasks.drift_tasks.run_drift_check")
    def test_trigger_module_check(self, mock_task, db):
        mock_task.delay = MagicMock()
        project = _make_project(db)
        mod = _make_module(db, project, status="applied")

        svc = DriftService(db)
        result = svc.trigger_module_check(mod.id)
        assert result["module_id"] == mod.id

    def test_trigger_module_check_invalid_status(self, db):
        project = _make_project(db)
        mod = _make_module(db, project, status="failed")

        svc = DriftService(db)
        with pytest.raises(BadRequestError, match="Cannot check drift"):
            svc.trigger_module_check(mod.id)

    def test_trigger_module_check_not_found(self, db):
        svc = DriftService(db)
        with pytest.raises(NotFoundError):
            svc.trigger_module_check(99999)


# ── Summaries ────────────────────────────────────────────────────────

class TestDriftSummaries:
    def test_global_summary_empty(self, db):
        svc = DriftService(db)
        result = svc.get_global_summary()
        assert result["total_checks"] >= 0

    def test_project_summary(self, db):
        project = _make_project(db)
        svc = DriftService(db)
        result = svc.get_project_summary(project.id)
        assert "total_checks" in result
        assert "drift_detected_count" in result

    @patch("services.drift_service.cache")
    def test_get_stats_cached(self, mock_cache, db):
        mock_cache.get.return_value = {"enabled_projects": 5}
        svc = DriftService(db)
        result = svc.get_stats()
        assert result["enabled_projects"] == 5

    @patch("services.drift_service.cache")
    def test_get_stats_uncached(self, mock_cache, db):
        mock_cache.get.return_value = None
        mock_cache.set = MagicMock()
        project = _make_project(db)
        svc = DriftService(db)
        result = svc.get_stats()
        assert "enabled_projects" in result
        assert "recent_checks_7d" in result
