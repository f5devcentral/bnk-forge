"""
Tests for llm_metrics_collector.py.

Uses the fake-k8s harness from conftest.py.  All external deps (kr8s, aiohttp,
control plane client) are mocked.
"""
import asyncio
import importlib
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

operator_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if operator_root not in sys.path:
    sys.path.insert(0, operator_root)

from llm_metrics_collector import LlmMetricsCollector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_analyzer(name="my-analyzer", namespace="prod", apps=None):
    """Fake F5BigAnalyzer cr object."""
    a = MagicMock()
    a.metadata = {"name": name, "namespace": namespace}
    a.spec = {"applications": apps or []}
    return a


def _make_client(connected=True):
    client = MagicMock()
    client.connected = connected
    client.send_llm_metrics = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# collect_and_report loop
# ---------------------------------------------------------------------------

class TestCollectAndReport:
    """Test the top-level loop."""

    @pytest.mark.asyncio
    async def test_disabled_exits_immediately(self):
        """When LLM_METRICS_ENABLED is false (default), loop exits."""
        collector = LlmMetricsCollector()
        client = _make_client()
        shutdown = asyncio.Event()

        with patch("llm_metrics_collector.LLM_METRICS_ENABLED", False):
            await collector.collect_and_report(client, shutdown)

        client.send_llm_metrics.assert_not_called()

    @pytest.mark.asyncio
    async def test_stops_on_shutdown(self):
        """Loop exits cleanly when shutdown_event is set."""
        collector = LlmMetricsCollector()
        client = _make_client()
        shutdown = asyncio.Event()

        async def stop_soon():
            await asyncio.sleep(0.05)
            shutdown.set()

        with (
            patch("llm_metrics_collector.LLM_METRICS_ENABLED", True),
            patch("llm_metrics_collector.LLM_METRICS_INTERVAL", 1),
            patch.object(collector, "_collect_and_push", new_callable=AsyncMock),
        ):
            await asyncio.gather(
                collector.collect_and_report(client, shutdown),
                stop_soon(),
            )

    @pytest.mark.asyncio
    async def test_error_increments_counter(self):
        """Exceptions in _collect_and_push increment error_count, loop continues."""
        collector = LlmMetricsCollector()
        client = _make_client()
        shutdown = asyncio.Event()
        call_count = 0

        async def failing_push(_client):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                shutdown.set()
            raise RuntimeError("simulated scrape error")

        with (
            patch("llm_metrics_collector.LLM_METRICS_ENABLED", True),
            patch("llm_metrics_collector.LLM_METRICS_INTERVAL", 0.05),
            patch.object(collector, "_collect_and_push", side_effect=failing_push),
        ):
            await collector.collect_and_report(client, shutdown)

        assert collector.error_count >= 1


# ---------------------------------------------------------------------------
# _collect_backend_refs
# ---------------------------------------------------------------------------

class TestCollectBackendRefs:
    """Test the HTTPRoute → service-name discovery."""

    @pytest.mark.asyncio
    async def test_skips_non_httproute_apps(self):
        """Apps with kind != httproute* are ignored."""
        collector = LlmMetricsCollector()
        api = AsyncMock()
        apps = [{"kind": "GRPCRoute", "name": "grpc-app", "namespace": "ns"}]
        refs = await collector._collect_backend_refs(api, apps, "ns", "analyzer")
        assert refs == []
        api.async_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_extracts_backend_refs_from_route(self):
        """Happy path: HTTPRoute with two backendRefs."""
        collector = LlmMetricsCollector()
        api = AsyncMock()

        route = MagicMock()
        route.spec = {
            "rules": [
                {"backendRefs": [
                    {"name": "svc-a", "namespace": "ns"},
                    {"name": "svc-b"},  # ns falls back to app_ns
                ]}
            ]
        }
        api.async_get.return_value = [route]

        apps = [{"kind": "HTTPRoute", "name": "my-route", "namespace": "ns"}]
        refs = await collector._collect_backend_refs(api, apps, "ns", "analyzer")

        assert ("ns", "svc-a") in refs
        assert ("ns", "svc-b") in refs

    @pytest.mark.asyncio
    async def test_deduplicates_refs(self):
        """Same (ns, svc) appearing twice → kept once."""
        collector = LlmMetricsCollector()
        api = AsyncMock()

        route = MagicMock()
        route.spec = {
            "rules": [
                {"backendRefs": [{"name": "svc-a", "namespace": "ns"}]},
                {"backendRefs": [{"name": "svc-a", "namespace": "ns"}]},
            ]
        }
        api.async_get.return_value = [route]

        apps = [{"kind": "HTTPRoute", "name": "my-route", "namespace": "ns"}]
        refs = await collector._collect_backend_refs(api, apps, "ns", "analyzer")
        assert refs.count(("ns", "svc-a")) == 1

    @pytest.mark.asyncio
    async def test_continues_on_route_fetch_error(self):
        """HTTPRoute GET failure → skip app, no exception."""
        collector = LlmMetricsCollector()
        api = AsyncMock()
        api.async_get.side_effect = Exception("not found")

        apps = [{"kind": "HTTPRoute", "name": "missing", "namespace": "ns"}]
        refs = await collector._collect_backend_refs(api, apps, "ns", "analyzer")
        assert refs == []


