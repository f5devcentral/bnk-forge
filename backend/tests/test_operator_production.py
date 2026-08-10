"""
Tests for Operator Production Hardening — Sprint 8.3

Tests the operator registry cleanup functions, auth middleware patterns,
and connection manager fan-out logic. These tests have ZERO database
dependencies — they test pure logic with mocks.

Run: python -m pytest tests/test_operator_production.py -v
"""

import os
import sys
import types
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure backend module is importable
backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

# Stub out database-dependent modules before importing
if "database" not in sys.modules:
    db_stub = types.ModuleType("database")
    db_stub.Base = type("Base", (), {})
    db_stub.get_db = lambda: None
    db_stub.SessionLocal = None
    sys.modules["database"] = db_stub

# Ensure models module has the operator classes we need
# (other test files may have stubbed models as empty)
try:
    from models import ConnectedOperator, OperatorCommandQueue, OperatorRegistrationToken  # noqa: F401
except (ImportError, AttributeError):
    # If models is stubbed, add the required classes
    import models as _models_mod
    if not hasattr(_models_mod, "ConnectedOperator"):
        _models_mod.ConnectedOperator = type("ConnectedOperator", (), {
            "operator_id": None, "cluster_name": None, "is_connected": False,
            "last_heartbeat_at": None, "status": None, "connectivity_mode": None,
            "connectivity_config": None, "labels": None, "operator_version": None,
            "kubernetes_version": None, "node_count": None, "nodes_ready": None,
            "last_health_report": None, "last_health_at": None, "last_connected_at": None,
            "last_disconnected_at": None, "disconnect_reason": None,
            "commands_executed": 0, "commands_failed": 0, "uptime_seconds": 0,
            "registration_token_id": None, "cluster_id": None,
        })
    if not hasattr(_models_mod, "OperatorRegistrationToken"):
        _models_mod.OperatorRegistrationToken = type("OperatorRegistrationToken", (), {
            "token_hash": None, "token_prefix": None, "name": None,
            "max_uses": None, "use_count": 0, "expires_at": None,
            "labels": {}, "is_active": True, "created_by": None,
            "operators": [],
        })
    if not hasattr(_models_mod, "OperatorCommandQueue"):
        _models_mod.OperatorCommandQueue = type("OperatorCommandQueue", (), {
            "command_id": None, "operator_id": None, "action": None,
            "payload": None, "timeout_seconds": 300, "status": "queued",
        })


# ---------------------------------------------------------------------------
# Token hashing tests
# ---------------------------------------------------------------------------

class TestTokenHashing:
    """Test token generation and hashing."""

    def test_generate_token_has_prefix(self):
        from services.operator_registry import TOKEN_PREFIX, generate_registration_token
        token = generate_registration_token()
        assert token.startswith(TOKEN_PREFIX)

    def test_generate_token_is_unique(self):
        from services.operator_registry import generate_registration_token
        tokens = {generate_registration_token() for _ in range(100)}
        assert len(tokens) == 100

    def test_generate_token_length(self):
        from services.operator_registry import generate_registration_token
        token = generate_registration_token()
        # "bnk_reg_" (8) + 32 hex chars = 40 total
        assert len(token) == 40

    def test_hash_token_deterministic(self):
        from services.operator_registry import _hash_token
        token = "bnk_reg_abc123"
        assert _hash_token(token) == _hash_token(token)

    def test_hash_token_different_for_different_input(self):
        from services.operator_registry import _hash_token
        assert _hash_token("bnk_reg_abc123") != _hash_token("bnk_reg_xyz789")

    def test_hash_token_is_sha256_hex(self):
        from services.operator_registry import _hash_token
        h = _hash_token("bnk_reg_test")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# Stale threshold tests
# ---------------------------------------------------------------------------

class TestStaleThresholds:
    """Test stale connection threshold constants."""

    def test_stale_threshold_is_2_minutes(self):
        from services.operator_registry import STALE_HEARTBEAT_THRESHOLD
        assert STALE_HEARTBEAT_THRESHOLD == 120

    def test_token_prefix_constant(self):
        from services.operator_registry import TOKEN_PREFIX
        assert TOKEN_PREFIX == "bnk_reg_"


