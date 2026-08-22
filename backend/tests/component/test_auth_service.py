"""
BC-001: Component tests for auth_service — DB-backed operations.

Tests create_user, authenticate_user, change_password, seed_admin_user,
and get_user_from_token against a real SQLite in-memory database.
Pure functions (hash, verify, token) are covered in unit tests.
"""

from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError

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

    def test_persist_tightens_a_preexisting_0644_file_to_0600(self, monkeypatch, tmp_path):
        # CR-5: os.open's mode arg applies ONLY on create. A pre-existing 0644 file
        # from an older release would be truncated in place but keep 0644 — writing
        # the generated secret world-readable. _persist_generated_password must
        # fchmod it to 0600 regardless of the prior mode.
        import os
        import stat

        from services.auth_service import _persist_generated_password
        monkeypatch.setenv("KEYS_DIR", str(tmp_path))
        stale = tmp_path / "initial_admin_password"
        stale.write_text("old-secret\n")
        os.chmod(stale, 0o644)
        assert stat.S_IMODE(stale.stat().st_mode) == 0o644  # precondition
        path = _persist_generated_password("brand-new-secret")
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600  # tightened
        with open(path) as fh:
            assert fh.read().strip() == "brand-new-secret"

    def test_losing_replica_does_not_clobber_keys_file(self, db, monkeypatch, tmp_path):
        # CR-1: on a concurrent fresh boot with DEFAULT_ADMIN_PASSWORD unset, both
        # replicas see 0 users and each GENERATES a different password. The losing
        # replica's create_user('admin') INSERT loses the username UNIQUE
        # constraint (IntegrityError). It must roll back and NOT overwrite the keys
        # file, or the file (loser's pw) and the committed row (winner's pw) would
        # disagree and permanently lock the operator out.
        import services.auth_service as auth_mod
        from models import User
        monkeypatch.setattr(settings, "DEFAULT_ADMIN_PASSWORD", None)
        monkeypatch.setenv("KEYS_DIR", str(tmp_path))
        # The winner already committed its row and persisted the matching file.
        key_file = tmp_path / "initial_admin_password"
        key_file.write_text("winner-password-from-replica-A\n")

        def _boom(*_a, **_k):
            # Simulate the losing INSERT: create_user flushes and the UNIQUE
            # constraint fires.
            raise IntegrityError("INSERT INTO users", {}, Exception("duplicate username"))
        monkeypatch.setattr(auth_mod, "create_user", _boom)

        result = seed_admin_user(db)
        assert result is None  # deferred to the winning replica
        # The keys file was NOT clobbered with the loser's generated password.
        assert key_file.read_text() == "winner-password-from-replica-A\n"
        # The aborted transaction left no half-seeded row.
        assert db.query(User).filter(User.username == "admin").count() == 0

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

    def test_must_change_false_seeds_without_gate_and_log_is_honest(
        self, db, monkeypatch, tmp_path, caplog
    ):
        # bonnyr-f5 #193 test-gap 5: DEFAULT_ADMIN_MUST_CHANGE=false with an unset
        # DEFAULT_ADMIN_PASSWORD seeds a GENERATED password AND no must-change gate.
        # The old log unconditionally said "you must change it on first login" — a
        # false instruction. The account must be seeded without the gate, and the
        # log must NOT promise a first-login change that is not enforced.
        import logging
        monkeypatch.setattr(settings, "DEFAULT_ADMIN_PASSWORD", None)
        monkeypatch.setattr(settings, "DEFAULT_ADMIN_MUST_CHANGE", False)
        caplog.set_level(logging.INFO)
        admin = seed_admin_user(db)
        # Logic: no must-change gate was applied.
        assert admin is not None
        assert admin.must_change_password is False
        assert (tmp_path / "initial_admin_password").exists()
        # Log: does not promise a first-login change; says the gate is off.
        seed_logs = " ".join(
            r.getMessage() for r in caplog.records if "Seeded admin" in r.getMessage()
        )
        assert seed_logs, "expected a 'Seeded admin' log line"
        assert "must change it on first login" not in seed_logs.lower()
        assert "change it on first login" not in seed_logs.lower()
        assert "DEFAULT_ADMIN_MUST_CHANGE is false" in seed_logs

    def test_must_change_true_log_still_instructs_first_login_change(
        self, db, monkeypatch, tmp_path, caplog
    ):
        # The complement: with the gate ON (default) the instruction is correct and
        # must remain.
        import logging
        monkeypatch.setattr(settings, "DEFAULT_ADMIN_PASSWORD", None)
        monkeypatch.setattr(settings, "DEFAULT_ADMIN_MUST_CHANGE", True)
        caplog.set_level(logging.INFO)
        admin = seed_admin_user(db)
        assert admin.must_change_password is True
        seed_logs = " ".join(
            r.getMessage() for r in caplog.records if "Seeded admin" in r.getMessage()
        )
        assert "change it on first login" in seed_logs.lower()

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


