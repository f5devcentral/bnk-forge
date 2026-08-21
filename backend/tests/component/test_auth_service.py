"""
BC-001: Component tests for auth_service — DB-backed operations.

Tests create_user, authenticate_user, change_password, seed_admin_user,
and get_user_from_token against a real SQLite in-memory database.
Pure functions (hash, verify, token) are covered in unit tests.
"""

from unittest.mock import patch

import pytest

from core.config import settings
from core.errors import BadRequestError, ConflictError, UnauthorizedError
from services.auth_service import (
    authenticate_user,
    change_password,
    create_access_token,
    create_user,
    ensure_service_user,
    get_user_from_token,
    seed_admin_user,
    verify_password,
)

# ── create_user ──────────────────────────────────────────────────────


class TestCreateUser:
    def test_creates_user_with_defaults(self, db):
        user = create_user(db, "alice", "alice@test.com", "password123")
        assert user.username == "alice"
        assert user.email == "alice@test.com"
        assert user.role == "operator"  # default
        assert user.is_active is True
        assert user.must_change_password is False
        assert user.id is not None

    def test_creates_user_with_custom_role(self, db):
        user = create_user(db, "bob", "bob@test.com", "password123", role="admin")
        assert user.role == "admin"

    def test_creates_user_with_must_change_password(self, db):
        user = create_user(
            db, "charlie", "charlie@test.com", "password123",
            must_change_password=True,
        )
        assert user.must_change_password is True

    def test_password_is_hashed(self, db):
        user = create_user(db, "dave", "dave@test.com", "mysecret")
        assert user.hashed_password != "mysecret"
        assert verify_password("mysecret", user.hashed_password)

    def test_duplicate_username_raises_conflict(self, db):
        create_user(db, "unique_user", "first@test.com", "password123")
        with pytest.raises(ConflictError) as exc_info:
            create_user(db, "unique_user", "second@test.com", "password123")
        assert "already exists" in exc_info.value.message

    def test_duplicate_email_raises_conflict(self, db):
        create_user(db, "user_a", "shared@test.com", "password123")
        with pytest.raises(ConflictError) as exc_info:
            create_user(db, "user_b", "shared@test.com", "password123")
        assert "already exists" in exc_info.value.message

    def test_invalid_role_raises_bad_request(self, db):
        with pytest.raises(BadRequestError) as exc_info:
            create_user(db, "eve", "eve@test.com", "password123", role="superadmin")
        assert "Invalid role" in exc_info.value.message

    def test_valid_roles_accepted(self, db):
        for role in ("admin", "operator", "viewer"):
            user = create_user(db, f"user_{role}", f"{role}@test.com", "password123", role=role)
            assert user.role == role


# ── authenticate_user ────────────────────────────────────────────────


class TestAuthenticateUser:
    def test_valid_credentials(self, db):
        create_user(db, "auth_user", "auth@test.com", "correct-password")
        db.commit()
        user = authenticate_user(db, "auth_user", "correct-password")
        assert user.username == "auth_user"

    def test_wrong_password_raises(self, db):
        create_user(db, "auth_fail", "authfail@test.com", "right-password")
        db.commit()
        with pytest.raises(UnauthorizedError):
            authenticate_user(db, "auth_fail", "wrong-password")

    def test_nonexistent_user_raises(self, db):
        with pytest.raises(UnauthorizedError):
            authenticate_user(db, "nobody", "password")

    def test_login_by_email(self, db):
        create_user(db, "email_user", "login@test.com", "mypass")
        db.commit()
        user = authenticate_user(db, "login@test.com", "mypass")
        assert user.username == "email_user"

    def test_disabled_user_raises(self, db):
        user = create_user(db, "disabled_u", "disabled@test.com", "pass123")
        user.is_active = False
        db.commit()
        with pytest.raises(UnauthorizedError) as exc_info:
            authenticate_user(db, "disabled_u", "pass123")
        assert "disabled" in exc_info.value.message.lower()

    def test_updates_last_login(self, db):
        create_user(db, "login_ts", "logints@test.com", "pass123")
        db.commit()
        user = authenticate_user(db, "login_ts", "pass123")
        assert user.last_login_at is not None


# ── change_password ──────────────────────────────────────────────────


class TestChangePassword:
    def test_successful_change(self, db):
        user = create_user(db, "chg_pass", "chgpass@test.com", "oldpassword")
        db.commit()
        change_password(db, user, "oldpassword", "newpassword123")
        assert verify_password("newpassword123", user.hashed_password)
        assert user.must_change_password is False

    def test_wrong_current_password_raises(self, db):
        user = create_user(db, "chg_fail", "chgfail@test.com", "current")
        db.commit()
        with pytest.raises(BadRequestError, match="incorrect"):
            change_password(db, user, "wrong-current", "newpassword")

    def test_short_new_password_raises(self, db):
        user = create_user(db, "chg_short", "chgshort@test.com", "currentpass")
        db.commit()
        with pytest.raises(BadRequestError, match="8 characters"):
            change_password(db, user, "currentpass", "short")

    def test_clears_must_change_flag(self, db):
        user = create_user(
            db, "chg_flag", "chgflag@test.com", "temppass1",
            must_change_password=True,
        )
        db.commit()
        assert user.must_change_password is True
        change_password(db, user, "temppass1", "permanent-password")
        assert user.must_change_password is False