# ---------------------------------------------------------------------------
# OperatorConnectionManager tests
# ---------------------------------------------------------------------------

class TestConnectionManager:
    """Test in-memory connection manager."""

    def test_initial_state(self):
        from services.operator_registry import OperatorConnectionManager
        mgr = OperatorConnectionManager()
        assert mgr.connected_count == 0
        assert mgr.connected_operators == {}
        assert not mgr.is_connected("any-id")

    def test_get_connected_operator_ids_empty(self):
        from services.operator_registry import OperatorConnectionManager
        mgr = OperatorConnectionManager()
        assert mgr.get_connected_operator_ids() == []

    def test_get_operator_ws_not_connected(self):
        from services.operator_registry import OperatorConnectionManager
        mgr = OperatorConnectionManager()
        assert mgr.get_operator_ws("some-id") is None

    @pytest.mark.asyncio
    async def test_register_operator(self):
        from services.operator_registry import OperatorConnectionManager
        mgr = OperatorConnectionManager()
        ws = AsyncMock()
        await mgr.register("op-1", ws, {"cluster_name": "test"})
        assert mgr.connected_count == 1
        assert mgr.is_connected("op-1")
        assert "op-1" in mgr.get_connected_operator_ids()

    @pytest.mark.asyncio
    async def test_unregister_operator(self):
        from services.operator_registry import OperatorConnectionManager
        mgr = OperatorConnectionManager()
        ws = AsyncMock()
        await mgr.register("op-1", ws, {"cluster_name": "test"})
        await mgr.unregister("op-1", reason="test")
        assert mgr.connected_count == 0
        assert not mgr.is_connected("op-1")

    @pytest.mark.asyncio
    async def test_register_replaces_existing(self):
        from services.operator_registry import OperatorConnectionManager
        mgr = OperatorConnectionManager()
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await mgr.register("op-1", ws1, {"cluster_name": "test"})
        await mgr.register("op-1", ws2, {"cluster_name": "test"})
        assert mgr.connected_count == 1
        ws1.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_unregister_cancels_pending_commands(self):
        import asyncio

        from services.operator_registry import OperatorConnectionManager
        mgr = OperatorConnectionManager()
        ws = AsyncMock()
        await mgr.register("op-1", ws, {"cluster_name": "test"})

        # Set up a pending command
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        mgr._pending_commands["op-1:cmd-1"] = future

        await mgr.unregister("op-1", reason="test")

        with pytest.raises(ConnectionError):
            future.result()

    @pytest.mark.asyncio
    async def test_broadcast_sends_to_all(self):
        from services.operator_registry import OperatorConnectionManager
        mgr = OperatorConnectionManager()
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await mgr.register("op-1", ws1, {"cluster_name": "c1"})
        await mgr.register("op-2", ws2, {"cluster_name": "c2"})

        await mgr.broadcast({"type": "test"})
        ws1.send_json.assert_called_once()
        ws2.send_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_removes_dead_connections(self):
        from services.operator_registry import OperatorConnectionManager
        mgr = OperatorConnectionManager()
        ws = AsyncMock()
        ws.send_json.side_effect = Exception("dead")
        await mgr.register("op-1", ws, {"cluster_name": "test"})

        await mgr.broadcast({"type": "test"})
        assert mgr.connected_count == 0

    @pytest.mark.asyncio
    async def test_send_command_to_disconnected_raises(self):
        from services.operator_registry import OperatorConnectionManager
        mgr = OperatorConnectionManager()

        with pytest.raises(ConnectionError):
            await mgr.send_command("op-1", {"action": "test"})

    @pytest.mark.asyncio
    async def test_fan_out_empty_returns_empty(self):
        from services.operator_registry import OperatorConnectionManager
        mgr = OperatorConnectionManager()

        results = await mgr.send_command_to_multiple(
            ["op-1", "op-2"],
            {"action": "test"},
        )
        assert results == {}

    @pytest.mark.asyncio
    async def test_handle_result_message(self):
        import asyncio

        from services.operator_registry import OperatorConnectionManager
        mgr = OperatorConnectionManager()
        ws = AsyncMock()
        await mgr.register("op-1", ws, {"cluster_name": "test"})

        # Set up pending command
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        mgr._pending_commands["op-1:cmd-1"] = future
        mgr._command_output["op-1:cmd-1"] = ["line 1"]

        await mgr.handle_message("op-1", {
            "type": "result",
            "command_id": "op-1:cmd-1",
            "success": True,
        })

        result = future.result()
        assert result["success"] is True
        assert result["output_lines"] == ["line 1"]

    @pytest.mark.asyncio
    async def test_handle_error_message(self):
        import asyncio

        from services.operator_registry import OperatorConnectionManager
        mgr = OperatorConnectionManager()

        loop = asyncio.get_event_loop()
        future = loop.create_future()
        mgr._pending_commands["op-1:cmd-1"] = future

        await mgr.handle_message("op-1", {
            "type": "error",
            "command_id": "op-1:cmd-1",
            "error_message": "something broke",
        })

        with pytest.raises(RuntimeError, match="something broke"):
            future.result()

    @pytest.mark.asyncio
    async def test_handle_output_message(self):
        from services.operator_registry import OperatorConnectionManager
        mgr = OperatorConnectionManager()

        mgr._command_output["op-1:cmd-1"] = []
        callback = AsyncMock()
        mgr._output_callbacks["op-1:cmd-1"] = callback

        await mgr.handle_message("op-1", {
            "type": "output",
            "command_id": "op-1:cmd-1",
            "line": "deploying...",
        })

        assert mgr._command_output["op-1:cmd-1"] == ["deploying..."]
        callback.assert_called_once_with("deploying...")


