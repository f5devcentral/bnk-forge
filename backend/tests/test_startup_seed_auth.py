"""Startup seed_auth_step coverage — bonnyr-f5 #188 round 5 (BLOCKER-1).

These tests drive the real ``seed_auth_step`` against a legacy-install fixture,
covering the previously-untested reconcile / disable branching. The central case
reproduces the *diligent operator* path: a legacy install whose ``.env`` still
carries ``MCP_USERNAME=admin`` but who follows the new docs and sets a strong
``MCP_PASSWORD``. Before the fix the stale ``mcp`` row kept authenticating with
the shipped default; the fix makes the provenance-keyed disable unconditional so
that path is closed too.
"""


import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database
import models  # noqa: F401 — register all tables on Base.metadata
import startup_steps
from core.errors import UnauthorizedError
from database import Base
from models import User
from services.auth_service import (
    authenticate_user,
    disable_stale_service_user,
    ensure_service_user,
    hash_password,
    seed_admin_user,
    verify_password,
)

LEGACY_DEFAULT = "mcp-service-changeme"
ADMIN_DEFAULT = "changeme"


@pytest.fixture()
def legacy_db(monkeypatch):
    """A DB whose only service account is the legacy ``mcp`` row holding the
    shipped default, is_active=True, is_service_account=True — i.e. exactly what
    v2_155 leaves behind on a pre-#188 upgrade."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    # seed_auth_step calls get_db_context(), which builds sessions from the
    # module-global SessionLocal — repoint it at our throwaway engine.
    monkeypatch.setattr(database, "SessionLocal", session_factory)

    db = session_factory()
    legacy = User(
        username="mcp",
        email="mcp@bnk-forge.local",
        hashed_password=hash_password(LEGACY_DEFAULT),
        role="admin",
        is_active=True,
        is_service_account=True,
        must_change_password=False,
    )
    # A human admin, to prove the disable never touches it.
    human = User(
        username="admin",
        email="admin@bnk-forge.local",
        hashed_password=hash_password("real-admin-secret"),
        role="admin",
        is_active=True,
        is_service_account=False,
        must_change_password=False,
    )
    db.add_all([legacy, human])
    db.commit()
    db.close()

    yield session_factory
    engine.dispose()


def _legacy_default_still_works(session_factory) -> bool:
    db = session_factory()
    try:
        authenticate_user(db, "mcp", LEGACY_DEFAULT)
        return True
    except UnauthorizedError:
        return False
    finally:
        db.close()


def _set_mcp_env(monkeypatch, username, password):
    monkeypatch.setattr(startup_steps.settings, "MCP_SERVICE_USERNAME", username)
    monkeypatch.setattr(startup_steps.settings, "MCP_SERVICE_PASSWORD", password)
    monkeypatch.setattr(startup_steps.settings, "REQUIRE_AUTH", True)


def test_diligent_operator_admin_username_disables_stale_default(legacy_db, monkeypatch):
    """BLOCKER-1: strong password set, but MCP_USERNAME left at legacy 'admin'.

    ensure_service_user raises a reserved-name ValueError (swallowed), so the
    ONLY thing that can neutralise the legacy row is the unconditional disable.
    """
    _set_mcp_env(monkeypatch, "admin", "strong-new-secret-xyz")
    assert _legacy_default_still_works(legacy_db) is True  # vulnerable pre-run

    startup_steps.seed_auth_step()

    assert _legacy_default_still_works(legacy_db) is False, (
        "legacy mcp/mcp-service-changeme still authenticates — remediation did "
        "not fire on the diligent-operator path"
    )
    # Human admin must be untouched.
    db = legacy_db()
    human = db.query(User).filter(User.username == "admin", User.is_service_account.is_(False)).one()
    assert human.is_active is True
    assert authenticate_user(db, "admin", "real-admin-secret")
    db.close()


def test_usable_password_dedicated_name_reconciles_and_disables_legacy(legacy_db, monkeypatch):
    """Strong password + a dedicated MCP username: the new account authenticates
    with the new secret AND the stale default row is disabled."""
    _set_mcp_env(monkeypatch, "mcp-svc", "strong-new-secret-xyz")

    startup_steps.seed_auth_step()

    assert _legacy_default_still_works(legacy_db) is False
    db = legacy_db()
    assert authenticate_user(db, "mcp-svc", "strong-new-secret-xyz")
    db.close()


def test_unset_password_disables_stale_default(legacy_db, monkeypatch):
    """No usable password at all: the stale default must be disabled."""
    _set_mcp_env(monkeypatch, "mcp", None)

    startup_steps.seed_auth_step()

    assert _legacy_default_still_works(legacy_db) is False


def test_known_default_password_treated_as_unset(legacy_db, monkeypatch):
    """A shipped default supplied as MCP_PASSWORD must not re-seed the account —
    it is treated as unset and the stale row is disabled."""
    _set_mcp_env(monkeypatch, "mcp", "changeme")

    startup_steps.seed_auth_step()

    assert _legacy_default_still_works(legacy_db) is False


def test_configured_mcp_row_is_reconciled_when_matching_username(legacy_db, monkeypatch):
    """When MCP_USERNAME matches the legacy row's name and a real password is set,
    the account is reconciled (hash rotated to the new secret, re-activated)."""
    _set_mcp_env(monkeypatch, "mcp", "brand-new-strong-secret")

    startup_steps.seed_auth_step()

    # Old default no longer works; new secret does.
    assert _legacy_default_still_works(legacy_db) is False
    db = legacy_db()
    assert authenticate_user(db, "mcp", "brand-new-strong-secret")
    db.close()


def test_disable_stale_service_user_neutralises_the_hash(legacy_db, monkeypatch):
    """bonnyr-f5 #193 (minor): disabling a stale service row must not leave the
    published-default hash intact and lean solely on is_active + the route guard —
    the credential itself must be dead so it cannot authenticate under any path."""
    db = legacy_db()
    try:
        disable_stale_service_user(db, skip_username=None, password_configured=False)
        row = db.query(User).filter(User.username == "mcp").one()
        assert row.is_active is False
        # The old default hash must no longer verify.
        assert verify_password(LEGACY_DEFAULT, row.hashed_password) is False
    finally:
        db.close()


# ── bonnyr-f5 #193: reserved human username is refused case-insensitively ──


@pytest.mark.parametrize("name", ["Admin", "ADMIN", " admin ", "aDmIn"])
def test_ensure_service_user_refuses_reserved_username_case_insensitively(legacy_db, name):
    """A service account may not co-opt the human 'admin' identity — and the Python
    guard must match Helm's `lower | trim`, refusing case/whitespace variants too."""
    db = legacy_db()
    try:
        with pytest.raises(ValueError, match="reserved human username"):
            ensure_service_user(db, username=name, password="a-real-secret")
    finally:
        db.close()


