"""
Tests for services.alert_service — alert dispatch to webhook/Slack/Teams channels.
BC-011: Alert service — real DB, mock httpx for outbound HTTP, verify filtering and rate limiting.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from models import AlertChannel, AlertHistory
from models.enums import AlertStatus
from services.alert_service import fire_alert
from services.alert_service import test_channel as send_test_channel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_channel(db, *, name="test-channel", channel_type="webhook",
                  enabled=True, url="https://hooks.example.com/alert",
                  event_types=None, project_ids=None, cluster_ids=None,
                  min_interval_seconds=0, last_fired_at=None, headers=None,
                  **kwargs):
    """Create an AlertChannel directly in the DB."""
    channel = AlertChannel(
        name=name,
        channel_type=channel_type,
        enabled=enabled,
        url=url,
        event_types=event_types or [],
        project_ids=project_ids or [],
        cluster_ids=cluster_ids or [],
        min_interval_seconds=min_interval_seconds,
        last_fired_at=last_fired_at,
        headers=headers,
        total_sent=0,
        total_failed=0,
        **kwargs,
    )
    db.add(channel)
    db.flush()
    return channel


def _mock_httpx_success(status_code=200, text="ok"):
    """Return a patch context for httpx.Client that returns a successful response."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.text = text

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_response

    return patch("services.alert_service.httpx.Client", return_value=mock_client)


