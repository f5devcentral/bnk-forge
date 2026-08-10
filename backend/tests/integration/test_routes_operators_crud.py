"""
Integration tests for operator CRUD routes — /api/operators.

Covers: list, detail, delete, cleanup-stale, connectivity-modes, RBAC.
Uses FastAPI TestClient with real SQLite DB.
"""

from unittest.mock import MagicMock, patch

import pytest

from models import ConnectedOperator


def _make_operator(db, **overrides):
    """Helper to create a ConnectedOperator record directly in the DB."""
    defaults = {
        "operator_id": "op-test-uuid-1234",
        "cluster_name": "test-cluster",
        "connectivity_mode": "direct_ws",
        "labels": {"env": "test"},
        "is_connected": False,
        "status": "registered",
        "operator_version": "1.2.0",
        "kubernetes_version": "1.28.3",
        "node_count": 3,
        "nodes_ready": 3,
        "commands_executed": 0,
        "commands_failed": 0,
        "uptime_seconds": 0,
    }
    defaults.update(overrides)
    op = ConnectedOperator(**defaults)
    db.add(op)
    db.commit()
    db.refresh(op)
    return op


class TestListOperators:
    """GET /api/operators/."""

    def test_list_operators(self, client, admin_headers, sample_user, db):
        """List returns operators created in the DB."""
        _make_operator(db, operator_id="op-list-1", cluster_name="cluster-alpha")
        _make_operator(db, operator_id="op-list-2", cluster_name="cluster-beta")

        response = client.get("/api/operators/", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 2

        names = {op["cluster_name"] for op in data}
        assert "cluster-alpha" in names
        assert "cluster-beta" in names

    def test_list_operators_empty(self, client, admin_headers, sample_user):
        """Empty operator list returns 200 with an empty array."""
        response = client.get("/api/operators/", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0


class TestGetOperatorDetail:
    """GET /api/operators/{operator_id}."""

    def test_get_operator_detail(self, client, admin_headers, sample_user, db):
        """Detail endpoint returns operator fields including health report."""
        op = _make_operator(
            db,
            operator_id="op-detail-uuid",
            cluster_name="detail-cluster",
            last_health_report={"cpu_percent": 42, "memory_mb": 512},
        )

        response = client.get(
            f"/api/operators/{op.operator_id}", headers=admin_headers
        )
        assert response.status_code == 200

        data = response.json()
        assert data["operator_id"] == "op-detail-uuid"
        assert data["cluster_name"] == "detail-cluster"
        assert "last_health_report" in data
        assert data["last_health_report"]["cpu_percent"] == 42
        assert "registration_token_name" in data

    def test_get_operator_not_found(self, client, admin_headers, sample_user):
        """Nonexistent operator returns 404."""
        response = client.get(
            "/api/operators/nonexistent-uuid", headers=admin_headers
        )
        assert response.status_code == 404


class TestDeleteOperator:
    """DELETE /api/operators/{operator_id}."""

    @patch("routes.operators.crud.operator_connections")
    @patch("routes.operators.crud.delete_operator")
    def test_delete_operator(
        self, mock_delete, mock_conns, client, operator_headers, sample_user, sample_operator_user, db
    ):
        """DELETE returns success when operator exists."""
        op = _make_operator(db, operator_id="op-to-delete", cluster_name="doomed-cluster")

        mock_conns.is_connected.return_value = False
        mock_delete.return_value = True

        response = client.delete(
            f"/api/operators/{op.operator_id}", headers=operator_headers
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert "op-to-delete" in data["message"]

    @patch("routes.operators.crud.operator_connections")
    @patch("routes.operators.crud.delete_operator")
    def test_delete_operator_not_found(
        self, mock_delete, mock_conns, client, operator_headers, sample_user, sample_operator_user
    ):
        """DELETE on nonexistent operator returns 404."""
        mock_conns.is_connected.return_value = False
        mock_delete.return_value = False

        response = client.delete(
            "/api/operators/nonexistent-op", headers=operator_headers
        )
        assert response.status_code == 404

    def test_viewer_cannot_delete(self, client, viewer_headers, sample_user, sample_viewer_user):
        """Viewers lack permission to delete operators."""
        response = client.delete(
            "/api/operators/any-op", headers=viewer_headers
        )
        assert response.status_code == 403


class TestCleanupStale:
    """POST /api/operators/cleanup-stale."""

    @patch("services.operator_registry.cleanup_expired_commands")
    @patch("services.operator_registry.cleanup_stale_operators")
    def test_cleanup_stale_admin_only(
        self, mock_stale, mock_expired, client, admin_headers, operator_headers,
        sample_user, sample_operator_user
    ):
        """Only admins can trigger cleanup; operators get 403."""
        # Operator should be rejected
        response = client.post(
            "/api/operators/cleanup-stale", headers=operator_headers
        )
        assert response.status_code == 403

        # Admin should succeed
        mock_stale.return_value = 2
        mock_expired.return_value = 1

        response = client.post(
            "/api/operators/cleanup-stale", headers=admin_headers
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert data["stale_operators_cleaned"] == 2
        assert data["expired_commands_cleaned"] == 1


class TestConnectivityModes:
    """GET /api/operators/connectivity-modes."""

    @patch("services.operator_connectivity.get_available_modes")
    def test_get_connectivity_modes(
        self, mock_get_modes, client, admin_headers, sample_user
    ):
        """Returns list of available connectivity modes."""
        mock_get_modes.return_value = [
            {"mode": "direct_ws", "label": "Direct WebSocket", "available": True},
            {"mode": "polling", "label": "HTTP Polling", "available": True},
            {"mode": "ngrok_tunnel", "label": "Ngrok Tunnel", "available": False},
        ]

        response = client.get(
            "/api/operators/connectivity-modes", headers=admin_headers
        )
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 3
        mode_names = {m["mode"] for m in data}
        assert "direct_ws" in mode_names
        assert "polling" in mode_names
