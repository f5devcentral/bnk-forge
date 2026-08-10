"""
Integration tests for config snapshot routes — /api/projects/{pid}/snapshots and /api/snapshots/*.

Covers: list, create, get, delete, restore snapshots, RBAC enforcement.
Uses FastAPI TestClient with real SQLite DB. SnapshotService is mocked.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest


def _make_mock_snapshot(**overrides):
    """Build a mock snapshot object with attribute access (ORM-style)."""
    snap = MagicMock()
    snap.id = overrides.get("id", 1)
    snap.project_id = overrides.get("project_id", 1)
    snap.cluster_id = overrides.get("cluster_id", None)
    snap.trigger = overrides.get("trigger", "manual")
    snap.label = overrides.get("label", "test-snapshot")
    snap.created_by = overrides.get("created_by", "admin")
    snap.resource_count = overrides.get("resource_count", 5)
    snap.module_count = overrides.get("module_count", 2)
    snap.config_data = overrides.get("config_data", {"resources": {}})
    snap.created_at = overrides.get("created_at", datetime.now(UTC))
    return snap


class TestListSnapshots:
    """GET /api/projects/{project_id}/snapshots."""

    @patch("routes.snapshots.SnapshotService")
    def test_list_snapshots(
        self, mock_svc_cls, client, viewer_headers, all_test_users, sample_project,
    ):
        """Viewer can list snapshots for a project."""
        mock_svc = MagicMock()
        mock_svc.list_snapshots.return_value = (
            [_make_mock_snapshot(project_id=sample_project.id)],
            1,
        )
        mock_svc_cls.return_value = mock_svc

        response = client.get(
            f"/api/projects/{sample_project.id}/snapshots",
            headers=viewer_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 1
        assert len(data["snapshots"]) == 1
        assert data["snapshots"][0]["label"] == "test-snapshot"
        mock_svc.list_snapshots.assert_called_once()


class TestCreateSnapshot:
    """POST /api/projects/{project_id}/snapshots."""

    @patch("routes.snapshots.SnapshotService")
    def test_create_snapshot(
        self, mock_svc_cls, client, operator_headers, all_test_users, sample_project,
    ):
        """Operator can create a manual config snapshot."""
        mock_svc = MagicMock()
        mock_svc.create_snapshot.return_value = _make_mock_snapshot(
            id=10,
            project_id=sample_project.id,
            label="my-snapshot",
            trigger="manual",
        )
        mock_svc_cls.return_value = mock_svc

        response = client.post(
            f"/api/projects/{sample_project.id}/snapshots",
            json={"label": "my-snapshot"},
            headers=operator_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["id"] == 10
        assert data["label"] == "my-snapshot"
        assert data["trigger"] == "manual"
        assert "message" in data
        mock_svc.create_snapshot.assert_called_once()


class TestGetSnapshot:
    """GET /api/snapshots/{snapshot_id}."""

    @patch("routes.snapshots.SnapshotService")
    def test_get_snapshot(
        self, mock_svc_cls, client, viewer_headers, all_test_users,
    ):
        """Viewer can get full snapshot detail."""
        mock_svc = MagicMock()
        mock_svc.get_snapshot.return_value = _make_mock_snapshot(
            id=5, config_data={"resources": {"gateways": []}},
        )
        mock_svc_cls.return_value = mock_svc

        response = client.get("/api/snapshots/5", headers=viewer_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["id"] == 5
        assert "config_data" in data
        mock_svc.get_snapshot.assert_called_once_with(5)

    @patch("routes.snapshots.SnapshotService")
    def test_get_snapshot_not_found(
        self, mock_svc_cls, client, viewer_headers, all_test_users,
    ):
        """Getting a nonexistent snapshot returns 404."""
        mock_svc = MagicMock()
        mock_svc.get_snapshot.return_value = None
        mock_svc_cls.return_value = mock_svc

        response = client.get("/api/snapshots/99999", headers=viewer_headers)
        assert response.status_code == 404


class TestDeleteSnapshot:
    """DELETE /api/snapshots/{snapshot_id}."""

    @patch("routes.snapshots.SnapshotService")
    def test_delete_snapshot_admin(
        self, mock_svc_cls, client, admin_headers, sample_user,
    ):
        """Admin can delete a snapshot."""
        mock_svc = MagicMock()
        mock_svc.delete_snapshot.return_value = True
        mock_svc_cls.return_value = mock_svc

        response = client.delete("/api/snapshots/5", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["id"] == 5
        mock_svc.delete_snapshot.assert_called_once_with(5)

    def test_delete_snapshot_operator_denied(
        self, client, operator_headers, all_test_users,
    ):
        """Operator cannot delete snapshots (admin-only) — returns 403."""
        response = client.delete("/api/snapshots/5", headers=operator_headers)
        assert response.status_code == 403


class TestRestoreSnapshot:
    """POST /api/snapshots/{snapshot_id}/restore."""

    @patch("routes.snapshots.SnapshotService")
    def test_restore_snapshot_admin(
        self, mock_svc_cls, client, admin_headers, sample_user,
    ):
        """Admin can restore from a snapshot."""
        mock_svc = MagicMock()
        mock_svc.restore_snapshot.return_value = {
            "success": True,
            "applied": 3,
            "failed": 0,
            "skipped": 1,
        }
        mock_svc_cls.return_value = mock_svc

        response = client.post("/api/snapshots/5/restore", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert data["applied"] == 3
        mock_svc.restore_snapshot.assert_called_once_with(5)


class TestSnapshotRBAC:
    """RBAC enforcement for snapshot endpoints."""

    def test_viewer_cannot_create(
        self, client, viewer_headers, all_test_users, sample_project,
    ):
        """Viewer cannot create a snapshot — returns 403."""
        response = client.post(
            f"/api/projects/{sample_project.id}/snapshots",
            json={"label": "blocked"},
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_operator_cannot_restore(
        self, client, operator_headers, all_test_users,
    ):
        """Operator cannot restore a snapshot (admin-only) — returns 403."""
        response = client.post(
            "/api/snapshots/5/restore",
            headers=operator_headers,
        )
        assert response.status_code == 403

    def test_unauthenticated_cannot_list(self, client, sample_project):
        """Unauthenticated request returns 401."""
        response = client.get(f"/api/projects/{sample_project.id}/snapshots")
        assert response.status_code == 401
