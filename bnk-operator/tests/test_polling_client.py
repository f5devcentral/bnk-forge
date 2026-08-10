"""
Tests for polling_client.py — HTTP polling alternative to WebSocket.

Covers:
  - Constructor and properties
  - Command handler registration
  - SSL context building
  - Auth headers
  - Registration flow (success, failure)
  - Poll and execute flow
  - Result submission with retries
  - Health report sending
  - Server-side poll interval adjustment
"""

import asyncio
import json
import ssl
import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sys
import os

operator_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if operator_root not in sys.path:
    sys.path.insert(0, operator_root)

from polling_client import PollingClient


# ---------------------------------------------------------------------------
# Helpers for aiohttp async context manager mocking
# ---------------------------------------------------------------------------


class FakeResponse:
    """Fake aiohttp response that works as an async context manager."""

    def __init__(self, status=200, json_data=None, text_data=""):
        self.status = status
        self._json_data = json_data or {}
        self._text_data = text_data

    async def json(self):
        return self._json_data

    async def text(self):
        return self._text_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class FakeSession:
    """Fake aiohttp ClientSession with controllable post/get responses."""

    def __init__(self):
        self._post_responses = []
        self._get_responses = []
        self._post_calls = []
        self._get_calls = []

    def set_post_response(self, resp):
        self._post_responses = [resp]

    def set_post_responses(self, resps):
        self._post_responses = list(resps)

    def set_get_response(self, resp):
        self._get_responses = [resp]

    def post(self, *args, **kwargs):
        self._post_calls.append((args, kwargs))
        if self._post_responses:
            resp = self._post_responses[0]
            if len(self._post_responses) > 1:
                self._post_responses.pop(0)
            return resp
        return FakeResponse(status=500)

    def get(self, *args, **kwargs):
        self._get_calls.append((args, kwargs))
        if self._get_responses:
            resp = self._get_responses[0]
            if len(self._get_responses) > 1:
                self._get_responses.pop(0)
            return resp
        return FakeResponse(status=500)

    @property
    def post_call_count(self):
        return len(self._post_calls)

    @property
    def get_call_count(self):
        return len(self._get_calls)


# =========================================================================
# Constructor & Properties
# =========================================================================


class TestConstructor:
    """Test PollingClient initialization."""

    def test_default_state(self):
        client = PollingClient(
            url="https://bnk-forge:443",
            token="bnk_reg_abc123",
            cluster_name="test-cluster",
        )
        assert client.url == "https://bnk-forge:443"
        assert client.token == "bnk_reg_abc123"
        assert client.cluster_name == "test-cluster"
        assert client.connected is False
        assert client.operator_id is None
        assert client.poll_interval == 5

    def test_trailing_slash_stripped(self):
        client = PollingClient(url="https://example.com/", token="t", cluster_name="c")
        assert client.url == "https://example.com"

    def test_custom_poll_interval(self):
        client = PollingClient(
            url="https://x", token="t", cluster_name="c",
            poll_interval=15,
        )
        assert client.poll_interval == 15

    def test_uptime(self):
        client = PollingClient(url="https://x", token="t", cluster_name="c")
        assert client.uptime_seconds >= 0

    def test_avg_duration_zero_initially(self):
        client = PollingClient(url="https://x", token="t", cluster_name="c")
        assert client.avg_command_duration == 0.0


# =========================================================================
# Command Registration
# =========================================================================


class TestCommandRegistration:
    def test_register_handler(self):
        client = PollingClient(url="https://x", token="t", cluster_name="c")

        async def handler(p, on_output=None):
            pass

        client.on_command("test", handler)
        assert "test" in client._handlers


# =========================================================================
# SSL Context
# =========================================================================


class TestSSLContext:
    def test_no_ssl_for_http(self):
        client = PollingClient(url="http://localhost", token="t", cluster_name="c")
        assert client._build_ssl_context() is None

    def test_ssl_for_https(self):
        client = PollingClient(url="https://bnk-forge", token="t", cluster_name="c")
        ctx = client._build_ssl_context()
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.check_hostname is True

    def test_insecure_ssl(self):
        client = PollingClient(
            url="https://bnk-forge", token="t", cluster_name="c",
            tls_insecure=True,
        )
        ctx = client._build_ssl_context()
        assert ctx.check_hostname is False
        assert ctx.verify_mode == ssl.CERT_NONE


# =========================================================================
# Auth Headers
# =========================================================================


