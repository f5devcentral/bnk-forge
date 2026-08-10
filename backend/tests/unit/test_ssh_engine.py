"""
Unit tests for SSHEngine — the deployment engine for bare-metal SSH modules.

Tests SSHEngine methods with mocked SSHSession (no real SSH connections).
Covers init, apply, destroy, health_check, and connectivity-risk flows.
"""

from typing import Any
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from modules.base import SSHModule
from services.bare_metal.ssh_session import SSHResult
from services.execution.engine_interface import ModuleContext, OperationResult
from services.execution.ssh_engine import SSHEngine

# ── Helpers ───────────────────────────────────────────────────────────


def _make_ctx(
    path="bare-metal/probe-dpu",
    variables=None,
    ssh_host="10.176.11.91",
    ssh_username="ubuntu",
    ssh_password="F5@apcj",
) -> ModuleContext:
    """Build a minimal ModuleContext for testing."""
    return ModuleContext(
        module_id=1,
        project_id=1,
        path=path,
        category="bare-metal",
        variables=variables or {"target": "host", "bare_metal_host_id": 1},
        ssh_host=ssh_host,
        ssh_port=22,
        ssh_username=ssh_username,
        ssh_password=ssh_password,
    )


def _ok_result(stdout="SSH_OK\nhostname\n5.15.0", exit_code=0) -> SSHResult:
    """Return a successful SSHResult."""
    return SSHResult(exit_code=exit_code, stdout=stdout, stderr="", duration_seconds=1.0)


def _fail_result(stderr="Connection refused", exit_code=-1) -> SSHResult:
    """Return a failed SSHResult."""
    return SSHResult(exit_code=exit_code, stdout="", stderr=stderr, duration_seconds=1.0)


def _make_module_def(
    connectivity_risk=False,
    estimated_duration=30,
    timeout=60,
    apply_cmds=None,
    validate_cmds=None,
    pre_stage_cmds=None,
    parse_output=None,
):
    """Build a real SSHModule subclass instance for testing.

    Using a genuine SSHModule subclass (not MagicMock) ensures
    ``type(mod).execute is SSHModule.execute`` is True, so SSHEngine.apply()
    takes the shell-string path (not the Python-execute path).  MagicMock
    cannot satisfy that identity check because ``type(MagicMock())`` is the
    MagicMock *class*, which has no ``.execute`` class attribute.
    """
    _apply_cmds = apply_cmds or ["echo 'hello'"]
    _validate_cmds = validate_cmds or ["echo 'ok'"]
    _pre_stage_cmds = pre_stage_cmds or []
    _parse_output = parse_output or {"result": "done"}

    class _FakeModule(SSHModule):
        name = "fake-module"
        path = "bare-metal/fake"
        version = "0.1.0"
        description = "Fake module for tests"
        inputs: dict = {}
        outputs: dict = {}
        dependencies: list = []

        def apply_commands(self, variables: dict[str, Any]) -> list[str]:
            return _apply_cmds

        def validate_commands(self, variables: dict[str, Any]) -> list[str]:
            return _validate_cmds

        def pre_stage_commands(self, variables: dict[str, Any]) -> list[str]:
            return _pre_stage_cmds

        def parse_apply_output(self, output: str, variables: dict[str, Any]) -> dict[str, Any]:
            return _parse_output

        def plan_commands(self, variables: dict[str, Any]) -> list[str]:
            return ["echo 'plan'"]

        def parse_plan_output(self, output: str, variables: dict[str, Any]) -> bool:
            return True

        def validate_inputs(self, variables: dict[str, Any]) -> list[str]:
            return []  # No required inputs in tests

        def prereq_commands(self, variables: dict[str, Any]) -> list[str]:
            return []

    mod = _FakeModule()
    mod.connectivity_risk = connectivity_risk
    mod.estimated_duration = estimated_duration
    mod.timeout = timeout
    mod.reconnect_timeout = 600
    return mod


# ── SSHEngine.health_check() ─────────────────────────────────────────


class TestSSHEngineHealthCheck:
    def test_health_check_returns_true(self):
        engine = SSHEngine()
        assert engine.health_check() is True


# ── SSHEngine.init() ─────────────────────────────────────────────────


