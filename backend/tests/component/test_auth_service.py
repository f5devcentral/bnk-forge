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
    @pytest.fixture(autouse=True)
    def _isolate_keys_dir(self, monkeypatch, tmp_path):
        # seed_admin_user may generate + persist a password to KEYS_DIR; keep it
        # out of the working tree (default is /app/keys) for every test here.
        monkeypatch.setenv("KEYS_DIR", str(tmp_path))

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

    def test_seeded_admin_can_login_with_explicit_password(self, db, monkeypatch):
        # When DEFAULT_ADMIN_PASSWORD is set, the seed uses it.
        monkeypatch.setattr(settings, "DEFAULT_ADMIN_PASSWORD", "explicit-admin-pw")
        seed_admin_user(db)
        user = authenticate_user(db, "admin", "explicit-admin-pw")
        assert user.username == "admin"

    def test_seeded_admin_generates_random_password_when_unset(self, db, monkeypatch, tmp_path):
        # #184: with DEFAULT_ADMIN_PASSWORD unset, the seed must NOT use a known
        # default -- it generates a random one, so the published "changeme"
        # never authenticates. KEYS_DIR -> tmp so the generated-password file
        # doesn't land in the working tree.
        monkeypatch.setattr(settings, "DEFAULT_ADMIN_PASSWORD", None)
        admin = seed_admin_user(db)
        assert (tmp_path / "initial_admin_password").exists()
        assert admin is not None
        assert admin.must_change_password is True
        with pytest.raises(UnauthorizedError):
            authenticate_user(db, "admin", "changeme")

    def test_generated_password_file_is_mode_0600(self, db, monkeypatch, tmp_path):
        # #186 (bonnyr-f5): the commit is titled "harden the password-file mode"
        # but only .exists() was asserted. The plaintext credential must be 0600,
        # never group/world-readable.
        import os
        import stat
        monkeypatch.setattr(settings, "DEFAULT_ADMIN_PASSWORD", None)
        seed_admin_user(db)
        pw = tmp_path / "initial_admin_password"
        mode = stat.S_IMODE(os.stat(pw).st_mode)
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"

    def test_rotates_existing_admin_still_on_a_known_default(self, db, tmp_path):
        # #186 (bonnyr-f5): an upgrade left admin/'changeme' with
        # must_change_password=False -- the seed logic never re-runs for it. On
        # boot, seed_admin_user (users exist -> None) must INVALIDATE the
        # published default, not merely flag it: /api/auth/change-password is
        # exempt from the gate and verifies against the stored hash, so a flag
        # alone leaves 'changeme' usable to rotate the account. The hash must be
        # overwritten and a fresh secret surfaced like a fresh install.
        from models.system import User
        create_user(db, "admin", "admin@bnk-forge.local", "changeme",
                    role="admin", must_change_password=False)
        db.commit()
        assert seed_admin_user(db) is None
        admin = db.query(User).filter(User.username == "admin").first()
        assert admin.must_change_password is True
        # The published default no longer authenticates -- capability removed.
        with pytest.raises(UnauthorizedError):
            authenticate_user(db, "admin", "changeme")
        # A fresh generated secret was surfaced exactly like a fresh install.
        pw_file = tmp_path / "initial_admin_password"
        assert pw_file.exists()
        new_pw = pw_file.read_text().strip()
        assert new_pw and new_pw != "changeme"
        assert authenticate_user(db, "admin", new_pw).username == "admin"

    def test_rotation_is_idempotent_across_boots(self, db, tmp_path):
        # #186: after the one-time overwrite the stored password is the generated
        # secret, so a second boot's verify("changeme", ...) is False and the
        # account is left untouched (no re-rotation, no new file churn).
        from models.system import User
        create_user(db, "admin", "admin@bnk-forge.local", "changeme",
                    role="admin", must_change_password=False)
        db.commit()
        seed_admin_user(db)
        first_pw = (tmp_path / "initial_admin_password").read_text().strip()
        seed_admin_user(db)  # second boot
        admin = db.query(User).filter(User.username == "admin").first()
        # Still the same generated secret from the first rotation.
        assert authenticate_user(db, "admin", first_pw).username == "admin"
        assert admin.must_change_password is True

    def test_does_not_touch_an_admin_with_a_real_password(self, db):
        from models.system import User
        create_user(db, "admin", "admin@bnk-forge.local", "a-Strong-Real-Pw-1",
                    role="admin", must_change_password=False)
        db.commit()
        seed_admin_user(db)
        admin = db.query(User).filter(User.username == "admin").first()
        assert admin.must_change_password is False  # not a known default → untouched

    def test_rotation_honors_default_admin_password_when_set(self, db, monkeypatch, tmp_path):
        # #186 (bonnyr-f5 r4) provenance: when DEFAULT_ADMIN_PASSWORD is set
        # (Helm wires it from the admin-password Secret), the upgrade rotation
        # must rotate TO that value so the documented source-of-truth (the
        # Secret / env) authenticates -- and it must NOT write a keys-file
        # (nothing was generated), so the docs' Helm "read the Secret"
        # instruction stays correct on upgrade.
        from models.system import User
        monkeypatch.setattr(settings, "DEFAULT_ADMIN_PASSWORD", "chart-supplied-secret-x")
        create_user(db, "admin", "admin@bnk-forge.local", "changeme",
                    role="admin", must_change_password=False)
        db.commit()
        assert seed_admin_user(db) is None
        admin = db.query(User).filter(User.username == "admin").first()
        assert admin.must_change_password is True
        with pytest.raises(UnauthorizedError):
            authenticate_user(db, "admin", "changeme")  # published default gone
        # The configured value now authenticates (Secret == source of truth).
        assert authenticate_user(db, "admin", "chart-supplied-secret-x").username == "admin"
        # No keys-file written: nothing was generated.
        assert not (tmp_path / "initial_admin_password").exists()

    def test_rotation_refuses_to_rotate_to_a_published_default(self, db, monkeypatch, tmp_path):
        # #186: DEFAULT_ADMIN_PASSWORD=changeme must NOT be used as the rotation
        # target (that would re-publish the hole) -- fall through to a generated
        # keys-file secret instead.
        from models.system import User
        monkeypatch.setattr(settings, "DEFAULT_ADMIN_PASSWORD", "changeme")
        create_user(db, "admin", "admin@bnk-forge.local", "changeme",
                    role="admin", must_change_password=False)
        db.commit()
        seed_admin_user(db)
        admin = db.query(User).filter(User.username == "admin").first()
        with pytest.raises(UnauthorizedError):
            authenticate_user(db, "admin", "changeme")
        pw_file = tmp_path / "initial_admin_password"
        assert pw_file.exists()
        assert authenticate_user(db, "admin", pw_file.read_text().strip()).username == "admin"


