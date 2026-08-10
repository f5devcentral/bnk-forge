"""Smoke tests for the cli/bnk-forge script.

The CLI is a stand-alone stdlib-only script (no pytest fixtures, no app
imports). We exercise it as a subprocess against a tiny mock HTTP server
running in-process. This catches:
- argparse breakage on every command's --help
- env-var auth resolution
- exit-code semantics (0 success, 2 plan-has-changes, 3 auth, 4 not-found)
- JSON output shape
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CLI_SCRIPT = REPO_ROOT / "cli" / "bnk-forge"


class _MockForgeServer:
    """Tiny HTTP server that maps a small route table → canned JSON responses.

    Binds to port 0 and reads back the kernel-assigned port (avoids races
    that surface when picking a free port in advance and then binding it).
    """

    def __init__(self, route_table: dict):
        self.route_table = route_table
        self.calls: list[tuple[str, str, dict | None]] = []
        self._thread: threading.Thread | None = None
        self._httpd: HTTPServer | None = None
        self.port: int = 0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        calls = self.calls
        route_table = self.route_table

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args, **_kwargs):  # silence stderr
                pass

            def _serve(self, method: str):
                length = int(self.headers.get("Content-Length", 0) or 0)
                body = json.loads(self.rfile.read(length).decode()) if length else None
                calls.append((method, self.path, body))
                key = f"{method} {self.path.split('?', 1)[0]}"
                entry = route_table.get(key)
                if entry is None:
                    self.send_response(404)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":{"code":"NOT_FOUND","message":"no route"}}')
                    return
                status, payload = entry
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

            def do_GET(self): self._serve("GET")
            def do_POST(self): self._serve("POST")
            def do_DELETE(self): self._serve("DELETE")
            def do_PUT(self): self._serve("PUT")

        self._httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()


# Liveness bound for a CLI subprocess, NOT a performance assertion. Each test
# spawns a fresh interpreter, and `make pre-push` runs seven suites at once
# (backend under coverage + vitest worker forks), which starves interpreter
# start-up: a bare `--help` was seen to blow a 30s bound under that load and fail
# the suite for no defensible reason. Generous enough to survive a saturated
# machine, still short enough to catch a genuinely hung CLI.
CLI_TIMEOUT = 180


def _run_cli(args: list[str], *, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(CLI_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=CLI_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# argparse health
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["--help"],
        ["auth", "--help"],
        ["auth", "token", "--help"],
        ["auth", "token", "create", "--help"],
        ["project", "--help"],
        ["project", "deploy", "--help"],
        ["project", "plan", "--help"],
        ["project", "destroy", "--help"],
        ["project", "show", "--help"],
        ["project", "secret", "set", "--help"],
        ["task", "status", "--help"],
    ],
)
def test_help_exits_zero(argv) -> None:
    proc = _run_cli(argv)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout, "help text should go to stdout"


# ---------------------------------------------------------------------------
# auth resolution: BNK_FORGE_URL + BNK_FORGE_TOKEN
# ---------------------------------------------------------------------------


def test_missing_token_exits_3() -> None:
    # No env vars and no config — should exit 3 (auth)
    proc = _run_cli(["project", "show", "1"], env_extra={"HOME": "/nonexistent"})
    assert proc.returncode == 3, proc.stdout + proc.stderr


def test_project_show_with_env_token_returns_zero() -> None:
    # `project show` reads the project, then its modules from the project-modules
    # route — the project response itself carries counts, not the module list.
    routes = {
        "GET /api/projects/42": (200, {"id": 42, "name": "demo", "module_count": 1}),
        "GET /api/project-modules/project/42": (
            200,
            {"modules": [{"id": 5, "path_in_project": "infra/vpc",
                          "status": "deployed", "module_name": "vpc"}]},
        ),
    }
    with _MockForgeServer(routes) as srv:
        proc = _run_cli(
            ["--format", "json", "project", "show", "42"],
            env_extra={"BNK_FORGE_URL": srv.base_url, "BNK_FORGE_TOKEN": "bnk_xx"},
        )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["id"] == 42
    assert [m["path_in_project"] for m in payload["modules"]] == ["infra/vpc"]


def test_project_show_404_exits_4() -> None:
    routes = {
        "GET /api/projects/9999": (404, {"error": {"code": "NOT_FOUND", "message": "no"}}),
    }
    with _MockForgeServer(routes) as srv:
        proc = _run_cli(
            ["project", "show", "9999"],
            env_extra={"BNK_FORGE_URL": srv.base_url, "BNK_FORGE_TOKEN": "bnk_xx"},
        )
    assert proc.returncode == 4


def test_project_show_401_exits_3() -> None:
    routes = {
        "GET /api/projects/1": (401, {"error": {"code": "UNAUTHORIZED", "message": "bad token"}}),
    }
    with _MockForgeServer(routes) as srv:
        proc = _run_cli(
            ["project", "show", "1"],
            env_extra={"BNK_FORGE_URL": srv.base_url, "BNK_FORGE_TOKEN": "bnk_xx"},
        )
    assert proc.returncode == 3


def test_auth_token_create_returns_plaintext() -> None:
    routes = {
        "POST /api/auth/tokens": (
            200,
            {"id": 1, "name": "ci", "role": "operator", "token": "bnk_secrettoken1234567890abcdefghij"},
        ),
    }
    with _MockForgeServer(routes) as srv:
        proc = _run_cli(
            ["--format", "json", "auth", "token", "create", "--name", "ci"],
            env_extra={"BNK_FORGE_URL": srv.base_url, "BNK_FORGE_TOKEN": "bnk_xx"},
        )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["token"].startswith("bnk_")


def test_unreachable_server_exits_1_not_0() -> None:
    """A connection failure must never read as success in a pipeline."""
    # Port 1 on loopback: nothing listens, connection refused immediately.
    proc = _run_cli(
        ["project", "show", "1"],
        env_extra={"BNK_FORGE_URL": "http://127.0.0.1:1", "BNK_FORGE_TOKEN": "bnk_xx"},
    )
    assert proc.returncode == 1, f"rc={proc.returncode}: {proc.stdout}{proc.stderr}"
    assert "Cannot reach" in proc.stdout


def test_plan_exits_2_when_changes_pending() -> None:
    routes = {
        "POST /api/project-modules/5/plan": (200, {"task_id": 99}),
        "GET /api/tasks/99": (200, {"id": 99, "status": "completed"}),
        "GET /api/project-modules/5/plan-status": (
            200,
            {"module_id": 5, "has_changes": True,
             "resource_changes": {"add": 2, "change": 1, "destroy": 0}},
        ),
    }
    with _MockForgeServer(routes) as srv:
        proc = _run_cli(
            ["project", "plan", "1", "--module", "5"],
            env_extra={"BNK_FORGE_URL": srv.base_url, "BNK_FORGE_TOKEN": "bnk_xx"},
        )
    assert proc.returncode == 2, proc.stdout + proc.stderr


def test_plan_exits_0_when_no_changes() -> None:
    routes = {
        "POST /api/project-modules/5/plan": (200, {"task_id": 99}),
        "GET /api/tasks/99": (200, {"id": 99, "status": "completed"}),
        "GET /api/project-modules/5/plan-status": (
            200,
            {"module_id": 5, "has_changes": False,
             "resource_changes": {"add": 0, "change": 0, "destroy": 0}},
        ),
    }
    with _MockForgeServer(routes) as srv:
        proc = _run_cli(
            ["project", "plan", "1", "--module", "5"],
            env_extra={"BNK_FORGE_URL": srv.base_url, "BNK_FORGE_TOKEN": "bnk_xx"},
        )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_plan_errors_when_no_plan_result_recorded() -> None:
    """`has_changes: null` means "never planned" — that is an error, not exit 0."""
    routes = {
        "POST /api/project-modules/5/plan": (200, {"task_id": 99}),
        "GET /api/tasks/99": (200, {"id": 99, "status": "completed"}),
        "GET /api/project-modules/5/plan-status": (
            200, {"module_id": 5, "has_changes": None, "resource_changes": None},
        ),
    }
    with _MockForgeServer(routes) as srv:
        proc = _run_cli(
            ["project", "plan", "1", "--module", "5"],
            env_extra={"BNK_FORGE_URL": srv.base_url, "BNK_FORGE_TOKEN": "bnk_xx"},
        )
    assert proc.returncode == 1, proc.stdout + proc.stderr


def test_destroy_module_sends_required_body() -> None:
    """The destroy route takes a required body — omitting it is a 422."""
    routes = {
        "POST /api/project-modules/5/destroy": (200, {"task_id": 7}),
    }
    with _MockForgeServer(routes) as srv:
        proc = _run_cli(
            ["project", "destroy", "1", "--module", "5", "--yes"],
            env_extra={"BNK_FORGE_URL": srv.base_url, "BNK_FORGE_TOKEN": "bnk_xx"},
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        call = next(c for c in srv.calls if c[1] == "/api/project-modules/5/destroy")
        assert call[2] == {"auto_approve": True}


def test_whole_project_deploy_wait_polls_the_orchestration_run() -> None:
    """A whole-project run returns `orchestrator_task_id` (a run handle), NOT an int
    `task_id`. Reading `task_id` made --wait a silent no-op: it printed "Deploy
    started" and exited 0 without waiting for anything."""
    routes = {
        "POST /api/projects/3/deploy-all": (200, {"orchestrator_task_id": "run-abc123"}),
        "GET /api/projects/3/orchestration/run-abc123": (
            200,
            {"run_handle": "run-abc123", "project_id": 3, "status": "completed",
             "total_layers": 2, "current_layer": 2, "progress_percent": 100.0,
             "failed_modules": [], "layers": []},
        ),
    }
    with _MockForgeServer(routes) as srv:
        proc = _run_cli(
            ["project", "deploy", "3", "--wait"],
            env_extra={"BNK_FORGE_URL": srv.base_url, "BNK_FORGE_TOKEN": "bnk_xx"},
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        # It must actually have polled the run — not just fired and forgotten.
        assert any(c[1] == "/api/projects/3/orchestration/run-abc123" for c in srv.calls), srv.calls


def test_whole_project_deploy_wait_fails_when_the_run_fails() -> None:
    routes = {
        "POST /api/projects/3/deploy-all": (200, {"orchestrator_task_id": "run-bad"}),
        "GET /api/projects/3/orchestration/run-bad": (
            200,
            {"run_handle": "run-bad", "project_id": 3, "status": "failed",
             "total_layers": 1, "current_layer": 1, "progress_percent": 50.0,
             "failed_modules": [7], "error_message": "module 7 blew up", "layers": []},
        ),
    }
    with _MockForgeServer(routes) as srv:
        proc = _run_cli(
            ["project", "deploy", "3", "--wait"],
            env_extra={"BNK_FORGE_URL": srv.base_url, "BNK_FORGE_TOKEN": "bnk_xx"},
        )
    assert proc.returncode == 1, proc.stdout + proc.stderr


def test_whole_project_destroy_wait_polls_the_orchestration_run() -> None:
    routes = {
        "POST /api/projects/3/destroy-all": (200, {"orchestrator_task_id": "run-d1"}),
        "GET /api/projects/3/orchestration/run-d1": (
            200,
            {"run_handle": "run-d1", "project_id": 3, "status": "completed",
             "total_layers": 1, "current_layer": 1, "progress_percent": 100.0,
             "failed_modules": [], "layers": []},
        ),
    }
    with _MockForgeServer(routes) as srv:
        proc = _run_cli(
            ["project", "destroy", "3", "--yes", "--wait"],
            env_extra={"BNK_FORGE_URL": srv.base_url, "BNK_FORGE_TOKEN": "bnk_xx"},
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert any(c[1] == "/api/projects/3/orchestration/run-d1" for c in srv.calls), srv.calls


def test_wait_with_no_handle_errors_instead_of_exiting_zero() -> None:
    """If --wait was asked for and the server returned nothing to wait on, that is an
    error. Exiting 0 would report success for a deploy we never observed."""
    routes = {"POST /api/projects/3/deploy-all": (200, {"detail": "queued"})}
    with _MockForgeServer(routes) as srv:
        proc = _run_cli(
            ["project", "deploy", "3", "--wait"],
            env_extra={"BNK_FORGE_URL": srv.base_url, "BNK_FORGE_TOKEN": "bnk_xx"},
        )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "cannot --wait" in proc.stdout


def test_secret_set_from_stdin_sends_correct_body() -> None:
    routes = {
        "GET /api/projects": (200, {"projects": [{"id": 7, "name": "demo"}]}),
        "POST /api/projects/7/secrets/value": (200, {"success": True}),
    }
    with _MockForgeServer(routes) as srv:
        proc = subprocess.run(
            [sys.executable, str(CLI_SCRIPT),
             "project", "secret", "set", "demo", "DB_PASSWORD", "--from-stdin"],
            input="hunter2\n",
            capture_output=True,
            text=True,
            env={**os.environ, "BNK_FORGE_URL": srv.base_url, "BNK_FORGE_TOKEN": "bnk_xx"},
            timeout=CLI_TIMEOUT,
        )
        assert proc.returncode == 0, proc.stderr
        # Capture call to verify shape
        secret_call = next(c for c in srv.calls if c[1] == "/api/projects/7/secrets/value")
        assert secret_call[0] == "POST"
        assert secret_call[2] == {"name": "DB_PASSWORD", "value": "hunter2"}
