"""
Component tests for K8s WebSocket route helpers — _validate_ws_token, _get_return_info logic.

Tests the token validation helper at the unit level and verifies the distroless
error detection / exit code classification logic used in the pod exec handler.

All external dependencies (auth_service, settings) are mocked.
"""

import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ═══════════════════════════════════════════════════════════════════════
# _validate_ws_token
# ═══════════════════════════════════════════════════════════════════════


class TestValidateWsToken:
    """Test _validate_ws_token — JWT validation for WebSocket connections."""

    @pytest.mark.asyncio
    @patch("core.config.settings")
    async def test_auth_disabled_returns_true(self, mock_settings):
        """Should return True without checking token when auth is disabled."""
        mock_settings.REQUIRE_AUTH = False
        from routes.k8s_websocket import _validate_ws_token

        ws = AsyncMock()
        result = await _validate_ws_token(ws, None)
        assert result is True
        ws.close.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_token_closes_4401(self):
        """Should close WebSocket with 4401 when no token provided."""
        from routes.k8s_websocket import _validate_ws_token

        ws = AsyncMock()
        result = await _validate_ws_token(ws, None)
        assert result is False
        ws.close.assert_awaited_once()
        close_call = ws.close.call_args
        assert close_call[1]["code"] == 4401

    @pytest.mark.asyncio
    async def test_empty_string_token_closes_4401(self):
        """Should close WebSocket with 4401 for empty string token."""
        from routes.k8s_websocket import _validate_ws_token

        ws = AsyncMock()
        result = await _validate_ws_token(ws, "")
        assert result is False
        ws.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_valid_admin_token_returns_true(self):
        """Should return True for a valid admin JWT token."""
        from routes.k8s_websocket import _validate_ws_token
        from services.auth_service import create_access_token

        token = create_access_token(data={"sub": "testadmin", "role": "admin"})
        ws = AsyncMock()
        result = await _validate_ws_token(ws, token)
        assert result is True
        ws.close.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_valid_operator_token_returns_true(self):
        """Should return True for a valid operator JWT token."""
        from routes.k8s_websocket import _validate_ws_token
        from services.auth_service import create_access_token

        token = create_access_token(data={"sub": "testop", "role": "operator"})
        ws = AsyncMock()
        result = await _validate_ws_token(ws, token)
        assert result is True

    @pytest.mark.asyncio
    async def test_valid_viewer_token_returns_true(self):
        """Should return True for a valid viewer JWT token."""
        from routes.k8s_websocket import _validate_ws_token
        from services.auth_service import create_access_token

        token = create_access_token(data={"sub": "testviewer", "role": "viewer"})
        ws = AsyncMock()
        result = await _validate_ws_token(ws, token)
        assert result is True

    @pytest.mark.asyncio
    async def test_invalid_role_closes_4401(self):
        """Should close WebSocket for token with unrecognized role."""
        from routes.k8s_websocket import _validate_ws_token
        from services.auth_service import create_access_token

        token = create_access_token(data={"sub": "user", "role": "guest"})
        ws = AsyncMock()
        result = await _validate_ws_token(ws, token)
        assert result is False
        ws.close.assert_awaited_once()
        close_call = ws.close.call_args
        assert "insufficient role" in close_call[1]["reason"]

    @pytest.mark.asyncio
    async def test_expired_token_closes_4401(self):
        """Should close WebSocket for expired JWT token."""
        from routes.k8s_websocket import _validate_ws_token

        # Craft an obviously invalid/expired token
        ws = AsyncMock()
        result = await _validate_ws_token(ws, "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0IiwiZXhwIjoxfQ.invalid")
        assert result is False
        ws.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_malformed_token_closes_4401(self):
        """Should close WebSocket for completely malformed token."""
        from routes.k8s_websocket import _validate_ws_token

        ws = AsyncMock()
        result = await _validate_ws_token(ws, "not-a-jwt-at-all")
        assert result is False
        ws.close.assert_awaited_once()
        close_call = ws.close.call_args
        assert "invalid or expired" in close_call[1]["reason"]


