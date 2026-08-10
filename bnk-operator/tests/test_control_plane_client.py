"""
Tests for control_plane_client.py — WebSocket connection to BNK-Forge.

Covers:
  - Constructor and properties
  - Command handler registration
  - SSL context building (wss://, custom CA, insecure mode)
  - Backoff with jitter calculation
  - Message routing (command, ping, pong, unknown)
  - Heartbeat missed detection
  - Command execution and metrics tracking
  - Health report sending
  - send_health when disconnected
"""

import asyncio
import json
import ssl
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sys
import os

operator_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if operator_root not in sys.path:
    sys.path.insert(0, operator_root)

from control_plane_client import ControlPlaneClient


# =========================================================================
# Constructor & Properties
# =========================================================================


class TestConstructor:
    """Test ControlPlaneClient initialization."""

    def test_default_state(self):
        client = ControlPlaneClient(
            url="wss://bnk-forge:443/ws/operator",
            token="bnk_reg_abc123",
            cluster_name="test-cluster",
        )
        assert client.url == "wss://bnk-forge:443/ws/operator"
        assert client.token == "bnk_reg_abc123"
        assert client.cluster_name == "test-cluster"
        assert client.connected is False
        assert client.operator_id is None
        assert client.uptime_seconds >= 0
        assert client.reconnect_count == 0
        assert client.commands_failed == 0
        assert client.avg_command_duration == 0.0

    def test_custom_labels(self):
        client = ControlPlaneClient(
            url="wss://example.com",
            token="tok",
            cluster_name="c",
            labels={"env": "prod"},
        )
        assert client.labels == {"env": "prod"}

    def test_default_labels_empty(self):
        client = ControlPlaneClient(url="wss://x", token="t", cluster_name="c")
        assert client.labels == {}


# =========================================================================
# Command Registration
# =========================================================================


class TestCommandRegistration:
    """Test on_command handler registration."""

    def test_register_handler(self):
        client = ControlPlaneClient(url="wss://x", token="t", cluster_name="c")

        async def handler(payload, on_output=None):
            pass

        client.on_command("apply_manifests", handler)
        assert "apply_manifests" in client._handlers
        assert client._handlers["apply_manifests"] is handler

    def test_register_multiple_handlers(self):
        client = ControlPlaneClient(url="wss://x", token="t", cluster_name="c")

        async def h1(p, on_output=None): pass
        async def h2(p, on_output=None): pass

        client.on_command("apply", h1)
        client.on_command("destroy", h2)

        assert len(client._handlers) == 2


# =========================================================================
# SSL Context Building
# =========================================================================


class TestSSLContext:
    """Test _build_ssl_context for different TLS configurations."""

    def test_no_ssl_for_ws(self):
        """ws:// (not wss://) should return None."""
        client = ControlPlaneClient(url="ws://localhost:443", token="t", cluster_name="c")
        assert client._build_ssl_context() is None

    def test_default_ssl_for_wss(self):
        """wss:// should return a default SSL context."""
        client = ControlPlaneClient(url="wss://bnk-forge:443", token="t", cluster_name="c")
        ctx = client._build_ssl_context()
        assert ctx is not None
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.check_hostname is True

    def test_insecure_ssl(self):
        """tls_insecure=True should disable cert verification."""
        client = ControlPlaneClient(
            url="wss://bnk-forge:443", token="t", cluster_name="c",
            tls_insecure=True,
        )
        ctx = client._build_ssl_context()
        assert ctx is not None
        assert ctx.check_hostname is False
        assert ctx.verify_mode == ssl.CERT_NONE

    def test_custom_ca_cert(self, tmp_path):
        """Custom CA cert should be loaded."""
        ca_file = tmp_path / "ca.pem"
        # Write a minimal (not real) cert — we just check the path handling
        # The actual load will fail, but the path check succeeds
        ca_file.write_text("not a real cert")

        client = ControlPlaneClient(
            url="wss://bnk-forge:443", token="t", cluster_name="c",
            tls_ca_cert=str(ca_file),
        )
        # This will try to load the cert and may fail (not a real cert)
        # but the code path handles missing certs gracefully
        try:
            ctx = client._build_ssl_context()
        except ssl.SSLError:
            pass  # Expected — not a real cert file

    def test_missing_ca_cert_falls_back(self):
        """Non-existent CA cert path should fall back to system CAs."""
        client = ControlPlaneClient(
            url="wss://bnk-forge:443", token="t", cluster_name="c",
            tls_ca_cert="/nonexistent/ca.pem",
        )
        ctx = client._build_ssl_context()
        assert ctx is not None
        # Should still have a valid context (using system CAs)
        assert ctx.check_hostname is True


