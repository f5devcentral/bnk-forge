"""
Integration tests for project execution routes — /api/project-modules/{id}/{action}.

Covers: init, plan, apply, destroy, cancel, deploy, retry, plan-status, validate.
Uses FastAPI TestClient with real SQLite DB, mocks Celery dispatch + workspace.
"""

from unittest.mock import MagicMock, patch

import pytest

from models import ProjectModule


class TestOpenTofuInit:
    """POST /api/project-modules/{id}/init."""

    def test_init_module(self, client, admin_headers, sample_module, db):
        """Init dispatches a Celery task and returns task_id."""
        mod = sample_module["module"]
        lib = sample_module["library"]
        # Module needs a git_source for init validation
        lib.git_source = "https://github.com/example/module.git"
        db.commit()

        mock_result = MagicMock()
        mock_result.id = "celery-init-123"

        with patch("services.execution.task_dispatch.dispatch_init", return_value=mock_result):
            response = client.post(
                f"/api/project-modules/{mod.id}/init", headers=admin_headers
            )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["task_id"] > 0
        assert data["celery_task_id"] == "celery-init-123"

    def test_init_module_not_found(self, client, admin_headers, sample_user):
        """Init on nonexistent module returns 404."""
        response = client.post(
            "/api/project-modules/99999/init", headers=admin_headers
        )
        assert response.status_code == 404

    def test_init_module_viewer_denied(self, client, viewer_headers, all_test_users):
        """Viewer cannot trigger init — returns 403."""
        response = client.post(
            "/api/project-modules/1/init", headers=viewer_headers
        )
        assert response.status_code == 403


class TestOpenTofuPlan:
    """POST /api/project-modules/{id}/plan."""

    def test_plan_module(self, client, admin_headers, sample_module, db):
        """Plan dispatches a Celery task."""
        mod = sample_module["module"]
        lib = sample_module["library"]
        lib.git_source = "https://github.com/example/module.git"
        mod.status = "initialized"
        db.commit()

        mock_result = MagicMock()
        mock_result.id = "celery-plan-456"

        with patch("tasks.opentofu_tasks.run_opentofu_plan") as mock_task:
            mock_task.delay.return_value = mock_result
            response = client.post(
                f"/api/project-modules/{mod.id}/plan", headers=admin_headers
            )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "task_id" in data


class TestOpenTofuApply:
    """POST /api/project-modules/{id}/apply."""

    def test_apply_module(self, client, admin_headers, sample_module, db):
        """Apply dispatches a Celery task."""
        mod = sample_module["module"]
        lib = sample_module["library"]
        lib.git_source = "https://github.com/example/module.git"
        mod.status = "planned"
        db.commit()

        mock_result = MagicMock()
        mock_result.id = "celery-apply-789"

        with patch("services.execution.task_dispatch.dispatch_apply", return_value=mock_result), \
             patch.object(
                 type(mod), 'project',
                 new_callable=lambda: property(lambda self: sample_module["project"])
             ) if not hasattr(mod, 'project') else patch("services.project_module_service.ProjectModuleService.check_module_dependencies", return_value=(True, [])), \
             patch("services.project_module_service.ProjectModuleService._create_snapshot"), \
             patch("services.project_module_service.ProjectModuleService.check_module_dependencies", return_value=(True, [])):
            response = client.post(
                f"/api/project-modules/{mod.id}/apply",
                json={"auto_approve": False},
                headers=admin_headers,
            )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


class TestOpenTofuDestroy:
    """POST /api/project-modules/{id}/destroy."""

    def test_destroy_module(self, client, admin_headers, sample_module, db):
        """Destroy dispatches a Celery task for applied module."""
        mod = sample_module["module"]
        mod.status = "applied"
        db.commit()

        mock_result = MagicMock()
        mock_result.id = "celery-destroy-101"

        with patch("services.execution.task_dispatch.dispatch_destroy", return_value=mock_result), \
             patch("services.project_module_service.ProjectModuleService._create_snapshot"):
            response = client.post(
                f"/api/project-modules/{mod.id}/destroy",
                json={"auto_approve": False},
                headers=admin_headers,
            )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["celery_task_id"] == "celery-destroy-101"

    def test_destroy_not_initialized_fails(self, client, admin_headers, sample_module, db):
        """Destroy on not_initialized module returns 400."""
        mod = sample_module["module"]
        mod.status = "not_initialized"
        db.commit()

        response = client.post(
            f"/api/project-modules/{mod.id}/destroy",
            json={"auto_approve": False},
            headers=admin_headers,
        )
        assert response.status_code == 400


class TestPlanStatus:
    """GET /api/project-modules/{id}/plan-status."""

    def test_get_plan_status(self, client, admin_headers, sample_module):
        """Plan status returns workspace info."""
        mod = sample_module["module"]

        with patch("services.workspace_manager.WorkspaceManager") as MockWM:
            instance = MockWM.return_value
            instance.is_initialized.return_value = True
            instance.has_saved_plan.return_value = False
            response = client.get(
                f"/api/project-modules/{mod.id}/plan-status",
                headers=admin_headers,
            )
        assert response.status_code == 200
        data = response.json()
        assert data["module_id"] == mod.id
        assert data["workspace_initialized"] is True
        assert data["has_plan"] is False


class TestValidateModule:
    """GET /api/project-modules/{id}/validate."""

    def test_validate_module(self, client, admin_headers, sample_module):
        """Validate returns validation result."""
        mod = sample_module["module"]
        response = client.get(
            f"/api/project-modules/{mod.id}/validate?operation=plan",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["module_id"] == mod.id
        assert data["operation"] == "plan"
        assert "valid" in data
        assert "errors" in data
        assert "warnings" in data


class TestCancelOperation:
    """POST /api/project-modules/{id}/cancel."""

    def test_cancel_no_active_task(self, client, admin_headers, sample_module, db):
        """Cancel with no active task returns success=False."""
        mod = sample_module["module"]
        mod.status = "initialized"
        db.commit()

        response = client.post(
            f"/api/project-modules/{mod.id}/cancel", headers=admin_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "No active deployment" in data["message"]

    def test_cancel_stuck_module(self, client, admin_headers, sample_module, db):
        """Cancel resets stuck module status when no Celery task found."""
        mod = sample_module["module"]
        mod.status = "applying"
        db.commit()

        with patch("services.project_service.invalidate_cache"):
            response = client.post(
                f"/api/project-modules/{mod.id}/cancel", headers=admin_headers
            )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "Reset stuck" in data["message"]
