"""
Integration tests for project deployment routes — /api/project-modules.

Covers: deployment logs, deployment history (per-module and per-project),
state info, state resources, RBAC enforcement.
Uses FastAPI TestClient with real SQLite DB.
"""

from datetime import UTC, datetime, timezone
from unittest.mock import patch

import pytest

from models import Deployment, DeploymentLog, ProjectModule


class TestGetDeploymentLogs:
    """GET /api/project-modules/{module_id}/logs."""

    def test_get_deployment_logs_empty(self, client, admin_headers, sample_module):
        """Module with no logs returns total_logs=0 and empty list."""
        mod = sample_module["module"]
        response = client.get(
            f"/api/project-modules/{mod.id}/logs", headers=admin_headers
        )
        assert response.status_code == 200

        data = response.json()
        assert data["module_id"] == mod.id
        assert data["module_name"] == "test-vpc"
        assert data["total_logs"] == 0
        assert data["logs"] == []

    def test_get_deployment_logs_with_data(
        self, client, admin_headers, sample_module, db
    ):
        """Logs are returned when DeploymentLog records exist."""
        mod = sample_module["module"]
        log = DeploymentLog(
            module_id=mod.id,
            level="info",
            message="Apply started",
            timestamp=datetime.now(UTC),
        )
        db.add(log)
        db.commit()

        response = client.get(
            f"/api/project-modules/{mod.id}/logs", headers=admin_headers
        )
        assert response.status_code == 200

        data = response.json()
        assert data["total_logs"] == 1
        assert data["logs"][0]["level"] == "info"
        assert data["logs"][0]["message"] == "Apply started"

    def test_get_deployment_logs_module_not_found(
        self, client, admin_headers, sample_user
    ):
        """Nonexistent module returns 404."""
        response = client.get(
            "/api/project-modules/99999/logs", headers=admin_headers
        )
        assert response.status_code == 404