class TestSSHEngineInit:
    def test_ssh_engine_init_succeeds_when_ssh_reachable(self):
        engine = SSHEngine()
        ctx = _make_ctx()

        mock_session = MagicMock()
        mock_session.execute.return_value = _ok_result()
        mock_session.host = "10.176.11.91"

        with patch.object(engine, "_get_session", return_value=mock_session), \
             patch.object(engine, "_get_module_def", return_value=None):
            result = engine.init(ctx)

        assert isinstance(result, OperationResult)
        assert result.success is True
        assert result.error_message is None
        mock_session.execute.assert_called_once()

    def test_ssh_engine_init_fails_when_ssh_unreachable(self):
        engine = SSHEngine()
        ctx = _make_ctx()

        mock_session = MagicMock()
        mock_session.execute.return_value = _fail_result()
        mock_session.host = "10.176.11.91"

        with patch.object(engine, "_get_session", return_value=mock_session), \
             patch.object(engine, "_get_module_def", return_value=None):
            result = engine.init(ctx)

        assert result.success is False
        assert "connectivity check failed" in result.error_message.lower()

    def test_ssh_engine_init_returns_error_on_exception(self):
        engine = SSHEngine()
        ctx = _make_ctx()

        with patch.object(engine, "_get_session", side_effect=ConnectionError("timeout")):
            result = engine.init(ctx)

        assert result.success is False
        assert "SSH init failed" in result.error_message

    def test_ssh_engine_init_runs_prereq_commands(self):
        engine = SSHEngine()
        ctx = _make_ctx()

        mock_session = MagicMock()
        mock_session.execute.return_value = _ok_result()
        mock_session.host = "10.176.11.91"

        mod_def = MagicMock()
        mod_def.prereq_commands.return_value = ["which apt-get"]
        mod_def.validate_inputs.return_value = []  # no input errors

        with patch.object(engine, "_get_session", return_value=mock_session), \
             patch.object(engine, "_get_module_def", return_value=mod_def):
            result = engine.init(ctx)

        assert result.success is True
        # SSH check + 1 prereq = 2 calls
        assert mock_session.execute.call_count == 2

    def test_ssh_engine_init_fails_when_prereq_fails(self):
        engine = SSHEngine()
        ctx = _make_ctx()

        mock_session = MagicMock()
        # First call succeeds (SSH check), second fails (prereq)
        mock_session.execute.side_effect = [_ok_result(), _fail_result()]
        mock_session.host = "10.176.11.91"

        mod_def = MagicMock()
        mod_def.prereq_commands.return_value = ["which nonexistent"]
        mod_def.validate_inputs.return_value = []  # no input errors

        with patch.object(engine, "_get_session", return_value=mock_session), \
             patch.object(engine, "_get_module_def", return_value=mod_def):
            result = engine.init(ctx)

        assert result.success is False
        assert "Prerequisite check failed" in result.error_message

    def test_ssh_engine_init_calls_on_output(self):
        engine = SSHEngine()
        ctx = _make_ctx()
        output_lines = []

        mock_session = MagicMock()
        mock_session.execute.return_value = _ok_result()
        mock_session.host = "10.176.11.91"

        with patch.object(engine, "_get_session", return_value=mock_session), \
             patch.object(engine, "_get_module_def", return_value=None):
            result = engine.init(ctx, on_output=output_lines.append)

        assert result.success is True
        assert any("[ssh] Validating connectivity" in line for line in output_lines)
        assert any("[ssh] Connected" in line for line in output_lines)

    def test_ssh_engine_init_routes_to_dpu_session_when_target_is_dpu(self):
        engine = SSHEngine()
        ctx = _make_ctx(variables={"target": "dpu"})

        mock_session = MagicMock()
        mock_session.execute.return_value = _ok_result()
        mock_session.host = "192.168.100.2"

        with patch.object(engine, "_get_session", return_value=mock_session) as get_sess, \
             patch.object(engine, "_get_module_def", return_value=None):
            engine.init(ctx)

        get_sess.assert_called_once_with(ctx)


# ── SSHEngine.apply() ────────────────────────────────────────────────


