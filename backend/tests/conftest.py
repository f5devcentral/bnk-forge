"""
Shared test fixtures for BNK-Forge backend integration tests.

Provides:
- SQLite in-memory database with all tables created
- FastAPI TestClient with dependency overrides
- Pre-authenticated JWT headers for admin/operator/viewer roles
- Sample data fixtures (project, user)

Usage:
    def test_example(client, admin_headers, sample_project):
        response = client.get("/api/projects", headers=admin_headers)
        assert response.status_code == 200
"""

import os
import sys
import tempfile

# ---------------------------------------------------------------------------
# Environment setup — MUST happen before any backend imports
# ---------------------------------------------------------------------------

# Use SQLite for tests (overrides PostgreSQL default in Settings)
os.environ["DATABASE_URL"] = "sqlite:///file::memory:?cache=shared"
os.environ["REQUIRE_AUTH"] = "true"
os.environ["ENVIRONMENT"] = "development"
# Force inline (single-threaded) discovery so tests run synchronously against the test session.
os.environ["DISCOVERY_MAX_PARALLEL"] = "1"

# Create a temporary encryption key file so core.encryption doesn't fail
_tmp_key_dir = tempfile.mkdtemp()
_encryption_key_file = os.path.join(_tmp_key_dir, "encryption.key")
os.environ["ENCRYPTION_KEY_FILE"] = _encryption_key_file

# Ensure backend package is importable
backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

# ---------------------------------------------------------------------------
# Imports (after env setup)
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Import ALL models so Base.metadata knows about every table
import models  # noqa: F401 — triggers barrel imports in models/__init__.py
from database import Base, get_db
from models import User
from services.auth_service import create_access_token, hash_password
from services.registry import ServiceRegistry

# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def engine():
    """Create a shared SQLite in-memory engine for the entire test session."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Enable WAL-like behaviour: allow nested transactions
    @event.listens_for(eng, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # Create all tables once
    Base.metadata.create_all(bind=eng)
    yield eng
    Base.metadata.drop_all(bind=eng)
    eng.dispose()


@pytest.fixture()
def db(engine):
    """
    Provide a transactional database session that rolls back after each test.

    This keeps tests isolated without paying the cost of table recreation.
    Also resets the ServiceRegistry singleton to avoid cross-test contamination.
    """
    # Reset service registry between tests
    ServiceRegistry.reset()

    connection = engine.connect()
    transaction = connection.begin()
    TestingSessionLocal = sessionmaker(bind=connection)
    session = TestingSessionLocal()

    # Code that runs outside FastAPI's dependency injection opens its own
    # session via database.SessionLocal (e.g. AuthMiddleware verifying an API
    # token). Point that factory at the test connection so it sees this test's
    # data instead of trying to reach the real database.
    import database
    real_session_local = database.SessionLocal
    database.SessionLocal = TestingSessionLocal

    yield session

    database.SessionLocal = real_session_local
    session.close()
    transaction.rollback()
    connection.close()


# ---------------------------------------------------------------------------
# Mock heavy services that aren't needed for route-level tests
# ---------------------------------------------------------------------------

def _noop_cache():
    """Return a mock CacheService that always misses."""
    mock = MagicMock()
    mock.get.return_value = None
    mock.set.return_value = True
    mock.delete.return_value = True
    mock.delete_pattern.return_value = 0
    mock.redis = None
    return mock


@pytest.fixture()
def client(db):
    """
    FastAPI TestClient with DB override and mocked external services.

    The TestClient uses `raise_server_exceptions=False` so that our custom
    error handlers (AppError -> JSONResponse) work as they do in production.
    """
    from main import app

    # Override get_db so every route receives our test session
    def _override_get_db():
        try:
            yield db
        finally:
            pass  # session cleanup handled by the `db` fixture

    app.dependency_overrides[get_db] = _override_get_db

    # Patch the cache to avoid needing Redis
    with patch("core.cache.cache", _noop_cache()):
        yield TestClient(app, raise_server_exceptions=False)

    # Cleanup overrides after each test
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Auth token helpers
# ---------------------------------------------------------------------------

def _make_token(username: str, role: str) -> str:
    """Create a valid JWT for the given user/role."""
    return create_access_token(data={"sub": username, "role": role})


def _make_headers(username: str, role: str) -> dict:
    """Return Authorization headers with a valid JWT."""
    return {"Authorization": f"Bearer {_make_token(username, role)}"}


@pytest.fixture()
def admin_headers() -> dict:
    """Authorization headers for an admin user."""
    return _make_headers("testadmin", "admin")


@pytest.fixture()
def operator_headers() -> dict:
    """Authorization headers for an operator user."""
    return _make_headers("testoperator", "operator")


@pytest.fixture()
def viewer_headers() -> dict:
    """Authorization headers for a viewer user."""
    return _make_headers("testviewer", "viewer")


# ---------------------------------------------------------------------------
# Sample data fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_user(db) -> User:
    """Create a pre-existing admin user in the test DB."""
    user = User(
        username="testadmin",
        email="testadmin@bnk-forge.test",
        hashed_password=hash_password("testpassword123"),
        role="admin",
        is_active=True,
        must_change_password=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def sample_operator_user(db) -> User:
    """Create a pre-existing operator user in the test DB."""
    user = User(
        username="testoperator",
        email="testoperator@bnk-forge.test",
        hashed_password=hash_password("testpassword123"),
        role="operator",
        is_active=True,
        must_change_password=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def sample_viewer_user(db) -> User:
    """Create a pre-existing viewer user in the test DB."""
    user = User(
        username="testviewer",
        email="testviewer@bnk-forge.test",
        hashed_password=hash_password("testpassword123"),
        role="viewer",
        is_active=True,
        must_change_password=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def sample_project(db):
    """Create a pre-existing project in the test DB."""
    from models import Project

    project = Project(
        name="Test Project",
        description="A test project for integration tests",
        project_type="kubernetes",
        cloud_provider="on-prem",
        environment="dev",
        backend_type="local",
        color="#a8337a",
        icon="cloud",
        is_active=True,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@pytest.fixture()
def all_test_users(db):
    """Create admin + operator + viewer users in one fixture."""
    users = {}
    for username, role in [("testadmin", "admin"), ("testoperator", "operator"), ("testviewer", "viewer")]:
        user = User(
            username=username,
            email=f"{username}@bnk-forge.test",
            hashed_password=hash_password("testpassword123"),
            role=role,
            is_active=True,
            must_change_password=False,
        )
        db.add(user)
        users[role] = user
    db.commit()
    for u in users.values():
        db.refresh(u)
    return users


# ---------------------------------------------------------------------------
# Factory-based fixtures (use these for new tests — more composable)
# ---------------------------------------------------------------------------

@pytest.fixture()
def make_user(db):
    """Factory fixture: returns a callable that creates users.

    Usage:
        def test_example(make_user):
            admin = make_user(role="admin")
            operator = make_user(role="operator")
    """
    from tests.factories import UserFactory
    return lambda **kwargs: UserFactory(db, **kwargs)


@pytest.fixture()
def make_project(db):
    """Factory fixture: returns a callable that creates projects."""
    from tests.factories import ProjectFactory
    return lambda **kwargs: ProjectFactory(db, **kwargs)


@pytest.fixture()
def make_module_library(db):
    """Factory fixture: returns a callable that creates module library entries."""
    from tests.factories import ModuleLibraryFactory
    return lambda **kwargs: ModuleLibraryFactory(db, **kwargs)


@pytest.fixture()
def make_project_module(db):
    """Factory fixture: returns a callable that creates project modules."""
    from tests.factories import ProjectModuleFactory
    return lambda **kwargs: ProjectModuleFactory(db, **kwargs)


@pytest.fixture()
def make_task(db):
    """Factory fixture: returns a callable that creates tasks."""
    from tests.factories import TaskFactory
    return lambda **kwargs: TaskFactory(db, **kwargs)


@pytest.fixture()
def make_stack_template(db):
    """Factory fixture: returns a callable that creates stack templates."""
    from tests.factories import StackTemplateFactory
    return lambda **kwargs: StackTemplateFactory(db, **kwargs)


@pytest.fixture()
def make_stack_instance(db):
    """Factory fixture: returns a callable that creates stack instances."""
    from tests.factories import StackInstanceFactory
    return lambda **kwargs: StackInstanceFactory(db, **kwargs)


@pytest.fixture()
def make_k8s_cluster(db):
    """Factory fixture: returns a callable that creates K8s clusters."""
    from tests.factories import KubernetesClusterFactory
    return lambda **kwargs: KubernetesClusterFactory(db, **kwargs)


@pytest.fixture()
def make_proxy_deployment(db):
    """Factory fixture: returns a callable that creates ProxyDeployment rows."""
    from tests.factories import ProxyDeploymentFactory
    return lambda **kwargs: ProxyDeploymentFactory(db, **kwargs)


@pytest.fixture()
def make_bnk_upgrade(db):
    """Factory fixture: returns a callable that creates BnkUpgrade rows."""
    from tests.factories import BnkUpgradeFactory
    return lambda **kwargs: BnkUpgradeFactory(db, **kwargs)


# ---------------------------------------------------------------------------
# Mock service fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_cache():
    """Provide a standalone MockCacheService for tests that need explicit cache control."""
    from tests.mocks.redis_mock import MockCacheService
    return MockCacheService()


@pytest.fixture()
def mock_tofu(monkeypatch):
    """Patch subprocess.run to return mock tofu results.

    Returns a function to configure the mock behavior:
        def test_example(mock_tofu):
            mock_tofu(command="apply", success=True, apply_added=5)
    """
    from tests.mocks.subprocess_mock import mock_tofu_subprocess
    results = []

    def _configure(**kwargs):
        result = mock_tofu_subprocess(**kwargs)
        results.append(result)
        return result

    def _side_effect(*args, **kwargs):
        if results:
            return results.pop(0)
        return mock_tofu_subprocess(command="init")

    monkeypatch.setattr("subprocess.run", _side_effect)
    return _configure
