"""
Tests for execution route endpoints — plan, apply, destroy, cancel, deploy, retry.

Covers:
  - POST /api/project-modules/{id}/init
  - POST /api/project-modules/{id}/plan
  - POST /api/project-modules/{id}/apply
  - POST /api/project-modules/{id}/destroy
  - POST /api/project-modules/{id}/cancel
  - POST /api/project-modules/{id}/deploy
  - POST /api/project-modules/{id}/retry
  - GET  /api/project-modules/{id}/validate
  - GET  /api/project-modules/{id}/plan-status
  - GET  /api/projects/{id}/execution-plan
  - GET  /api/project-modules/{id}/logs
  - GET  /api/project-modules/{id}/deployments
  - RBAC enforcement (viewer cannot submit, operator can)
  - Error handling for nonexistent modules
"""

from unittest.mock import MagicMock, patch

import pytest


class TestExecutionEndpoints:
    """Test execution route endpoints (init, plan, apply, destroy, cancel, deploy, retry)."""

    def test_init_requires_operator(self, client, viewer_headers, all_test_users, sample_project, make_project_module):
        """POST /api/project-modules/{id}/init requires operator role."""
        module = make_project_module(project=sample_project)

        response = client.post(
            f"/api/project-modules/{module.id}/init",
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_init_success(self, client, admin_headers, all_test_users, sample_project, make_project_module):
        """POST /api/project-modules/{id}/init dispatches init task."""
        module = make_project_module(project=sample_project)

        with patch("services.project_module_service.ProjectModuleService.submit_init") as mock_submit:
            mock_submit.return_value = {"task_id": 1, "celery_task_id": "abc-123", "status": "queued"}
            response = client.post(
                f"/api/project-modules/{module.id}/init",
                headers=admin_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == 1

    def test_plan_requires_operator(self, client, viewer_headers, all_test_users, sample_project, make_project_module):
        """POST /api/project-modules/{id}/plan requires operator role."""
        module = make_project_module(project=sample_project)

        response = client.post(
            f"/api/project-modules/{module.id}/plan",
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_plan_success(self, client, admin_headers, all_test_users, sample_project, make_project_module):
        """POST /api/project-modules/{id}/plan dispatches plan task."""
        module = make_project_module(project=sample_project)

        with patch("services.project_module_service.ProjectModuleService.submit_plan") as mock_submit:
            mock_submit.return_value = {"task_id": 2, "status": "queued"}
            response = client.post(
                f"/api/project-modules/{module.id}/plan",
                headers=admin_headers,
            )

        assert response.status_code == 200

    def test_apply_requires_operator(self, client, viewer_headers, all_test_users, sample_project, make_project_module):
        """POST /api/project-modules/{id}/apply requires operator role."""
        module = make_project_module(project=sample_project)

        response = client.post(
            f"/api/project-modules/{module.id}/apply",
            headers=viewer_headers,
            json={"auto_approve": True},
        )
        assert response.status_code == 403

    def test_apply_success(self, client, admin_headers, all_test_users, sample_project, make_project_module):
        """POST /api/project-modules/{id}/apply dispatches apply task."""
        module = make_project_module(project=sample_project)

        with patch("services.project_module_service.ProjectModuleService.submit_apply") as mock_submit:
            mock_submit.return_value = {"task_id": 3, "status": "queued"}
            response = client.post(
                f"/api/project-modules/{module.id}/apply",
                headers=admin_headers,
                json={"auto_approve": True},
            )

        assert response.status_code == 200

    def test_destroy_requires_operator(self, client, viewer_headers, all_test_users, sample_project, make_project_module):
        """POST /api/project-modules/{id}/destroy requires operator role."""
        module = make_project_module(project=sample_project)

        response = client.post(
            f"/api/project-modules/{module.id}/destroy",
            headers=viewer_headers,
            json={"auto_approve": True},
        )
        assert response.status_code == 403

    def test_destroy_success(self, client, admin_headers, all_test_users, sample_project, make_project_module):
        """POST /api/project-modules/{id}/destroy dispatches destroy task."""
        module = make_project_module(project=sample_project)

        with patch("services.project_module_service.ProjectModuleService.submit_destroy") as mock_submit:
            mock_submit.return_value = {"task_id": 4, "status": "queued"}
            response = client.post(
                f"/api/project-modules/{module.id}/destroy",
                headers=admin_headers,
                json={"auto_approve": True},
            )

        assert response.status_code == 200

    def test_cancel_success(self, client, admin_headers, all_test_users, sample_project, make_project_module):
        """POST /api/project-modules/{id}/cancel cancels a running operation."""
        module = make_project_module(project=sample_project)

        with patch("services.project_module_service.ProjectModuleService.cancel_operation") as mock_cancel:
            mock_cancel.return_value = {"cancelled": True, "module_id": module.id}
            response = client.post(
                f"/api/project-modules/{module.id}/cancel",
                headers=admin_headers,
            )

        assert response.status_code == 200

    def test_deploy_success(self, client, admin_headers, all_test_users, sample_project, make_project_module):
        """POST /api/project-modules/{id}/deploy triggers full deployment pipeline."""
        module = make_project_module(project=sample_project)

        with patch("services.project_module_service.ProjectModuleService.deploy_module") as mock_deploy:
            mock_deploy.return_value = {"task_id": 5, "status": "queued"}
            response = client.post(
                f"/api/project-modules/{module.id}/deploy",
                headers=admin_headers,
            )

        assert response.status_code == 200

    def test_retry_success(self, client, admin_headers, all_test_users, sample_project, make_project_module):
        """POST /api/project-modules/{id}/retry retries a failed deployment."""
        module = make_project_module(project=sample_project)

        with patch("services.project_module_service.ProjectModuleService.retry_deployment") as mock_retry:
            mock_retry.return_value = {"task_id": 6, "status": "queued"}
            response = client.post(
                f"/api/project-modules/{module.id}/retry",
                headers=admin_headers,
            )

        assert response.status_code == 200


class TestValidateEndpoint:
    """Test the module validation endpoint."""

    def test_validate_viewer_can_access(self, client, viewer_headers, all_test_users, sample_project, make_project_module):
        """GET /api/project-modules/{id}/validate allows viewer access."""
        module = make_project_module(project=sample_project)

        with patch("services.project_module_service.ProjectModuleService.validate_module") as mock_validate:
            mock_validate.return_value = {"valid": True, "errors": []}
            response = client.get(
                f"/api/project-modules/{module.id}/validate?operation=plan",
                headers=viewer_headers,
            )

        assert response.status_code == 200


class TestPlanStatusEndpoint:
    """Test the plan status endpoint."""

    def test_plan_status_viewer_can_access(self, client, viewer_headers, all_test_users, sample_project, make_project_module):
        """GET /api/project-modules/{id}/plan-status allows viewer access."""
        module = make_project_module(project=sample_project)

        with patch("services.project_module_service.ProjectModuleService.get_plan_status") as mock_status:
            mock_status.return_value = {"has_plan": False, "plan_serial": 0}
            response = client.get(
                f"/api/project-modules/{module.id}/plan-status",
                headers=viewer_headers,
            )

        assert response.status_code == 200


class TestLogsEndpoint:
    """Test the deployment logs endpoint."""

    def test_logs_returns_entries(self, client, viewer_headers, all_test_users, sample_project, make_project_module):
        """GET /api/project-modules/{id}/logs returns log entries."""
        module = make_project_module(project=sample_project)

        response = client.get(
            f"/api/project-modules/{module.id}/logs",
            headers=viewer_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "logs" in data
        assert "module_id" in data

    def test_logs_invalid_limit_returns_400(self, client, viewer_headers, all_test_users, sample_project, make_project_module):
        """GET /api/project-modules/{id}/logs with invalid limit returns 400."""
        module = make_project_module(project=sample_project)

        response = client.get(
            f"/api/project-modules/{module.id}/logs?limit=99999",
            headers=viewer_headers,
        )

        assert response.status_code == 400

    def test_logs_nonexistent_module_returns_404(self, client, viewer_headers, all_test_users):
        """GET /api/project-modules/99999/logs returns 404."""
        response = client.get(
            "/api/project-modules/99999/logs",
            headers=viewer_headers,
        )

        assert response.status_code == 404


class TestDeploymentsEndpoint:
    """Test the deployment history endpoint."""

    def test_deployments_returns_list(self, client, viewer_headers, all_test_users, sample_project, make_project_module):
        """GET /api/project-modules/{id}/deployments returns history list."""
        module = make_project_module(project=sample_project)

        response = client.get(
            f"/api/project-modules/{module.id}/deployments",
            headers=viewer_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "deployments" in data
        assert "module_id" in data

    def test_deployments_nonexistent_module_returns_404(self, client, viewer_headers, all_test_users):
        """GET /api/project-modules/99999/deployments returns 404."""
        response = client.get(
            "/api/project-modules/99999/deployments",
            headers=viewer_headers,
        )

        assert response.status_code == 404


class TestExecutionPlanEndpoint:
    """Test the execution plan endpoint for project-level orchestration."""

    def test_execution_plan_requires_auth(self, client, sample_project):
        """GET /api/projects/{id}/execution-plan requires authentication."""
        response = client.get(f"/api/projects/{sample_project.id}/execution-plan")
        assert response.status_code == 401

    def test_execution_plan_viewer_can_access(self, client, viewer_headers, all_test_users, sample_project):
        """GET /api/projects/{id}/execution-plan allows viewer access."""
        with patch("services.parallel_execution_service.ParallelExecutionService.get_execution_plan") as mock_plan:
            mock_plan.return_value = {
                "layers": [],
                "total_estimated_time_sequential": 0,
                "total_estimated_time_parallel": 0,
                "time_savings_percent": 0,
                "parallelization_factor": 1.0,
            }
            response = client.get(
                f"/api/projects/{sample_project.id}/execution-plan",
                headers=viewer_headers,
            )

        assert response.status_code == 200

    def test_execution_plan_nonexistent_project(self, client, viewer_headers, all_test_users):
        """GET /api/projects/99999/execution-plan returns 404."""
        response = client.get(
            "/api/projects/99999/execution-plan",
            headers=viewer_headers,
        )

        assert response.status_code == 404


class TestAllModulesEndpoint:
    """Test the cross-project module listing endpoint."""

    def test_all_modules_returns_paginated(self, client, viewer_headers, all_test_users, sample_project, make_project_module):
        """GET /api/projects/modules/all returns paginated module list."""
        make_project_module(project=sample_project)
        make_project_module(project=sample_project)

        response = client.get(
            "/api/projects/modules/all",
            headers=viewer_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "pagination" in data
        assert data["pagination"]["total_items"] >= 2

    def test_all_modules_filter_by_project(self, client, viewer_headers, all_test_users, make_project, make_project_module):
        """GET /api/projects/modules/all?project_id=X filters correctly."""
        proj1 = make_project(name="proj-filter-1")
        proj2 = make_project(name="proj-filter-2")
        make_project_module(project=proj1)
        make_project_module(project=proj2)

        response = client.get(
            f"/api/projects/modules/all?project_id={proj1.id}",
            headers=viewer_headers,
        )

        assert response.status_code == 200
        data = response.json()
        # All returned items should belong to proj1
        for item in data["items"]:
            assert item["project_id"] == proj1.id