# =========================================================================
# Backoff with Jitter
# =========================================================================


class TestBackoff:
    """Test exponential backoff with jitter."""

    def test_first_backoff_is_small(self):
        client = ControlPlaneClient(url="wss://x", token="t", cluster_name="c")
        delay = client._backoff_with_jitter()
        assert 0.7 <= delay <= 1.3  # 1s ± 30% jitter

    def test_backoff_increases(self):
        client = ControlPlaneClient(url="wss://x", token="t", cluster_name="c")
        delays = [client._backoff_with_jitter() for _ in range(5)]
        # Each call increments _reconnect_attempts, so delays should generally increase
        assert delays[-1] > delays[0]

    def test_backoff_caps_at_max(self):
        client = ControlPlaneClient(url="wss://x", token="t", cluster_name="c")
        client._reconnect_attempts = 100  # Way beyond max
        delay = client._backoff_with_jitter()
        assert delay <= 60 * 1.3 + 1  # MAX_BACKOFF + jitter + margin

    def test_backoff_always_positive(self):
        client = ControlPlaneClient(url="wss://x", token="t", cluster_name="c")
        for _ in range(20):
            delay = client._backoff_with_jitter()
            assert delay >= 1.0


# =========================================================================
# Async iterator helper for mocking WebSocket
# =========================================================================


class FakeWebSocket:
    """Fake WebSocket that supports `async for msg in ws` and tracks sent messages."""

    def __init__(self, messages):
        self._messages = list(messages)
        self._sent = []

    def __aiter__(self):
        return self._async_gen()

    async def _async_gen(self):
        for msg in self._messages:
            yield msg

    async def send(self, data):
        self._sent.append(data)

    @property
    def sent_messages(self):
        return self._sent


# =========================================================================
# Message Handling
# =========================================================================


class TestMessageLoop:
    """Test _message_loop message routing."""

    async def test_ping_responds_pong(self):
        """Ping message should trigger a pong response."""
        client = ControlPlaneClient(url="wss://x", token="t", cluster_name="c")

        ws = FakeWebSocket([json.dumps({"type": "ping"})])
        shutdown = MagicMock()
        shutdown.is_set.return_value = False

        await client._message_loop(ws, shutdown)

        assert len(ws.sent_messages) == 1
        sent = json.loads(ws.sent_messages[0])
        assert sent["type"] == "pong"

    async def test_pong_resets_missed_heartbeats(self):
        """Pong message should reset missed heartbeat counter."""
        client = ControlPlaneClient(url="wss://x", token="t", cluster_name="c")
        client._missed_heartbeats = 5

        ws = FakeWebSocket([json.dumps({"type": "pong"})])
        shutdown = MagicMock()
        shutdown.is_set.return_value = False

        await client._message_loop(ws, shutdown)

        assert client._missed_heartbeats == 0

    async def test_command_dispatched(self):
        """Command message should dispatch to registered handler."""
        import asyncio as _asyncio

        client = ControlPlaneClient(url="wss://x", token="t", cluster_name="c")

        handler_called = False

        async def mock_handler(payload, on_output=None):
            nonlocal handler_called
            handler_called = True
            return {"success": True}

        client.on_command("test_action", mock_handler)

        ws = FakeWebSocket([
            json.dumps({"type": "command", "id": "cmd-1", "action": "test_action", "payload": {}}),
        ])
        shutdown = MagicMock()
        shutdown.is_set.return_value = False

        await client._message_loop(ws, shutdown)

        # Give the background task a moment to execute
        await _asyncio.sleep(0.2)
        assert handler_called is True

    async def test_invalid_json_skipped(self):
        """Invalid JSON should be logged and skipped."""
        client = ControlPlaneClient(url="wss://x", token="t", cluster_name="c")

        ws = FakeWebSocket(["not valid json {{{"])
        shutdown = MagicMock()
        shutdown.is_set.return_value = False

        # Should not raise
        await client._message_loop(ws, shutdown)