def _mock_httpx_failure(status_code=500, text="Internal Server Error"):
    """Return a patch context for httpx.Client that returns a failure response."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.text = text

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_response

    return patch("services.alert_service.httpx.Client", return_value=mock_client)


# ---------------------------------------------------------------------------
# fire_alert — channel matching
# ---------------------------------------------------------------------------

class TestFireAlertChannelMatching:
    """Channel filtering by enabled state, event_type, project, and cluster."""

    def test_no_channels_returns_empty(self, db):
        results = fire_alert(db, "health_change", "critical", "Test", "msg")
        assert results == []

    def test_disabled_channel_skipped(self, db):
        _make_channel(db, enabled=False)
        with _mock_httpx_success():
            results = fire_alert(db, "health_change", "critical", "Test", "msg")
        assert results == []

    def test_matching_enabled_channel_fires(self, db):
        _make_channel(db, name="hook-1")
        with _mock_httpx_success():
            results = fire_alert(db, "health_change", "critical", "Alert!", "details")
        assert len(results) == 1
        assert results[0]["status"] == "sent"
        assert results[0]["channel_name"] == "hook-1"

    def test_event_type_filter_excludes_non_matching(self, db):
        _make_channel(db, event_types=["deploy_success"])
        with _mock_httpx_success():
            results = fire_alert(db, "drift_detected", "warning", "Drift", "msg")
        assert results == []

    def test_event_type_filter_includes_matching(self, db):
        _make_channel(db, event_types=["drift_detected", "health_change"])
        with _mock_httpx_success():
            results = fire_alert(db, "drift_detected", "warning", "Drift", "msg")
        assert len(results) == 1
        assert results[0]["status"] == "sent"

    def test_project_filter_excludes_non_matching(self, db):
        _make_channel(db, project_ids=[10, 20])
        with _mock_httpx_success():
            results = fire_alert(
                db, "health_change", "critical", "Alert", "msg", project_id=99
            )
        assert results == []

    def test_cluster_filter_excludes_non_matching(self, db):
        _make_channel(db, cluster_ids=[1, 2])
        with _mock_httpx_success():
            results = fire_alert(
                db, "health_change", "critical", "Alert", "msg", cluster_id=99
            )
        assert results == []


# ---------------------------------------------------------------------------
# fire_alert — rate limiting
# ---------------------------------------------------------------------------

class TestFireAlertRateLimiting:
    """Rate limiting based on min_interval_seconds and last_fired_at."""

    def test_rate_limited_when_fired_recently(self, db):
        # SQLite returns naive datetimes; the service does datetime.now(UTC) which is
        # tz-aware. Patch datetime.now inside the service to return naive UTC so the
        # comparison succeeds (production uses PostgreSQL which preserves tz).
        _make_channel(
            db,
            min_interval_seconds=300,
            last_fired_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=10),
        )
        with _mock_httpx_success(), \
             patch("services.alert_service.datetime") as mock_dt:
            mock_dt.now.return_value = datetime.now(UTC).replace(tzinfo=None)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            results = fire_alert(db, "health_change", "critical", "Alert", "msg")
        assert len(results) == 1
        assert results[0]["status"] == "rate_limited"

    def test_not_rate_limited_when_interval_elapsed(self, db):
        _make_channel(
            db,
            min_interval_seconds=60,
            last_fired_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=120),
        )
        with _mock_httpx_success(), \
             patch("services.alert_service.datetime") as mock_dt:
            mock_dt.now.return_value = datetime.now(UTC).replace(tzinfo=None)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            results = fire_alert(db, "health_change", "critical", "Alert", "msg")
        assert len(results) == 1
        assert results[0]["status"] == "sent"

    def test_rate_limited_creates_history_record(self, db):
        ch = _make_channel(
            db,
            min_interval_seconds=300,
            last_fired_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=5),
        )
        with _mock_httpx_success(), \
             patch("services.alert_service.datetime") as mock_dt:
            mock_dt.now.return_value = datetime.now(UTC).replace(tzinfo=None)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            fire_alert(db, "health_change", "critical", "Alert", "msg")
        history = db.query(AlertHistory).filter(
            AlertHistory.channel_id == ch.id
        ).first()
        assert history is not None
        assert history.status == AlertStatus.RATE_LIMITED


# ---------------------------------------------------------------------------
# fire_alert — dispatch and history
# ---------------------------------------------------------------------------

class TestFireAlertDispatch:
    """HTTP dispatch and AlertHistory recording."""

    def test_successful_dispatch_records_sent_history(self, db):
        ch = _make_channel(db)
        with _mock_httpx_success():
            fire_alert(db, "deploy_success", "info", "Deploy OK", "All good")
        history = db.query(AlertHistory).filter(
            AlertHistory.channel_id == ch.id
        ).first()
        assert history is not None
        assert history.status == AlertStatus.SENT
        assert history.event_type == "deploy_success"
        assert history.title == "Deploy OK"

    def test_failed_dispatch_records_failed_history(self, db):
        ch = _make_channel(db)
        with _mock_httpx_failure(status_code=502, text="Bad Gateway"):
            results = fire_alert(db, "deploy_failed", "critical", "Deploy Failed", "err")
        assert results[0]["status"] == "failed"
        history = db.query(AlertHistory).filter(
            AlertHistory.channel_id == ch.id
        ).first()
        assert history is not None
        assert history.status == AlertStatus.FAILED

    def test_successful_dispatch_updates_channel_stats(self, db):
        ch = _make_channel(db)
        with _mock_httpx_success():
            fire_alert(db, "health_change", "warning", "Alert", "msg")
        db.refresh(ch)
        assert ch.total_sent == 1
        assert ch.last_fired_at is not None
        assert ch.last_error is None

    def test_failed_dispatch_updates_channel_stats(self, db):
        ch = _make_channel(db)
        with _mock_httpx_failure():
            fire_alert(db, "health_change", "critical", "Alert", "msg")
        db.refresh(ch)
        assert ch.total_failed == 1
        assert ch.last_error is not None


# ---------------------------------------------------------------------------
# test_channel
# ---------------------------------------------------------------------------

class TestTestChannel:
    """Send a test alert to a specific channel."""

    def test_channel_not_found(self, db):
        result = send_test_channel(db, channel_id=99999)
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_successful_test_alert(self, db):
        ch = _make_channel(db, name="slack-test", channel_type="slack")
        with _mock_httpx_success():
            result = send_test_channel(db, channel_id=ch.id)
        assert result["success"] is True
        history = db.query(AlertHistory).filter(
            AlertHistory.channel_id == ch.id
        ).first()
        assert history is not None
        assert history.event_type == "test"
        assert history.status == AlertStatus.SENT

    def test_failed_test_alert_records_history(self, db):
        ch = _make_channel(db, channel_type="msteams")
        with _mock_httpx_failure(status_code=403, text="Forbidden"):
            result = send_test_channel(db, channel_id=ch.id)
        assert result["success"] is False
        history = db.query(AlertHistory).filter(
            AlertHistory.channel_id == ch.id
        ).first()
        assert history is not None
        assert history.status == AlertStatus.FAILED
