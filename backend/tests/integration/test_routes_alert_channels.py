"""
Integration tests for alert channel routes — /api/alert-channels.

Covers: list, create, get (with history), toggle, delete channels, and RBAC.
These routes do inline ORM queries — records are created directly in DB.
Uses FastAPI TestClient with real SQLite DB.
"""

import pytest

from models import AlertChannel


class TestListAlertChannels:
    """GET /api/alert-channels."""

    def test_list_channels(self, client, viewer_headers, all_test_users, db):
        """Viewer can list alert channels created in the DB."""
        ch = AlertChannel(
            name="ops-webhook",
            channel_type="webhook",
            enabled=True,
            url="https://hooks.example.com/ops",
            event_types=["deploy_success", "deploy_failure"],
            created_by="testoperator",
        )
        db.add(ch)
        db.commit()

        response = client.get("/api/alert-channels", headers=viewer_headers)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        names = [c["name"] for c in data]
        assert "ops-webhook" in names

    def test_list_channels_filter_by_type(self, client, viewer_headers, all_test_users, db):
        """Filtering by channel_type narrows results."""
        db.add(AlertChannel(name="slack-ch", channel_type="slack", url="https://hooks.slack.com/x"))
        db.add(AlertChannel(name="email-ch", channel_type="email"))
        db.commit()

        response = client.get(
            "/api/alert-channels?channel_type=slack",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        for ch in data:
            assert ch["channel_type"] == "slack"


class TestCreateAlertChannel:
    """POST /api/alert-channels."""

    def test_create_channel(self, client, operator_headers, all_test_users):
        """Operator can create a webhook alert channel."""
        response = client.post(
            "/api/alert-channels",
            json={
                "name": "deploy-alerts",
                "channel_type": "webhook",
                "url": "https://hooks.example.com/deploy",
                "event_types": ["deploy_success"],
                "description": "Deployment notifications",
            },
            headers=operator_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "deploy-alerts"
        assert data["channel_type"] == "webhook"
        assert data["enabled"] is True
        assert data["url"] == "https://hooks.example.com/deploy"

    def test_create_channel_invalid_type(self, client, operator_headers, all_test_users):
        """Creating a channel with invalid type returns 400."""
        response = client.post(
            "/api/alert-channels",
            json={"name": "bad-channel", "channel_type": "carrier_pigeon"},
            headers=operator_headers,
        )
        assert response.status_code == 400


class TestGetAlertChannelDetail:
    """GET /api/alert-channels/{id}."""

    def test_get_channel_detail(self, client, viewer_headers, all_test_users, db):
        """Viewer can get channel detail including recent_history."""
        ch = AlertChannel(
            name="detail-test",
            channel_type="slack",
            url="https://hooks.slack.com/detail",
            enabled=True,
        )
        db.add(ch)
        db.commit()
        db.refresh(ch)

        response = client.get(
            f"/api/alert-channels/{ch.id}",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "detail-test"
        assert "recent_history" in data
        assert isinstance(data["recent_history"], list)

    def test_get_channel_not_found(self, client, viewer_headers, all_test_users):
        """Requesting nonexistent channel returns 404."""
        response = client.get("/api/alert-channels/99999", headers=viewer_headers)
        assert response.status_code == 404


class TestToggleAlertChannel:
    """POST /api/alert-channels/{id}/toggle."""

    def test_toggle_channel(self, client, operator_headers, all_test_users, db):
        """Operator can toggle an alert channel enabled/disabled."""
        ch = AlertChannel(
            name="toggle-me",
            channel_type="webhook",
            url="https://hooks.example.com/toggle",
            enabled=True,
        )
        db.add(ch)
        db.commit()
        db.refresh(ch)

        # First toggle — enabled=True -> enabled=False
        response = client.post(
            f"/api/alert-channels/{ch.id}/toggle",
            headers=operator_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["enabled"] is False

        # Second toggle — enabled=False -> enabled=True
        response = client.post(
            f"/api/alert-channels/{ch.id}/toggle",
            headers=operator_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True


class TestDeleteAlertChannel:
    """DELETE /api/alert-channels/{id}."""

    def test_delete_channel(self, client, operator_headers, all_test_users, db):
        """Operator can delete an alert channel."""
        ch = AlertChannel(
            name="delete-me",
            channel_type="webhook",
            url="https://hooks.example.com/delete",
        )
        db.add(ch)
        db.commit()
        db.refresh(ch)
        channel_id = ch.id

        response = client.delete(
            f"/api/alert-channels/{channel_id}",
            headers=operator_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify channel is gone
        response = client.get(
            f"/api/alert-channels/{channel_id}",
            headers=operator_headers,
        )
        assert response.status_code == 404


class TestAlertChannelRBAC:
    """RBAC enforcement — viewer cannot create, toggle, or delete."""

    def test_viewer_cannot_create(self, client, viewer_headers, all_test_users):
        """Viewer cannot create alert channels — returns 403."""
        response = client.post(
            "/api/alert-channels",
            json={
                "name": "sneaky-channel",
                "channel_type": "webhook",
                "url": "https://evil.com/hook",
            },
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_viewer_cannot_toggle(self, client, viewer_headers, all_test_users, db):
        """Viewer cannot toggle channels — returns 403."""
        ch = AlertChannel(name="v-toggle", channel_type="webhook", url="https://x.com")
        db.add(ch)
        db.commit()
        db.refresh(ch)

        response = client.post(
            f"/api/alert-channels/{ch.id}/toggle",
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_viewer_cannot_delete(self, client, viewer_headers, all_test_users, db):
        """Viewer cannot delete channels — returns 403."""
        ch = AlertChannel(name="v-delete", channel_type="email")
        db.add(ch)
        db.commit()
        db.refresh(ch)

        response = client.delete(
            f"/api/alert-channels/{ch.id}",
            headers=viewer_headers,
        )
        assert response.status_code == 403
