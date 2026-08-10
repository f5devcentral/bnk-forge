"""Contract test — every endpoint the CLI calls must exist in the real app.

The CLI (`cli/bnk-forge`) is exercised elsewhere against a *mock* HTTP server
whose route table is hand-written (tests/unit/test_cli_smoke.py). That proves
argparse and exit-code semantics, but it cannot prove the CLI is calling routes
the backend actually serves — a hand-written mock happily answers a path that
does not exist in production. That gap shipped four broken commands
(`--module` 404s, `destroy --module` 422s, `show` listing nothing, and project
"slugs" that were never a thing).

So: parse the CLI's real call sites out of its source, and assert each
(method, path) resolves against `app.routes`. This test fails the moment
someone points the CLI at an endpoint the backend doesn't have — or renames a
route out from under it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CLI_SCRIPT = REPO_ROOT / "cli" / "bnk-forge"

# Calls that take an API path as their first positional argument.
_API_CALLERS = {"api_call", "_make_request", "_api"}

# Route path params differ in name between the CLI f-string and the FastAPI
# decorator ({module_id} vs {id}); only the shape matters for resolution.
_PARAM = re.compile(r"\{[^}]*\}")


def _normalize(path: str) -> str:
    """`/api/projects/{project_id}/x?q=1` → `/api/projects/{}/x`."""
    return _PARAM.sub("{}", path.split("?", 1)[0]).rstrip("/") or "/"


def _literal_path(node: ast.AST) -> str | None:
    """Recover the URL path from a str literal or an f-string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                parts.append("{}")  # interpolated id — a path param
        return "".join(parts)
    return None


def _paths_in_scope(scope: ast.AST) -> dict[str, list[str]]:
    """`var = "/api/..."` assignments inside ONE function, per variable name.

    The CLI picks an endpoint per branch (`path = f"/api/.../deploy"` vs
    `path = f"/api/.../deploy-all"`) and then calls `api_call(path, ...)`, so a
    literals-only parser would silently skip exactly the deploy/destroy calls
    this test exists to protect. Scoping per function matters: several commands
    reuse the name `path`, and a global map would cross-wire deploy's call with
    destroy's endpoint.
    """
    assigned: dict[str, list[str]] = {}
    for node in ast.walk(scope):
        if not isinstance(node, ast.Assign):
            continue
        value = _literal_path(node.value)
        if not value or not value.startswith("/api/"):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                assigned.setdefault(target.id, []).append(value)
    return assigned


def _calls_in_scope(scope: ast.AST) -> set[tuple[str, str, bool]]:
    assigned = _paths_in_scope(scope)
    calls: set[tuple[str, str, bool]] = set()

    for node in ast.walk(scope):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name not in _API_CALLERS:
            continue

        # _api(base_url, token, path, ...) passes the path third.
        path_arg = node.args[2] if name == "_api" and len(node.args) > 2 else node.args[0]
        literal = _literal_path(path_arg)
        candidates = [literal] if literal else []
        if not candidates and isinstance(path_arg, ast.Name):
            candidates = assigned.get(path_arg.id, [])

        method = "GET"
        sends_body = False
        for kw in node.keywords:
            if kw.arg == "method" and isinstance(kw.value, ast.Constant):
                method = str(kw.value.value).upper()
            if kw.arg == "data":
                # A bare `data=None` is not a body; anything else may be one.
                sends_body = not (
                    isinstance(kw.value, ast.Constant) and kw.value.value is None
                )

        for path in candidates:
            # cmd_login / cmd_version build absolute URLs from a base.
            if path.startswith("http"):
                idx = path.find("/api/")
                if idx == -1:
                    continue
                path = path[idx:]
            if not path.startswith("/api/"):
                continue
            calls.add((method, _normalize(path), sends_body))

    return calls


def _cli_api_calls() -> set[tuple[str, str, bool]]:
    """Every (METHOD, path, sends_body) the CLI issues, recovered from source."""
    tree = ast.parse(CLI_SCRIPT.read_text())
    functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    calls: set[tuple[str, str, bool]] = set()
    for func in functions:
        calls |= _calls_in_scope(func)
    return calls


def _app_routes() -> dict[tuple[str, str], dict]:
    """(METHOD, normalized path) → the OpenAPI operation object serving it.

    `app.routes` is not a reliable source of truth for what's actually
    served: newer fastapi/starlette defer `include_router()` to an internal
    lazily-resolved tree (each nested `include_router()` call — including
    the ones inside `routes/*/__init__.py` that compose sub-routers before
    the top-level app includes them — only combines the accumulated prefix
    when building the *effective* route, not on the raw `APIRoute.path`
    stored on the child router). Walking `app.routes` directly, or even
    recursing into that raw tree, yields un-prefixed paths for anything
    nested more than one level deep (e.g. `/fleet-health` instead of
    `/api/operators/fleet-health`).

    `app.openapi()["paths"]` is what fastapi itself uses to describe what's
    actually served — it already applies every accumulated prefix — so it's
    the version-robust source for this contract.
    """
    from main import app

    routes: dict[tuple[str, str], dict] = {}
    for path, operations in app.openapi()["paths"].items():
        for method, operation in operations.items():
            method_upper = method.upper()
            if method_upper in ("HEAD", "OPTIONS"):
                continue
            routes[(method_upper, _normalize(path))] = operation
    return routes


def _requires_body(operation: dict) -> bool:
    return bool(operation.get("requestBody", {}).get("required", False))


def test_cli_script_is_parseable() -> None:
    assert CLI_SCRIPT.exists(), f"CLI not found at {CLI_SCRIPT}"
    assert _cli_api_calls(), "no API calls recovered from the CLI — the parser is broken"


def test_cli_deploy_and_destroy_calls_are_covered() -> None:
    """Guard the parser itself: the deploy/destroy paths are branch-assigned."""
    paths = {path for _, path, _ in _cli_api_calls()}
    for expected in (
        "/api/project-modules/{}/deploy",
        "/api/project-modules/{}/destroy",
        "/api/projects/{}/deploy-all",
        "/api/projects/{}/destroy-all",
    ):
        assert expected in paths, f"parser missed {expected} — it would not be contract-checked"


@pytest.mark.parametrize("method,path,sends_body", sorted(_cli_api_calls()))
def test_cli_endpoint_exists_in_app(method: str, path: str, sends_body: bool) -> None:
    """Every endpoint the CLI calls is served by the backend."""
    routes = _app_routes()
    if (method, path) not in routes:
        served = sorted(m for m, p in routes if p == path)
        if served:
            pytest.fail(
                f"CLI calls {method} {path}, but the backend only serves "
                f"{', '.join(served)} on that path."
            )
        pytest.fail(f"CLI calls {method} {path}, which does not exist in the app.")

    # A route with a required request model answers 422 to a bodiless call —
    # which is how `project destroy --module` shipped broken.
    if _requires_body(routes[(method, path)]) and not sends_body:
        pytest.fail(
            f"CLI calls {method} {path} without a body, but the route requires "
            f"a request model — this is a 422, not a deploy."
        )
