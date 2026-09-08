"""
Unit tests for BnkctlEngine — the *bnkctl local-subprocess deployment engine.

Tests use a shell stub binary (a temporary script that echoes lines) to
exercise the streaming path without a real awsbnkctl binary.  No AWS calls,
no Docker, no network — pure subprocess + I/O.

Coverage:
  - health_check() returns False when binary is absent
  - health_check() returns True when a stub binary exists
  - plan() runs up --dry-run, returns PlanResult
  - apply() streams stdout line-by-line through on_output
  - destroy() passes --yes to down
  - get_outputs() extracts cluster name / workspace from variables
"""

import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from services.execution.cli_engine import BnkctlEngine, BnkctlToolDescriptor
from services.execution.engine_interface import ModuleContext

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_ctx(
    project_id: int = 42,
    module_id: int = 7,
    variables: dict | None = None,
    credentials_env: dict | None = None,
) -> ModuleContext:
    """Minimal ModuleContext for BnkctlEngine tests."""
    return ModuleContext(
        module_id=module_id,
        project_id=project_id,
        path="cli-bnkctl/bnk-demo",
        category="infra",
        variables=variables or {"name": "test-cluster", "region": "ap-southeast-2"},
        credentials_env=credentials_env or {},
    )


def _make_stub_binary(tmpdir: Path, exit_code: int = 0, lines: list[str] | None = None) -> str:
    """Write a minimal shell script that mimics awsbnkctl stdout behaviour.

    The stub echoes `lines` (or a default) and exits with `exit_code`.
    Returns the absolute path to the stub.
    """
    output_lines = lines or ["[phase01] VPC", "[phase02] Subnets", "[done] dry-run complete"]
    script = "#!/bin/sh\n"
    for line in output_lines:
        script += f'echo "{line}"\n'
    script += f"exit {exit_code}\n"

    stub_path = Path(tmpdir) / "awsbnkctl"
    stub_path.write_text(script)
    stub_path.chmod(stub_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(stub_path)


def _make_engine_with_stub(stub_path: str, workspace_root: str) -> BnkctlEngine:
    """Return a BnkctlEngine wired to a stub binary and an isolated workspace root."""
    engine = BnkctlEngine(db_session_factory=None)
    engine._WORKSPACE_ROOT = workspace_root

    # Override the descriptor in BNKCTL_TOOLS to point at our stub
    from services.execution import cli_engine as _mod
    _mod.BNKCTL_TOOLS["awsbnkctl"] = BnkctlToolDescriptor(
        tool="awsbnkctl",
        binary_path=stub_path,
    )
    return engine


# ── health_check ──────────────────────────────────────────────────────────────

def test_health_check_returns_false_when_binary_absent():
    """health_check must not raise when the binary is missing."""
    engine = BnkctlEngine()
    with patch("services.execution.cli_engine.BNKCTL_TOOLS", {
        "awsbnkctl": BnkctlToolDescriptor(
            tool="awsbnkctl",
            binary_path="/nonexistent/path/awsbnkctl",
        ),
    }):
        result = engine.health_check()
    assert result is False


def test_health_check_returns_true_with_stub(tmp_path):
    """health_check returns True when a valid binary echoes a version line."""
    stub = _make_stub_binary(tmp_path, exit_code=0, lines=["awsbnkctl version 0.0.1-test"])
    engine = BnkctlEngine()

    import services.execution.cli_engine as _mod
    original = dict(_mod.BNKCTL_TOOLS)
    try:
        _mod.BNKCTL_TOOLS["awsbnkctl"] = BnkctlToolDescriptor(
            tool="awsbnkctl",
            binary_path=stub,
        )
        result = engine.health_check()
    finally:
        _mod.BNKCTL_TOOLS.update(original)

    assert result is True


# ── plan ─────────────────────────────────────────────────────────────────────

def test_plan_returns_plan_result_on_success(tmp_path):
    """plan() runs up --dry-run and returns PlanResult(has_changes=True)."""
    stub = _make_stub_binary(
        tmp_path,
        exit_code=0,
        lines=["[dry-run] Phase 1: VPC", "[dry-run] Phase 2: EKS", "dry-run complete"],
    )
    workspace_root = str(tmp_path / "workspace")
    engine = _make_engine_with_stub(stub, workspace_root)
    ctx = _make_ctx()

    output_lines: list[str] = []
    result = engine.plan(ctx, on_output=output_lines.append)

    assert result.has_changes is True
    assert "dry-run" in result.details.lower() or "phase" in result.details.lower()
    assert len(output_lines) > 0


def test_plan_returns_no_changes_on_failure(tmp_path):
    """plan() with exit code 1 returns PlanResult(has_changes=False)."""
    stub = _make_stub_binary(tmp_path, exit_code=1, lines=["error: config invalid"])
    workspace_root = str(tmp_path / "workspace")
    engine = _make_engine_with_stub(stub, workspace_root)
    ctx = _make_ctx()

    result = engine.plan(ctx)

    assert result.has_changes is False
    assert "failed" in result.details.lower() or "exit" in result.details.lower()


# ── apply ─────────────────────────────────────────────────────────────────────

def test_apply_streams_stdout_through_on_output(tmp_path):
    """apply() iterates stdout and calls on_output for each line."""
    expected_lines = ["[phase01] VPC created", "[phase02] EKS created", "[done] cluster ready"]
    stub = _make_stub_binary(tmp_path, exit_code=0, lines=expected_lines)
    workspace_root = str(tmp_path / "workspace")
    engine = _make_engine_with_stub(stub, workspace_root)
    ctx = _make_ctx()

    received: list[str] = []
    result = engine.apply(ctx, on_output=received.append)

    assert result.success is True
    # All expected lines must appear in received output (may include [bnkctl] prefix line)
    received_text = "\n".join(received)
    for line in expected_lines:
        assert line in received_text, f"Expected line not in output: {line!r}"


def test_apply_returns_failure_on_nonzero_exit(tmp_path):
    """apply() maps a non-zero exit code to OperationResult(success=False)."""
    stub = _make_stub_binary(
        tmp_path, exit_code=2, lines=["[phase01] VPC created", "error: phase02 failed"],
    )
    workspace_root = str(tmp_path / "workspace")
    engine = _make_engine_with_stub(stub, workspace_root)
    ctx = _make_ctx()

    result = engine.apply(ctx)

    assert result.success is False
    assert result.error_message is not None
    assert "2" in result.error_message  # exit code in message


def test_apply_streams_in_order(tmp_path):
    """apply() preserves stdout ordering across multiple lines."""
    lines = [f"line-{i}" for i in range(10)]
    stub = _make_stub_binary(tmp_path, exit_code=0, lines=lines)
    workspace_root = str(tmp_path / "workspace")
    engine = _make_engine_with_stub(stub, workspace_root)
    ctx = _make_ctx()

    received: list[str] = []
    engine.apply(ctx, on_output=received.append)

    # Skip the first line (the "[bnkctl] apply: ..." command echo)
    received_data = [ln for ln in received if not ln.startswith("[bnkctl] apply:")]
    for i, expected in enumerate(lines):
        assert expected in received_data[i], (
            f"Line {i} out of order: expected {expected!r}, got {received_data[i]!r}"
        )


# ── destroy ───────────────────────────────────────────────────────────────────

def test_destroy_passes_yes_flag(tmp_path):
    """destroy() must pass --yes to awsbnkctl down (flag contract from lifecycle.go)."""
    # The stub captures argv[0..] via "$@" echo so we can inspect the call
    cfg_echo_stub = tmp_path / "awsbnkctl"
    cfg_echo_stub.write_text('#!/bin/sh\necho "ARGS: $@"\nexit 0\n')
    cfg_echo_stub.chmod(cfg_echo_stub.stat().st_mode | stat.S_IEXEC)

    workspace_root = str(tmp_path / "workspace")
    engine = _make_engine_with_stub(str(cfg_echo_stub), workspace_root)
    ctx = _make_ctx()

    # Seed the applied cluster.yaml. Since #82 destroy runs against the config
    # written at apply time and refuses when it is absent, rather than rendering
    # one from the current form variables (which could target the wrong cluster
    # and orphan the live one). A real destroy always follows an apply, so this
    # is the state the test means to exercise; this test asserts the --yes flag
    # contract, not the missing-config path.
    workspace = Path(workspace_root) / str(ctx.project_id) / "awsbnkctl"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "cluster.yaml").write_text(
        "metadata:\n  name: test-cluster\n"
    )

    received: list[str] = []
    result = engine.destroy(ctx, on_output=received.append)

    combined = "\n".join(received)
    assert result.success is True
    assert "--yes" in combined, f"--yes not found in destroy output: {combined!r}"