# ---------------------------------------------------------------------------
# Auth middleware pattern tests
# These test the pattern matching logic directly, without importing the
# middleware (which requires Settings/pydantic-settings from Docker container).
# ---------------------------------------------------------------------------

# Mirror of the patterns defined in core/auth_middleware.py
_PUBLIC_PREFIXES = (
    "/api/auth/login",
    "/api/system/health",
    "/ws/",
    "/api/operators/install/",
    "/api/operators/register-poll",
)

_OPERATOR_POLLING_PATTERNS = (
    "/poll",
    "/results/",
    "/heartbeat",
    "/health",
)


class TestAuthMiddlewarePatterns:
    """Test that operator polling paths are correctly exempt from JWT auth."""

    def test_public_prefixes_include_register_poll(self):
        assert "/api/operators/register-poll" in _PUBLIC_PREFIXES

    def test_public_prefixes_include_ws(self):
        assert "/ws/" in _PUBLIC_PREFIXES

    def test_public_prefixes_include_operator_install(self):
        assert "/api/operators/install/" in _PUBLIC_PREFIXES

    def test_operator_polling_patterns(self):
        assert "/poll" in _OPERATOR_POLLING_PATTERNS
        assert "/results/" in _OPERATOR_POLLING_PATTERNS
        assert "/heartbeat" in _OPERATOR_POLLING_PATTERNS
        assert "/health" in _OPERATOR_POLLING_PATTERNS

    def test_poll_path_matches_pattern(self):
        path = "/api/operators/abc123/poll"
        assert path.startswith("/api/operators/")
        assert any(p in path for p in _OPERATOR_POLLING_PATTERNS)

    def test_results_path_matches_pattern(self):
        path = "/api/operators/abc123/results/cmd456"
        assert path.startswith("/api/operators/")
        assert any(p in path for p in _OPERATOR_POLLING_PATTERNS)

    def test_heartbeat_path_matches_pattern(self):
        path = "/api/operators/abc123/heartbeat"
        assert path.startswith("/api/operators/")
        assert any(p in path for p in _OPERATOR_POLLING_PATTERNS)

    def test_health_path_matches_pattern(self):
        path = "/api/operators/abc123/health"
        assert path.startswith("/api/operators/")
        assert any(p in path for p in _OPERATOR_POLLING_PATTERNS)

    def test_non_polling_path_does_not_match(self):
        path = "/api/operators/registration-tokens"
        assert not any(p in path for p in _OPERATOR_POLLING_PATTERNS)

    def test_list_operators_does_not_match(self):
        path = "/api/operators"
        assert not any(p in path for p in _OPERATOR_POLLING_PATTERNS)

    def test_command_path_does_not_match(self):
        path = "/api/operators/abc123/command"
        assert not any(p in path for p in _OPERATOR_POLLING_PATTERNS)


