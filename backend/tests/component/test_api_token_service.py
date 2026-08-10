"""Component tests for ApiTokenService.

Covers create / list / verify / revoke against an in-memory test DB.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.errors import BadRequestError, NotFoundError, UnauthorizedError
from services.api_token_service import ApiTokenService, clamp_role
from services.auth_service import create_user


@pytest.fixture
def operator_user(db):
    return create_user(db, "tok-op", "tok-op@test.com", "password123", role="operator")


@pytest.fixture
def admin_user(db):
    return create_user(db, "tok-admin", "tok-admin@test.com", "password123", role="admin")


class TestCreate:
    def test_creates_token_with_plaintext_returned_once(self, db, operator_user):
        svc = ApiTokenService(db)
        issued = svc.create(user=operator_user, name="ci-pipeline")
        assert issued.plaintext.startswith("bnk_")
        assert issued.id > 0
        assert issued.role == "operator"
        # Plaintext is not stored on the row.
        listed = svc.list_for_user(operator_user)
        assert len(listed) == 1
        assert "token" not in listed[0]
        assert listed[0]["token_prefix"].startswith("bnk_")

    def test_rejects_blank_name(self, db, operator_user):
        with pytest.raises(BadRequestError, match="name is required"):
            ApiTokenService(db).create(user=operator_user, name="")

    def test_rejects_role_escalation(self, db, operator_user):
        with pytest.raises(BadRequestError, match="role escalation|escalation|Cannot issue"):
            ApiTokenService(db).create(user=operator_user, name="x", role="admin")

    def test_admin_can_issue_admin_token(self, db, admin_user):
        issued = ApiTokenService(db).create(user=admin_user, name="ops", role="admin")
        assert issued.role == "admin"

    def test_max_role_ceilings_below_account_role(self, db, admin_user):
        # An admin acting through a viewer-scoped token cannot mint an admin token.
        with pytest.raises(BadRequestError, match="Cannot issue"):
            ApiTokenService(db).create(
                user=admin_user, name="escalate", role="admin", max_role="viewer"
            )

    def test_max_role_is_the_default_role_for_the_new_token(self, db, admin_user):
        issued = ApiTokenService(db).create(user=admin_user, name="ci", max_role="viewer")
        assert issued.role == "viewer"


class TestClampRole:
    """The role in force = min(token role, owner's current account role)."""

    def test_token_role_downscopes_below_account_role(self):
        assert clamp_role("viewer", "admin") == "viewer"

    def test_token_role_cannot_exceed_account_role(self):
        # Owner demoted after the token was issued — the token narrows with them.
        assert clamp_role("admin", "viewer") == "viewer"

    def test_equal_roles_pass_through(self):
        assert clamp_role("operator", "operator") == "operator"

    def test_missing_or_bogus_token_role_falls_back_to_account_role(self):
        assert clamp_role(None, "operator") == "operator"
        assert clamp_role("superuser", "operator") == "operator"

    def test_expiry_in_days_translates_to_future_datetime(self, db, operator_user):
        issued = ApiTokenService(db).create(user=operator_user, name="x", expires_in_days=30)
        assert issued.expires_at is not None
        # SQLite test DB strips timezone info; normalize both sides.
        exp = issued.expires_at if issued.expires_at.tzinfo else issued.expires_at.replace(tzinfo=UTC)
        delta = exp - datetime.now(UTC)
        # 30 days, ± a few seconds for clock + flush latency
        assert 29 * 86400 < delta.total_seconds() < 31 * 86400

    def test_rejects_invalid_expiry(self, db, operator_user):
        with pytest.raises(BadRequestError):
            ApiTokenService(db).create(user=operator_user, name="x", expires_in_days=0)
        with pytest.raises(BadRequestError):
            ApiTokenService(db).create(user=operator_user, name="x", expires_in_days=10000)


class TestVerify:
    def test_verify_returns_user_and_updates_last_used(self, db, operator_user):
        svc = ApiTokenService(db)
        issued = svc.create(user=operator_user, name="ci")
        user, token = svc.verify(issued.plaintext)
        assert user.id == operator_user.id
        assert token.last_used_at is not None

    def test_verify_rejects_unknown_token(self, db, operator_user):
        with pytest.raises(UnauthorizedError):
            ApiTokenService(db).verify("bnk_doesnotexist")

    def test_verify_rejects_bad_format(self, db):
        with pytest.raises(UnauthorizedError, match="format"):
            ApiTokenService(db).verify("not-a-token")

    def test_verify_rejects_expired_token(self, db, operator_user):
        svc = ApiTokenService(db)
        issued = svc.create(user=operator_user, name="ci", expires_in_days=1)
        # Backdate the row so it's expired
        from models import ApiToken
        row = db.query(ApiToken).filter(ApiToken.id == issued.id).first()
        row.expires_at = datetime.now(UTC) - timedelta(days=2)
        db.flush()
        with pytest.raises(UnauthorizedError, match="expired"):
            svc.verify(issued.plaintext)

    def test_verify_rejects_inactive_user(self, db, operator_user):
        svc = ApiTokenService(db)
        issued = svc.create(user=operator_user, name="ci")
        operator_user.is_active = False
        db.flush()
        with pytest.raises(UnauthorizedError, match="inactive"):
            svc.verify(issued.plaintext)


class TestRevoke:
    def test_revoke_removes_the_row(self, db, operator_user):
        svc = ApiTokenService(db)
        issued = svc.create(user=operator_user, name="ci")
        svc.revoke(user=operator_user, token_id=issued.id)
        assert svc.list_for_user(operator_user) == []
        with pytest.raises(UnauthorizedError):
            svc.verify(issued.plaintext)

    def test_revoke_other_users_token_404s(self, db, operator_user, admin_user):
        svc = ApiTokenService(db)
        issued = svc.create(user=admin_user, name="theirs")
        with pytest.raises(NotFoundError):
            svc.revoke(user=operator_user, token_id=issued.id)
