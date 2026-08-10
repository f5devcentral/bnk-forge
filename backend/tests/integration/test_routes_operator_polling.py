"""
Integration tests for operator polling routes — /api/operators.

Covers: register-poll, poll commands, submit results, heartbeat/health.
Uses FastAPI TestClient with real SQLite DB. Operator tokens are mocked.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from models import ConnectedOperator, OperatorCommandQueue
from models.enums import OperatorCommandStatus, OperatorStatus


class TestRegisterPollOperator:
    """POST /api/operators/register-poll."""

    def test_register_poll_missing_token(self, client, sample_user):
        """Missing token returns 401."""
        response = client.post(
            "/api/operators/register-poll",
            json={"cluster_name": "test-cluster"},
        )
        assert response.status_code == 401

    @patch("routes.operator_polling._validate_operator_token")
    def test_register_poll_invalid_body(self, mock_validate, client, sample_user):
        """Invalid body returns 422."""
        mock_validate.return_value = "bnk_reg_test"

        response = client.post(
            "/api/operators/register-poll",
            json={},  # missing cluster_name
            headers={"Authorization": "Bearer bnk_reg_test"},
        )
        assert response.status_code == 422


class TestPollCommands:
    """GET /api/operators/{operator_id}/poll."""

    @patch("routes.operator_polling._validate_operator_token")
    def test_poll_no_commands(self, mock_validate, client, db, sample_user):
        """Polling with no pending commands returns empty list."""
        mock_validate.return_value = "bnk_reg_test"

        op = ConnectedOperator(
            operator_id="op-test-1",
            cluster_name="test-cluster",
            is_connected=True,
            status=OperatorStatus.CONNECTED,
        )
        db.add(op)
        db.commit()

        response = client.get(
            "/api/operators/op-test-1/poll",
            headers={"Authorization": "Bearer bnk_reg_test"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["commands"] == []
        assert "poll_interval_seconds" in data

    @patch("routes.operator_polling._validate_operator_token")
    def test_poll_nonexistent_operator(self, mock_validate, client, db, sample_user):
        """Polling for nonexistent operator returns 404."""
        mock_validate.return_value = "bnk_reg_test"

        response = client.get(
            "/api/operators/does-not-exist/poll",
            headers={"Authorization": "Bearer bnk_reg_test"},
        )
        assert response.status_code == 404


class TestSubmitResult:
    """POST /api/operators/{operator_id}/results/{command_id}."""

    @patch("routes.operator_polling._validate_operator_token")
    @patch("routes.operator_polling._notify_command_result")
    def test_submit_result_success(self, mock_notify, mock_validate, client, db, sample_user):
        """Submit a successful command result."""
        mock_validate.return_value = "bnk_reg_test"

        op = ConnectedOperator(
            operator_id="op-res-1",
            cluster_name="res-cluster",
            is_connected=True,
            status=OperatorStatus.CONNECTED,
        )
        db.add(op)
        db.flush()

        cmd = OperatorCommandQueue(
            command_id="op-res-1:cmd-abc",
            operator_id="op-res-1",
            action="apply_manifests",
            payload={},
            timeout_seconds=300,
            status=OperatorCommandStatus.PICKED_UP,
        )
        db.add(cmd)
        db.commit()

        response = client.post(
            "/api/operators/op-res-1/results/op-res-1:cmd-abc",
            json={"success": True, "result": {"applied": 3}},
            headers={"Authorization": "Bearer bnk_reg_test"},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

    @patch("routes.operator_polling._validate_operator_token")
    def test_submit_result_not_found(self, mock_validate, client, db, sample_user):
        """Submitting result for nonexistent command returns 404."""
        mock_validate.return_value = "bnk_reg_test"

        response = client.post(
            "/api/operators/op-x/results/nonexistent-cmd",
            json={"success": True},
            headers={"Authorization": "Bearer bnk_reg_test"},
        )
        assert response.status_code == 404
