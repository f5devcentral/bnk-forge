"""
BC-023: Component tests for WebSocketManager and RedisSubscriber.

All Redis/WebSocket I/O is mocked.
"""

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.websocket_service import (
    RedisSubscriber,
    WebSocketManager,
    publish_task_update,
)

# ── WebSocketManager ─────────────────────────────────────────────────

class TestWebSocketManager:
    @pytest.mark.asyncio
    async def test_connect(self):
        mgr = WebSocketManager()
        ws = AsyncMock()
        await mgr.connect(ws)
        assert ws in mgr.active_connections
        ws.accept.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnect(self):
        mgr = WebSocketManager()
        ws = AsyncMock()
        await mgr.connect(ws)
        mgr.disconnect(ws)
        assert ws not in mgr.active_connections

    @pytest.mark.asyncio
    async def test_disconnect_not_connected(self):
        mgr = WebSocketManager()
        ws = AsyncMock()
        mgr.disconnect(ws)  # No error
        assert len(mgr.active_connections) == 0

    @pytest.mark.asyncio
    async def test_broadcast(self):
        mgr = WebSocketManager()
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await mgr.connect(ws1)
        await mgr.connect(ws2)

        msg = {"type": "task_update", "task_id": 1}
        await mgr.broadcast(msg)

        ws1.send_json.assert_awaited_once_with(msg)
        ws2.send_json.assert_awaited_once_with(msg)

    @pytest.mark.asyncio
    async def test_broadcast_removes_failed_connections(self):
        mgr = WebSocketManager()
        ws_ok = AsyncMock()
        ws_bad = AsyncMock()
        ws_bad.send_json.side_effect = Exception("connection closed")

        await mgr.connect(ws_ok)
        await mgr.connect(ws_bad)
        assert len(mgr.active_connections) == 2

        await mgr.broadcast({"type": "test"})

        # Bad connection should be removed
        assert ws_bad not in mgr.active_connections
        assert ws_ok in mgr.active_connections

    @pytest.mark.asyncio
    async def test_broadcast_empty_connections(self):
        mgr = WebSocketManager()
        # Should not raise
        await mgr.broadcast({"type": "test"})

    @pytest.mark.asyncio
    async def test_send_ping(self):
        mgr = WebSocketManager()
        ws = AsyncMock()
        await mgr.connect(ws)
        await mgr.send_ping()
        ws.send_json.assert_awaited_once()
        call_args = ws.send_json.call_args[0][0]
        assert call_args["type"] == "ping"
        assert "timestamp" in call_args


# ── RedisSubscriber ──────────────────────────────────────────────────

class TestRedisSubscriber:
    def test_init(self):
        sub = RedisSubscriber("redis://localhost:6379/0", "test-channel")
        assert sub.redis_url == "redis://localhost:6379/0"
        assert sub.channel == "test-channel"

    @pytest.mark.asyncio
    async def test_listen_not_connected_raises(self):
        sub = RedisSubscriber("redis://localhost:6379/0", "test-channel")
        with pytest.raises(RuntimeError, match="Not connected"):
            await sub.listen()


# ── publish_task_update ──────────────────────────────────────────────

class TestPublishTaskUpdate:
    @patch("services.websocket_service.get_sync_redis_client")
    def test_publish_basic(self, mock_get_client):
        mock_redis = MagicMock()
        mock_get_client.return_value = mock_redis

        publish_task_update(
            task_id=1, status="completed", project_id=10,
            module_id=5, task_type="apply",
        )

        mock_redis.publish.assert_called_once()
        channel, data = mock_redis.publish.call_args[0]
        assert channel == "bnk-forge:task-updates"
        parsed = json.loads(data)
        assert parsed["task_id"] == 1
        assert parsed["status"] == "completed"

    @patch("services.websocket_service.get_sync_redis_client")
    def test_publish_with_optional_fields(self, mock_get_client):
        mock_redis = MagicMock()
        mock_get_client.return_value = mock_redis

        publish_task_update(
            task_id=2, status="failed", project_id=10,
            exit_code=1, error="something failed", duration_seconds=5.5,
            metadata={"key": "val"},
        )

        _, data = mock_redis.publish.call_args[0]
        parsed = json.loads(data)
        assert parsed["exit_code"] == 1
        assert parsed["error"] == "something failed"
        assert parsed["duration_seconds"] == 5.5
        assert parsed["metadata"] == {"key": "val"}

    @patch("services.websocket_service.get_sync_redis_client")
    def test_publish_handles_redis_error(self, mock_get_client):
        mock_redis = MagicMock()
        mock_redis.publish.side_effect = Exception("Redis down")
        mock_get_client.return_value = mock_redis

        # Should not raise — errors are logged and suppressed
        publish_task_update(task_id=3, status="completed", project_id=10)
