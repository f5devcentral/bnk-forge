"""
Integration tests for authentication routes.

Tests:
  5. test_login_success — POST /api/auth/login with valid creds
  6. test_login_wrong_password — POST /api/auth/login with bad creds returns 401
  7. test_rbac_viewer_cannot_create — viewer role cannot POST to admin-only routes
"""

import pytest


class TestAuthRoutes:
    """Route-level integration tests for /api/auth."""

    def test_login_success(self, client, sample_user):
        """POST /api/auth/login returns a JWT token for valid credentials."""
        response = client.post(
            "/api/auth/login",
            json={"username": "testadmin", "password": "testpassword123"},
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

        data = response.json()
        assert "token" in data
        assert len(data["token"]) > 20  # JWT is a long string
        assert data["user"]["username"] == "testadmin"
        assert data["user"]["role"] == "admin"

    def test_login_wrong_password(self, client, sample_user):
        """POST /api/auth/login with wrong password returns 401."""
        response = client.post(
            "/api/auth/login",
            json={"username": "testadmin", "password": "wrongpassword"},
        )
        assert response.status_code == 401

        data = response.json()
        assert data["error"]["code"] == "UNAUTHORIZED"

    def test_rbac_viewer_cannot_create_user(self, client, all_test_users, viewer_headers):
        """Viewer role cannot POST to admin-only routes like /api/auth/users."""
        response = client.post(
            "/api/auth/users",
            json={
                "username": "newuser",
                "email": "new@test.com",
                "password": "password123",
                "role": "viewer",
            },
            headers=viewer_headers,
        )
        assert response.status_code == 403

        data = response.json()
        assert data["error"]["code"] == "FORBIDDEN"