@pytest.mark.parametrize("variant", ["MCP", " mcp ", "  Mcp  "])
def test_service_username_variant_account_matches_what_the_client_sends(legacy_db, monkeypatch, variant):
    """bonnyr-f5 #193 M-2 (regression fix; this REPLACES the round-3 'reconcile the
    legacy row' test, which locked in the bug). The MCP client receives the RAW
    MCP_SERVICE_USERNAME as BNK_FORGE_USERNAME and authenticate_user matches exactly,
    so the seeded account MUST carry the RAW value the client will send — not a
    normalised 'mcp'. Round-3 normalised the row to 'mcp' and every non-lowercase
    login was DENIED. The correct invariant: the row matches what the client sends,
    and the reserved/skip logic keys on that same raw value. The provenance-keyed
    disable neutralises the legacy default in the same boot."""
    _set_mcp_env(monkeypatch, variant, "brand-new-strong-secret")

    startup_steps.seed_auth_step()

    db = legacy_db()
    try:
        # The account the CLIENT will authenticate as exists under the raw value and
        # its new secret works — this is the login round-3 broke.
        assert authenticate_user(db, variant, "brand-new-strong-secret")
        # The normalised name is NOT what the client sends; nothing was seeded there.
        if variant != "mcp":
            with pytest.raises(UnauthorizedError):
                authenticate_user(db, "mcp", "brand-new-strong-secret")
        # The legacy 'mcp'/shipped-default row is neutralised (provenance-keyed
        # disable + hash scrub), so the published default no longer authenticates.
        with pytest.raises(UnauthorizedError):
            authenticate_user(db, "mcp", LEGACY_DEFAULT)
        legacy = db.query(User).filter(User.username == "mcp").one()
        assert legacy.is_active is False
    finally:
        db.close()


