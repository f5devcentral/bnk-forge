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
from services.auth_service import authenticate_user, hash_password

LEGACY_DEFAULT = "mcp-service-changeme"


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
