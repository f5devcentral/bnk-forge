"""
Integration tests for project CRUD routes.

Tests:
  1. test_create_project — POST /api/projects with admin auth
  2. test_list_projects — GET /api/projects returns created projects
  3. test_get_project — GET /api/projects/{id} returns specific project
  4. test_create_project_unauthorized — POST without auth returns 401
"""

import pytest


class TestProjectRoutes:
    """Route-level integration tests for /api/projects."""

    def test_create_project(self, client, admin_headers, sample_user):
        """POST /api/projects creates a project and returns its ID."""
        payload = {
            "name": "My Integration Test Project",
            "description": "Created by integration test",
            "project_type": "kubernetes",
            "environment": "dev",
            "backend_type": "local",
        }
        response = client.post("/api/projects", json=payload, headers=admin_headers)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

        data = response.json()
        assert data["success"] is True
        assert data["project_id"] > 0
        assert data["name"] == "My Integration Test Project"

    def test_list_projects(self, client, admin_headers, sample_user, sample_project):
        """GET /api/projects returns the list including pre-created project."""
        response = client.get("/api/projects", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert "projects" in data
        assert data["total"] >= 1

        names = [p["name"] for p in data["projects"]]
        assert "Test Project" in names

    def test_get_project(self, client, admin_headers, sample_user, sample_project):
        """GET /api/projects/{id} returns the specific project."""
        response = client.get(
            f"/api/projects/{sample_project.id}", headers=admin_headers
        )
        assert response.status_code == 200

        data = response.json()
        assert data["id"] == sample_project.id
        assert data["name"] == "Test Project"
        assert data["description"] == "A test project for integration tests"
        assert data["project_type"] == "kubernetes"

    def test_create_project_unauthorized(self, client):
        """POST /api/projects without auth returns 401."""
        payload = {
            "name": "Unauthorized Project",
            "description": "Should not be created",
        }
        response = client.post("/api/projects", json=payload)
        assert response.status_code == 401
