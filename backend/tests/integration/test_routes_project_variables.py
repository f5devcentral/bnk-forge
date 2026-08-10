"""
Integration tests for project variable routes — /api/projects/{id}/variable-defaults
and /api/projects/{id}/variables.

Covers: get/update/reset variable defaults, get/update project variables, RBAC.
Uses FastAPI TestClient with real SQLite DB.
"""

import pytest

from models import Project


class TestGetVariableDefaults:
    """GET /api/projects/{id}/variable-defaults."""

    def test_get_variable_defaults(
        self, client, admin_headers, sample_user, sample_project
    ):
        """Returns common_defaults, custom_defaults, and effective_defaults."""
        response = client.get(
            f"/api/projects/{sample_project.id}/variable-defaults",
            headers=admin_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["project_id"] == sample_project.id
        assert "common_defaults" in data
        assert "custom_defaults" in data
        assert "effective_defaults" in data
        # Common defaults include known keys
        assert "vpc_cidr" in data["common_defaults"]
        assert "instance_type" in data["common_defaults"]

    def test_get_variable_defaults_project_not_found(
        self, client, admin_headers, sample_user
    ):
        """Nonexistent project returns 404."""
        response = client.get(
            "/api/projects/99999/variable-defaults",
            headers=admin_headers,
        )
        assert response.status_code == 404


class TestUpdateVariableDefaults:
    """PUT /api/projects/{id}/variable-defaults."""

    def test_update_variable_defaults(
        self, client, admin_headers, sample_user, sample_project, db
    ):
        """PUT with defaults persists custom overrides in DB."""
        payload = {"defaults": {"vpc_cidr": "172.16.0.0/16", "instance_type": "t3.large"}}
        response = client.put(
            f"/api/projects/{sample_project.id}/variable-defaults",
            json=payload,
            headers=admin_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert data["defaults_count"] == 2

        # Verify in DB
        db.refresh(sample_project)
        stored = sample_project.project_variables or {}
        assert stored.get("variable_defaults", {}).get("vpc_cidr") == "172.16.0.0/16"

    def test_viewer_cannot_write_defaults(
        self, client, viewer_headers, all_test_users, sample_project
    ):
        """Viewer cannot update variable defaults — returns 403."""
        response = client.put(
            f"/api/projects/{sample_project.id}/variable-defaults",
            json={"defaults": {"vpc_cidr": "10.0.0.0/8"}},
            headers=viewer_headers,
        )
        assert response.status_code == 403


class TestResetVariableDefaults:
    """DELETE /api/projects/{id}/variable-defaults."""

    def test_reset_variable_defaults(
        self, client, admin_headers, sample_user, sample_project, db
    ):
        """DELETE removes custom overrides, leaving only common defaults."""
        # First set some custom defaults
        sample_project.project_variables = {
            "variable_defaults": {"vpc_cidr": "172.16.0.0/16"}
        }
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(sample_project, "project_variables")
        db.commit()

        response = client.delete(
            f"/api/projects/{sample_project.id}/variable-defaults",
            headers=admin_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True

        # Verify custom defaults are removed from DB
        db.refresh(sample_project)
        stored = sample_project.project_variables or {}
        assert "variable_defaults" not in stored


class TestGetProjectVariables:
    """GET /api/projects/{id}/variables."""

    def test_get_project_variables(
        self, client, admin_headers, sample_user, sample_project
    ):
        """Returns variables, discovered, configurable, modules, and effective."""
        response = client.get(
            f"/api/projects/{sample_project.id}/variables",
            headers=admin_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["project_id"] == sample_project.id
        assert "variables" in data
        assert "discovered" in data
        assert "configurable" in data
        assert "modules" in data
        assert "effective" in data

    def test_viewer_can_read_variables(
        self, client, viewer_headers, all_test_users, sample_project
    ):
        """Viewer can read project variables."""
        response = client.get(
            f"/api/projects/{sample_project.id}/variables",
            headers=viewer_headers,
        )
        assert response.status_code == 200


class TestUpdateProjectVariables:
    """PUT /api/projects/{id}/variables."""

    def test_update_project_variables(
        self, client, admin_headers, sample_user, sample_project, db
    ):
        """PUT with variables body persists and returns updated variables."""
        payload = {"variables": {"cluster_name": "my-cluster", "region": "us-east-1"}}
        response = client.put(
            f"/api/projects/{sample_project.id}/variables",
            json=payload,
            headers=admin_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert data["variables_count"] == 2
        assert data["variables"]["cluster_name"] == "my-cluster"

        # Verify in DB
        db.refresh(sample_project)
        assert sample_project.project_variables["cluster_name"] == "my-cluster"

    def test_update_project_variables_viewer_denied(
        self, client, viewer_headers, all_test_users, sample_project
    ):
        """Viewer cannot update project variables — returns 403."""
        response = client.put(
            f"/api/projects/{sample_project.id}/variables",
            json={"variables": {"hacked": "true"}},
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_update_project_variables_project_not_found(
        self, client, admin_headers, sample_user
    ):
        """Updating variables on nonexistent project returns 404."""
        response = client.put(
            "/api/projects/99999/variables",
            json={"variables": {"key": "val"}},
            headers=admin_headers,
        )
        assert response.status_code == 404