def _seed_legacy_stale_service_row(db, username="mcp", password="mcp-service-changeme"):
    """Build a pre-fix / pre-provenance service-account row directly.

    An older release seeded the 'mcp' account holding the shipped published
    default; the v2_155 backfill flags the known legacy row is_service_account on
    upgrade. ensure_service_user NO LONGER creates such a row (it now refuses a
    published default — that dead generate/rotate-on-unset path was removed in the
    #193 review), so tests that need a stale service row build it here.
    """
    from services.auth_service import create_user, hash_password
    user = create_user(db, username, f"{username}@bnk-forge.local", password,
                       role="admin", must_change_password=False)
    user.hashed_password = hash_password(password)
    user.is_service_account = True
    db.commit()
    return user


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
        from services.auth_service import disable_stale_service_user
        _seed_legacy_stale_service_row(db)  # sets is_service_account
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
        )
        _seed_legacy_stale_service_row(db)  # legacy service row
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
        _seed_legacy_stale_service_row(db)
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
    def test_published_default_seed_is_refused(self, db, published_default):
        """#193: seeding the mcp account with a shipped published default is
        REFUSED (ValueError) and creates no row. The merged model treats unset /
        published-default as "not usable"; that case is owned by
        disable_stale_service_user, never seeded here — the old generate-on-default
        path was removed. seed_auth_step's _mcp_pw_usable gate means this branch is
        only ever reached defensively, but it must still fail closed."""
        from models import User
        with pytest.raises(ValueError, match="usable MCP_SERVICE_PASSWORD"):
            ensure_service_user(db, username="mcp", password=published_default)
        assert db.query(User).filter(User.username == "mcp").count() == 0

    def test_none_password_is_refused(self, db):
        """#193: MCP_SERVICE_PASSWORD unset (None) is REFUSED and creates no row.
        The unset case is handled by disable_stale_service_user (account left
        disabled), not by generating a secret here — that dead path was removed."""
        from models import User
        with pytest.raises(ValueError, match="usable MCP_SERVICE_PASSWORD"):
            ensure_service_user(db, username="mcp", password=None)
        assert db.query(User).filter(User.username == "mcp").count() == 0

    def test_upgrade_disables_backfilled_published_default(self, db):
        """An account carried over from a pre-fix install still holding the
        published default is neutralised on upgrade by disable_stale_service_user
        (unset MCP_SERVICE_PASSWORD path): the v2_155 backfill flags it
        is_service_account, and the provenance-keyed disable deactivates it so the
        published default can no longer authenticate. ensure_service_user is NOT
        called on the unset path — disable owns it."""
        from core.errors import UnauthorizedError as UnauthError
        from services.auth_service import disable_stale_service_user
        _seed_legacy_stale_service_row(db)  # published default + is_service_account (backfill)
        assert authenticate_user(db, "mcp", "mcp-service-changeme")  # live before upgrade
        disable_stale_service_user(db)  # unset MCP_SERVICE_PASSWORD upgrade boot
        with pytest.raises(UnauthError):
            authenticate_user(db, "mcp", "mcp-service-changeme")  # dead after upgrade

    def test_adopts_and_reconciles_a_legacy_row_holding_a_published_default(self, db):
        """#186 integration, scoped by bonnyr-f5 #193 B1 (reachable adopt path): a
        row NOT flagged is_service_account is adopted ONLY when it carries v2_155's
        exact backfill fingerprint (username 'mcp' AND email 'mcp@bnk-forge.local')
        and still holds a shipped published default — a stale service credential
        from a pre-provenance install. When a REAL MCP_SERVICE_PASSWORD is
        configured, ensure_service_user ADOPTS it (flags provenance) and reconciles
        to the real secret instead of refusing — the published default stops
        working. Adoption must NOT clear the must_change_password gate (#193 B1)."""
        from models import User
        from services.auth_service import create_user, hash_password
        u = create_user(db, "mcp", "mcp@bnk-forge.local", "mcp-service-changeme",
                        role="admin", must_change_password=True)
        u.hashed_password = hash_password("mcp-service-changeme")
        # is_service_account intentionally left False (pre-provenance row).
        db.commit()
        ensure_service_user(db, username="mcp", password="a-real-strong-secret")
        mcp = db.query(User).filter(User.username == "mcp").first()
        assert mcp.is_service_account is True          # adopted
        # #193 B1: adopting a row must not clear its must-change gate as a side effect.
        assert mcp.must_change_password is True
        assert authenticate_user(db, "mcp", "a-real-strong-secret").username == "mcp"
        with pytest.raises(UnauthorizedError):
            authenticate_user(db, "mcp", "mcp-service-changeme")  # published default dead

    def test_human_row_holding_changeme_is_refused(self, db):
        """bonnyr-f5 #193 B1 (the takeover): before the fix, ensure_service_user
        adopted ANY non-service row whose password was a known default — and
        `changeme` is both a known default AND one of the most common human
        passwords. A human `operator`/`changeme` row (email that is NOT v2_155's
        'mcp@bnk-forge.local' fingerprint) must be REFUSED, not adopted: no
        takeover, no lockout, and the must-change gate is left intact."""
        from models import User
        from services.auth_service import create_user, hash_password
        u = create_user(db, "operator", "ops@corp.example", "changeme",
                        role="admin", must_change_password=True)
        u.hashed_password = hash_password("changeme")
        # is_service_account False (a genuine human row), holds the default `changeme`.
        db.commit()
        with pytest.raises(ValueError, match="not a service account"):
            ensure_service_user(db, username="operator", password="mcp-shared-secret")
        # ensure_service_user raises BEFORE any write, so the committed row is intact
        # (no rollback needed — a rollback would discard the fixture's savepoint).
        row = db.query(User).filter(User.username == "operator").first()
        # The human row is untouched: still human, still gated, own password still works.
        assert row.is_service_account is False
        assert row.must_change_password is True
        assert authenticate_user(db, "operator", "changeme").username == "operator"
        with pytest.raises(UnauthorizedError):
            authenticate_user(db, "operator", "mcp-shared-secret")  # takeover refused

    def test_named_mcp_but_wrong_email_holding_changeme_is_refused(self, db):
        """bonnyr-f5 #193 B1: even a row literally named 'mcp' is only adopted when
        its email matches v2_155's fingerprint. A human who merely happens to be
        named 'mcp' with a real email is left untouched (mirrors the migration's own
        conservative rule)."""
        from models import User
        from services.auth_service import create_user, hash_password
        u = create_user(db, "mcp", "real.person@corp.example", "changeme",
                        role="admin", must_change_password=True)
        u.hashed_password = hash_password("changeme")
        db.commit()
        with pytest.raises(ValueError, match="not a service account"):
            ensure_service_user(db, username="mcp", password="mcp-shared-secret")
        row = db.query(User).filter(User.username == "mcp").first()
        assert row.is_service_account is False
        assert row.must_change_password is True

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

    # NOTE (#193): the service-account seed/rotate-on-unset paths were removed —
    # ensure_service_user no longer generates or persists a secret (it requires a
    # usable password; the unset case is owned by disable_stale_service_user), so
    # the two former "service seed/rotate fails closed" cases no longer exist. Only
    # the admin seed + admin rotation still generate, and are covered above.


