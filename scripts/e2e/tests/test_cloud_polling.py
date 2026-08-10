"""Unit tests for the cloud polling waiters in cloud_steps.py.

Tests _wait_for_run_terminal, _wait_for_bnk_ready, _wait_for_license_active
with fake clients — no network, no sleep (time is mocked).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from scripts.e2e.client import BnkForgeApiError
from scripts.e2e.cloud_steps import (
    _find_latest_exec_id,
    _wait_for_bnk_ready,
    _wait_for_license_active,
    _wait_for_run_terminal,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_client(**methods: Any) -> MagicMock:
    """Minimal mock client whose methods return from a call-sequence."""
    c = MagicMock()
    for name, side_effect in methods.items():
        getattr(c, name).side_effect = side_effect
    return c


def _api_error(status: int = 500, msg: str = "err") -> BnkForgeApiError:
    return BnkForgeApiError(status, msg)


# ---------------------------------------------------------------------------
# _wait_for_run_terminal
# ---------------------------------------------------------------------------


class TestWaitForRunTerminal:
    def test_reaches_completed(self):
        responses = [
            {"id": 7, "status": "in_progress", "progress_percent": 10.0},
            {"id": 7, "status": "in_progress", "progress_percent": 50.0},
            {"id": 7, "status": "completed", "progress_percent": 100.0},
        ]
        client = _fake_client(get_parallel_execution_status=responses)
        with patch("time.sleep"):
            with patch("time.time", side_effect=[0, 0, 1, 1, 2, 2, 99]):
                result = _wait_for_run_terminal(
                    client, project_id=1, exec_id=7,
                    timeout=100, poll_interval=1,
                )
        assert result["status"] == "completed"

    def test_reaches_failed(self):
        responses = [
            {"id": 7, "status": "in_progress"},
            {"id": 7, "status": "failed", "error_message": "module died"},
        ]
        client = _fake_client(get_parallel_execution_status=responses)
        with patch("time.sleep"):
            with patch("time.time", side_effect=[0, 0, 1, 1, 99]):
                result = _wait_for_run_terminal(
                    client, project_id=1, exec_id=7,
                    timeout=100, poll_interval=1,
                )
        assert result["status"] == "failed"
        assert "module died" in result["error_message"]

    def test_raises_timeout(self):
        # Always returns in_progress — should raise TimeoutError
        client = MagicMock()
        client.get_parallel_execution_status.return_value = {
            "id": 7, "status": "in_progress",
        }
        # time.time always > deadline from the start
        with patch("time.sleep"):
            with patch("time.time", side_effect=[0, 99]):
                with pytest.raises(TimeoutError, match="exec_id"):
                    _wait_for_run_terminal(
                        client, project_id=1, exec_id=7,
                        timeout=5, poll_interval=1,
                    )

    def test_single_poll_completed(self):
        """First poll returns completed — exits immediately."""
        client = MagicMock()
        client.get_parallel_execution_status.return_value = {
            "id": 7, "status": "completed",
        }
        with patch("time.sleep") as mock_sleep:
            with patch("time.time", side_effect=[0, 0, 99]):
                result = _wait_for_run_terminal(
                    client, project_id=1, exec_id=7,
                    timeout=60, poll_interval=5,
                )
        assert result["status"] == "completed"
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# _find_latest_exec_id
# ---------------------------------------------------------------------------


class TestFindLatestExecId:
    def test_picks_in_flight_record(self):
        """Prefers an in_progress record over a completed one."""
        records = [
            {"id": 10, "status": "in_progress", "action": "deploy"},
            {"id": 9, "status": "completed", "action": "deploy"},
        ]
        client = MagicMock()
        client.get_parallel_executions.return_value = records
        assert _find_latest_exec_id(client, project_id=1) == 10

    def test_falls_back_to_newest_when_all_terminal(self):
        """When all records are terminal, returns the newest (first in list)."""
        records = [
            {"id": 8, "status": "completed", "action": "deploy"},
            {"id": 7, "status": "failed", "action": "deploy"},
        ]
        client = MagicMock()
        client.get_parallel_executions.return_value = records
        assert _find_latest_exec_id(client, project_id=1) == 8

    def test_picks_pending_record(self):
        """'pending' is also treated as in-flight."""
        records = [
            {"id": 5, "status": "pending", "action": "destroy"},
        ]
        client = MagicMock()
        client.get_parallel_executions.return_value = records
        assert _find_latest_exec_id(client, project_id=1) == 5

    def test_raises_on_empty_list(self):
        client = MagicMock()
        client.get_parallel_executions.return_value = []
        with pytest.raises(RuntimeError, match="empty"):
            _find_latest_exec_id(client, project_id=1)

    def test_raises_on_missing_id_field(self):
        client = MagicMock()
        client.get_parallel_executions.return_value = [{"status": "in_progress"}]
        with pytest.raises(RuntimeError, match="'id' field"):
            _find_latest_exec_id(client, project_id=1)


# ---------------------------------------------------------------------------
# _wait_for_bnk_ready
# ---------------------------------------------------------------------------


class TestWaitForBnkReady:
    def test_reaches_healthy(self):
        responses = [
            {"overall": "unknown"},
            {"overall": "unknown"},
            {"overall": "healthy", "platform": {"severity": "healthy"}, "counts": {}},
        ]
        client = _fake_client(get_bnk_health=responses)
        with patch("time.sleep"):
            with patch("time.time", side_effect=[0, 0, 1, 1, 2, 2, 99]):
                result = _wait_for_bnk_ready(
                    client, cluster_id=3,
                    timeout=100, poll_interval=1,
                )
        assert result["overall"] == "healthy"

    def test_api_error_keeps_polling(self):
        """API errors are swallowed and polling continues."""
        responses = [
            _api_error(502, "cluster not reachable"),
            {"overall": "healthy"},
        ]
        client = _fake_client(get_bnk_health=responses)
        with patch("time.sleep"):
            with patch("time.time", side_effect=[0, 0, 1, 1, 99]):
                result = _wait_for_bnk_ready(
                    client, cluster_id=3,
                    timeout=100, poll_interval=1,
                )
        assert result["overall"] == "healthy"

    def test_raises_timeout_when_always_unknown(self):
        client = MagicMock()
        client.get_bnk_health.return_value = {"overall": "unknown"}
        with patch("time.sleep"):
            with patch("time.time", side_effect=[0, 99]):
                with pytest.raises(TimeoutError, match="unknown"):
                    _wait_for_bnk_ready(
                        client, cluster_id=3,
                        timeout=5, poll_interval=1,
                    )

    def test_exits_on_critical(self):
        """'critical' is not 'unknown' so waiter returns it; step decides."""
        client = MagicMock()
        client.get_bnk_health.return_value = {"overall": "critical"}
        with patch("time.sleep"):
            with patch("time.time", side_effect=[0, 0, 99]):
                result = _wait_for_bnk_ready(
                    client, cluster_id=3,
                    timeout=60, poll_interval=1,
                )
        assert result["overall"] == "critical"


# ---------------------------------------------------------------------------
# _wait_for_license_active
# ---------------------------------------------------------------------------


class TestWaitForLicenseActive:
    def test_already_active(self):
        client = MagicMock()
        client.get_license_status.return_value = {
            "success": True, "license_state": "Active",
        }
        with patch("time.sleep"):
            with patch("time.time", side_effect=[0, 0, 99]):
                result = _wait_for_license_active(
                    client, cluster_id=3, jwt=None,
                    timeout=60, poll_interval=1,
                )
        assert result["license_state"] == "Active"
        client.activate_license.assert_not_called()

    def test_activates_when_jwt_configured(self):
        """Not active → tries activate once → then polls to Active."""
        responses_status = [
            {"success": True, "license_state": "Device Registration Failed"},
            {"success": True, "license_state": "Active"},
        ]
        client = _fake_client(
            get_license_status=responses_status,
            activate_license=[{"success": True}],
        )
        with patch("time.sleep"):
            with patch("time.time", side_effect=[0, 0, 1, 1, 99]):
                result = _wait_for_license_active(
                    client, cluster_id=3, jwt="myjwt",
                    timeout=60, poll_interval=1,
                )
        assert result["license_state"] == "Active"
        client.activate_license.assert_called_once_with(3, "myjwt")

    def test_activate_only_called_once_even_if_still_not_active(self):
        """Failed activate → keep polling, but don't call activate again."""
        # Returns "Not Active" every time, activate raises on first call
        client = MagicMock()
        client.get_license_status.return_value = {
            "success": True, "license_state": "Initializing",
        }
        client.activate_license.side_effect = _api_error(502, "CWC unreachable")
        with patch("time.sleep"):
            # time ticks: start=0, check loop < deadline (budget 5s)
            # first poll: time=0 (< 5), try activate (fails), sleep
            # second poll: time=1 (< 5), skip activate (already tried), sleep
            # third poll: time=6 (>= 5) → exits loop → TimeoutError
            with patch("time.time", side_effect=[0, 0, 1, 1, 2, 2, 6]):
                with pytest.raises(TimeoutError):
                    _wait_for_license_active(
                        client, cluster_id=3, jwt="myjwt",
                        timeout=5, poll_interval=1,
                    )
        # activate was called only once despite multiple polls
        assert client.activate_license.call_count == 1

    def test_case_insensitive_active(self):
        """'active' (lowercase) is also accepted."""
        client = MagicMock()
        client.get_license_status.return_value = {"license_state": "active"}
        with patch("time.sleep"):
            with patch("time.time", side_effect=[0, 0, 99]):
                result = _wait_for_license_active(
                    client, cluster_id=3, jwt=None,
                    timeout=60, poll_interval=1,
                )
        assert result["license_state"] == "active"

    def test_licensed_state_accepted(self):
        client = MagicMock()
        client.get_license_status.return_value = {"license_state": "Licensed"}
        with patch("time.sleep"):
            with patch("time.time", side_effect=[0, 0, 99]):
                result = _wait_for_license_active(
                    client, cluster_id=3, jwt=None,
                    timeout=60, poll_interval=1,
                )
        assert result["license_state"] == "Licensed"

    def test_raises_timeout(self):
        client = MagicMock()
        client.get_license_status.return_value = {
            "license_state": "Pending",
        }
        with patch("time.sleep"):
            with patch("time.time", side_effect=[0, 99]):
                with pytest.raises(TimeoutError, match="license"):
                    _wait_for_license_active(
                        client, cluster_id=3, jwt=None,
                        timeout=5, poll_interval=1,
                    )
