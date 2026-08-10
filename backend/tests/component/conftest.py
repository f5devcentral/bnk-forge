"""
Conftest for backend component tests.

Component tests exercise service classes with a real database (SQLite in-memory).
They mock only external systems (AWS, K8s clusters, Redis, Celery).

Available fixtures (inherited from root conftest):
- db — transactional SQLite session (rolls back after each test)
- make_user, make_project, make_module_library, make_project_module
- make_task, make_stack_template, make_stack_instance, make_k8s_cluster
- mock_cache — MockCacheService
- mock_tofu — patched subprocess.run for OpenTofu

Additional fixtures defined here:
- mock_celery — patched Celery task dispatch (tasks run synchronously)
- project_with_modules — a project pre-loaded with 2 modules
- module_library — a set of 3 module library entries
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def mock_celery():
    """Patch Celery so tasks don't actually dispatch to a worker.

    Returns a MagicMock where .delay() and .apply_async() are no-ops.
    """
    mock = MagicMock()
    mock.delay.return_value = MagicMock(id="mock-task-id")
    mock.apply_async.return_value = MagicMock(id="mock-task-id")
    return mock


@pytest.fixture()
def module_library(make_module_library):
    """Create 3 module library entries for testing service lookups."""
    return [
        make_module_library(
            name="vpc",
            description="AWS VPC module",
            category="networking",
        ),
        make_module_library(
            name="security-group",
            description="AWS Security Group module",
            category="security",
        ),
        make_module_library(
            name="eks-cluster",
            description="AWS EKS Cluster module",
            category="kubernetes",
        ),
    ]


@pytest.fixture()
def project_with_modules(make_project, make_project_module, module_library):
    """Create a project with 2 modules attached."""
    project = make_project(name="Test Project With Modules")
    modules = []
    for lib_entry in module_library[:2]:
        mod = make_project_module(
            project=project,
            library_module=lib_entry,
        )
        modules.append(mod)
    return project, modules
