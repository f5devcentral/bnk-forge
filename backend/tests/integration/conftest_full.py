"""
Full-mode integration test fixtures.

Full-mode tests run against a REAL application stack (Docker Compose).
They test the complete path: HTTP -> FastAPI -> Service -> PostgreSQL/Redis/Celery.

Requirements:
  - Application stack running: `docker compose -f docker-compose.yml -f docker-compose.test.yml up -d`
  - Seeded admin user (default: admin/admin)

Usage:
  Run full-mode tests with the `full` marker:
    pytest backend/tests/integration/ -m full -v

  Or via Makefile:
    make test-integration-full

Configuration:
  Override defaults with environment variables:
    TEST_BASE_URL=https://localhost         # Proxy URL (HTTPS on 443)
    TEST_API_URL=http://localhost:8000     # Direct backend URL
    TEST_ADMIN_USER=admin
    TEST_ADMIN_PASS=admin
"""

import os
import time
from collections.abc import Generator
from typing import Any

import pytest
import requests

# ============================================================================
# Configuration
# ============================================================================

TEST_BASE_URL = os.environ.get("TEST_BASE_URL", "https://localhost")
TEST_API_URL = os.environ.get("TEST_API_URL", "http://localhost:8000")
TEST_ADMIN_USER = os.environ.get("TEST_ADMIN_USER", "admin")
TEST_ADMIN_PASS = os.environ.get("TEST_ADMIN_PASS", "changeme")
TASK_POLL_INTERVAL = float(os.environ.get("TASK_POLL_INTERVAL", "2"))
TASK_TIMEOUT = float(os.environ.get("TASK_TIMEOUT", "120"))


# ============================================================================
# Session-scoped fixtures (shared across all full-mode tests)
# ============================================================================


@pytest.fixture(scope="session")
def base_url() -> str:
    """Base URL for the running application (via proxy)."""
    return TEST_BASE_URL


@pytest.fixture(scope="session")
def api_url() -> str:
    """Direct backend API URL (bypasses proxy)."""
    return TEST_API_URL


@pytest.fixture(scope="session")
def live_session() -> requests.Session:
    """HTTP session with keep-alive for the running stack."""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


@pytest.fixture(scope="session")
def admin_token(live_session: requests.Session, api_url: str) -> str:
    """Authenticate as admin and return JWT token.

    This runs once per session and caches the token.
    """
    resp = live_session.post(
        f"{api_url}/api/auth/login",
        json={"username": TEST_ADMIN_USER, "password": TEST_ADMIN_PASS},
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    data = resp.json()
    token = data["token"]

    # Set default auth header for the session
    live_session.headers["Authorization"] = f"Bearer {token}"
    return token


@pytest.fixture(scope="session")
def admin_headers(admin_token: str) -> dict[str, str]:
    """Auth headers dict for use with requests."""
    return {
        "Authorization": f"Bearer {admin_token}",
        "Content-Type": "application/json",
    }


# ============================================================================
# Function-scoped fixtures
# ============================================================================


@pytest.fixture()
def live_client(
    live_session: requests.Session, api_url: str, admin_token: str
) -> requests.Session:
    """Authenticated requests.Session pointing at the live API.

    Usage:
        def test_something(live_client, api_url):
            resp = live_client.get(f"{api_url}/api/projects")
            assert resp.status_code == 200
    """
    # admin_token fixture ensures we're authenticated
    return live_session


@pytest.fixture()
def wait_for_task(live_session: requests.Session, api_url: str):
    """Factory fixture: poll a task until it reaches a terminal state.

    Usage:
        def test_apply(live_client, api_url, wait_for_task):
            resp = live_client.post(f"{api_url}/api/project-modules/1/apply")
            task_id = resp.json()["task_id"]
            result = wait_for_task(task_id)
            assert result["status"] == "completed"
    """

    def _wait(task_id: int | str, timeout: float = TASK_TIMEOUT) -> dict[str, Any]:
        """Poll task until completed/failed/cancelled or timeout."""
        start = time.time()
        terminal_states = {"completed", "failed", "cancelled", "error"}

        while time.time() - start < timeout:
            resp = live_session.get(f"{api_url}/api/tasks/{task_id}")
            if resp.status_code != 200:
                time.sleep(TASK_POLL_INTERVAL)
                continue

            data = resp.json()
            status = data.get("status", "")

            if status in terminal_states:
                return data

            time.sleep(TASK_POLL_INTERVAL)

        raise TimeoutError(
            f"Task {task_id} did not complete within {timeout}s. "
            f"Last status: {data.get('status', 'unknown')}"
        )

    return _wait


@pytest.fixture()
def create_test_project(
    live_session: requests.Session, api_url: str, admin_token: str
) -> Generator[dict[str, Any], None, None]:
    """Create a temporary project for testing and clean it up afterward.

    Yields the project creation response dict including project_id.
    """
    # Create
    resp = live_session.post(
        f"{api_url}/api/projects",
        json={
            "name": f"test-{int(time.time())}",
            "description": "Temporary test project",
            "project_type": "cloud-aws",
            "region": "us-east-1",
        },
    )
    assert resp.status_code == 200, f"Failed to create project: {resp.text}"
    project_data = resp.json()

    yield project_data

    # Cleanup: force-delete the project
    project_id = project_data.get("project_id") or project_data.get("id")
    if project_id:
        live_session.delete(
            f"{api_url}/api/projects/{project_id}",
            params={"force": True},
        )


# ============================================================================
# Stack health check (auto-use in session)
# ============================================================================


@pytest.fixture(scope="session", autouse=True)
def _ensure_stack_running(api_url: str) -> None:
    """Verify the application stack is running before any full-mode test.

    Skips the entire test session if the stack is not reachable.
    """
    try:
        resp = requests.get(f"{api_url}/api/system/health", timeout=5)
        if resp.status_code != 200:
            pytest.skip(
                f"Application stack not healthy (status {resp.status_code}). "
                f"Start with: docker compose up -d"
            )
    except requests.ConnectionError:
        pytest.skip(
            f"Application stack not reachable at {api_url}. "
            f"Start with: docker compose up -d"
        )
