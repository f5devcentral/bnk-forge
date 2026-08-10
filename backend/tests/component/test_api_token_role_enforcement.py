"""API-token role downscoping must be enforced on every request.

`ApiToken.role` is a *downscope*: an admin issuing a `--role viewer` CI token
must get a token that can only read. Authorization used to read `user.role` (the
account role), so the token's role was decorative — a viewer-scoped token on an
admin account still had full admin. These tests pin the real behavior end-to-end
through the HTTP stack, which is the only place the bug was observable.
"""

from __future__ import annotations

import pytest

from services.api_token_service import ApiTokenService
from services.auth_service import create_user


@pytest.fixture
def admin_account(db):
    return create_user(db, "tok-admin", "tok-admin@test.com", "password123", role="admin")


def _bearer(db, user, role: str | None) -> dict:
    issued = ApiTokenService(db).create(user=user, name=f"ci-{role or 'default'}", role=role)
    db.commit()
    return {"Authorization": f"Bearer {issued.plaintext}"}


class TestDownscopedTokenIsEnforced:
    def test_viewer_token_on_admin_account_is_refused_an_admin_route(
        self, client, db, admin_account
    ):
        headers = _bearer(db, admin_account, "viewer")
        # /api/auth/users is admin-only.
        resp = client.get("/api/auth/users", headers=headers)
        assert resp.status_code == 403, resp.text

    def test_viewer_token_on_admin_account_can_still_read(self, client, db, admin_account):
        headers = _bearer(db, admin_account, "viewer")
        resp = client.get("/api/projects", headers=headers)
        assert resp.status_code == 200, resp.text

    def test_admin_token_on_admin_account_keeps_admin(self, client, db, admin_account):
        headers = _bearer(db, admin_account, "admin")
        resp = client.get("/api/auth/users", headers=headers)
        assert resp.status_code == 200, resp.text

    def test_me_reports_the_role_in_force_not_the_account_role(
        self, client, db, admin_account
    ):
        headers = _bearer(db, admin_account, "viewer")
        resp = client.get("/api/auth/me", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["user"]["role"] == "viewer"

    def test_downscoped_token_cannot_mint_a_more_privileged_token(
        self, client, db, admin_account
    ):
        """Otherwise the downscope is a speed bump: mint an admin token and carry on."""
        headers = _bearer(db, admin_account, "operator")
        resp = client.post(
            "/api/auth/tokens",
            headers=headers,
            json={"name": "escalate", "role": "admin"},
        )
        assert resp.status_code == 400, resp.text

    def test_downscoped_token_issues_at_its_own_role_by_default(
        self, client, db, admin_account
    ):
        headers = _bearer(db, admin_account, "operator")
        resp = client.post("/api/auth/tokens", headers=headers, json={"name": "child"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["role"] == "operator"

    def test_token_role_does_not_persist_onto_the_user_row(self, client, db, admin_account):
        """The request-scoped role must never be written back to the account."""
        headers = _bearer(db, admin_account, "viewer")
        client.get("/api/projects", headers=headers)
        db.refresh(admin_account)
        assert admin_account.role == "admin"


class TestMiddlewareVerifiesApiTokens:
    """AuthMiddleware runs before any route dependency and used to JWT-decode
    every bearer token — which rejected valid API tokens outright. It now
    verifies them against the DB, which it MUST do itself: a number of /api
    routes carry no auth dependency and are gated by this middleware alone.
    """

    # No auth dependency of its own — reachable only through the middleware gate.
    UNGUARDED_ROUTE = "/api/system/process-metrics"

    def test_valid_api_token_passes_the_middleware(self, client, db, admin_account):
        headers = _bearer(db, admin_account, "admin")
        resp = client.get(self.UNGUARDED_ROUTE, headers=headers)
        assert resp.status_code != 401, resp.text

    def test_bogus_api_token_is_rejected(self, client, db, admin_account):
        headers = {"Authorization": "Bearer bnk_notarealtokenatall0000000000000"}
        resp = client.get(self.UNGUARDED_ROUTE, headers=headers)
        assert resp.status_code == 401, resp.text

    def test_revoked_api_token_is_rejected(self, client, db, admin_account):
        svc = ApiTokenService(db)
        issued = svc.create(user=admin_account, name="ci", role="admin")
        db.commit()
        headers = {"Authorization": f"Bearer {issued.plaintext}"}
        assert client.get(self.UNGUARDED_ROUTE, headers=headers).status_code != 401

        svc.revoke(user=admin_account, token_id=issued.id)
        db.commit()
        assert client.get(self.UNGUARDED_ROUTE, headers=headers).status_code == 401
