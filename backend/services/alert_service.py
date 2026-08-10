"""
Alert dispatch service for BNK-Forge.

Handles sending alerts to configured channels (webhook, Slack, MS Teams)
when health changes, drift is detected, or deployments complete.

Usage:
    from services.alert_service import fire_alert

    fire_alert(
        db=db,
        event_type="health_change",
        severity="critical",
        title="TMM pods unhealthy",
        message="3 of 7 TMM pods are not ready on cluster SG-AI-Lab",
        project_id=1,
        cluster_id=2,
        extra={"component": "tmm", "healthy": 4, "total": 7},
    )
"""
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session

from models import AlertChannel, AlertHistory
from models.enums import AlertStatus

logger = logging.getLogger(__name__)

# Valid event types
EVENT_TYPES = {
    "health_change",
    "drift_detected",
    "deploy_success",
    "deploy_failed",
    "deploy_started",
}

# Severity levels
SEVERITY_LEVELS = {"info", "warning", "critical"}


def fire_alert(
    db: Session,
    event_type: str,
    severity: str,
    title: str,
    message: str = "",
    project_id: int | None = None,
    cluster_id: int | None = None,
    extra: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Fire an alert to all matching enabled channels.

    Returns list of delivery results: [{"channel_id": int, "status": "sent"|"failed"|"rate_limited", ...}]
    """
    results = []

    # Find matching channels
    channels = db.query(AlertChannel).filter(
        AlertChannel.enabled
    ).all()

    for channel in channels:
        # Check event type filter
        if channel.event_types and event_type not in channel.event_types:
            continue

        # Check project filter
        if channel.project_ids and project_id and project_id not in channel.project_ids:
            continue

        # Check cluster filter
        if channel.cluster_ids and cluster_id and cluster_id not in channel.cluster_ids:
            continue

        # Check rate limiting
        if channel.last_fired_at and channel.min_interval_seconds:
            min_interval = timedelta(seconds=channel.min_interval_seconds)
            if datetime.now(UTC) - channel.last_fired_at < min_interval:
                # Rate limited — log but don't send
                history = AlertHistory(
                    channel_id=channel.id,
                    event_type=event_type,
                    severity=severity,
                    title=title,
                    message=message,
                    project_id=project_id,
                    cluster_id=cluster_id,
                    status=AlertStatus.RATE_LIMITED,
                )
                db.add(history)
                results.append({"channel_id": channel.id, "status": "rate_limited", "channel_name": channel.name})
                continue

        # Build payload
        payload = _build_payload(channel, event_type, severity, title, message, project_id, cluster_id, extra)

        # Send
        result = _dispatch_to_channel(channel, payload)

        # Record history
        history = AlertHistory(
            channel_id=channel.id,
            event_type=event_type,
            severity=severity,
            title=title,
            message=message,
            project_id=project_id,
            cluster_id=cluster_id,
            payload=payload,
            status=AlertStatus.SENT if result["success"] else AlertStatus.FAILED,
            http_status_code=result.get("status_code"),
            response_body=result.get("response_body", "")[:2000],  # Truncate
            error_message=result.get("error"),
            duration_ms=result.get("duration_ms"),
        )
        db.add(history)

        # Update channel stats
        channel.last_fired_at = datetime.now(UTC)
        if result["success"]:
            channel.total_sent = (channel.total_sent or 0) + 1
            channel.last_success_at = datetime.now(UTC)
            channel.last_error = None
        else:
            channel.total_failed = (channel.total_failed or 0) + 1
            channel.last_error = result.get("error", "Unknown error")

        results.append({
            "channel_id": channel.id,
            "channel_name": channel.name,
            "status": "sent" if result["success"] else "failed",
            "error": result.get("error"),
        })

    db.flush()

    if results:
        sent = sum(1 for r in results if r["status"] == "sent")
        failed = sum(1 for r in results if r["status"] == "failed")
        logger.info(f"Alert '{title}' dispatched: {sent} sent, {failed} failed, {len(results) - sent - failed} rate-limited")

    return results


def _build_payload(
    channel: AlertChannel,
    event_type: str,
    severity: str,
    title: str,
    message: str,
    project_id: int | None,
    cluster_id: int | None,
    extra: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the JSON payload for the channel type."""

    timestamp = datetime.now(UTC).isoformat() + "Z"

    if channel.channel_type == "slack":
        return _build_slack_payload(event_type, severity, title, message, timestamp, extra)
    elif channel.channel_type == "msteams":
        return _build_msteams_payload(event_type, severity, title, message, timestamp, extra)
    else:
        # Generic webhook — structured JSON
        return {
            "source": "bnk-forge",
            "event_type": event_type,
            "severity": severity,
            "title": title,
            "message": message,
            "timestamp": timestamp,
            "project_id": project_id,
            "cluster_id": cluster_id,
            "data": extra or {},
        }


def _build_slack_payload(
    event_type: str, severity: str, title: str, message: str,
    timestamp: str, extra: dict | None,
) -> dict[str, Any]:
    """Build Slack incoming webhook payload with Block Kit."""
    severity_emoji = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(severity, "📢")
    event_label = event_type.replace("_", " ").title()

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{severity_emoji} {title}"}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Event:* {event_label}"},
                {"type": "mrkdwn", "text": f"*Severity:* {severity.upper()}"},
            ]
        },
    ]

    if message:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": message}
        })

    if extra:
        detail_lines = [f"*{k}:* {v}" for k, v in extra.items() if v is not None]
        if detail_lines:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n".join(detail_lines[:10])}
            })

    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"BNK-Forge | {timestamp}"}]
    })

    return {"blocks": blocks}


