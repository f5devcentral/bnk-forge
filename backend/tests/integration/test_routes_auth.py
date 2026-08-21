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

    def test_list_users_exposes_service_account_flag(self, client, admin_headers, all_test_users, db):
        """bonnyr-f5 #188: the user listing surfaces is_service_account so the UI can
        tell a service account (whose re-enable is guarded) from a human account
        instead of blindly 400ing on the toggle."""
        from services.auth_service import ensure_service_user
        ensure_service_user(db, username="mcp", password="a-strong-real-secret")
        db.commit()

        response = client.get("/api/auth/users", headers=admin_headers)
        assert response.status_code == 200
        by_name = {u["username"]: u for u in response.json()["users"]}
        assert "is_service_account" in by_name["testadmin"]
        assert by_name["testadmin"]["is_service_account"] is False
        assert by_name["mcp"]["is_service_account"] is True

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


class TestServiceAccountReEnableGuard:
    """bonnyr-f5 #188 (round 4): re-enabling a disabled service account via
    PUT /api/auth/users/{id} must not resurrect a shipped default credential.
    disable_stale_service_user only flips is_active; the bcrypt hash of
    'mcp-service-changeme' stays, so a naive re-enable brought the default back.
    """

    def _seed_disabled_default_mcp(self, db):
        # Simulate a pre-#186 upgrade row that GENUINELY holds bcrypt("mcp-service
        # -changeme"). The merged ensure_service_user (integration: #186 + #188)
        # refuses to STORE a published default — it generates a random secret
        # instead — so build the legacy row directly, exactly as an already-deployed
        # DB carries it: the v2_155 migration flags it is_service_account=True, and
        # disable_stale_service_user then deactivates it. This is precisely the state
        # the PUT-route guard defends against (re-enabling would resurrect the
        # publicly-known default).
        from services.auth_service import create_user, disable_stale_service_user
        mcp = create_user(
            db,
            username="mcp",
            email="mcp@bnk-forge.local",
            password="mcp-service-changeme",
            role="admin",
            must_change_password=False,
        )
        mcp.is_service_account = True  # v2_155 backfill marks the legacy mcp row
        db.commit()
        disable_stale_service_user(db)
        mcp = db.query(User).filter(User.username == "mcp").first()
        assert mcp.is_active is False
        assert mcp.is_service_account is True
        return mcp

    def test_reenable_refused_while_default_hash_present(
        self, client, admin_headers, sample_user, db
    ):
        from core.errors import UnauthorizedError
        from services.auth_service import authenticate_user

        mcp = self._seed_disabled_default_mcp(db)
        resp = client.put(
            f"/api/auth/users/{mcp.id}",
            json={"is_active": True},
            headers=admin_headers,
        )
        assert resp.status_code == 400
        assert "known default password" in resp.json()["error"]["message"]

        db.refresh(mcp)
        assert mcp.is_active is False  # re-enable refused

        # Mutation test: the shipped default must NOT authenticate.
        with pytest.raises(UnauthorizedError):
            authenticate_user(db, "mcp", "mcp-service-changeme")

    def test_reenable_allowed_after_real_password_rotation(
        self, client, admin_headers, sample_user, db
    ):
        """A service account carrying a real (non-default) hash re-enables fine —
        the guard is scoped to known-default hashes only."""
        from core.errors import UnauthorizedError
        from services.auth_service import authenticate_user, ensure_service_user

        self._seed_disabled_default_mcp(db)
        # Operator rotates to a strong secret (startup re-seeds + re-activates).
        ensure_service_user(db, username="mcp", password="a-real-strong-secret")
        mcp = db.query(User).filter(User.username == "mcp").first()
        assert mcp.is_active is True

        # Admin may still toggle it via the route now that no default hash remains.
        resp = client.put(
            f"/api/auth/users/{mcp.id}", json={"is_active": True}, headers=admin_headers
        )
        assert resp.status_code == 200
        db.refresh(mcp)
        assert mcp.is_active is True
        assert authenticate_user(db, "mcp", "a-real-strong-secret").is_active is True
        # And the old default is gone for good.
        with pytest.raises(UnauthorizedError):
            authenticate_user(db, "mcp", "mcp-service-changeme")


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

    def test_dependency_less_route_is_gated_by_the_middleware(self, client, db):
        """#186 (bonnyr-f5): the gate lived only in get_current_user, so a route
        that declares NO auth dependency and relies on AuthMiddleware alone was
        bypassable with the seed credential. /api/system/process-metrics is such
        a route (public_router, no get_current_user, not in PUBLIC_PATHS). A
        must-change token must be refused there, at the middleware, not served.
        """
        self._make_must_change_admin(db)
        token = self._login(client, "mustchange", "startpw")
        hdr = {"Authorization": f"Bearer {token}"}

        # Middleware-only route: must be 403 while must-change (was 200 = bypass).
        blocked = client.get("/api/system/process-metrics", headers=hdr)
        assert blocked.status_code == 403, blocked.text

        # Exempt read still works so the UI can drive the change screen.
        assert client.get("/api/auth/me", headers=hdr).status_code == 200

        # After rotating, the same middleware-only route is reachable.
        assert client.post(
            "/api/auth/change-password", headers=hdr,
            json={"current_password": "startpw", "new_password": "BrandNewPw123!"},
        ).status_code == 200
        assert client.get("/api/system/process-metrics", headers=hdr).status_code == 200

    def test_non_must_change_user_is_not_gated(self, client, admin_headers, sample_user):
        # Regression guard: an ordinary user (must_change False) reaches the API.
        assert client.get("/api/auth/users", headers=admin_headers).status_code == 200

    def test_path_route_with_auth_me_suffix_is_not_exempted(self, client, db):
        # #184 review: a ':path' route (e.g. /api/state/.../resource/{addr:path})
        # takes an attacker-chosen tail. Exact-path matching on request.url.path
        # must NOT exempt "/api/state/module/1/resource/x/auth/me" just because it
        # ends in /auth/me -- the gate refuses it (403) before the handler runs.
        self._make_must_change_admin(db)
        token = self._login(client, "mustchange", "startpw")
        hdr = {"Authorization": f"Bearer {token}"}
        r = client.get("/api/state/module/1/resource/x/auth/me", headers=hdr)
        assert r.status_code == 403, r.text