# ── bonnyr-f5 #193 test-gap 3: disable_stale_service_user(skip_username=...) ──


class TestDisableStaleSkipUsername:
    """The #193 M2 change (skip_username) had ZERO direct coverage. M2's PROPERTY
    is that the skipped (live MCP) row never gets an inactive window — not merely
    that it ends up active. Assert the skipped row is NEVER modified (is_active True
    AND its hash byte-identical), while a genuinely stale EXTRA service row is
    disabled and hash-scrubbed."""

    def test_skip_username_leaves_live_row_completely_untouched(self, db):
        from models import User
        from services.auth_service import disable_stale_service_user

        live = _seed_legacy_stale_service_row(db, username="mcp", password="live-real-secret")
        extra = _seed_legacy_stale_service_row(
            db, username="mcp-old", password="mcp-service-changeme"
        )
        live_hash_before = live.hashed_password
        extra_hash_before = extra.hashed_password

        disable_stale_service_user(db, skip_username="mcp", password_configured=True)

        live = db.query(User).filter(User.username == "mcp").first()
        extra = db.query(User).filter(User.username == "mcp-old").first()
        # The skipped live row: never entered the disable set → no inactive window.
        assert live.is_active is True
        assert live.hashed_password == live_hash_before  # hash never scrubbed
        # The stale EXTRA row: disabled and its credential neutralised.
        assert extra.is_active is False
        assert extra.hashed_password != extra_hash_before
        assert verify_password("mcp-service-changeme", extra.hashed_password) is False

    def test_skip_username_is_exact_raw_match(self, db):
        # bonnyr-f5 #193 M-2: the skip keys on the RAW MCP_SERVICE_USERNAME — the
        # exact value ensure_service_user reconciles under and the client
        # authenticates with. So skip 'MCP' protects a raw-'MCP' live row, while a
        # differently-cased stale 'mcp' row (NOT what the client sends) is correctly
        # disabled. Normalising the skip would instead spare that stale default row.
        from models import User
        from services.auth_service import disable_stale_service_user

        live = _seed_legacy_stale_service_row(db, username="MCP", password="live-real-secret")
        stale = _seed_legacy_stale_service_row(
            db, username="mcp", password="mcp-service-changeme"
        )
        live_hash_before = live.hashed_password

        disable_stale_service_user(db, skip_username="MCP", password_configured=True)

        live = db.query(User).filter(User.username == "MCP").first()
        stale = db.query(User).filter(User.username == "mcp").first()
        # The row the client actually uses is skipped — no inactive window.
        assert live.is_active is True
        assert live.hashed_password == live_hash_before
        # The differently-cased stale default row is disabled + scrubbed.
        assert stale.is_active is False
        assert verify_password("mcp-service-changeme", stale.hashed_password) is False


