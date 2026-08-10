"""
BC-C3: Component tests for drift detection Celery tasks.

Tests drift check dispatch, plan parsing, scheduled checks.
Mocks Celery, DB context, and OpenTofu runtime.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from models import DriftCheck, DriftSettings, ModuleLibrary, Project, ProjectModule

_MOD = "tasks.drift_tasks"


# ── Helpers ──────────────────────────────────────────────────────────

def _now():
    return datetime.utcnow()


def _make_project(db, name="Drift Task Project"):
    project = Project(
        name=name, project_type="kubernetes", cloud_provider="on-prem",
        environment="dev", backend_type="local", color="#000", icon="cloud",
        is_active=True, module_count=1, deployed_count=1,
    )
    db.add(project)
    db.flush()
    return project


def _make_library(db, name="drift-mod"):
    lib = ModuleLibrary(
        name=name, category="networking", path=f"modules/{name}",
        provider="test", description="Test",
        git_source="https://github.com/test/test.git",
        is_official=True, is_active=True,
    )
    db.add(lib)
    db.flush()
    return lib


def _make_module(db, project, library, status="applied"):
    pm = ProjectModule(
        project_id=project.id, module_library_id=library.id,
        path_in_project=f"modules/{library.name}",
        status=status, variables={}, enabled=True,
    )
    db.add(pm)
    db.flush()
    return pm


# ── parse_drift_from_plan ────────────────────────────────────────────

class TestParseDriftFromPlan:
    """Unit tests for parse_drift_from_plan helper."""

    def test_no_drift_exit_code_0(self):
        from tasks.drift_tasks import parse_drift_from_plan
        result = parse_drift_from_plan(0, "No changes", "")
        assert result["drift_detected"] is False

    def test_drift_detected_exit_code_2(self):
        from tasks.drift_tasks import parse_drift_from_plan
        stdout = "Plan: 1 to add, 2 to change, 0 to destroy"
        result = parse_drift_from_plan(2, stdout, "")
        assert result["drift_detected"] is True
        assert result["resource_changes"]["add"] == 1
        assert result["resource_changes"]["change"] == 2
        assert result["resource_changes"]["destroy"] == 0

    def test_plan_failure_exit_code_1(self):
        from tasks.drift_tasks import parse_drift_from_plan
        result = parse_drift_from_plan(1, "Error", "")
        assert result["drift_detected"] is False
        assert "failed" in result["summary"].lower()

    def test_resource_parsing(self):
        from tasks.drift_tasks import parse_drift_from_plan
        stdout = (
            "Plan: 0 to add, 1 to change, 1 to destroy\n"
            "  # aws_instance.example will be updated\n"
            "  # aws_s3_bucket.data will be destroyed\n"
        )
        result = parse_drift_from_plan(2, stdout, "")
        assert len(result["changed_resources"]) == 2


# ── run_drift_check ──────────────────────────────────────────────────

class TestRunDriftCheck:
    """tasks.drift_tasks.run_drift_check."""

    @patch(f"{_MOD}._fire_drift_alert")
    @patch(f"{_MOD}._is_k8s_engine_module", return_value=False)
    @patch(f"{_MOD}._run_opentofu_drift_check")
    @patch(f"{_MOD}.get_db_context")
    @patch(f"{_MOD}.datetime")
    def test_drift_check_no_drift(self, mock_dt, mock_db_ctx, mock_run_tofu,
                                   mock_is_k8s, mock_fire, db):
        """Drift check with no drift detected completes successfully."""
        naive_now = _now()
        mock_dt.now.return_value = naive_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        project = _make_project(db)
        lib = _make_library(db)
        module = _make_module(db, project, lib)
        drift_check = DriftCheck(
            project_id=project.id, module_id=module.id,
            status="scheduled",
        )
        db.add(drift_check)
        db.commit()

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

        mock_run_tofu.return_value = {
            "drift_detected": False,
            "summary": "No drift",
            "resource_changes": {"add": 0, "change": 0, "destroy": 0},
        }

        from tasks.drift_tasks import run_drift_check
        result = run_drift_check(drift_check.id, module.id)

        assert result["success"] is True
        assert result["drift_detected"] is False
        mock_fire.assert_not_called()


# ── schedule_drift_checks ────────────────────────────────────────────

class TestScheduleDriftChecks:
    """tasks.drift_tasks.schedule_drift_checks."""

    @patch(f"{_MOD}.get_db_context")
    def test_no_enabled_projects(self, mock_db_ctx, db):
        """Returns 0 scheduled when no projects have drift enabled."""
        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

        from tasks.drift_tasks import schedule_drift_checks
        result = schedule_drift_checks()

        assert result["scheduled"] == 0