class TestSSHEngineApply:
    def test_ssh_engine_apply_calls_apply_commands(self):
        engine = SSHEngine()
        ctx = _make_ctx()

        mock_session = MagicMock()
        mock_session.execute.return_value = _ok_result()

        mod_def = _make_module_def(apply_cmds=["echo step1", "echo step2"])

        with patch.object(engine, "_get_session", return_value=mock_session), \
             patch.object(engine, "_get_module_def", return_value=mod_def):
            result = engine.apply(ctx)

        assert result.success is True
        assert result.outputs == {"result": "done"}
        # 2 apply commands + 1 validate
        assert mock_session.execute.call_count == 3

    def test_ssh_engine_apply_fails_when_no_module_def(self):
        engine = SSHEngine()
        ctx = _make_ctx()

        with patch.object(engine, "_get_session", return_value=MagicMock()), \
             patch.object(engine, "_get_module_def", return_value=None):
            result = engine.apply(ctx)

        assert result.success is False
        assert "No SSHModule definition" in result.error_message

    def test_ssh_engine_apply_fails_on_command_error(self):
        engine = SSHEngine()
        ctx = _make_ctx()

        mock_session = MagicMock()
        mock_session.execute.return_value = SSHResult(
            exit_code=1, stdout="", stderr="command failed", duration_seconds=1.0,
        )

        mod_def = _make_module_def()

        with patch.object(engine, "_get_session", return_value=mock_session), \
             patch.object(engine, "_get_module_def", return_value=mod_def):
            result = engine.apply(ctx)

        assert result.success is False
        assert "Command failed" in result.error_message

    def test_ssh_engine_apply_fails_on_validation_error(self):
        engine = SSHEngine()
        ctx = _make_ctx()

        mock_session = MagicMock()
        # Apply succeeds, validate fails
        mock_session.execute.side_effect = [
            _ok_result(),
            SSHResult(exit_code=1, stdout="", stderr="validation error", duration_seconds=1.0),
        ]

        mod_def = _make_module_def()

        with patch.object(engine, "_get_session", return_value=mock_session), \
             patch.object(engine, "_get_module_def", return_value=mod_def):
            result = engine.apply(ctx)

        assert result.success is False
        assert "Validation failed" in result.error_message

    def test_ssh_engine_apply_returns_outputs_from_parse(self):
        engine = SSHEngine()
        ctx = _make_ctx()

        mock_session = MagicMock()
        mock_session.execute.return_value = _ok_result()

        expected_outputs = {"mst_device": "/dev/mst/mt41692_pciconf0", "nic_mode": "EMBEDDED_CPU(1)"}
        mod_def = _make_module_def(parse_output=expected_outputs)

        with patch.object(engine, "_get_session", return_value=mock_session), \
             patch.object(engine, "_get_module_def", return_value=mod_def):
            result = engine.apply(ctx)

        assert result.success is True
        assert result.outputs == expected_outputs

    def test_ssh_engine_apply_captures_stdout(self):
        engine = SSHEngine()
        ctx = _make_ctx()

        mock_session = MagicMock()
        mock_session.execute.return_value = SSHResult(
            exit_code=0, stdout="command output line", stderr="", duration_seconds=1.0,
        )

        mod_def = _make_module_def()

        with patch.object(engine, "_get_session", return_value=mock_session), \
             patch.object(engine, "_get_module_def", return_value=mod_def):
            result = engine.apply(ctx)

        assert "command output line" in result.stdout

    def test_ssh_engine_apply_on_output_called(self):
        engine = SSHEngine()
        ctx = _make_ctx()
        output_lines = []

        mock_session = MagicMock()
        mock_session.execute.return_value = _ok_result()

        mod_def = _make_module_def()

        with patch.object(engine, "_get_session", return_value=mock_session), \
             patch.object(engine, "_get_module_def", return_value=mod_def):
            result = engine.apply(ctx, on_output=output_lines.append)

        assert result.success is True
        assert any("[ssh] Applying" in line for line in output_lines)

    def test_ssh_engine_apply_handles_exception(self):
        engine = SSHEngine()
        ctx = _make_ctx()

        mod_def = _make_module_def()

        with patch.object(engine, "_get_session", side_effect=RuntimeError("boom")), \
             patch.object(engine, "_get_module_def", return_value=mod_def):
            result = engine.apply(ctx)

        assert result.success is False
        assert "SSH apply failed" in result.error_message


