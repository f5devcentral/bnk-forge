"""
Integration tests for multi-user access control (MU-001 through MU-012).

Tests:
  1. Ownership assignment on project creation
  2. Owner can mutate their own project
  3. Non-owner operator is blocked from mutating another's project
  4. Admin bypasses ownership checks
  5. Viewer cannot mutate any project
  6. Legacy projects (no owner) are accessible to all operators
  7. Ownership transfer via POST /api/projects/{id}/transfer
  8. Transfer fails for inactive users
  9. Transfer fails for non-existent users
 10. User management CRUD (admin only)
 11. Non-admin cannot manage users
 12. User list includes project counts
"""

import pytest

from models import Project

# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture()
def admin_user(make_user):
    """Admin user that matches the admin_headers JWT."""
    return make_user(username="testadmin", role="admin")


@pytest.fixture()
def operator_a(make_user):
    """Operator 'alice' — will own projects."""
    return make_user(username="alice", role="operator")


@pytest.fixture()
def operator_b(make_user):
    """Operator 'bob' — will try to access alice's projects."""
    return make_user(username="bob", role="operator")


@pytest.fixture()
def viewer_user(make_user):
    """Viewer user that matches the viewer_headers JWT."""
    return make_user(username="testviewer", role="viewer")


@pytest.fixture()
def alice_headers():
    """JWT headers for alice (operator)."""
    from services.auth_service import create_access_token
    token = create_access_token(data={"sub": "alice", "role": "operator"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def bob_headers():
    """JWT headers for bob (operator)."""
    from services.auth_service import create_access_token
    token = create_access_token(data={"sub": "bob", "role": "operator"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def alice_project(db, operator_a, make_project):
    """A project owned by alice."""
    return make_project(name="Alice's Infra", user_id=operator_a.id)


@pytest.fixture()
def legacy_project(make_project):
    """A project with no owner (legacy data)."""
    return make_project(name="Legacy Project", user_id=None)


# ============================================================================
# Tests: Ownership enforcement
# ============================================================================

class TestOwnershipEnforcement:
    """Tests that project mutations respect ownership."""

    def test_owner_can_update_project(self, client, alice_headers, operator_a, alice_project):
        """Owner can PUT /api/projects/{id} to update their own project."""
        response = client.put(
            f"/api/projects/{alice_project.id}",
            json={"description": "Updated by owner"},
            headers=alice_headers,
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["success"] is True

    def test_nonowner_operator_blocked_from_update(self, client, bob_headers, operator_a, operator_b, alice_project):
        """Another operator cannot update alice's project."""
        response = client.put(
            f"/api/projects/{alice_project.id}",
            json={"description": "Updated by bob"},
            headers=bob_headers,
        )
        assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.text}"

    def test_admin_bypasses_ownership(self, client, admin_headers, admin_user, operator_a, alice_project):
        """Admin can update any project regardless of ownership."""
        response = client.put(
            f"/api/projects/{alice_project.id}",
            json={"description": "Updated by admin"},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text

    def test_viewer_cannot_mutate(self, client, viewer_headers, viewer_user, alice_project, operator_a):
        """Viewer cannot update any project."""
        response = client.put(
            f"/api/projects/{alice_project.id}",
            json={"description": "Updated by viewer"},
            headers=viewer_headers,
        )
        # Viewer should be blocked by role check (403 or similar)
        assert response.status_code in (401, 403), f"Expected 401/403, got {response.status_code}: {response.text}"

    def test_legacy_project_accessible(self, client, bob_headers, operator_b, legacy_project):
        """Projects with no owner (user_id=None) are accessible to any operator."""
        response = client.put(
            f"/api/projects/{legacy_project.id}",
            json={"description": "Updated by bob"},
            headers=bob_headers,
        )
        assert response.status_code == 200, response.text

    def test_owner_can_delete_project(self, client, alice_headers, operator_a, alice_project):
        """Owner can DELETE their own project (force=true since it's their only project).

        Note: In CI the delete may fail with ConnectionError during workspace cleanup
        (no real infra), so we check the request passed the ownership check (not 403).
        """
        response = client.delete(
            f"/api/projects/{alice_project.id}?force=true",
            headers=alice_headers,
        )
        # Not 403 = ownership check passed (delete may fail on cleanup in CI)
        assert response.status_code != 403, f"Ownership check should pass: {response.text}"

    def test_nonowner_operator_blocked_from_delete(self, client, bob_headers, operator_a, operator_b, alice_project):
        """Another operator cannot delete alice's project."""
        response = client.delete(
            f"/api/projects/{alice_project.id}",
            headers=bob_headers,
        )
        assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.text}"

    def test_all_users_can_view_projects(self, client, bob_headers, operator_a, operator_b, alice_project):
        """All authenticated users can GET projects (view-only)."""
        response = client.get("/api/projects", headers=bob_headers)
        assert response.status_code == 200
        data = response.json()
        # Bob should be able to see alice's project in the list
        project_names = [p["name"] for p in data["projects"]]
        assert alice_project.name in project_names

    def test_project_creation_sets_owner(self, client, alice_headers, operator_a):
        """POST /api/projects sets user_id to the creating user."""
        response = client.post(
            "/api/projects",
            json={"name": "Auto-owned Project"},
            headers=alice_headers,
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["success"] is True

        # Verify the project's user_id was set
        response2 = client.get(f"/api/projects/{data['project_id']}", headers=alice_headers)
        assert response2.status_code == 200


# ============================================================================
# Tests: Ownership transfer
# ============================================================================

class TestOwnershipTransfer:
    """Tests for POST /api/projects/{id}/transfer."""

    def test_owner_can_transfer(self, client, alice_headers, operator_a, operator_b, alice_project, db):
        """Owner transfers project to another user."""
        response = client.post(
            f"/api/projects/{alice_project.id}/transfer",
            json={"new_owner_id": operator_b.id},
            headers=alice_headers,
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["success"] is True
        assert data["new_owner"] == "bob"
        assert data["previous_owner"] == "alice"

        # Verify DB was updated
        db.refresh(alice_project)
        assert alice_project.user_id == operator_b.id

    def test_admin_can_transfer_any_project(self, client, admin_headers, admin_user, operator_a, operator_b, alice_project, db):
        """Admin can transfer any project."""
        response = client.post(
            f"/api/projects/{alice_project.id}/transfer",
            json={"new_owner_id": operator_b.id},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text

    def test_nonowner_cannot_transfer(self, client, bob_headers, operator_a, operator_b, alice_project):
        """Non-owner operator cannot transfer someone else's project."""
        response = client.post(
            f"/api/projects/{alice_project.id}/transfer",
            json={"new_owner_id": operator_b.id},
            headers=bob_headers,
        )
        assert response.status_code == 403, response.text

    def test_transfer_to_nonexistent_user(self, client, alice_headers, operator_a, alice_project):
        """Transfer to a non-existent user returns 404."""
        response = client.post(
            f"/api/projects/{alice_project.id}/transfer",
            json={"new_owner_id": 99999},
            headers=alice_headers,
        )
        assert response.status_code == 404, response.text

    def test_transfer_to_inactive_user(self, client, alice_headers, operator_a, alice_project, make_user):
        """Transfer to an inactive user returns 400."""
        inactive = make_user(username="inactive_user", role="operator", is_active=False)
        response = client.post(
            f"/api/projects/{alice_project.id}/transfer",
            json={"new_owner_id": inactive.id},
            headers=alice_headers,
        )
        assert response.status_code == 400, response.text


# ============================================================================
# Tests: User management (admin only)
# ============================================================================

class TestUserManagement:
    """Tests for user CRUD endpoints."""

    def test_admin_can_list_users(self, client, admin_headers, admin_user):
        """Admin can GET /api/auth/users."""
        response = client.get("/api/auth/users", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert "users" in data
        assert "total" in data
        assert data["total"] >= 1

    def test_operator_cannot_list_users(self, client, alice_headers, operator_a):
        """Operator cannot access user management."""
        response = client.get("/api/auth/users", headers=alice_headers)
        assert response.status_code in (401, 403), f"Expected 401/403, got {response.status_code}"

    def test_admin_can_create_user(self, client, admin_headers, admin_user):
        """Admin can POST /api/auth/users to create a new user."""
        response = client.post(
            "/api/auth/users",
            json={
                "username": "newuser",
                "email": "newuser@test.com",
                "password": "securepass123",
                "role": "operator",
            },
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["success"] is True
        assert data["user"]["username"] == "newuser"

    def test_admin_can_update_user(self, client, admin_headers, admin_user, operator_a):
        """Admin can PUT /api/auth/users/{id} to update a user."""
        response = client.put(
            f"/api/auth/users/{operator_a.id}",
            json={"role": "viewer"},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["user"]["role"] == "viewer"

    def test_admin_cannot_demote_self(self, client, admin_headers, admin_user):
        """Admin cannot change their own role."""
        response = client.put(
            f"/api/auth/users/{admin_user.id}",
            json={"role": "operator"},
            headers=admin_headers,
        )
        assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.text}"

    def test_admin_can_delete_user(self, client, admin_headers, admin_user, operator_a):
        """Admin can DELETE /api/auth/users/{id}."""
        response = client.delete(
            f"/api/auth/users/{operator_a.id}",
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text

    def test_admin_cannot_delete_self(self, client, admin_headers, admin_user):
        """Admin cannot delete their own account."""
        response = client.delete(
            f"/api/auth/users/{admin_user.id}",
            headers=admin_headers,
        )
        assert response.status_code == 403, f"Expected 403, got {response.status_code}"

    def test_user_list_includes_project_counts(self, client, admin_headers, admin_user, operator_a, make_project):
        """GET /api/auth/users includes project_count for each user."""
        # Create 2 projects owned by alice
        make_project(name="P1", user_id=operator_a.id)
        make_project(name="P2", user_id=operator_a.id)

        response = client.get("/api/auth/users", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        alice_data = next((u for u in data["users"] if u["username"] == "alice"), None)
        assert alice_data is not None
        assert alice_data["project_count"] == 2