class TestAuthHeaders:
    def test_headers_include_token(self):
        client = PollingClient(
            url="https://x", token="bnk_reg_abc",
            cluster_name="prod", operator_version="1.2.0",
        )
        headers = client._auth_headers()
        assert headers["Authorization"] == "Bearer bnk_reg_abc"
        assert headers["X-Operator-Version"] == "1.2.0"
        assert headers["X-Cluster-Name"] == "prod"


# =========================================================================
# Registration
# =========================================================================


class TestRegistration:
    async def test_successful_registration(self):
        client = PollingClient(url="https://x", token="t", cluster_name="c")
        session = FakeSession()
        session.set_post_response(FakeResponse(
            status=200,
            json_data={"operator_id": "op-456"},
        ))

        result = await client._register(session)

        assert result is True
        assert client.operator_id == "op-456"

    async def test_failed_registration(self):
        client = PollingClient(url="https://x", token="t", cluster_name="c")
        session = FakeSession()
        session.set_post_response(FakeResponse(status=401, text_data="Unauthorized"))

        result = await client._register(session)

        assert result is False
        assert client.operator_id is None

    async def test_registration_network_error(self):
        client = PollingClient(url="https://x", token="t", cluster_name="c")

        session = MagicMock()
        session.post.side_effect = Exception("connection refused")

        result = await client._register(session)

        assert result is False


# =========================================================================
# Poll and Execute
# =========================================================================


class TestPollAndExecute:
    async def test_no_poll_without_operator_id(self):
        """Should not poll if operator_id is not set."""
        client = PollingClient(url="https://x", token="t", cluster_name="c")
        client.operator_id = None

        session = FakeSession()
        await client._poll_and_execute(session)

        assert session.get_call_count == 0

    async def test_poll_with_no_commands(self):
        """Empty command list should be handled gracefully."""
        client = PollingClient(url="https://x", token="t", cluster_name="c")
        client.operator_id = "op-1"

        session = FakeSession()
        session.set_get_response(FakeResponse(
            status=200,
            json_data={"commands": []},
        ))

        await client._poll_and_execute(session)

        assert session.get_call_count == 1

    async def test_poll_401_disconnects(self):
        """401 response should mark client as disconnected."""
        client = PollingClient(url="https://x", token="t", cluster_name="c")
        client.operator_id = "op-1"
        client.connected = True

        session = FakeSession()
        session.set_get_response(FakeResponse(status=401))

        await client._poll_and_execute(session)

        assert client.connected is False

    async def test_server_poll_interval_adjustment(self):
        """Server hint should adjust poll interval."""
        client = PollingClient(url="https://x", token="t", cluster_name="c")
        client.operator_id = "op-1"

        session = FakeSession()
        session.set_get_response(FakeResponse(
            status=200,
            json_data={"commands": [], "poll_interval_seconds": 30},
        ))

        await client._poll_and_execute(session)

        assert client.poll_interval == 30

    async def test_poll_interval_clamped(self):
        """Server poll interval should be clamped to [1, 60]."""
        client = PollingClient(url="https://x", token="t", cluster_name="c")
        client.operator_id = "op-1"

        session = FakeSession()
        session.set_get_response(FakeResponse(
            status=200,
            json_data={"commands": [], "poll_interval_seconds": 999},
        ))

        await client._poll_and_execute(session)

        assert client.poll_interval == 60

    async def test_poll_interval_min_clamp(self):
        """Poll interval should not go below 1."""
        client = PollingClient(url="https://x", token="t", cluster_name="c")
        client.operator_id = "op-1"

        # 0 is falsy so the guard `if server_interval and isinstance(...)` skips it
        # Use -1 which is truthy but should be clamped to 1
        session = FakeSession()
        session.set_get_response(FakeResponse(
            status=200,
            json_data={"commands": [], "poll_interval_seconds": -1},
        ))

        await client._poll_and_execute(session)

        assert client.poll_interval == 1

    async def test_poll_interval_zero_ignored(self):
        """Zero poll interval from server is ignored (falsy guard)."""
        client = PollingClient(url="https://x", token="t", cluster_name="c")
        client.operator_id = "op-1"

        session = FakeSession()
        session.set_get_response(FakeResponse(
            status=200,
            json_data={"commands": [], "poll_interval_seconds": 0},
        ))

        await client._poll_and_execute(session)

        # Should keep default (0 is falsy, so guard skips)
        assert client.poll_interval == 5


# =========================================================================
# Command Execution
# =========================================================================