# ── SSHEngine.apply() with connectivity risk ─────────────────────────


class TestSSHEngineApplyConnectivityRisk:
    """Test the pre-stage/execute/wait-for-reconnect/validate cycle."""

    def test_apply_calls_pre_stage_when_connectivity_risk(self):
        engine = SSHEngine()
        ctx = _make_ctx()

        mock_session = MagicMock()
        mock_session.execute.return_value = _ok_result()

        mod_def = _make_module_def(
            connectivity_risk=True,
            pre_stage_cmds=["echo staging vf netplan"],
            apply_cmds=["echo flashing dpu"],
            validate_cmds=["echo validated"],
        )

        # Mock wait_for_reconnect to return True immediately
        with patch.object(engine, "_get_session", return_value=mock_session), \
             patch.object(engine, "_get_module_def", return_value=mod_def), \
             patch.object(engine, "_wait_for_reconnect", return_value=True):
            result = engine.apply(ctx)

        assert result.success is True
        # Pre-stage and validate commands both go through session.execute().
        # Apply commands use _execute_streaming (not session.execute directly).
        # So we expect at least 2: 1 pre-stage + 1 validate.
        assert mock_session.execute.call_count >= 2

    def test_apply_fails_when_pre_stage_fails(self):
        engine = SSHEngine()
        ctx = _make_ctx()

        mock_session = MagicMock()
        mock_session.execute.return_value = _fail_result(stderr="pre-stage error")

        mod_def = _make_module_def(
            connectivity_risk=True,
            pre_stage_cmds=["echo staging"],
        )

        with patch.object(engine, "_get_session", return_value=mock_session), \
             patch.object(engine, "_get_module_def", return_value=mod_def):
            result = engine.apply(ctx)

        assert result.success is False
        assert "Pre-stage failed" in result.error_message

    def test_apply_fails_when_reconnect_times_out(self):
        engine = SSHEngine()
        ctx = _make_ctx()

        mock_session = MagicMock()
        mock_session.execute.return_value = _ok_result()

        mod_def = _make_module_def(
            connectivity_risk=True,
            pre_stage_cmds=[],
            apply_cmds=["echo flashing"],
        )

        with patch.object(engine, "_get_session", return_value=mock_session), \
             patch.object(engine, "_get_module_def", return_value=mod_def), \
             patch.object(engine, "_wait_for_reconnect", return_value=False):
            result = engine.apply(ctx)

        assert result.success is False
        assert "did not reconnect" in result.error_message.lower()

    def test_apply_uses_streaming_for_connectivity_risk(self):
        engine = SSHEngine()
        ctx = _make_ctx()

        mock_session = MagicMock()
        mock_session.execute.return_value = _ok_result()

        mod_def = _make_module_def(
            connectivity_risk=True,
            pre_stage_cmds=[],
            apply_cmds=["echo flashing"],
            validate_cmds=["echo ok"],
        )

        with patch.object(engine, "_get_session", return_value=mock_session), \
             patch.object(engine, "_get_module_def", return_value=mod_def), \
             patch.object(engine, "_execute_streaming", return_value=_ok_result()) as mock_stream, \
             patch.object(engine, "_wait_for_reconnect", return_value=True):
            result = engine.apply(ctx)

        assert result.success is True
        mock_stream.assert_called_once()

    def test_apply_recreates_session_after_reconnect(self):
        engine = SSHEngine()
        ctx = _make_ctx()

        mock_session = MagicMock()
        mock_session.execute.return_value = _ok_result()

        mod_def = _make_module_def(
            connectivity_risk=True,
            pre_stage_cmds=[],
            apply_cmds=["echo flashing"],
            validate_cmds=["echo ok"],
        )

        call_count = 0

        def mock_get_session(c):
            nonlocal call_count
            call_count += 1
            return mock_session

        with patch.object(engine, "_get_session", side_effect=mock_get_session), \
             patch.object(engine, "_get_module_def", return_value=mod_def), \
             patch.object(engine, "_execute_streaming", return_value=_ok_result()), \
             patch.object(engine, "_wait_for_reconnect", return_value=True):
            result = engine.apply(ctx)

        assert result.success is True
        # Session created twice: once for apply, once after reconnect
        assert call_count == 2