class TestGetDeploymentHistory:
    """GET /api/project-modules/{module_id}/deployments."""

    def test_get_deployment_history(
        self, client, admin_headers, sample_module, db
    ):
        """Deployment records are returned in history."""
        mod = sample_module["module"]
        dep = Deployment(
            module_id=mod.id,
            action="apply",
            status="success",
            triggered_by="testadmin",
            started_at=datetime.now(UTC),
            duration_seconds=45.0,
            resources_to_add=3,
        )
        db.add(dep)
        db.commit()

        response = client.get(
            f"/api/project-modules/{mod.id}/deployments",
            headers=admin_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["module_id"] == mod.id
        assert data["total_deployments"] == 1
        assert data["deployments"][0]["action"] == "apply"
        assert data["deployments"][0]["status"] == "success"
        assert data["deployments"][0]["resources_to_add"] == 3

    def test_get_deployment_history_filter_by_action(
        self, client, admin_headers, sample_module, db
    ):
        """Filtering by action narrows results."""
        mod = sample_module["module"]
        db.add(Deployment(
            module_id=mod.id,
            action="apply",
            status="success",
            triggered_by="testadmin",
            started_at=datetime.now(UTC),
        ))
        db.add(Deployment(
            module_id=mod.id,
            action="destroy",
            status="success",
            triggered_by="testadmin",
            started_at=datetime.now(UTC),
        ))
        db.commit()

        response = client.get(
            f"/api/project-modules/{mod.id}/deployments?action=apply",
            headers=admin_headers,
        )
        assert response.status_code == 200

        data = response.json()
        for dep in data["deployments"]:
            assert dep["action"] == "apply"

    def test_get_deployment_history_module_not_found(
        self, client, admin_headers, sample_user
    ):
        """Nonexistent module returns 404."""
        response = client.get(
            "/api/project-modules/99999/deployments",
            headers=admin_headers,
        )
        assert response.status_code == 404


class TestGetProjectDeployments:
    """GET /api/project-modules/project/{project_id}/deployments."""

    def test_get_project_deployments(
        self, client, admin_headers, sample_module, db
    ):
        """Project-level deployments include module name and path."""
        mod = sample_module["module"]
        dep = Deployment(
            module_id=mod.id,
            action="apply",
            status="success",
            triggered_by="testadmin",
            started_at=datetime.now(UTC),
        )
        db.add(dep)
        db.commit()

        project = sample_module["project"]
        response = client.get(
            f"/api/project-modules/project/{project.id}/deployments",
            headers=admin_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["project_id"] == project.id
        assert data["total_deployments"] >= 1
        assert data["deployments"][0]["module_id"] == mod.id

    def test_get_project_deployments_project_not_found(
        self, client, admin_headers, sample_user
    ):
        """Nonexistent project returns 404."""
        response = client.get(
            "/api/project-modules/project/99999/deployments",
            headers=admin_headers,
        )
        assert response.status_code == 404


class TestGetStateInfo:
    """GET /api/project-modules/{module_id}/state-info."""

    def test_get_state_info_no_state_file(
        self, client, admin_headers, sample_module
    ):
        """When no state file exists, state_exists=False."""
        mod = sample_module["module"]

        with patch("os.path.exists", return_value=False):
            response = client.get(
                f"/api/project-modules/{mod.id}/state-info",
                headers=admin_headers,
            )
        assert response.status_code == 200

        data = response.json()
        assert data["module_id"] == mod.id
        assert data["state_exists"] is False

    def test_get_state_info_module_not_found(
        self, client, admin_headers, sample_user
    ):
        """Nonexistent module returns 404."""
        response = client.get(
            "/api/project-modules/99999/state-info", headers=admin_headers
        )
        assert response.status_code == 404


class TestStateResources:
    """GET /api/project-modules/{module_id}/state-resources."""

    def test_get_state_resources_no_state_file(
        self, client, admin_headers, sample_module
    ):
        """When no state file exists, returns empty resources list."""
        mod = sample_module["module"]

        with patch("os.path.exists", return_value=False):
            response = client.get(
                f"/api/project-modules/{mod.id}/state-resources",
                headers=admin_headers,
            )
        assert response.status_code == 200

        data = response.json()
        assert data["module_id"] == mod.id
        assert data["resources"] == []
        assert data["resources_count"] == 0


class TestDeploymentRBAC:
    """RBAC enforcement for deployment read endpoints."""

    def test_viewer_can_read_deployment_logs(
        self, client, viewer_headers, all_test_users, sample_project, db
    ):
        """Viewer can read deployment logs."""
        from tests.factories import ModuleLibraryFactory, ProjectModuleFactory

        lib = ModuleLibraryFactory(db, name="rbac-log-test", category="test")
        mod = ProjectModuleFactory(
            db, project=sample_project, library_module=lib
        )
        db.commit()

        response = client.get(
            f"/api/project-modules/{mod.id}/logs", headers=viewer_headers
        )
        assert response.status_code == 200


class TestRepairInfrastructureAccess:
    """POST /api/project-modules/{module_id}/repair-access."""

    def test_repair_access_requires_applied_status(self, client, admin_headers, sample_module, db):
        module = sample_module["module"]
        module.status = "initialized"
        db.commit()

        response = client.post(f"/api/project-modules/{module.id}/repair-access", headers=admin_headers)
        assert response.status_code == 400

    def test_repair_access_normalizes_stale_outputs(self, client, admin_headers, sample_module, db):
        module = sample_module["module"]
        module.status = "applied"
        module.outputs = {
            "infrastructure_private_key_path": "~/.ssh/missing.pem",
            "jumphost_ssh_command": "ssh -i ~/.ssh/missing.pem ubuntu@1.2.3.4",
        }
        db.commit()

        response = client.post(f"/api/project-modules/{module.id}/repair-access", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["infrastructure_private_key_available"] is False
        assert data["infrastructure_private_key_path"] is None
        assert data["infrastructure_access_status"] == "recovery_required"