# ── bonnyr-f5 #193: admin still holding the DEFAULT (`changeme`) — the real ──
# upgrade shape the previous fixtures never exercised (they gave admin a
# non-default secret). Drives seed_auth_step against it.


@pytest.fixture()
def admin_default_db(monkeypatch, tmp_path):
    """A DB whose human ``admin`` row still holds the shipped ``changeme`` default —
    exactly what a pre-#184 install carries into an upgrade."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(database, "SessionLocal", session_factory)
    # Generation persists to KEYS_DIR; point it at a writable tmp dir.
    monkeypatch.setenv("KEYS_DIR", str(tmp_path))

    db = session_factory()
    db.add(
        User(
            username="admin",
            email="admin@bnk-forge.local",
            hashed_password=hash_password(ADMIN_DEFAULT),
            role="admin",
            is_active=True,
            is_service_account=False,
            must_change_password=False,
        )
    )
    db.commit()
    db.close()
    # MCP unset for these admin-focused cases: the MCP path just no-ops/disables.
    _set_mcp_env(monkeypatch, "mcp", None)

    yield session_factory, tmp_path
    engine.dispose()


def _admin_default_still_works(session_factory) -> bool:
    db = session_factory()
    try:
        authenticate_user(db, "admin", ADMIN_DEFAULT)
        return True
    except UnauthorizedError:
        return False
    finally:
        db.close()


def test_upgrade_admin_default_rotated_when_password_unset(admin_default_db, monkeypatch):
    """DEFAULT_ADMIN_PASSWORD unset: seed_auth_step must rotate admin OFF the shipped
    default to a generated secret persisted to the keys dir."""
    session_factory, tmp_path = admin_default_db
    monkeypatch.setattr(startup_steps.settings, "DEFAULT_ADMIN_PASSWORD", None)
    assert _admin_default_still_works(session_factory) is True  # vulnerable pre-run

    startup_steps.seed_auth_step()

    assert _admin_default_still_works(session_factory) is False
    assert (tmp_path / "initial_admin_password").exists()


def test_upgrade_admin_default_rotated_to_configured_value(admin_default_db, monkeypatch):
    """DEFAULT_ADMIN_PASSWORD set to a real value: admin is rotated TO that value so
    the documented source (env/Secret) is authoritative; the default stops working."""
    session_factory, _ = admin_default_db
    monkeypatch.setattr(
        startup_steps.settings, "DEFAULT_ADMIN_PASSWORD", "operator-chosen-secret-value"
    )

    startup_steps.seed_auth_step()

    assert _admin_default_still_works(session_factory) is False
    db = session_factory()
    try:
        assert authenticate_user(db, "admin", "operator-chosen-secret-value")
    finally:
        db.close()


def test_upgrade_admin_default_configured_known_default_is_refused(admin_default_db, monkeypatch):
    """DEFAULT_ADMIN_PASSWORD itself a known published default (`changeme`): it must
    NOT be used (that would re-publish the hole) — admin rotates to a generated
    secret and the default no longer authenticates."""
    session_factory, tmp_path = admin_default_db
    monkeypatch.setattr(startup_steps.settings, "DEFAULT_ADMIN_PASSWORD", ADMIN_DEFAULT)

    startup_steps.seed_auth_step()

    assert _admin_default_still_works(session_factory) is False
    assert (tmp_path / "initial_admin_password").exists()


def test_fresh_seed_refuses_known_default_admin_password(monkeypatch, tmp_path):
    """bonnyr-f5 #193 (minor): a FRESH install with DEFAULT_ADMIN_PASSWORD set to a
    known published default must not seed that credential verbatim — it generates a
    strong random one instead (the MCP path already refused its defaults)."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setenv("KEYS_DIR", str(tmp_path))
    monkeypatch.setattr(startup_steps.settings, "DEFAULT_ADMIN_PASSWORD", ADMIN_DEFAULT)

    db = session_factory()
    try:
        admin = seed_admin_user(db)
        assert admin is not None  # fresh install: a row was created
        # The published default must NOT authenticate; a random one was generated.
        with pytest.raises(UnauthorizedError):
            authenticate_user(db, "admin", ADMIN_DEFAULT)
    finally:
        db.close()
        engine.dispose()
    assert (tmp_path / "initial_admin_password").exists()