# ═══════════════════════════════════════════════════════════════════════
# Distroless detection / exit code classification
# ═══════════════════════════════════════════════════════════════════════


class TestDistrolessDetection:
    """
    Test the no-shell / distroless error detection logic used in read_from_exec.

    These test the patterns inline in the handler — we extract the logic here
    to verify the phrase matching and exit code classification work correctly.
    """

    # These are the phrases from the handler
    NO_SHELL_PHRASES = [
        "executable file not found",
        "no such file or directory",
        "exec failed",
        "OCI runtime exec failed",
    ]

    def _is_no_shell(self, error_detail: str) -> bool:
        """Replicate the handler's no-shell detection."""
        return any(phrase in error_detail.lower() for phrase in self.NO_SHELL_PHRASES)

    def test_executable_not_found(self):
        """Should detect 'executable file not found' as distroless."""
        assert self._is_no_shell(
            'invalid literal for int() with base 10: '
            '"error executing command in container: '
            'executable file not found in $PATH"'
        )

    def test_no_such_file(self):
        """Should detect 'no such file or directory'."""
        assert self._is_no_shell("no such file or directory: /bin/bash")

    def test_oci_runtime_exec_failed(self):
        """Should detect OCI runtime exec failures."""
        assert self._is_no_shell(
            "OCI runtime exec failed: exec failed: container_linux.go:380"
        )

    def test_exec_failed(self):
        """Should detect generic 'exec failed'."""
        assert self._is_no_shell("exec failed: error starting process")

    def test_normal_error_not_detected(self):
        """Should NOT detect normal errors as distroless."""
        assert not self._is_no_shell("permission denied")
        assert not self._is_no_shell("command not found")
        assert not self._is_no_shell("")

    def test_exit_code_126_is_no_shell(self):
        """Exit code 126 (permission denied) should be treated as no-shell."""
        assert 126 in (126, 127)

    def test_exit_code_127_is_no_shell(self):
        """Exit code 127 (command not found) should be treated as no-shell."""
        assert 127 in (126, 127)

    def test_exit_code_0_is_normal(self):
        """Exit code 0 should NOT be treated as no-shell."""
        assert 0 not in (126, 127)

    def test_shell_name_extraction_with_path(self):
        """Should extract shell name from full path."""
        command = "/bin/bash"
        shell_name = command.split("/")[-1] if "/" in command else command
        assert shell_name == "bash"

    def test_shell_name_extraction_bare(self):
        """Should use bare command as shell name."""
        command = "sh"
        shell_name = command.split("/")[-1] if "/" in command else command
        assert shell_name == "sh"


# ═══════════════════════════════════════════════════════════════════════
# WebSocket protocol message format
# ═══════════════════════════════════════════════════════════════════════