# ── ensure_service_user ──────────────────────────────────────────────


class TestEnsureServiceUser:
    @pytest.fixture(autouse=True)
    def _isolate_keys_dir(self, monkeypatch, tmp_path):
        # ensure_service_user may generate + persist a secret to KEYS_DIR; keep it
        # out of the working tree (default /app/keys). Persist now fails closed on
        # an unwritable dir, so a writable KEYS_DIR is required for these tests.
        monkeypatch.setenv("KEYS_DIR", str(tmp_path))

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
        disable_stale_service_user(db)
        from models import User
        assert db.query(User).filter(User.username == "mcp").first().is_active is False

    def test_disable_stale_is_keyed_on_provenance_not_configured_username(self, db):
        # bonnyr-f5 #188 round 4 (INV-11): on the dist/IBM upgrade path
        # MCP_SERVICE_USERNAME resolves from a legacy .env to 'admin', so a
        # name-keyed disable early-returned and left the legacy 'mcp' service row
        # (mcp-service-changeme, role=admin) still authenticating. The disable must
        # deactivate the service account by provenance regardless of the configured
        # name, while never touching the human admin.
        from models import User
        from services.auth_service import (
            authenticate_user,
            create_user,
            disable_stale_service_user,
            ensure_service_user,
        )
        ensure_service_user(db, username="mcp", password="mcp-service-changeme")  # legacy service row
        create_user(db, "admin", "admin@bnk-forge.local", "human-admin-pw", role="admin")
        db.commit()
        disable_stale_service_user(db)  # startup no longer passes a username at all
        assert db.query(User).filter(User.username == "mcp").first().is_active is False
        assert db.query(User).filter(User.username == "admin").first().is_active is True
        with pytest.raises(UnauthorizedError):
            authenticate_user(db, "mcp", "mcp-service-changeme")  # default no longer works
        # Human admin login is untouched.
        assert authenticate_user(db, "admin", "human-admin-pw").username == "admin"

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
        # A human admin carries is_service_account=False, so provenance-keyed
        # disable leaves it active even though its name is 'admin'.
        from services.auth_service import create_user, disable_stale_service_user
        create_user(db, "admin", "admin@bnk-forge.local", "pw", role="admin")
        db.commit()
        disable_stale_service_user(db)  # provenance-keyed -> human admin untouched
        from models import User
        assert db.query(User).filter(User.username == "admin").first().is_active is True

    def test_reconcile_reactivates_a_disabled_stale_service_account(self, db):
        # bonnyr-f5 #188 BLOCKER 3: disable_stale_service_user deactivates the mcp
        # row when no real password is set; setting a real MCP_SERVICE_PASSWORD must
        # then re-seed AND re-activate it (as its docstring promises). Without the
        # reactivation the account stays is_active=False and every MCP login fails
        # with "Account is disabled" despite a correct password.
        from models import User
        from services.auth_service import disable_stale_service_user, ensure_service_user
        ensure_service_user(db, username="mcp", password="mcp-service-changeme")
        disable_stale_service_user(db)
        assert db.query(User).filter(User.username == "mcp").first().is_active is False
        # Operator now configures a real secret -> reconcile must revive the account.
        ensure_service_user(db, username="mcp", password="a-real-strong-secret")
        mcp = db.query(User).filter(User.username == "mcp").first()
        assert mcp.is_active is True                       # re-activated
        assert mcp.is_service_account is True              # provenance preserved
        # And the new secret authenticates while the old default does not.
        assert authenticate_user(db, "mcp", "a-real-strong-secret").username == "mcp"
        with pytest.raises(UnauthorizedError):
            authenticate_user(db, "mcp", "mcp-service-changeme")
    # ── #186 BLOCKER 1: the published mcp default must never authenticate ──

    @pytest.mark.parametrize("published_default", ["mcp-service-changeme", "changeme"])
    def test_published_default_seed_cannot_authenticate(self, db, published_default):
        """Seeding the mcp account with a shipped default must NOT store that
        value — the published credential can never authenticate."""
        from core.errors import UnauthorizedError as UnauthError
        ensure_service_user(db, username="mcp", password=published_default)
        from models import User
        assert db.query(User).filter(User.username == "mcp").count() == 1
        with pytest.raises(UnauthError):
            authenticate_user(db, "mcp", published_default)

    def test_none_password_seeds_generated_secret(self, db):
        """MCP_SERVICE_PASSWORD unset (None) still creates the row, but with a
        generated secret — neither None nor the published default authenticates."""
        from core.errors import UnauthorizedError as UnauthError
        ensure_service_user(db, username="mcp", password=None)
        from models import User
        assert db.query(User).filter(User.username == "mcp").count() == 1
        with pytest.raises(UnauthError):
            authenticate_user(db, "mcp", "mcp-service-changeme")

    def test_upgrade_rotates_existing_published_default(self, db):
        """An account carried over from a pre-fix install still holding the
        published default is OVERWRITTEN with a random secret on next boot."""
        from core.errors import UnauthorizedError as UnauthError
        from services.auth_service import create_user, hash_password
        # Simulate the pre-fix seeded row: hash of the published default.
        user = create_user(db, username="mcp", email="mcp@bnk-forge.local",
                           password="mcp-service-changeme", role="admin")
        db.commit()
        assert authenticate_user(db, "mcp", "mcp-service-changeme")  # live before fix
        ensure_service_user(db, username="mcp", password=None)  # unset env on upgrade boot
        with pytest.raises(UnauthError):
            authenticate_user(db, "mcp", "mcp-service-changeme")  # dead after fix

    def test_generated_secret_not_churned_on_reboot(self, db):
        """With no operator password, a row already holding a generated (non-
        default) secret is left untouched — reboots don't rotate it, so the MCP
        client's retrieved secret keeps working."""
        from models import User
        ensure_service_user(db, username="mcp", password=None)
        first_hash = db.query(User).filter(User.username == "mcp").first().hashed_password
        ensure_service_user(db, username="mcp", password=None)
        second_hash = db.query(User).filter(User.username == "mcp").first().hashed_password
        assert first_hash == second_hash

    def test_operator_password_still_reconciles(self, db):
        """A genuine operator-set password is honored (MCP stays usable when the
        operator configures MCP_SERVICE_PASSWORD)."""
        ensure_service_user(db, username="mcp", password="a-real-operator-secret")
        user = authenticate_user(db, "mcp", "a-real-operator-secret")
        assert user.username == "mcp"
        assert user.must_change_password is False

    def test_reserved_human_username_is_refused(self, db):
        """#186 BLOCKER 3 (bonnyr-f5): a service account may not adopt a reserved
        human identity such as `admin`. The call raises and never touches the row."""
        import pytest
        with pytest.raises(ValueError, match="reserved human username"):
            ensure_service_user(db, username="admin", password="mcp-service-secret")

    def test_reserved_username_does_not_rewrite_human_admin(self, db):
        """The attack bonnyr reproduced: pointing the mcp reconcile at `admin`
        would clear must_change and grant the mcp secret admin access. The guard
        must leave the real admin row (its hash + must_change gate) intact."""
        import pytest

        from models import User
        create_user(db, "admin", "admin@test.com", "human-admin-pw",
                    role="admin", must_change_password=True)
        db.commit()
        with pytest.raises(ValueError):
            ensure_service_user(db, username="admin", password="mcp-secret")
        db.rollback()
        admin = db.query(User).filter(User.username == "admin").first()
        # Human admin credential + gate survive; the mcp secret never authenticates as admin.
        assert admin.must_change_password is True
        authenticate_user(db, "admin", "human-admin-pw")  # still the human's password
        with pytest.raises(UnauthorizedError):
            authenticate_user(db, "admin", "mcp-secret")