class TestExecuteCommand:
    async def test_unknown_action(self):
        """Unknown action should submit error result."""
        client = PollingClient(url="https://x", token="t", cluster_name="c")
        client.operator_id = "op-1"

        session = FakeSession()
        session.set_post_response(FakeResponse(status=200))

        await client._execute_command(session, {
            "id": "cmd-1", "action": "bogus", "payload": {},
        })

        assert session.post_call_count == 1

    async def test_successful_execution(self):
        """Successful command should track metrics."""
        client = PollingClient(url="https://x", token="t", cluster_name="c")
        client.operator_id = "op-1"

        async def handler(payload, on_output=None):
            return {"success": True}

        client.on_command("test", handler)

        session = FakeSession()
        session.set_post_response(FakeResponse(status=200))

        await client._execute_command(session, {
            "id": "cmd-1", "action": "test", "payload": {},
        })

        assert client._commands_executed == 1

    async def test_failed_execution(self):
        """Failed command should increment failure counter."""
        client = PollingClient(url="https://x", token="t", cluster_name="c")
        client.operator_id = "op-1"

        async def handler(payload, on_output=None):
            raise RuntimeError("crashed")

        client.on_command("bad", handler)

        session = FakeSession()
        session.set_post_response(FakeResponse(status=200))

        await client._execute_command(session, {
            "id": "cmd-1", "action": "bad", "payload": {},
        })

        assert client._commands_failed == 1

    async def test_output_lines_collected(self):
        """Output callback lines should be collected."""
        client = PollingClient(url="https://x", token="t", cluster_name="c")
        client.operator_id = "op-1"

        async def handler(payload, on_output=None):
            if on_output:
                await on_output("step 1")
                await on_output("step 2")
            return {"success": True}

        client.on_command("test", handler)

        session = FakeSession()
        session.set_post_response(FakeResponse(status=200))

        await client._execute_command(session, {
            "id": "cmd-1", "action": "test", "payload": {},
        })

        # Verify the POST body includes output_lines
        post_args = session._post_calls[0]
        post_kwargs = post_args[1]
        body = post_kwargs.get("json", {})
        assert "output_lines" in body
        assert len(body["output_lines"]) == 2


# =========================================================================
# Result Submission
# =========================================================================


class TestSubmitResult:
    async def test_successful_submission(self):
        client = PollingClient(url="https://x", token="t", cluster_name="c")
        client.operator_id = "op-1"

        session = FakeSession()
        session.set_post_response(FakeResponse(status=200))

        await client._submit_result(session, "cmd-1", {"success": True})

        assert session.post_call_count == 1

    async def test_retries_on_failure(self):
        """Should retry up to 3 times on non-200 responses."""
        client = PollingClient(url="https://x", token="t", cluster_name="c")
        client.operator_id = "op-1"

        session = FakeSession()
        session.set_post_response(FakeResponse(status=500))

        await client._submit_result(session, "cmd-1", {"success": True})

        assert session.post_call_count == 3

    async def test_retries_on_exception(self):
        """Should retry on network exceptions."""
        client = PollingClient(url="https://x", token="t", cluster_name="c")
        client.operator_id = "op-1"

        session = MagicMock()
        session.post.side_effect = Exception("network error")

        await client._submit_result(session, "cmd-1", {"success": True})

        assert session.post.call_count == 3


# =========================================================================
# Health Reporting
# =========================================================================


class TestSendHealth:
    async def test_send_health_when_connected(self):
        client = PollingClient(url="https://x", token="t", cluster_name="c")
        client.operator_id = "op-1"

        session = FakeSession()
        session.set_post_response(FakeResponse(status=200))
        client._session = session

        await client.send_health({"success": True})

        assert session.post_call_count == 1

    async def test_no_send_without_session(self):
        client = PollingClient(url="https://x", token="t", cluster_name="c")
        client._session = None

        # Should not raise
        await client.send_health({"success": True})

    async def test_no_send_without_operator_id(self):
        client = PollingClient(url="https://x", token="t", cluster_name="c")
        session = FakeSession()
        client._session = session
        client.operator_id = None

        await client.send_health({"success": True})

        assert session.post_call_count == 0

    async def test_send_health_error_handled(self):
        client = PollingClient(url="https://x", token="t", cluster_name="c")
        client.operator_id = "op-1"

        session = MagicMock()
        session.post.side_effect = Exception("network error")
        client._session = session

        # Should not raise
        await client.send_health({"success": True})
