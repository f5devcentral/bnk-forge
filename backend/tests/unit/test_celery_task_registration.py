"""
Regression guard: every task module dispatched by task_dispatch.py must be
listed in celery_app.py's ``include`` list.

Background (regression eb32caf2):
    ``tasks.ssh_tasks`` was accidentally dropped from ``celery_app.py``'s
    ``include=`` list. Because ``task_dispatch.py`` uses lazy imports inside
    function bodies (``from tasks.ssh_tasks import run_ssh_init`` inside
    ``dispatch_init``), all Python import-time checks pass — the module exists
    on disk and the test suite never exercises the actual Celery worker boot
    path. At runtime the worker silently drops every SSH task with
    "Received unregistered task of type ...".

What this test does:
    1. AST-parses ``task_dispatch.py`` to collect every ``tasks.*`` module
       referenced in any ``from tasks.X import ...`` statement.
    2. Imports ``celery_app`` and reads its ``conf.include`` list.
    3. Asserts the two sets are identical (dispatched ⊆ registered, with
       clear error output listing any missing modules).

Self-maintaining:
    Adding a new engine dispatch to ``task_dispatch.py`` automatically makes
    this test fail if the corresponding module isn't added to
    ``celery_app.py``'s ``include`` list — no manual update needed.
"""

import ast
import pathlib

import pytest

# ── helpers ──────────────────────────────────────────────────────────────────

_BACKEND_ROOT = pathlib.Path(__file__).parent.parent.parent  # backend/
_DISPATCH_FILE = _BACKEND_ROOT / "services" / "execution" / "task_dispatch.py"


def _extract_dispatched_task_modules() -> set[str]:
    """
    Parse task_dispatch.py with the ``ast`` module and return every unique
    ``tasks.*`` module name found in ``from tasks.X import ...`` statements.

    Only top-level and function-body ``ImportFrom`` nodes with module prefix
    ``tasks`` are collected; bare ``import tasks`` forms are ignored because
    the dispatcher never uses them.
    """
    source = _DISPATCH_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_DISPATCH_FILE))

    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "tasks" or module.startswith("tasks."):
                modules.add(module)
    return modules


# ── test ─────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_dispatched_task_modules_are_registered_in_celery():
    """
    Every ``tasks.*`` module imported by task_dispatch.py must appear in
    celery_app.py's ``include`` list so the worker actually loads it.

    Failure means a new dispatch engine was wired in task_dispatch.py but
    the corresponding module was not added to celery_app.py's include=.
    That causes silent task loss at runtime ("Received unregistered task").
    """
    # Arrange
    dispatched_modules = _extract_dispatched_task_modules()
    assert dispatched_modules, (
        f"No 'tasks.*' imports found in {_DISPATCH_FILE} — "
        "either the file moved or the AST extraction is broken."
    )

    # Import celery_app inside the test so import errors surface clearly.
    # celery_app.py reads env vars at import time (REDIS_URL etc.) but does
    # not connect to Redis, so it is safe to import in unit tests.
    from celery_app import celery_app  # noqa: PLC0415

    registered_modules: set[str] = set(celery_app.conf.include)

    # Act
    missing = dispatched_modules - registered_modules

    # Assert
    assert not missing, (
        "The following task module(s) are imported by task_dispatch.py "
        "but NOT listed in celery_app.py's include= list.\n"
        "Celery workers will silently drop every task sent to these modules.\n\n"
        "Missing:\n"
        + "\n".join(f"  - {m}" for m in sorted(missing))
        + "\n\nFix: add the module(s) to the include= list in celery_app.py."
    )