class TestTokenUserState:
    """#184: the WS gate helper -- resolve the User row and fail CLOSED.

    Returns the User on success, None on any resolution failure; the WS
    validators refuse on None OR must_change_password.
    """

    def test_resolves_must_change_user(self, db):
        from services.auth_service import token_user_state
        create_user(db, "wsmust", "wsmust@test.com", "pw", role="admin", must_change_password=True)
        db.commit()
        token = create_access_token(data={"sub": "wsmust", "role": "admin"})
        user = token_user_state(token)
        assert user is not None and user.must_change_password is True

    def test_resolves_normal_user(self, db):
        from services.auth_service import token_user_state
        create_user(db, "wsok", "wsok@test.com", "pw", role="admin", must_change_password=False)
        db.commit()
        token = create_access_token(data={"sub": "wsok", "role": "admin"})
        user = token_user_state(token)
        assert user is not None and user.must_change_password is False

    def test_none_on_garbage_token(self):
        from services.auth_service import token_user_state
        assert token_user_state("not-a-token") is None

    def test_none_for_deactivated_account(self, db):
        # #184 review: a disabled account must fail closed on WS, matching
        # get_current_user. get_user_from_token raises "Account is disabled",
        # which token_user_state turns into None (-> WS refuses).
        u = create_user(db, "wsdisabled", "wsdisabled@test.com", "pw", role="admin")
        u.is_active = False
        db.commit()
        from services.auth_service import token_user_state
        token = create_access_token(data={"sub": "wsdisabled", "role": "admin"})
        assert token_user_state(token) is None

    def test_none_for_deleted_account(self, db):
        from services.auth_service import token_user_state
        token = create_access_token(data={"sub": "ghost", "role": "admin"})
        assert token_user_state(token) is None