# ── bonnyr-f5 #193 test-gap 6: MCP_SERVICE_USERNAME case/whitespace variant ──


class TestServiceUsernameRawMatchesClient:
    """bonnyr-f5 #193 M-2 (regression fix; replaces the round-3 'normalise the
    lookup' tests, which locked in a client-breaking bug). The MCP client sends the
    RAW MCP_SERVICE_USERNAME and authenticate_user matches exactly, so the account
    must be created under the raw value — not folded to 'mcp'."""

    @pytest.fixture(autouse=True)
    def _isolate_keys_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KEYS_DIR", str(tmp_path))

    def test_variant_creates_account_under_raw_value_client_sends(self, db):
        # MCP_SERVICE_USERNAME="MCP" (case variant): the row is created under 'MCP',
        # so the client's 'MCP' login works — the login round-3 denied.
        from models import User
        ensure_service_user(db, username="MCP", password="a-real-strong-secret")
        assert db.query(User).filter(User.username == "MCP").count() == 1
        assert db.query(User).filter(User.username == "mcp").count() == 0
        assert authenticate_user(db, "MCP", "a-real-strong-secret").username == "MCP"
        # The normalised name is NOT what the client sends — nothing seeded there.
        with pytest.raises(UnauthorizedError):
            authenticate_user(db, "mcp", "a-real-strong-secret")

    def test_variant_create_uses_raw_name_and_email(self, db):
        # The created row carries the raw username and a raw-derived email.
        from models import User
        ensure_service_user(db, username="MCP", password="a-real-strong-secret")
        row = db.query(User).filter(User.username == "MCP").one()
        assert row.email == "MCP@bnk-forge.local"
        # Idempotent under the SAME raw value: reconciles the one row, no twin.
        ensure_service_user(db, username="MCP", password="rotated-strong-secret")
        assert db.query(User).filter(User.username == "MCP").count() == 1
        assert authenticate_user(db, "MCP", "rotated-strong-secret").username == "MCP"

    def test_default_lowercase_name_still_reconciles_legacy_row(self, db):
        # The default MCP_SERVICE_USERNAME='mcp' (raw == what the client sends) still
        # reconciles the legacy 'mcp' row rather than minting a second account.
        from models import User
        _seed_legacy_stale_service_row(db, username="mcp", password="mcp-service-changeme")
        ensure_service_user(db, username="mcp", password="a-real-strong-secret")
        assert db.query(User).filter(User.username == "mcp").count() == 1
        assert authenticate_user(db, "mcp", "a-real-strong-secret").username == "mcp"


