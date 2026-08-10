"""
Integration tests for notification routes — /api/notifications.

Covers: list, unread count, mark as read, create, delete notifications.
ALL endpoints require viewer role (router-level dependency).
These routes do inline ORM queries — records are created directly in DB.
Uses FastAPI TestClient with real SQLite DB.
"""

from datetime import datetime, timezone

import pytest

from models import Notification


class TestListNotifications:
    """GET /api/notifications."""

    def test_list_notifications(self, client, viewer_headers, all_test_users, db):
        """Viewer can list notifications for a user."""
        n1 = Notification(
            user="testviewer",
            type="deploy",
            title="Deployment Complete",
            message="Module vpc deployed successfully",
            is_read=False,
        )
        n2 = Notification(
            user="testviewer",
            type="alert",
            title="Drift Detected",
            message="Drift found in module networking",
            is_read=True,
        )
        db.add_all([n1, n2])
        db.commit()

        response = client.get(
            "/api/notifications?user=testviewer",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 2
        titles = [n["title"] for n in data]
        assert "Deployment Complete" in titles
        assert "Drift Detected" in titles

    def test_list_empty(self, client, viewer_headers, all_test_users):
        """Listing notifications for user with none returns empty list."""
        response = client.get(
            "/api/notifications?user=nobody",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data == []


class TestGetUnreadCount:
    """GET /api/notifications/unread-count."""

    def test_get_unread_count(self, client, viewer_headers, all_test_users, db):
        """Returns count response for unread notifications endpoint."""
        db.add(Notification(
            user="testviewer", type="info", title="N1", message="msg1", is_read=False,
        ))
        db.add(Notification(
            user="testviewer", type="info", title="N2", message="msg2", is_read=False,
        ))
        db.add(Notification(
            user="testviewer", type="info", title="N3", message="msg3", is_read=True,
        ))
        db.commit()

        response = client.get(
            "/api/notifications/unread-count?user=testviewer",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "count" in data
        assert isinstance(data["count"], int)


class TestMarkAsRead:
    """PATCH /api/notifications/{id}/read."""

    def test_mark_as_read(self, client, viewer_headers, all_test_users, db):
        """Viewer can mark a notification as read."""
        notif = Notification(
            user="testviewer",
            type="deploy",
            title="Mark Me",
            message="This should become read",
            is_read=False,
        )
        db.add(notif)
        db.commit()
        db.refresh(notif)

        response = client.patch(
            f"/api/notifications/{notif.id}/read",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify in DB
        db.refresh(notif)
        assert notif.is_read is True
        assert notif.read_at is not None

    def test_mark_nonexistent_returns_404(self, client, viewer_headers, all_test_users):
        """Marking a nonexistent notification returns 404."""
        response = client.patch(
            "/api/notifications/99999/read",
            headers=viewer_headers,
        )
        assert response.status_code == 404


class TestCreateNotification:
    """POST /api/notifications."""

    def test_create_notification(self, client, viewer_headers, all_test_users):
        """Viewer can create a notification (system use)."""
        response = client.post(
            "/api/notifications",
            json={
                "user": "testviewer",
                "type": "system",
                "title": "System Alert",
                "message": "A new update is available",
                "resource_type": "system",
                "resource_id": 1,
            },
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "System Alert"
        assert data["type"] == "system"
        assert data["user"] == "testviewer"
        assert data["is_read"] is False
        assert "id" in data


class TestDeleteNotification:
    """DELETE /api/notifications/{id}."""

    def test_delete_notification(self, client, viewer_headers, all_test_users, db):
        """Viewer can delete a notification."""
        notif = Notification(
            user="testviewer",
            type="info",
            title="Delete Me",
            message="This will be removed",
        )
        db.add(notif)
        db.commit()
        db.refresh(notif)
        notif_id = notif.id

        response = client.delete(
            f"/api/notifications/{notif_id}",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify it's gone
        response = client.patch(
            f"/api/notifications/{notif_id}/read",
            headers=viewer_headers,
        )
        assert response.status_code == 404

    def test_delete_nonexistent_returns_404(self, client, viewer_headers, all_test_users):
        """Deleting a nonexistent notification returns 404."""
        response = client.delete(
            "/api/notifications/99999",
            headers=viewer_headers,
        )
        assert response.status_code == 404


class TestNotificationAuthentication:
    """Authentication enforcement for notification endpoints."""

    def test_unauthenticated_list(self, client):
        """Unauthenticated request returns 401."""
        response = client.get("/api/notifications")
        assert response.status_code == 401

    def test_unauthenticated_create(self, client):
        """Unauthenticated create returns 401."""
        response = client.post(
            "/api/notifications",
            json={
                "user": "admin",
                "type": "info",
                "title": "Test",
                "message": "msg",
            },
        )
        assert response.status_code == 401