# ── get_user_from_token ──────────────────────────────────────────────


class TestGetUserFromToken:
    def test_valid_token_returns_user(self, db):
        create_user(db, "token_user", "token@test.com", "pass123")
        db.commit()
        token = create_access_token(data={"sub": "token_user"})
        user = get_user_from_token(db, token)
        assert user.username == "token_user"

    def test_nonexistent_user_raises(self, db):
        token = create_access_token(data={"sub": "ghost"})
        with pytest.raises(UnauthorizedError, match="not found"):
            get_user_from_token(db, token)

    def test_disabled_user_raises(self, db):
        user = create_user(db, "dis_token", "distoken@test.com", "pass")
        user.is_active = False
        db.commit()
        token = create_access_token(data={"sub": "dis_token"})
        with pytest.raises(UnauthorizedError, match="disabled"):
            get_user_from_token(db, token)


# ── seed_admin_user ──────────────────────────────────────────────────


class TestSeedAdminUser:
    def test_seeds_when_no_users(self, db):
        admin = seed_admin_user(db)
        assert admin is not None
        assert admin.username == "admin"
        assert admin.role == "admin"
        assert admin.must_change_password is True

    def test_returns_none_when_users_exist(self, db):
        create_user(db, "existing", "existing@test.com", "pass")
        db.commit()
        result = seed_admin_user(db)
        assert result is None

    def test_seeded_admin_can_login(self, db):
        seed_admin_user(db)
        user = authenticate_user(db, "admin", settings.DEFAULT_ADMIN_PASSWORD)
        assert user.username == "admin"


# ── ensure_service_user ──────────────────────────────────────────────


class TestEnsureServiceUser:
    def test_creates_service_user_when_absent(self, db):
        ensure_service_user(db, username="mcp", password="secret")
        user = authenticate_user(db, "mcp", "secret")
        assert user.username == "mcp"
        assert user.role == "admin"
        assert user.must_change_password is False

    def test_reconciles_password_when_user_exists(self, db):
        ensure_service_user(db, username="mcp", password="initial")
        # Rotate password
        ensure_service_user(db, username="mcp", password="rotated")
        # Old password no longer works
        from core.errors import UnauthorizedError as UnauthError
        with pytest.raises(UnauthError):
            authenticate_user(db, "mcp", "initial")
        # New password works
        user = authenticate_user(db, "mcp", "rotated")
        assert user.username == "mcp"

    def test_idempotent_create(self, db):
        ensure_service_user(db, username="mcp", password="pw")
        ensure_service_user(db, username="mcp", password="pw")
        from models import User
        count = db.query(User).filter(User.username == "mcp").count()
        assert count == 1

    def test_refuses_to_reconcile_the_human_admin(self, db):
        # #188 (bonnyr-f5): MCP_USERNAME still 'admin' on an old .env would point
        # ensure_service_user at the human admin row and take it over (rewrite
        # hash, clear must_change). Refuse, and leave the admin row untouched.
        from services.auth_service import create_user, verify_password
        create_user(db, "admin", "admin@bnk-forge.local", "human-admin-pw",
                    role="admin", must_change_password=True)
        db.commit()
        with pytest.raises(ValueError, match="reserved human username"):
            ensure_service_user(db, username="admin", password="mcp-secret")
        from models import User
        admin = db.query(User).filter(User.username == "admin").first()
        assert admin.must_change_password is True            # gate not cleared
        assert verify_password("human-admin-pw", admin.hashed_password)  # hash intact

    def test_disable_stale_service_user_deactivates_mcp(self, db):
        # #188: upgrade with MCP_SERVICE_PASSWORD unset must not leave the old
        # mcp/'mcp-service-changeme' account authenticating.
        from services.auth_service import disable_stale_service_user, ensure_service_user
        ensure_service_user(db, username="mcp", password="mcp-service-changeme")  # sets is_service_account
        disable_stale_service_user(db, "mcp")
        from models import User
        assert db.query(User).filter(User.username == "mcp").first().is_active is False

    def test_refuses_to_reconcile_a_non_reserved_human(self, db):
        # bonnyr-f5 #188 r2: the guard was a one-name denylist. Point the service
        # username at ANY existing human row (here 'operator') and the reconcile
        # would take it over. Provenance (is_service_account) refuses it.
        from services.auth_service import create_user, verify_password
        create_user(db, "operator", "operator@bnk-forge.local", "human-op-pw",
                    role="operator", must_change_password=False)
        db.commit()
        with pytest.raises(ValueError, match="not a service account"):
            ensure_service_user(db, username="operator", password="mcp-secret")
        from models import User
        op = db.query(User).filter(User.username == "operator").first()
        assert op.role == "operator"                         # NOT promoted to admin
        assert verify_password("human-op-pw", op.hashed_password)  # password intact

    def test_disable_stale_never_touches_admin(self, db):
        from services.auth_service import create_user, disable_stale_service_user
        create_user(db, "admin", "admin@bnk-forge.local", "pw", role="admin")
        db.commit()
        disable_stale_service_user(db, "admin")  # reserved -> no-op
        from models import User
        assert db.query(User).filter(User.username == "admin").first().is_active is True
