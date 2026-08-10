"""
Tests for health_watcher.py — continuous health monitoring and delta reporting.

Covers:
  - watch_and_report loop lifecycle
  - Delta detection (_has_changed)
  - Periodic full reports
  - Error counting
  - Shared vs fallback handlers
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sys
import os

operator_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if operator_root not in sys.path:
    sys.path.insert(0, operator_root)

from health_watcher import HealthWatcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_health(bnk_installed=True, tmm_ready=1):
    """Create a sample health report dict."""
    return {
        "success": True,
        "timestamp": "2026-01-01T00:00:00Z",
        "cluster": {"kubernetes_version": "v1.29.0", "node_count": 3, "nodes_ready": 3},
        "bnk": {
            "installed": bnk_installed,
            "tmm_total": 1,
            "tmm_ready": tmm_ready,
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHasChanged:
    """Test the delta detection logic."""

    def test_first_report_always_changed(self):
        watcher = HealthWatcher()
        assert watcher._has_changed({"some": "data"}) is True

    def test_same_data_not_changed(self):
        watcher = HealthWatcher()
        health = _make_health()
        watcher._last_health = health.copy()
        assert watcher._has_changed(health) is False

    def test_different_data_is_changed(self):
        watcher = HealthWatcher()
        watcher._last_health = _make_health(tmm_ready=1)
        new_health = _make_health(tmm_ready=0)
        assert watcher._has_changed(new_health) is True

    def test_timestamp_ignored_in_comparison(self):
        """Timestamp changes alone should NOT trigger a report."""
        watcher = HealthWatcher()
        old = _make_health()
        old["timestamp"] = "2026-01-01T00:00:00Z"
        watcher._last_health = old

        new = _make_health()
        new["timestamp"] = "2026-01-01T00:01:00Z"  # Different timestamp

        assert watcher._has_changed(new) is False

    def test_serialization_error_returns_changed(self):
        """If JSON serialization fails, report as changed (safe default)."""
        watcher = HealthWatcher()
        watcher._last_health = {"data": object()}  # Not JSON-serializable
        assert watcher._has_changed({"other": "data"}) is True


class TestCheckAndReport:
    """Test the _check_and_report method."""

    @pytest.mark.asyncio
    async def test_reports_on_first_check(self):
        """First health check should always report."""
        watcher = HealthWatcher()
        mock_handlers = MagicMock()
        mock_handlers.get_health = AsyncMock(return_value=_make_health())
        watcher._handlers = mock_handlers

        client = MagicMock()
        client.send_health = AsyncMock()

        await watcher._check_and_report(client)

        assert watcher._check_count == 1
        client.send_health.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_report_when_unchanged(self):
        """Second check with same data should NOT report (within full report interval)."""
        watcher = HealthWatcher()
        mock_handlers = MagicMock()
        mock_handlers.get_health = AsyncMock(return_value=_make_health())
        watcher._handlers = mock_handlers
        watcher._last_full_report_time = time.time()  # Just reported

        client = MagicMock()
        client.send_health = AsyncMock()

        # First call — stores state
        watcher._last_health = _make_health()

        await watcher._check_and_report(client)

        # Should NOT send because data hasn't changed and full report interval hasn't elapsed
        client.send_health.assert_not_called()

    @pytest.mark.asyncio
    async def test_reports_on_change(self):
        """Changed health should trigger a report."""
        watcher = HealthWatcher()
        mock_handlers = MagicMock()
        mock_handlers.get_health = AsyncMock(return_value=_make_health(tmm_ready=0))
        watcher._handlers = mock_handlers
        watcher._last_health = _make_health(tmm_ready=1)
        watcher._last_full_report_time = time.time()

        client = MagicMock()
        client.send_health = AsyncMock()

        await watcher._check_and_report(client)

        client.send_health.assert_called_once()

    @pytest.mark.asyncio
    async def test_periodic_full_report(self):
        """Should force a report after FULL_REPORT_INTERVAL."""
        watcher = HealthWatcher()
        mock_handlers = MagicMock()
        mock_handlers.get_health = AsyncMock(return_value=_make_health())
        watcher._handlers = mock_handlers
        watcher._last_health = _make_health()  # Same data
        watcher._last_full_report_time = time.time() - 600  # 10 minutes ago

        client = MagicMock()
        client.send_health = AsyncMock()

        await watcher._check_and_report(client)

        client.send_health.assert_called_once()

    @pytest.mark.asyncio
    async def test_failed_health_still_reported(self):
        """Failed health check should still be sent to control plane."""
        watcher = HealthWatcher()
        mock_handlers = MagicMock()
        mock_handlers.get_health = AsyncMock(return_value={
            "success": False,
            "error_message": "connection refused",
        })
        watcher._handlers = mock_handlers

        client = MagicMock()
        client.send_health = AsyncMock()

        await watcher._check_and_report(client)

        client.send_health.assert_called_once()


class TestWatchAndReport:
    """Test the main watch_and_report loop."""

    @pytest.mark.asyncio
    async def test_waits_for_connection(self):
        """Loop should wait until client is connected."""
        watcher = HealthWatcher()
        client = MagicMock()
        client.connected = False

        shutdown = asyncio.Event()

        async def set_connected():
            await asyncio.sleep(0.1)
            client.connected = True
            await asyncio.sleep(0.1)
            shutdown.set()

        with patch.object(watcher, '_check_and_report', new_callable=AsyncMock):
            await asyncio.gather(
                watcher.watch_and_report(client, shutdown),
                set_connected(),
            )

    @pytest.mark.asyncio
    async def test_stops_on_shutdown(self):
        """Loop should exit cleanly when shutdown_event is set."""
        watcher = HealthWatcher()
        client = MagicMock()
        client.connected = True

        shutdown = asyncio.Event()

        async def stop_soon():
            await asyncio.sleep(0.05)
            shutdown.set()

        with patch.object(watcher, '_check_and_report', new_callable=AsyncMock):
            with patch('health_watcher.CHECK_INTERVAL', 1):
                await asyncio.gather(
                    watcher.watch_and_report(client, shutdown),
                    stop_soon(),
                )

    @pytest.mark.asyncio
    async def test_error_increments_counter(self):
        """Errors during check should increment error_count."""
        watcher = HealthWatcher()
        client = MagicMock()
        client.connected = True

        shutdown = asyncio.Event()
        check_count = 0

        async def failing_check(c):
            nonlocal check_count
            check_count += 1
            if check_count >= 2:
                shutdown.set()
            raise RuntimeError("boom")

        watcher._check_and_report = failing_check

        with patch('health_watcher.CHECK_INTERVAL', 0.05):
            await watcher.watch_and_report(client, shutdown)

        assert watcher.error_count >= 1


class TestFallbackHandlers:
    """Test handler selection in _check_and_report."""

    @pytest.mark.asyncio
    async def test_uses_shared_handlers(self):
        """Should use shared handlers instance when provided."""
        mock_handlers = MagicMock()
        mock_handlers.get_health = AsyncMock(return_value=_make_health())

        watcher = HealthWatcher(handlers=mock_handlers)

        client = MagicMock()
        client.send_health = AsyncMock()

        await watcher._check_and_report(client)

        mock_handlers.get_health.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_creates_new_handlers(self):
        """Without shared handlers, should create a new CommandHandlers each time."""
        watcher = HealthWatcher(handlers=None)

        client = MagicMock()
        client.send_health = AsyncMock()

        with patch('command_handlers.CommandHandlers') as MockHandlers:
            mock_instance = MagicMock()
            mock_instance.get_health = AsyncMock(return_value=_make_health())
            MockHandlers.return_value = mock_instance

            await watcher._check_and_report(client)

            MockHandlers.assert_called_once()


class TestProperties:
    """Test HealthWatcher properties."""

    def test_initial_counts(self):
        watcher = HealthWatcher()
        assert watcher.check_count == 0
        assert watcher.error_count == 0