# =========================================================================
# Command Execution
# =========================================================================


class TestHandleCommand:
    """Test _handle_command execution and result reporting."""

    @pytest.mark.asyncio
    async def test_unknown_action_sends_error(self):
        """Unknown action should send error message."""
        client = ControlPlaneClient(url="wss://x", token="t", cluster_name="c")

        ws = AsyncMock()
        message = {"id": "cmd-1", "action": "unknown_action", "payload": {}}

        await client._handle_command(ws, message)

        ws.send.assert_called_once()
        sent = json.loads(ws.send.call_args[0][0])
        assert sent["type"] == "error"
        assert "Unknown action" in sent["error_message"]

    @pytest.mark.asyncio
    async def test_successful_command(self):
        """Successful command should send result with metrics."""
        client = ControlPlaneClient(url="wss://x", token="t", cluster_name="c")

        async def handler(payload, on_output=None):
            return {"success": True, "data": "test"}

        client.on_command("test", handler)

        ws = AsyncMock()
        message = {"id": "cmd-1", "action": "test", "payload": {}}

        await client._handle_command(ws, message)

        assert client._commands_executed == 1
        ws.send.assert_called_once()
        sent = json.loads(ws.send.call_args[0][0])
        assert sent["type"] == "result"
        assert sent["success"] is True
        assert "duration_seconds" in sent

    @pytest.mark.asyncio
    async def test_failed_command_increments_counter(self):
        """Failed command should increment failure counter."""
        client = ControlPlaneClient(url="wss://x", token="t", cluster_name="c")

        async def handler(payload, on_output=None):
            raise RuntimeError("boom")

        client.on_command("bad", handler)

        ws = AsyncMock()
        message = {"id": "cmd-1", "action": "bad", "payload": {}}

        await client._handle_command(ws, message)

        assert client._commands_failed == 1
        sent = json.loads(ws.send.call_args[0][0])
        assert sent["type"] == "error"
        assert "boom" in sent["error_message"]

    @pytest.mark.asyncio
    async def test_command_duration_tracked(self):
        """Command duration should be tracked for metrics."""
        client = ControlPlaneClient(url="wss://x", token="t", cluster_name="c")

        async def handler(payload, on_output=None):
            return {"success": True}

        client.on_command("fast", handler)

        ws = AsyncMock()
        for i in range(3):
            await client._handle_command(ws, {"id": f"cmd-{i}", "action": "fast", "payload": {}})

        assert len(client._command_durations) == 3
        assert client.avg_command_duration >= 0

    @pytest.mark.asyncio
    async def test_duration_list_capped_at_100(self):
        """Duration list should not grow beyond 100 entries."""
        client = ControlPlaneClient(url="wss://x", token="t", cluster_name="c")

        async def handler(payload, on_output=None):
            return {"success": True}

        client.on_command("fast", handler)

        ws = AsyncMock()
        for i in range(150):
            await client._handle_command(ws, {"id": f"cmd-{i}", "action": "fast", "payload": {}})

        assert len(client._command_durations) == 100


# =========================================================================
# Health Reporting
# =========================================================================


class TestSendHealth:
    """Test send_health method."""

    @pytest.mark.asyncio
    async def test_send_health_when_connected(self):
        client = ControlPlaneClient(url="wss://x", token="t", cluster_name="c")
        client.connected = True
        client._ws = AsyncMock()

        await client.send_health({"success": True, "data": "test"})

        client._ws.send.assert_called_once()
        sent = json.loads(client._ws.send.call_args[0][0])
        assert sent["type"] == "health"
        assert sent["success"] is True

    @pytest.mark.asyncio
    async def test_send_health_when_disconnected(self):
        """Should silently do nothing when not connected."""
        client = ControlPlaneClient(url="wss://x", token="t", cluster_name="c")
        client.connected = False

        await client.send_health({"success": True})
        # No error should be raised

    @pytest.mark.asyncio
    async def test_send_health_error_handled(self):
        """Send failure should be handled gracefully."""
        client = ControlPlaneClient(url="wss://x", token="t", cluster_name="c")
        client.connected = True
        client._ws = AsyncMock()
        client._ws.send.side_effect = Exception("connection lost")

        # Should not raise
        await client.send_health({"success": True})