# ── bonnyr-f5 #193 test-gap 4: db.commit() fails after the keys file is written ──


class TestRotateCommitFailureLeavesRetriableState:
    """If db.commit() raises AFTER _persist_generated_password wrote the keys file,
    the keys file holds a password that does not authenticate while the published
    default still does. It self-heals on the next boot (the rotation retries), but
    the interim state must be exactly that — not a lockout."""

    def test_commit_failure_after_file_write(self, monkeypatch, tmp_path):
        # Uses its OWN throwaway engine (not the shared savepoint-based `db`
        # fixture) so a rollback here is real and isolated — a rollback on the
        # shared fixture session would discard the fixture's own savepoint.
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        import models  # noqa: F401 — register tables
        from database import Base
        from models import User
        from services.auth_service import _rotate_known_default_admin

        monkeypatch.setenv("KEYS_DIR", str(tmp_path))
        monkeypatch.setattr(settings, "DEFAULT_ADMIN_PASSWORD", None)  # force generation

        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            create_user(db, "admin", "admin@bnk-forge.local", "changeme",
                        role="admin", must_change_password=False)
            db.commit()

            def _boom():
                raise RuntimeError("simulated commit failure after file write")
            monkeypatch.setattr(db, "commit", _boom)

            with pytest.raises(RuntimeError, match="simulated commit failure"):
                _rotate_known_default_admin(db)

            # Roll the uncommitted hash change back (as get_db_context would on the
            # raised exception). Restore commit first so rollback works cleanly.
            monkeypatch.undo()
            db.rollback()

            # The keys file WAS written with a generated secret ...
            pw_file = tmp_path / "initial_admin_password"
            assert pw_file.exists()
            orphan_pw = pw_file.read_text().strip()
            assert orphan_pw and orphan_pw != "changeme"

            admin = db.query(User).filter(User.username == "admin").first()
            # ... but the DB row's hash was NOT committed: the published default
            # STILL authenticates (self-heals next boot) and the orphan file
            # password does NOT.
            assert verify_password("changeme", admin.hashed_password)
            assert verify_password(orphan_pw, admin.hashed_password) is False
        finally:
            db.close()
            engine.dispose()