class TestUnwritableKeysDirNeverLeaksPlaintext:
    """#186 (bonnyr-f5): the docs promise "the plaintext is never logged".

    On an UNWRITABLE /app/keys the old code fell back to LOGGING the generated
    plaintext (a real secret-into-logs leak). Every generated-credential path
    must now fail closed (raise GeneratedCredentialPersistError) WITHOUT the
    plaintext ever reaching a log record or the exception message.

    Each test patches secrets.token_urlsafe to a sentinel so the assertion is
    exact: the sentinel must appear in NO log message and NOT in str(exc).
    """

    SENTINEL = "SENTINEL-do-not-log-this-secret-42"

    @pytest.fixture(autouse=True)
    def _sentinel_secret(self, monkeypatch):
        # Make every generated secret a known sentinel we can search for.
        monkeypatch.setattr(
            "services.auth_service.secrets.token_urlsafe", lambda *_a, **_k: self.SENTINEL
        )

    def _unwritable_keys(self, monkeypatch, tmp_path):
        # A FILE used as a directory -> os.makedirs raises NotADirectoryError
        # (an OSError), simulating an unwritable /app/keys mount.
        blocker = tmp_path / "blocker"
        blocker.write_text("x")
        monkeypatch.setenv("KEYS_DIR", str(blocker / "keys"))

    def _assert_no_leak(self, caplog):
        for rec in caplog.records:
            assert self.SENTINEL not in rec.getMessage(), (
                f"plaintext leaked into logs: {rec.getMessage()!r}"
            )

    def test_seed_generated_fails_closed_no_leak(self, db, monkeypatch, tmp_path, caplog):
        import logging

        from services.auth_service import GeneratedCredentialPersistError
        monkeypatch.setattr(settings, "DEFAULT_ADMIN_PASSWORD", None)
        self._unwritable_keys(monkeypatch, tmp_path)
        caplog.set_level(logging.DEBUG)
        with pytest.raises(GeneratedCredentialPersistError) as ei:
            seed_admin_user(db)
        assert self.SENTINEL not in str(ei.value)
        self._assert_no_leak(caplog)
        # Fail closed: no admin row was committed, so the next boot retries.
        from models.system import User
        assert db.query(User).filter(User.username == "admin").first() is None

    def test_rotate_admin_fails_closed_no_leak(self, db, monkeypatch, tmp_path, caplog):
        import logging

        from models.system import User
        from services.auth_service import GeneratedCredentialPersistError
        create_user(db, "admin", "admin@bnk-forge.local", "changeme",
                    role="admin", must_change_password=False)
        db.commit()
        self._unwritable_keys(monkeypatch, tmp_path)
        caplog.set_level(logging.DEBUG)
        with pytest.raises(GeneratedCredentialPersistError) as ei:
            seed_admin_user(db)
        assert self.SENTINEL not in str(ei.value)
        self._assert_no_leak(caplog)
        # Fail closed: the published-default hash is left untouched (still
        # 'changeme') so the next boot retries the rotation cleanly rather than
        # stranding an admin whose generated password nobody can read. (The
        # rotate path raises BEFORE mutating the row, so nothing to roll back.)
        admin = db.query(User).filter(User.username == "admin").first()
        assert verify_password("changeme", str(admin.hashed_password))

    def test_service_seed_fails_closed_no_leak(self, db, monkeypatch, tmp_path, caplog):
        import logging

        from models import User
        from services.auth_service import GeneratedCredentialPersistError
        self._unwritable_keys(monkeypatch, tmp_path)
        caplog.set_level(logging.DEBUG)
        with pytest.raises(GeneratedCredentialPersistError) as ei:
            ensure_service_user(db, username="mcp", password=None)
        assert self.SENTINEL not in str(ei.value)
        self._assert_no_leak(caplog)
        assert db.query(User).filter(User.username == "mcp").first() is None

    def test_service_rotate_fails_closed_no_leak(self, db, monkeypatch, tmp_path, caplog):
        import logging

        from models import User
        from services.auth_service import (
            GeneratedCredentialPersistError,
            hash_password,
        )
        from services.auth_service import (
            create_user as _cu,
        )
        u = _cu(db, "mcp", "mcp@bnk-forge.local", "mcp-service-changeme",
                role="admin", must_change_password=False)
        u.hashed_password = hash_password("mcp-service-changeme")
        db.commit()
        self._unwritable_keys(monkeypatch, tmp_path)
        caplog.set_level(logging.DEBUG)
        with pytest.raises(GeneratedCredentialPersistError) as ei:
            ensure_service_user(db, username="mcp", password=None)
        assert self.SENTINEL not in str(ei.value)
        self._assert_no_leak(caplog)
        # Published default hash untouched -> next boot retries the rotation.
        mcp = db.query(User).filter(User.username == "mcp").first()
        assert verify_password("mcp-service-changeme", str(mcp.hashed_password))