# ── SSHEngine.destroy() ──────────────────────────────────────────────


class TestSSHEngineDestroy:
    def test_ssh_engine_destroy_returns_success(self):
        engine = SSHEngine()
        ctx = _make_ctx()
        result = engine.destroy(ctx)

        assert isinstance(result, OperationResult)
        assert result.success is True
        assert result.duration_seconds == 0.0
        assert "no-op" in result.stdout.lower() or "not applicable" in result.stdout.lower()

    def test_ssh_engine_destroy_calls_on_output(self):
        engine = SSHEngine()
        ctx = _make_ctx()
        output_lines = []

        result = engine.destroy(ctx, on_output=output_lines.append)

        assert result.success is True
        assert len(output_lines) == 1
        assert "no-op" in output_lines[0].lower() or "hardware" in output_lines[0].lower()


# ── SSHEngine.get_outputs() ──────────────────────────────────────────


class TestSSHEngineGetOutputs:
    def test_get_outputs_returns_empty_without_db_factory(self):
        engine = SSHEngine()
        ctx = _make_ctx()
        assert engine.get_outputs(ctx) == {}

    def test_get_outputs_queries_db_when_factory_provided(self):
        mock_db = MagicMock()
        mock_module = MagicMock()
        mock_module.outputs = {"key": "value"}
        mock_db.query.return_value.filter.return_value.first.return_value = mock_module

        engine = SSHEngine(db_session_factory=lambda: mock_db)
        ctx = _make_ctx()

        result = engine.get_outputs(ctx)
        assert result == {"key": "value"}

    def test_get_outputs_returns_empty_when_module_not_found(self):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        engine = SSHEngine(db_session_factory=lambda: mock_db)
        ctx = _make_ctx()

        result = engine.get_outputs(ctx)
        assert result == {}


# ── SSHEngine._build_*_session() helpers ─────────────────────────────


class TestSessionBuilders:
    def test_build_host_session_uses_ctx_fields(self):
        engine = SSHEngine()
        ctx = _make_ctx(ssh_host="1.2.3.4", ssh_username="admin", ssh_password="pass123")

        # Patch SSHSession constructor to capture args
        with patch("services.execution.ssh_engine.SSHSession") as mock_cls:
            engine._build_host_session(ctx)

        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args
        assert kwargs[1]["host"] == "1.2.3.4" or kwargs[0][0] == "1.2.3.4"

    def test_build_dpu_session_chains_host_as_jumphost(self):
        engine = SSHEngine()
        ctx = _make_ctx()
        ctx.dpu_host = "192.168.100.2"
        ctx.dpu_username = "ubuntu"
        ctx.dpu_password = "password"

        with patch("services.execution.ssh_engine.SSHSession") as mock_cls:
            engine._build_dpu_session(ctx)

        call_kwargs = mock_cls.call_args[1] if mock_cls.call_args[1] else {}
        call_args = mock_cls.call_args[0] if mock_cls.call_args[0] else ()
        # DPU session should target dpu_host
        if call_args:
            assert call_args[0] == "192.168.100.2"
        else:
            assert call_kwargs.get("host") == "192.168.100.2"

    def test_get_session_routes_to_dpu_when_target_is_dpu(self):
        engine = SSHEngine()
        ctx = _make_ctx(variables={"target": "dpu"})
        ctx.dpu_host = "192.168.100.2"
        ctx.dpu_username = "ubuntu"

        with patch.object(engine, "_build_dpu_session") as mock_dpu, \
             patch.object(engine, "_build_host_session") as mock_host:
            mock_dpu.return_value = MagicMock()
            engine._get_session(ctx)

        mock_dpu.assert_called_once()
        mock_host.assert_not_called()

    def test_get_session_routes_to_host_by_default(self):
        engine = SSHEngine()
        ctx = _make_ctx(variables={"target": "host"})

        with patch.object(engine, "_build_dpu_session") as mock_dpu, \
             patch.object(engine, "_build_host_session") as mock_host:
            mock_host.return_value = MagicMock()
            engine._get_session(ctx)

        mock_host.assert_called_once()
        mock_dpu.assert_not_called()
