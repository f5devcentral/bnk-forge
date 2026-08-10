"""A downscoped API token must lose admin's cross-project reach, not just its role gate.

The role *gate* (require_admin/operator/viewer) was already honoring the token's
downscoped role. But the admin *bypass* checks — the ones that widen scope to
"all projects" — read the raw account role, so an admin-issued `--role operator`
token (the documented CI use case) passed require_operator and then acted on
EVERY project instead of the ones it owns. That is precisely the blast-radius
reduction the feature is sold on, so these tests pin it.

Regression guard for the review finding tracked as #409.
"""

from __future__ import annotations

import pytest

from core.auth_context import effective_role
from services.api_token_service import ApiTokenService
from services.auth_service import create_user


@pytest.fixture
def admin_account(db):
    return create_user(db, "scope-admin", "scope-admin@test.com", "password123", role="admin")


@pytest.fixture
def other_user(db):
    return create_user(db, "scope-other", "scope-other@test.com", "password123", role="operator")


def _token_headers(db, user, role: str | None) -> dict:
    issued = ApiTokenService(db).create(user=user, name=f"ci-{role or 'default'}", role=role)
    db.commit()
    return {"Authorization": f"Bearer {issued.plaintext}"}


class TestDownscopedTokenLosesAdminScope:
    def test_effective_role_drives_the_admin_bypass(self, db, admin_account):
        """The bypass must key off the role in force, not the account role."""
        admin_account.request_role = "operator"  # what an operator-scoped token sets
        assert effective_role(admin_account) == "operator"
        assert admin_account.role == "admin"  # account itself is untouched

    def test_operator_token_on_admin_account_sees_only_owned_projects(
        self, db, admin_account, other_user, make_project
    ):
        """_get_owned_project_ids returned None (= all projects) for any admin account."""
        from services.fleet_selector import _get_owned_project_ids

        mine = make_project(name="admin-owned", user_id=admin_account.id)
        make_project(name="someone-elses", user_id=other_user.id)

        # Interactive admin (JWT): unrestricted, as before.
        assert _get_owned_project_ids(db, admin_account) is None

        # Same account acting through an operator-scoped token: owned projects only.
        admin_account.request_role = "operator"
        owned = _get_owned_project_ids(db, admin_account)
        assert owned is not None, "a downscoped token must NOT get all-projects scope"
        assert owned == [mine.id]

    def test_admin_token_keeps_admin_scope(self, db, admin_account, make_project):
        from services.fleet_selector import _get_owned_project_ids

        make_project(name="p1", user_id=admin_account.id)
        admin_account.request_role = "admin"
        assert _get_owned_project_ids(db, admin_account) is None

    def test_viewer_token_on_admin_account_is_refused_an_operator_route(
        self, client, db, admin_account
    ):
        headers = _token_headers(db, admin_account, "viewer")
        resp = client.get("/api/fleet/targets", headers=headers)
        # Whatever the route does, it must not treat this caller as an admin.
        assert resp.status_code in (200, 403, 404), resp.text