# ── get_outputs ───────────────────────────────────────────────────────────────

def test_get_outputs_extracts_cluster_name(tmp_path):
    """get_outputs() returns cluster name and workspace from variables."""
    engine = BnkctlEngine()
    engine._WORKSPACE_ROOT = str(tmp_path)
    ctx = _make_ctx(variables={"name": "my-cluster", "region": "us-east-1"})

    outputs = engine.get_outputs(ctx)

    assert outputs["cluster_name"] == "my-cluster"
    assert outputs["region"] == "us-east-1"
    assert "my-cluster" in outputs["kubeconfig_path"]


# ── engine_registry integration ───────────────────────────────────────────────

def test_cli_bnkctl_registered_in_engine_registry():
    """cli-bnkctl must appear in explicit_execution_engines() for dispatch to fire."""
    from services.engine_registry import engine_registry

    assert "cli-bnkctl" in engine_registry.explicit_execution_engines()


# ── plan cwd regression ───────────────────────────────────────────────────────

def test_plan_passes_workspace_as_cwd_to_subprocess(tmp_path):
    """Regression: plan() must pass cwd=<workspace> to subprocess.run.

    Before the fix, _run_captured was called without cwd, so relative paths in
    the bnk: block (e.g. ./secrets/cne_pull_64.json) were resolved against the
    worker's cwd instead of the per-project workspace — causing dry-run to fail.
    """
    from unittest.mock import MagicMock

    workspace_root = str(tmp_path / "workspace")
    engine = BnkctlEngine()
    engine._WORKSPACE_ROOT = workspace_root

    ctx = _make_ctx(project_id=99)
    expected_workspace = (
        tmp_path / "workspace" / "99" / "awsbnkctl"
    )

    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = "dry-run complete"
    fake_result.stderr = ""

    with patch("subprocess.run", return_value=fake_result) as mock_run:
        engine.plan(ctx)

    assert mock_run.called, "subprocess.run was never called"
    _, kwargs = mock_run.call_args
    assert "cwd" in kwargs, "cwd kwarg missing from subprocess.run call"
    assert kwargs["cwd"] == str(expected_workspace), (
        f"Expected cwd={expected_workspace!s}, got {kwargs['cwd']!r}"
    )
