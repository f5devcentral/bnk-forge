"""
Integration tests for authentication routes — /api/auth.

Covers: login, /me, change-password, user CRUD, RBAC enforcement.
Uses FastAPI TestClient with real SQLite DB.
"""

import pytest

from models import User


class TestLogin:
    """POST /api/auth/login."""

    def test_login_success(self, client, sample_user):
        """Valid credentials return JWT token and user info."""
        response = client.post(
            "/api/auth/login",
            json={"username": "testadmin", "password": "testpassword123"},
        )
        assert response.status_code == 200

        data = response.json()
        assert "token" in data
        assert len(data["token"]) > 20
        assert data["user"]["username"] == "testadmin"
        assert data["user"]["role"] == "admin"
        assert "hashed_password" not in data["user"]
        assert data["must_change_password"] is False

    def test_login_wrong_password(self, client, sample_user):
        """Wrong password returns 401 with UNAUTHORIZED code."""
        response = client.post(
            "/api/auth/login",
            json={"username": "testadmin", "password": "wrongpassword"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"

    def test_login_nonexistent_user(self, client):
        """Nonexistent user returns 401 — no user enumeration."""
        response = client.post(
            "/api/auth/login",
            json={"username": "doesnotexist", "password": "anything"},
        )
        assert response.status_code == 401

    def test_login_inactive_user(self, client, db):
        """Inactive user cannot log in."""
        from services.auth_service import hash_password

        user = User(
            username="inactive_user",
            email="inactive@test.com",
            hashed_password=hash_password("password123"),
            role="admin",
            is_active=False,
        )
        db.add(user)
        db.commit()

        response = client.post(
            "/api/auth/login",
            json={"username": "inactive_user", "password": "password123"},
        )
        assert response.status_code == 401


class TestGetMe:
    """GET /api/auth/me."""

    def test_get_me_authenticated(self, client, admin_headers, sample_user):
        """Returns current user info when authenticated."""
        response = client.get("/api/auth/me", headers=admin_headers)
        assert response.status_code == 200

        user = response.json()["user"]
        assert user["username"] == "testadmin"
        assert user["role"] == "admin"
        assert user["email"] == "testadmin@bnk-forge.test"
        assert "hashed_password" not in user

    def test_get_me_unauthenticated(self, client):
        """Returns 401 without auth token."""
        response = client.get("/api/auth/me")
        assert response.status_code == 401

    def test_get_me_expired_token(self, client, sample_user):
        """Returns 401 with an expired/invalid token."""
        response = client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert response.status_code == 401


class TestChangePassword:
    """POST /api/auth/change-password."""

    def test_change_password_success(self, client, admin_headers, sample_user):
        """Changing password succeeds, old password stops working."""
        response = client.post(
            "/api/auth/change-password",
            json={
                "current_password": "testpassword123",
                "new_password": "newpassword456",
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

        # Old password should fail
        login_old = client.post(
            "/api/auth/login",
            json={"username": "testadmin", "password": "testpassword123"},
        )
        assert login_old.status_code == 401

        # New password should work
        login_new = client.post(
            "/api/auth/login",
            json={"username": "testadmin", "password": "newpassword456"},
        )
        assert login_new.status_code == 200

    def test_change_password_wrong_current(self, client, admin_headers, sample_user):
        """Wrong current password returns error, password unchanged."""
        response = client.post(
            "/api/auth/change-password",
            json={
                "current_password": "wrongpassword",
                "new_password": "newpassword456",
            },
            headers=admin_headers,
        )
        assert response.status_code in (400, 401)

        # Original password still works
        login = client.post(
            "/api/auth/login",
            json={"username": "testadmin", "password": "testpassword123"},
        )
        assert login.status_code == 200


class TestUserCRUD:
    """POST/GET/DELETE /api/auth/users."""

    def test_create_user_admin(self, client, admin_headers, sample_user, db):
        """Admin can create a new user, user appears in DB."""
        response = client.post(
            "/api/auth/users",
            json={
                "username": "newoperator",
                "email": "newop@test.com",
                "password": "password123",
                "role": "operator",
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["success"] is True
        assert response.json()["user"]["username"] == "newoperator"

        # Verify in DB
        user = db.query(User).filter(User.username == "newoperator").first()
        assert user is not None
        assert user.role == "operator"

    def test_create_user_viewer_denied(self, client, viewer_headers, all_test_users, db):
        """Viewer cannot create users — returns 403."""
        count_before = db.query(User).count()
        response = client.post(
            "/api/auth/users",
            json={
                "username": "sneaky",
                "email": "sneaky@test.com",
                "password": "password123",
                "role": "viewer",
            },
            headers=viewer_headers,
        )
        assert response.status_code == 403
        assert db.query(User).count() == count_before

    def test_create_user_operator_denied(self, client, operator_headers, all_test_users, db):
        """Operator cannot create users — returns 403."""
        response = client.post(
            "/api/auth/users",
            json={
                "username": "sneaky",
                "email": "sneaky@test.com",
                "password": "password123",
                "role": "viewer",
            },
            headers=operator_headers,
        )
        assert response.status_code == 403

    def test_list_users_admin(self, client, admin_headers, all_test_users, db):
        """Admin can list all users."""
        response = client.get("/api/auth/users", headers=admin_headers)
        assert response.status_code == 200

        users = response.json()["users"]
        assert len(users) == db.query(User).count()
        usernames = [u["username"] for u in users]
        assert "testadmin" in usernames

    def test_list_users_viewer_denied(self, client, viewer_headers, all_test_users):
        """Viewer cannot list users — returns 403."""
        response = client.get("/api/auth/users", headers=viewer_headers)
        assert response.status_code == 403

    def test_delete_user(self, client, admin_headers, all_test_users, db):
        """Admin can delete another user, user disappears from DB."""
        viewer = db.query(User).filter(User.username == "testviewer").first()
        response = client.delete(
            f"/api/auth/users/{viewer.id}", headers=admin_headers
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

        # Verify deleted
        assert db.query(User).filter(User.id == viewer.id).first() is None

    def test_delete_self_denied(self, client, admin_headers, sample_user, db):
        """Admin cannot delete their own account."""
        response = client.delete(
            f"/api/auth/users/{sample_user.id}", headers=admin_headers
        )
        assert response.status_code == 403

        # User still exists
        assert db.query(User).filter(User.id == sample_user.id).first() is not None

    def test_delete_nonexistent_user(self, client, admin_headers, sample_user):
        """Deleting nonexistent user returns 404."""
        response = client.delete("/api/auth/users/99999", headers=admin_headers)
        assert response.status_code == 404


class TestMustChangePasswordEnforcement:
    """#184: must_change_password must gate the API server-side, not just the UI.

    A seeded/admin-created must-change user gets a valid token, so without a
    server gate a client could skip the change-password screen and call every
    endpoint directly with the seed credential.
    """

    def _make_must_change_admin(self, db):
        from services.auth_service import create_user
        u = create_user(
            db, "mustchange", "mustchange@test.com", "startpw",
            role="admin", must_change_password=True,
        )
        db.commit()
        return u

    def _login(self, client, username, password):
        r = client.post("/api/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        assert r.json()["must_change_password"] is True
        return r.json()["token"]

    def test_protected_endpoint_refused_until_password_changed(self, client, db):
        self._make_must_change_admin(db)
        token = self._login(client, "mustchange", "startpw")
        hdr = {"Authorization": f"Bearer {token}"}

        # A normal protected endpoint is refused with 403 while must-change.
        blocked = client.get("/api/auth/users", headers=hdr)
        assert blocked.status_code == 403, blocked.text

        # The exempt endpoints still work: read own state and change password.
        assert client.get("/api/auth/me", headers=hdr).status_code == 200

        changed = client.post(
            "/api/auth/change-password",
            headers=hdr,
            json={"current_password": "startpw", "new_password": "BrandNewPw123!"},
        )
        assert changed.status_code == 200, changed.text

        # After the change the flag clears, so the same endpoint now works.
        after = client.get("/api/auth/users", headers=hdr)
        assert after.status_code == 200, after.text

    def test_non_must_change_user_is_not_gated(self, client, admin_headers, sample_user):
        # Regression guard: an ordinary user (must_change False) reaches the API.
        assert client.get("/api/auth/users", headers=admin_headers).status_code == 200
