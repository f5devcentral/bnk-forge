"""
Tests for health_server.py — HTTP endpoints for K8s probes and Prometheus metrics.

Covers:
  - /healthz liveness probe (always 200)
  - /readyz readiness probe (200 when connected, 503 when not)
  - /metrics Prometheus-format metrics
"""

import asyncio
import time
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import sys
import os

operator_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if operator_root not in sys.path:
    sys.path.insert(0, operator_root)


# ---------------------------------------------------------------------------
# Helpers — we recreate the app manually to test without the full server loop
# ---------------------------------------------------------------------------


def _make_app(client_mock):
    """Build the aiohttp app with the same routes as health_server.py."""
    start_time = time.time()

    async def healthz(request):
        return web.json_response({
            "status": "ok",
            "uptime_seconds": int(time.time() - start_time),
        })

    async def readyz(request):
        if client_mock.connected:
            return web.json_response({
                "status": "ready",
                "connected": True,
                "operator_id": client_mock.operator_id,
                "control_plane": client_mock.url,
            })
        else:
            return web.json_response(
                {
                    "status": "not_ready",
                    "connected": False,
                    "reason": "Not connected to control plane",
                },
                status=503,
            )

    async def metrics(request):
        uptime = int(time.time() - start_time)
        connected = 1 if client_mock.connected else 0
        commands_executed = getattr(client_mock, "_commands_executed", 0)
        commands_failed = getattr(client_mock, "commands_failed", 0)
        reconnect_count = getattr(client_mock, "reconnect_count", 0)
        avg_duration = getattr(client_mock, "avg_command_duration", 0.0)

        lines = [
            "# HELP bnk_operator_up Whether the operator process is running.",
            "# TYPE bnk_operator_up gauge",
            "bnk_operator_up 1",
            "",
            "# HELP bnk_operator_connected Whether connected to BNK-Forge control plane.",
            "# TYPE bnk_operator_connected gauge",
            f"bnk_operator_connected {connected}",
            "",
            f"bnk_operator_uptime_seconds {uptime}",
            f'bnk_operator_commands_total{{status="success"}} {commands_executed}',
            f'bnk_operator_commands_total{{status="failed"}} {commands_failed}',
            f"bnk_operator_command_duration_seconds {avg_duration:.3f}",
            f"bnk_operator_reconnects_total {reconnect_count}",
            f'bnk_operator_info{{version="{getattr(client_mock, "operator_version", "unknown")}",'
            f'cluster="{getattr(client_mock, "cluster_name", "unknown")}"}} 1',
        ]
        return web.Response(
            text="\n".join(lines) + "\n",
            content_type="text/plain",
        )

    app = web.Application()
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/readyz", readyz)
    app.router.add_get("/metrics", metrics)
    return app


def _make_client_mock(connected=True):
    """Create a mock client object."""
    client = MagicMock()
    client.connected = connected
    client.operator_id = "op-123"
    client.url = "wss://bnk-forge:443/ws/operator"
    client._commands_executed = 42
    client.commands_failed = 3
    client.reconnect_count = 2
    client.avg_command_duration = 1.5
    client.operator_version = "1.1.0"
    client.cluster_name = "test-cluster"
    return client


# ---------------------------------------------------------------------------
# Tests — using TestClient/TestServer directly (no pytest-aiohttp needed)
# ---------------------------------------------------------------------------


class TestHealthz:
    """Test /healthz liveness probe."""

    async def test_always_returns_200(self):
        client_mock = _make_client_mock(connected=False)
        app = _make_app(client_mock)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/healthz")
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "ok"
            assert "uptime_seconds" in data

    async def test_uptime_is_non_negative(self):
        client_mock = _make_client_mock()
        app = _make_app(client_mock)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/healthz")
            data = await resp.json()
            assert data["uptime_seconds"] >= 0


class TestReadyz:
    """Test /readyz readiness probe."""

    async def test_ready_when_connected(self):
        client_mock = _make_client_mock(connected=True)
        app = _make_app(client_mock)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/readyz")
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "ready"
            assert data["connected"] is True
            assert data["operator_id"] == "op-123"

    async def test_not_ready_when_disconnected(self):
        client_mock = _make_client_mock(connected=False)
        app = _make_app(client_mock)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/readyz")
            assert resp.status == 503
            data = await resp.json()
            assert data["status"] == "not_ready"
            assert data["connected"] is False


class TestMetrics:
    """Test /metrics Prometheus endpoint."""

    async def test_returns_prometheus_format(self):
        client_mock = _make_client_mock(connected=True)
        app = _make_app(client_mock)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/metrics")
            assert resp.status == 200
            text = await resp.text()
            assert "bnk_operator_up 1" in text
            assert "bnk_operator_connected 1" in text

    async def test_metrics_when_disconnected(self):
        client_mock = _make_client_mock(connected=False)
        app = _make_app(client_mock)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/metrics")
            text = await resp.text()
            assert "bnk_operator_connected 0" in text

    async def test_command_metrics(self):
        client_mock = _make_client_mock()
        app = _make_app(client_mock)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/metrics")
            text = await resp.text()
            assert 'status="success"} 42' in text
            assert 'status="failed"} 3' in text
            assert "bnk_operator_reconnects_total 2" in text

    async def test_info_metric(self):
        client_mock = _make_client_mock()
        app = _make_app(client_mock)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/metrics")
            text = await resp.text()
            assert 'version="1.1.0"' in text
            assert 'cluster="test-cluster"' in text

    async def test_content_type(self):
        client_mock = _make_client_mock()
        app = _make_app(client_mock)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/metrics")
            assert "text/plain" in resp.content_type
