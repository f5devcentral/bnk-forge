"""
Integration tests for project orchestration routes — /api/projects.

Covers: execution plan, deploy-all, destroy-all, parallel execution status/list,
cross-project modules query, RBAC enforcement.
Uses FastAPI TestClient with real SQLite DB.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

# ParallelExecution removed (D-001 Phase 3 S3b — table dropped in v2_121)


class TestGetExecutionPlan:
    """GET /api/projects/{project_id}/execution-plan."""

    def test_get_execution_plan(
        self, client, admin_headers, sample_user, sample_project
    ):
        """Returns execution plan from ParallelExecutionService."""
        mock_plan = {
            "layers": [
                {"layer": 0, "modules": [{"id": 1, "name": "vpc"}]},
                {"layer": 1, "modules": [{"id": 2, "name": "eks"}]},
            ],
            "total_estimated_time_sequential": 10,
            "total_estimated_time_parallel": 5,
            "time_savings_percent": 50.0,
            "parallelization_factor": 2.0,
        }

        with patch(
            "services.parallel_execution_service.ParallelExecutionService.get_execution_plan",
            return_value=mock_plan,
        ):
            response = client.get(
                f"/api/projects/{sample_project.id}/execution-plan",
                headers=admin_headers,
            )
        assert response.status_code == 200

        data = response.json()
        assert "layers" in data
        assert len(data["layers"]) == 2
        assert data["total_estimated_time_sequential"] == 10
        assert data["time_savings_percent"] == 50.0

    def test_get_execution_plan_project_not_found(
        self, client, admin_headers, sample_user
    ):
        """Nonexistent project returns 404."""
        response = client.get(
            "/api/projects/99999/execution-plan", headers=admin_headers
        )
        assert response.status_code == 404


class TestDeployAll:
    """POST /api/projects/{project_id}/deploy-all."""

    def test_deploy_all_viewer_denied(
        self, client, viewer_headers, all_test_users, sample_project
    ):
        """Viewer cannot trigger deploy-all — returns 403."""
        response = client.post(
            f"/api/projects/{sample_project.id}/deploy-all",
            json={"parallel": True},
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_deploy_all_operator_allowed(
        self, client, operator_headers, all_test_users, sample_project, db
    ):
        """Operator can trigger deploy-all (with mocked service)."""
        mock_result = {
            "orchestrator_task_id": "mock-orch-123",
            "execution_plan": {"layers": []},
            "total_modules": 2,
            "total_layers": 1,
            "estimated_time_minutes": 5,
        }

        with patch(
            "services.parallel_execution_service.ParallelExecutionService.validate_project_ready",
            return_value=(True, None),
        ), patch(
            "services.parallel_execution_service.ParallelExecutionService.deploy_project_parallel",
            return_value=mock_result,
        ):
            response = client.post(
                f"/api/projects/{sample_project.id}/deploy-all",
                json={"parallel": True},
                headers=operator_headers,
            )
        assert response.status_code == 200

        data = response.json()
        assert data["orchestrator_task_id"] == "mock-orch-123"
        assert data["total_modules"] == 2


class TestDestroyAll:
    """POST /api/projects/{project_id}/destroy-all."""

    def test_destroy_all_viewer_denied(
        self, client, viewer_headers, all_test_users, sample_project
    ):
        """Viewer cannot trigger destroy-all — returns 403."""
        response = client.post(
            f"/api/projects/{sample_project.id}/destroy-all",
            json={"parallel": True},
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_destroy_all_operator_allowed(
        self, client, operator_headers, all_test_users, sample_project, db
    ):
        """Operator can trigger destroy-all (with mocked service)."""
        mock_result = {
            "orchestrator_task_id": "mock-destroy-456",
            "execution_plan": {"layers": []},
            "total_modules": 3,
            "total_layers": 2,
            "estimated_time_minutes": 8,
        }

        with patch(
            "services.parallel_execution_service.ParallelExecutionService.validate_project_ready",
            return_value=(True, None),
        ), patch(
            "services.parallel_execution_service.ParallelExecutionService.destroy_project_parallel",
            return_value=mock_result,
        ):
            response = client.post(
                f"/api/projects/{sample_project.id}/destroy-all",
                json={"parallel": True},
                headers=operator_headers,
            )
        assert response.status_code == 200

        data = response.json()
        assert data["orchestrator_task_id"] == "mock-destroy-456"
        assert data["total_modules"] == 3

    def test_destroy_all_force_destroy_passed_to_service(
        self, client, operator_headers, all_test_users, sample_project, db
    ):
        """force_destroy=True is forwarded to destroy_project_parallel (#329)."""
        mock_result = {
            "orchestrator_task_id": "mock-force-789",
            "execution_plan": {"layers": []},
            "total_modules": 2,
            "total_layers": 1,
            "estimated_time_minutes": 5,
        }
        captured_kwargs: dict = {}

        def capture_kwargs(**kwargs):
            captured_kwargs.update(kwargs)
            return mock_result

        with patch(
            "services.parallel_execution_service.ParallelExecutionService.validate_project_ready",
            return_value=(True, None),
        ), patch(
            "services.parallel_execution_service.ParallelExecutionService.destroy_project_parallel",
            side_effect=lambda *a, **kw: capture_kwargs(**kw) or mock_result,
        ):
            response = client.post(
                f"/api/projects/{sample_project.id}/destroy-all",
                json={"force_destroy": True},
                headers=operator_headers,
            )
        assert response.status_code == 200
        assert captured_kwargs.get("force_destroy") is True

    def test_destroy_all_force_destroy_defaults_false(
        self, client, operator_headers, all_test_users, sample_project, db
    ):
        """force_destroy defaults to False when not provided (#329)."""
        mock_result = {
            "orchestrator_task_id": "mock-default-000",
            "execution_plan": {"layers": []},
            "total_modules": 1,
            "total_layers": 1,
            "estimated_time_minutes": 3,
        }
        captured_kwargs: dict = {}

        def capture_kwargs(**kwargs):
            captured_kwargs.update(kwargs)
            return mock_result

        with patch(
            "services.parallel_execution_service.ParallelExecutionService.validate_project_ready",
            return_value=(True, None),
        ), patch(
            "services.parallel_execution_service.ParallelExecutionService.destroy_project_parallel",
            side_effect=lambda *a, **kw: capture_kwargs(**kw) or mock_result,
        ):
            response = client.post(
                f"/api/projects/{sample_project.id}/destroy-all",
                json={},
                headers=operator_headers,
            )
        assert response.status_code == 200
        assert captured_kwargs.get("force_destroy") is False


class TestGetParallelExecutionStatus:
    """GET /api/projects/{project_id}/parallel-executions/{exec_id} — removed in D-001 Phase 3 S3b."""

    def test_get_parallel_execution_status_route_removed(
        self, client, admin_headers, sample_user, sample_project
    ):
        """parallel-executions/{id} route removed when table dropped (v2_121)."""
        response = client.get(
            f"/api/projects/{sample_project.id}/parallel-executions/1",
            headers=admin_headers,
        )
        # Route no longer exists — FastAPI returns 404 or 405
        assert response.status_code in (404, 405)

    def test_get_parallel_execution_nonexistent_returns_404_or_405(
        self, client, admin_headers, sample_user, sample_project
    ):
        """Nonexistent execution ID → 404 or 405 (route removed)."""
        response = client.get(
            f"/api/projects/{sample_project.id}/parallel-executions/99999",
            headers=admin_headers,
        )
        assert response.status_code in (404, 405)


class TestListParallelExecutions:
    """GET /api/projects/{project_id}/parallel-executions — removed in D-001 Phase 3 S3b."""

    def test_list_parallel_executions_route_removed(
        self, client, admin_headers, sample_user, sample_project
    ):
        """parallel-executions route was removed when table was dropped (v2_121)."""
        response = client.get(
            f"/api/projects/{sample_project.id}/parallel-executions",
            headers=admin_headers,
        )
        # Route no longer exists
        assert response.status_code in (404, 405)


class TestGetAllModules:
    """GET /api/projects/modules/all."""

    def test_get_all_modules(
        self, client, admin_headers, sample_module
    ):
        """Returns paginated list of modules across all projects."""
        response = client.get(
            "/api/projects/modules/all", headers=admin_headers
        )
        assert response.status_code == 200

        data = response.json()
        assert "items" in data
        assert "pagination" in data
        assert len(data["items"]) >= 1
        assert data["pagination"]["total_items"] >= 1

        # Verify module shape
        item = data["items"][0]
        assert "id" in item
        assert "module_name" in item
        assert "status" in item
        assert "project_id" in item

    def test_get_all_modules_pagination(
        self, client, admin_headers, sample_user, sample_project, db
    ):
        """Pagination limits results and provides correct metadata."""
        from tests.factories import ModuleLibraryFactory, ProjectModuleFactory

        # Create multiple modules
        for i in range(3):
            lib = ModuleLibraryFactory(
                db, name=f"paginate-mod-{i}", category="test"
            )
            ProjectModuleFactory(
                db, project=sample_project, library_module=lib
            )
        db.commit()

        response = client.get(
            "/api/projects/modules/all?page=1&page_size=1",
            headers=admin_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert len(data["items"]) == 1
        assert data["pagination"]["page"] == 1
        assert data["pagination"]["page_size"] == 1
        assert data["pagination"]["total_items"] >= 3
        assert data["pagination"]["has_next"] is True