# ---------------------------------------------------------------------------
# _scrape_pod
# ---------------------------------------------------------------------------

class TestScrapePod:
    """Test in-cluster pod scrape."""

    def _make_session(self, status: int, text: str = "") -> MagicMock:
        """Build a mock aiohttp.ClientSession with a context-manager response."""
        mock_resp = MagicMock()
        mock_resp.status = status
        mock_resp.text = AsyncMock(return_value=text)

        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=mock_resp)
        cm.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get.return_value = cm
        return session

    @pytest.mark.asyncio
    async def test_returns_error_on_non_200(self):
        """Non-200 HTTP response → error string, empty row."""
        collector = LlmMetricsCollector()
        session = self._make_session(503)

        row, err = await collector._scrape_pod(session, "10.0.0.1", 8080, "pod-0", "ns", "svc")
        assert row == {}
        assert "503" in err

    @pytest.mark.asyncio
    async def test_returns_error_on_timeout(self):
        """Timeout during GET → error string."""
        collector = LlmMetricsCollector()

        cm = MagicMock()
        cm.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError())
        cm.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get.return_value = cm

        row, err = await collector._scrape_pod(session, "10.0.0.1", 8080, "pod-0", "ns", "svc")
        assert row == {}
        assert "timeout" in err

    @pytest.mark.asyncio
    async def test_parses_valid_metrics(self):
        """Valid Prometheus text → healthy row."""
        from tests.test_vllm_metrics import _SAMPLE_METRICS

        collector = LlmMetricsCollector()
        session = self._make_session(200, _SAMPLE_METRICS)

        row, err = await collector._scrape_pod(session, "10.0.0.1", 8080, "pod-0", "prod", "svc")
        assert err is None
        assert row["status"] == "healthy"
        assert row["backend_kind"] == "vllm"


# ---------------------------------------------------------------------------
# Module-level config parsing
# ---------------------------------------------------------------------------

class TestLlmMetricsIntervalParsing:
    """LLM_METRICS_INTERVAL env var is parsed defensively."""

    def _reload_module(self, env_value):
        """Reload llm_metrics_collector with a patched env and return module."""
        import llm_metrics_collector as mod
        env = {**os.environ}
        if env_value is None:
            env.pop("LLM_METRICS_INTERVAL", None)
        else:
            env["LLM_METRICS_INTERVAL"] = env_value
        with patch.dict(os.environ, env, clear=True):
            importlib.reload(mod)
        return mod

    def test_non_numeric_falls_back_to_default(self):
        """Non-numeric LLM_METRICS_INTERVAL must not raise and must yield 30."""
        import llm_metrics_collector as mod
        with patch.dict(os.environ, {"LLM_METRICS_INTERVAL": "abc"}, clear=False):
            importlib.reload(mod)
            assert mod.LLM_METRICS_INTERVAL == 30

    def test_numeric_value_is_applied(self):
        """Numeric LLM_METRICS_INTERVAL above floor is respected."""
        import llm_metrics_collector as mod
        with patch.dict(os.environ, {"LLM_METRICS_INTERVAL": "60"}, clear=False):
            importlib.reload(mod)
            assert mod.LLM_METRICS_INTERVAL == 60

    def test_floor_enforced_below_15(self):
        """Values below 15 are clamped to 15."""
        import llm_metrics_collector as mod
        with patch.dict(os.environ, {"LLM_METRICS_INTERVAL": "5"}, clear=False):
            importlib.reload(mod)
            assert mod.LLM_METRICS_INTERVAL == 15

    def test_missing_env_uses_default(self):
        """Absent LLM_METRICS_INTERVAL yields 30."""
        import llm_metrics_collector as mod
        env = {k: v for k, v in os.environ.items() if k != "LLM_METRICS_INTERVAL"}
        with patch.dict(os.environ, env, clear=True):
            importlib.reload(mod)
            assert mod.LLM_METRICS_INTERVAL == 30
