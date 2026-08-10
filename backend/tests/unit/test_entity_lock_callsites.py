"""Static AST check: every EntityLockService(...) constructor call passes a
string literal as the `table=` argument.

This catches runtime-expression table arguments before they reach production,
enforcing the discipline that table names must be traceable statically.

The check walks all Python source files under backend/ (excluding tests/,
alembic/, and .venv/) and asserts that every Call node whose function name
resolves to 'EntityLockService' passes its first positional argument or its
`table=` keyword argument as a string literal.
"""

import ast
import os
from pathlib import Path

import pytest

# Root of the backend source tree (not tests, not alembic, not venv)
_BACKEND_ROOT = Path(__file__).parent.parent.parent
_EXCLUDE_DIRS = {"tests", "alembic", ".venv", "__pycache__", ".pytest_cache"}

# Files that are exempt from the literal-table-arg check. entity_lock.py itself
# contains internal EntityLockService() calls that receive a validated table name
# from an already-validated EntityLock dataclass (HeartbeatRefresher) or from a
# runtime variable that was just validated by the whitelist (entity_lock context
# manager). These are safe internal usages, not caller sites.
_EXEMPT_FILES = {"services/entity_lock.py"}


def _iter_python_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames if d not in _EXCLUDE_DIRS
        ]
        for fname in filenames:
            if fname.endswith(".py"):
                yield Path(dirpath) / fname


def _is_entity_lock_service_call(node: ast.Call) -> bool:
    """Return True if the Call node appears to construct EntityLockService."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "EntityLockService"
    if isinstance(func, ast.Attribute):
        return func.attr == "EntityLockService"
    return False


def _find_violations(source_file: Path) -> list[tuple[int, str]]:
    """Return list of (lineno, reason) for each violating EntityLockService call."""
    try:
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _is_entity_lock_service_call(node):
            continue

        # Look for table= keyword argument as a string literal.
        table_kwarg = None
        for kw in node.keywords:
            if kw.arg == "table":
                table_kwarg = kw.value
                break

        # Also check first positional arg (position 1, after db).
        # EntityLockService(db, table) — table is index 1 (0-based).
        positional_table = node.args[1] if len(node.args) >= 2 else None

        table_node = table_kwarg or positional_table
        if table_node is None:
            # table not passed at all — may be a ModuleLockService subclass (ok)
            continue

        if not isinstance(table_node, ast.Constant) or not isinstance(table_node.value, str):
            violations.append((
                node.lineno,
                f"EntityLockService 'table' argument at line {node.lineno} "
                f"is not a string literal (found: {ast.dump(table_node)})",
            ))

    return violations


def test_entity_lock_service_table_is_always_string_literal():
    """No EntityLockService(...) call passes a non-literal table= argument."""
    all_violations: list[tuple[Path, int, str]] = []

    for py_file in _iter_python_files(_BACKEND_ROOT):
        rel = py_file.relative_to(_BACKEND_ROOT)
        if str(rel) in _EXEMPT_FILES:
            continue
        violations = _find_violations(py_file)
        for lineno, reason in violations:
            all_violations.append((rel, lineno, reason))

    if all_violations:
        msg_lines = ["EntityLockService table= must be a string literal. Violations found:"]
        for path, lineno, reason in all_violations:
            msg_lines.append(f"  {path}:{lineno} — {reason}")
        pytest.fail("\n".join(msg_lines))