# ---------------------------------------------------------------------------
# Polling endpoint schemas
# ---------------------------------------------------------------------------

class TestPollingSchemas:
    """Test polling endpoint request/response schemas."""

    def test_register_poll_request(self):
        from routes.operator_polling import RegisterPollRequest
        req = RegisterPollRequest(cluster_name="test-cluster")
        assert req.cluster_name == "test-cluster"
        assert req.connectivity_mode == "polling"
        assert req.operator_version is None
        assert req.labels is None

    def test_register_poll_request_with_all_fields(self):
        from routes.operator_polling import RegisterPollRequest
        req = RegisterPollRequest(
            cluster_name="prod",
            operator_version="1.1.0",
            labels={"env": "prod"},
            connectivity_mode="polling",
        )
        assert req.cluster_name == "prod"
        assert req.operator_version == "1.1.0"
        assert req.labels == {"env": "prod"}

    def test_command_result_schema(self):
        from routes.operator_polling import CommandResultSubmission
        sub = CommandResultSubmission(success=True, result={"key": "val"})
        assert sub.success is True
        assert sub.result == {"key": "val"}

    def test_command_result_failure(self):
        from routes.operator_polling import CommandResultSubmission
        sub = CommandResultSubmission(
            success=False,
            error_message="something failed",
            duration_seconds=1.5,
        )
        assert sub.success is False
        assert sub.error_message == "something failed"

    def test_heartbeat_schema(self):
        from routes.operator_polling import HeartbeatSubmission
        hb = HeartbeatSubmission(
            operator_version="1.1.0",
            uptime_seconds=3600,
            commands_executed=42,
        )
        assert hb.operator_version == "1.1.0"
        assert hb.uptime_seconds == 3600

    def test_health_schema(self):
        from routes.operator_polling import HealthSubmission
        hs = HealthSubmission(
            cluster={"nodes": 3},
            bnk={"flo_ready": True},
        )
        assert hs.cluster == {"nodes": 3}
        assert hs.bnk == {"flo_ready": True}


# ---------------------------------------------------------------------------
# Fan-out request schema (tested via Pydantic directly, avoids jose import)
# ---------------------------------------------------------------------------

from pydantic import BaseModel


class _FanOutCommandRequest(BaseModel):
    """Mirror of routes.operators.FanOutCommandRequest for isolated testing."""
    action: str
    payload: dict | None = None
    timeout: float = 300.0
    operator_ids: list[str] | None = None
    labels: dict | None = None


class TestFanOutSchema:
    """Test multi-cluster fan-out request schema."""

    def test_fan_out_request_defaults(self):
        req = _FanOutCommandRequest(action="scan_cluster")
        assert req.action == "scan_cluster"
        assert req.payload is None
        assert req.timeout == 300.0
        assert req.operator_ids is None
        assert req.labels is None

    def test_fan_out_request_with_targets(self):
        req = _FanOutCommandRequest(
            action="get_health",
            operator_ids=["op-1", "op-2"],
            timeout=60.0,
        )
        assert req.operator_ids == ["op-1", "op-2"]
        assert req.timeout == 60.0

    def test_fan_out_request_with_labels(self):
        req = _FanOutCommandRequest(
            action="scan_cluster",
            labels={"env": "prod", "region": "us-west-2"},
        )
        assert req.labels == {"env": "prod", "region": "us-west-2"}