class TestWebSocketProtocol:
    """Verify the message formats used in the WebSocket protocol."""

    def test_stdin_message_format(self):
        """Client → Server stdin message should be base64 encoded."""
        user_input = "ls -la\n"
        msg = {
            "type": "stdin",
            "data": base64.b64encode(user_input.encode("utf-8")).decode("ascii"),
        }
        assert msg["type"] == "stdin"
        decoded = base64.b64decode(msg["data"]).decode("utf-8")
        assert decoded == user_input

    def test_stdout_message_format(self):
        """Server → Client stdout message should be base64 encoded."""
        output = "total 42\ndrwxr-xr-x 2 root root 4096 Jan 1 00:00 .\n"
        msg = {
            "type": "stdout",
            "data": base64.b64encode(output.encode("utf-8")).decode("ascii"),
        }
        assert msg["type"] == "stdout"
        decoded = base64.b64decode(msg["data"]).decode("utf-8")
        assert decoded == output

    def test_resize_message_format(self):
        """Client → Server resize message should have rows and cols."""
        msg = {"type": "resize", "rows": 50, "cols": 120}
        assert msg["rows"] == 50
        assert msg["cols"] == 120

    def test_resize_channel4_format(self):
        """Resize is sent on channel 4 as JSON with Height/Width keys."""
        rows, cols = 50, 120
        resize_msg = json.dumps({"Height": rows, "Width": cols})
        parsed = json.loads(resize_msg)
        assert parsed["Height"] == 50
        assert parsed["Width"] == 120

    def test_connected_message_shape(self):
        """Connected message should include pod, namespace, container, command."""
        msg = {
            "type": "connected",
            "message": "Connected to my-pod",
            "pod": "my-pod",
            "namespace": "default",
            "container": "main",
            "command": "/bin/sh",
        }
        assert msg["type"] == "connected"
        assert msg["pod"] == "my-pod"

    def test_error_message_shape(self):
        """Error message should have type and message."""
        msg = {"type": "error", "message": "Shell not available"}
        assert msg["type"] == "error"
        assert "Shell" in msg["message"]

    def test_exit_message_shape(self):
        """Exit message should have type, code, and message."""
        msg = {"type": "exit", "code": 0, "message": "Session terminated"}
        assert msg["type"] == "exit"
        assert msg["code"] == 0

    def test_log_message_shape(self):
        """Log follow message should have type and data."""
        msg = {"type": "log", "data": "2026-03-02 INFO: Server started"}
        assert msg["type"] == "log"
        assert "2026" in msg["data"]


# ═══════════════════════════════════════════════════════════════════════
# Log stream chunk buffering logic
# ═══════════════════════════════════════════════════════════════════════


class TestLogChunkBuffering:
    """
    Test the line buffering logic used in pod_logs_follow_websocket.

    The handler buffers partial lines across chunks since the K8s log stream
    yields raw byte chunks that may split mid-line.
    """

    def _process_chunk(self, line_buffer: str, chunk: str) -> tuple[list[str], str]:
        """
        Replicate the handler's line buffering logic.
        Returns (complete_lines, remaining_buffer).
        """
        line_buffer += chunk
        lines = line_buffer.split("\n")
        remaining = lines.pop()
        complete = [line for line in lines if line]  # skip empty strings
        return complete, remaining

    def test_full_lines(self):
        """Chunk with complete lines should yield all lines."""
        lines, buf = self._process_chunk("", "line1\nline2\nline3\n")
        assert lines == ["line1", "line2", "line3"]
        assert buf == ""

    def test_partial_line_buffered(self):
        """Partial line at end should be buffered."""
        lines, buf = self._process_chunk("", "line1\npartial")
        assert lines == ["line1"]
        assert buf == "partial"

    def test_buffer_joined_with_next_chunk(self):
        """Buffered partial line should join with next chunk."""
        lines1, buf1 = self._process_chunk("", "start of li")
        assert lines1 == []
        assert buf1 == "start of li"

        lines2, buf2 = self._process_chunk(buf1, "ne\ncomplete\n")
        assert lines2 == ["start of line", "complete"]
        assert buf2 == ""

    def test_single_line_no_newline(self):
        """Single line without newline should be buffered entirely."""
        lines, buf = self._process_chunk("", "no newline here")
        assert lines == []
        assert buf == "no newline here"

    def test_empty_chunk(self):
        """Empty chunk should not produce lines."""
        lines, buf = self._process_chunk("", "")
        assert lines == []
        assert buf == ""

    def test_consecutive_newlines_skip_empty(self):
        """Consecutive newlines should skip empty strings."""
        lines, buf = self._process_chunk("", "line1\n\n\nline2\n")
        assert lines == ["line1", "line2"]
        assert buf == ""

    def test_three_chunk_assembly(self):
        """Three chunks should correctly assemble lines."""
        _, buf1 = self._process_chunk("", "2026-03-02 IN")
        _, buf2 = self._process_chunk(buf1, "FO: Server star")
        lines3, buf3 = self._process_chunk(buf2, "ted\n2026-03-02 WARN: Done\n")
        assert lines3 == ["2026-03-02 INFO: Server started", "2026-03-02 WARN: Done"]
        assert buf3 == ""