def _build_msteams_payload(
    event_type: str, severity: str, title: str, message: str,
    timestamp: str, extra: dict | None,
) -> dict[str, Any]:
    """Build Microsoft Teams Adaptive Card payload."""
    color = {"info": "0078D4", "warning": "FFC107", "critical": "D32F2F"}.get(severity, "757575")

    facts = [
        {"name": "Event", "value": event_type.replace("_", " ").title()},
        {"name": "Severity", "value": severity.upper()},
    ]
    if extra:
        for k, v in list(extra.items())[:8]:
            if v is not None:
                facts.append({"name": k, "value": str(v)})

    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": color,
        "summary": title,
        "sections": [{
            "activityTitle": title,
            "activitySubtitle": f"BNK-Forge | {timestamp}",
            "facts": facts,
            "text": message,
            "markdown": True,
        }],
    }


def _dispatch_to_channel(channel: AlertChannel, payload: dict[str, Any]) -> dict[str, Any]:
    """Send the payload to the channel URL via HTTP POST."""
    if not channel.url:
        return {"success": False, "error": "No URL configured"}

    headers = {"Content-Type": "application/json"}
    if channel.headers:
        headers.update(channel.headers)

    start = time.monotonic()
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                channel.url,
                json=payload,
                headers=headers,
            )
        duration_ms = int((time.monotonic() - start) * 1000)

        if response.status_code < 300:
            return {
                "success": True,
                "status_code": response.status_code,
                "response_body": response.text[:500],
                "duration_ms": duration_ms,
            }
        else:
            return {
                "success": False,
                "status_code": response.status_code,
                "response_body": response.text[:500],
                "error": f"HTTP {response.status_code}: {response.text[:200]}",
                "duration_ms": duration_ms,
            }
    except httpx.TimeoutException:
        duration_ms = int((time.monotonic() - start) * 1000)
        return {"success": False, "error": "Request timed out (10s)", "duration_ms": duration_ms}
    except Exception as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        return {"success": False, "error": str(e), "duration_ms": duration_ms}


def test_channel(db: Session, channel_id: int) -> dict[str, Any]:
    """Send a test alert to a specific channel."""
    channel = db.query(AlertChannel).filter(AlertChannel.id == channel_id).first()
    if not channel:
        return {"success": False, "error": "Channel not found"}

    payload = _build_payload(
        channel,
        event_type="test",
        severity="info",
        title="BNK-Forge Test Alert",
        message=f"This is a test alert from BNK-Forge sent to channel '{channel.name}'. If you see this, the channel is configured correctly.",
        project_id=None,
        cluster_id=None,
        extra={"test": True, "channel_type": channel.channel_type},
    )

    result = _dispatch_to_channel(channel, payload)

    # Record in history
    history = AlertHistory(
        channel_id=channel.id,
        event_type="test",
        severity="info",
        title="BNK-Forge Test Alert",
        message="Test alert",
        payload=payload,
        status=AlertStatus.SENT if result["success"] else AlertStatus.FAILED,
        http_status_code=result.get("status_code"),
        response_body=result.get("response_body", "")[:2000],
        error_message=result.get("error"),
        duration_ms=result.get("duration_ms"),
    )
    db.add(history)
    db.flush()

    return result
