"""
Integration tests for operator command routes — /api/operators/{id}/command, /api/operators/fan-out.

Covers: send command, fan-out, RBAC.
Uses FastAPI TestClient with real SQLite DB.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models import ConnectedOperator


def _make_operator(db, **overrides):
    """Helper to create a ConnectedOperator record directly in the DB."""
    defaults = {
        "operator_id": "op-cmd-uuid-1",
        "cluster_name": "cmd-cluster",
        "connectivity_mode": "direct_ws",
        "labels": {},
        "is_connected": True,
        "status": "connected",
        "commands_executed": 0,
        "commands_failed": 0,
        "uptime_seconds": 100,
    }
    defaults.update(overrides)
    op = ConnectedOperator(**defaults)
    db.add(op)
    db.commit()
    db.refresh(op)
    return op


class TestSendCommand:
    """POST /api/operators/{operator_id}/command."""

    @patch("routes.operators.commands.operator_connections")
    def test_send_command(
        self, mock_conns, client, operator_headers, sample_user, sample_operator_user, db
    ):
        """Sending a command to a connected operator returns success."""
        op = _make_operator(db, operator_id="op-cmd-1")

        mock_conns.is_connected.return_value = True
        mock_conns.send_command = AsyncMock(return_value={
            "success": True,
            "command_id": "cmd-abc-123",
            "status": "ok",
        })

        response = client.post(
            f"/api/operators/{op.operator_id}/command",
            json={"action": "health-check"},
            headers=operator_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert data["command_id"] == "cmd-abc-123"
        assert "result" in data

    @patch("routes.operators.commands.operator_connections")
    def test_send_command_operator_not_connected(
        self, mock_conns, client, operator_headers, sample_user, sample_operator_user, db
    ):
        """Sending a command to a disconnected operator returns 400."""
        op = _make_operator(db, operator_id="op-cmd-offline", is_connected=False)
        mock_conns.is_connected.return_value = False

        response = client.post(
            f"/api/operators/{op.operator_id}/command",
            json={"action": "health-check"},
            headers=operator_headers,
        )
        assert response.status_code == 400

    def test_viewer_cannot_send_command(
        self, client, viewer_headers, sample_user, sample_viewer_user
    ):
        """Viewers are forbidden from sending operator commands."""
        response = client.post(
            "/api/operators/any-op/command",
            json={"action": "health-check"},
            headers=viewer_headers,
        )
        assert response.status_code == 403


class TestFanOutCommand:
    """POST /api/operators/fan-out."""

    @patch("routes.operators.commands.operator_connections")
    def test_fan_out_command(
        self, mock_conns, client, operator_headers, sample_user, sample_operator_user, db
    ):
        """Fan-out sends commands to multiple operators and aggregates results."""
        _make_operator(db, operator_id="op-fan-1", cluster_name="fan-alpha")
        _make_operator(db, operator_id="op-fan-2", cluster_name="fan-beta")

        mock_conns.send_command_to_multiple = AsyncMock(return_value={
            "op-fan-1": {"success": True, "status": "ok"},
            "op-fan-2": {"success": True, "status": "ok"},
        })

        response = client.post(
            "/api/operators/fan-out",
            json={
                "action": "scan-cluster",
                "operator_ids": ["op-fan-1", "op-fan-2"],
            },
            headers=operator_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert data["total_operators"] == 2
        assert data["succeeded"] == 2
        assert data["failed"] == 0
        assert "op-fan-1" in data["results"]
        assert "op-fan-2" in data["results"]

    @patch("routes.operators.commands.operator_connections")
    def test_fan_out_no_targets(
        self, mock_conns, client, operator_headers, sample_user, sample_operator_user
    ):
        """Fan-out with no matching operators returns success with empty results."""
        mock_conns.get_connected_operator_ids.return_value = []

        response = client.post(
            "/api/operators/fan-out",
            json={"action": "scan-cluster"},
            headers=operator_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert data["total_operators"] == 0

    def test_viewer_cannot_fan_out(
        self, client, viewer_headers, sample_user, sample_viewer_user
    ):
        """Viewers are forbidden from fan-out commands."""
        response = client.post(
            "/api/operators/fan-out",
            json={"action": "scan-cluster"},
            headers=viewer_headers,
        )
        assert response.status_code == 403
