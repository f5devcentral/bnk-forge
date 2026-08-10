"""
Conftest for backend integration tests.

Integration tests exercise the full HTTP stack: routes -> services -> DB.
They use FastAPI TestClient with real SQLite but mock external systems
(AWS, K8s, Celery workers, Redis).

Available fixtures (inherited from root conftest):
- client — FastAPI TestClient with DB override
- db — transactional SQLite session
- admin_headers, operator_headers, viewer_headers — JWT auth headers
- sample_user, sample_operator_user, sample_viewer_user — pre-created users
- sample_project — pre-created project
- All factory fixtures (make_user, make_project, etc.)
- mock_cache, mock_tofu

Additional fixtures defined here:
- mock_celery_dispatch — patches Celery task dispatch for route tests
- sample_module — a module library entry + project module for testing module routes
- sample_task — a pre-created task for testing task routes
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def mock_celery_dispatch(monkeypatch):
    """Patch all Celery task .delay() calls so route tests don't need a worker.

    Returns a dict of task_name -> MagicMock for assertion.
    """
    mocks = {}

    def _make_mock(task_name):
        mock = MagicMock()
        mock.delay.return_value = MagicMock(id=f"mock-{task_name}-id")
        mock.apply_async.return_value = MagicMock(id=f"mock-{task_name}-id")
        mocks[task_name] = mock
        return mock

    # Patch common task dispatchers — add more as needed
    task_names = [
        "tasks.execution_tasks.execute_module_action",
        "tasks.execution_tasks.execute_parallel_action",
        "tasks.kubernetes_tasks.execute_k8s_action",
    ]
    for task_name in task_names:
        try:
            monkeypatch.setattr(task_name + ".delay", _make_mock(task_name).delay)
        except AttributeError:
            pass  # Task module may not be importable in test env

    yield mocks


@pytest.fixture()
def sample_module(db, sample_user, sample_project):
    """Create a module library entry and attach it to sample_project."""
    from tests.factories import ModuleLibraryFactory, ProjectModuleFactory
    lib = ModuleLibraryFactory(
        db,
        name="test-vpc",
        description="Test VPC module",
        category="networking",
    )
    mod = ProjectModuleFactory(
        db,
        project=sample_project,
        library_module=lib,
    )
    db.commit()
    return {"library": lib, "module": mod, "project": sample_project}


@pytest.fixture()
def sample_task(db, sample_project, make_task):
    """Create a pre-existing task for testing task routes."""
    from tests.factories import TaskFactory
    task = TaskFactory(db, project=sample_project, task_type="init", status="completed")
    db.commit()
    return task
