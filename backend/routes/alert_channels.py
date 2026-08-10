"""
Alert Channels API routes.

CRUD for alert channel management + test endpoint.
Sprint 7 — Day 2 Operations.
"""
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.errors import BadRequestError, NotFoundError
from database import get_db
from models import AlertChannel, AlertHistory
from routes.auth import require_operator, require_viewer
from services.alert_service import test_channel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/alert-channels", tags=["alert-channels"])


# ============================================================
# Pydantic Schemas
# ============================================================

class AlertChannelCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    channel_type: str = Field(..., description="webhook, slack, msteams, email")
    url: str | None = None
    headers: dict | None = None
    secret: str | None = None
    email_to: list[str] | None = None
    email_from: str | None = None
    event_types: list[str] | None = Field(default_factory=list)
    project_ids: list[int] | None = Field(default_factory=list)
    cluster_ids: list[int] | None = Field(default_factory=list)
    min_interval_seconds: int = 60
    description: str | None = None
    enabled: bool = True


class AlertChannelUpdate(BaseModel):
    name: str | None = None
    channel_type: str | None = None
    url: str | None = None
    headers: dict | None = None
    secret: str | None = None
    email_to: list[str] | None = None
    email_from: str | None = None
    event_types: list[str] | None = None
    project_ids: list[int] | None = None
    cluster_ids: list[int] | None = None
    min_interval_seconds: int | None = None
    description: str | None = None
    enabled: bool | None = None


def _serialize_channel(channel: AlertChannel) -> dict:
    return {
        "id": channel.id,
        "name": channel.name,
        "channel_type": channel.channel_type,
        "enabled": channel.enabled,
        "url": channel.url,
        "headers": channel.headers,
        "event_types": channel.event_types or [],
        "project_ids": channel.project_ids or [],
        "cluster_ids": channel.cluster_ids or [],
        "min_interval_seconds": channel.min_interval_seconds,
        "description": channel.description,
        "total_sent": channel.total_sent or 0,
        "total_failed": channel.total_failed or 0,
        "last_error": channel.last_error,
        "last_fired_at": channel.last_fired_at.isoformat() if channel.last_fired_at else None,
        "last_success_at": channel.last_success_at.isoformat() if channel.last_success_at else None,
        "created_by": channel.created_by,
        "created_at": channel.created_at.isoformat() if channel.created_at else None,
        "updated_at": channel.updated_at.isoformat() if channel.updated_at else None,
    }


# ============================================================
# CRUD Endpoints
# ============================================================

@router.get("", dependencies=[Depends(require_viewer)])
def list_alert_channels(
    enabled: bool | None = Query(None),
    channel_type: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """List all alert channels, optionally filtered."""
    query = db.query(AlertChannel)
    if enabled is not None:
        query = query.filter(AlertChannel.enabled == enabled)
    if channel_type:
        query = query.filter(AlertChannel.channel_type == channel_type)

    channels = query.order_by(AlertChannel.name).all()
    return [_serialize_channel(c) for c in channels]


@router.get("/{channel_id}", dependencies=[Depends(require_viewer)])
def get_alert_channel(channel_id: int, db: Session = Depends(get_db)):
    """Get a single alert channel with recent history."""
    channel = db.query(AlertChannel).filter(AlertChannel.id == channel_id).first()
    if not channel:
        raise NotFoundError("alert_channel", channel_id)

    result = _serialize_channel(channel)

    # Include recent history (last 20)
    history = db.query(AlertHistory).filter(
        AlertHistory.channel_id == channel_id
    ).order_by(AlertHistory.created_at.desc()).limit(20).all()

    result["recent_history"] = [{
        "id": h.id,
        "event_type": h.event_type,
        "severity": h.severity,
        "title": h.title,
        "status": h.status,
        "http_status_code": h.http_status_code,
        "error_message": h.error_message,
        "duration_ms": h.duration_ms,
        "created_at": h.created_at.isoformat() if h.created_at else None,
    } for h in history]

    return result


@router.post("", dependencies=[Depends(require_operator)])
def create_alert_channel(
    data: AlertChannelCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Create a new alert channel."""
    valid_types = {"webhook", "slack", "msteams", "email"}
    if data.channel_type not in valid_types:
        raise BadRequestError(f"Invalid channel_type '{data.channel_type}'. Must be one of: {', '.join(valid_types)}")

    if data.channel_type in ("webhook", "slack", "msteams") and not data.url:
        raise BadRequestError(f"URL is required for {data.channel_type} channels")

    # Extract username from JWT
    username = "system"
    try:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            from services.auth_service import decode_token
            payload = decode_token(auth_header.split(" ", 1)[1])
            username = payload.get("sub", "system")
    except Exception:
        pass

    channel = AlertChannel(
        name=data.name,
        channel_type=data.channel_type,
        enabled=data.enabled,
        url=data.url,
        headers=data.headers,
        secret=data.secret,
        email_to=data.email_to,
        email_from=data.email_from,
        event_types=data.event_types or [],
        project_ids=data.project_ids or [],
        cluster_ids=data.cluster_ids or [],
        min_interval_seconds=data.min_interval_seconds,
        description=data.description,
        created_by=username,
    )
    db.add(channel)
    db.commit()
    db.refresh(channel)

    logger.info(f"Alert channel created: {channel.name} ({channel.channel_type}) by {username}")
    return _serialize_channel(channel)


@router.put("/{channel_id}", dependencies=[Depends(require_operator)])
def update_alert_channel(
    channel_id: int,
    data: AlertChannelUpdate,
    db: Session = Depends(get_db),
):
    """Update an alert channel."""
    channel = db.query(AlertChannel).filter(AlertChannel.id == channel_id).first()
    if not channel:
        raise NotFoundError("alert_channel", channel_id)

    update_fields = data.model_dump(exclude_unset=True)
    for field, value in update_fields.items():
        setattr(channel, field, value)

    channel.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(channel)

    logger.info(f"Alert channel updated: {channel.name} (id={channel_id})")
    return _serialize_channel(channel)


@router.delete("/{channel_id}", dependencies=[Depends(require_operator)])
def delete_alert_channel(channel_id: int, db: Session = Depends(get_db)):
    """Delete an alert channel and its history."""
    channel = db.query(AlertChannel).filter(AlertChannel.id == channel_id).first()
    if not channel:
        raise NotFoundError("alert_channel", channel_id)

    name = channel.name
    db.delete(channel)
    db.commit()

    logger.info(f"Alert channel deleted: {name} (id={channel_id})")
    return {"success": True, "message": f"Alert channel '{name}' deleted"}


@router.post("/{channel_id}/test", dependencies=[Depends(require_operator)])
def test_alert_channel(channel_id: int, db: Session = Depends(get_db)):
    """Send a test alert to verify channel configuration."""
    channel = db.query(AlertChannel).filter(AlertChannel.id == channel_id).first()
    if not channel:
        raise NotFoundError("alert_channel", channel_id)

    result = test_channel(db, channel_id)
    db.commit()

    if result["success"]:
        return {
            "success": True,
            "message": f"Test alert sent successfully to '{channel.name}'",
            "status_code": result.get("status_code"),
            "duration_ms": result.get("duration_ms"),
        }
    else:
        return {
            "success": False,
            "message": f"Failed to send test alert to '{channel.name}'",
            "error": result.get("error"),
            "status_code": result.get("status_code"),
            "duration_ms": result.get("duration_ms"),
        }


@router.post("/{channel_id}/toggle", dependencies=[Depends(require_operator)])
def toggle_alert_channel(channel_id: int, db: Session = Depends(get_db)):
    """Toggle an alert channel enabled/disabled."""
    channel = db.query(AlertChannel).filter(AlertChannel.id == channel_id).first()
    if not channel:
        raise NotFoundError("alert_channel", channel_id)

    channel.enabled = not channel.enabled
    channel.updated_at = datetime.now(UTC)
    db.commit()

    state = "enabled" if channel.enabled else "disabled"
    logger.info(f"Alert channel {state}: {channel.name} (id={channel_id})")
    return {"success": True, "enabled": channel.enabled, "message": f"Channel '{channel.name}' {state}"}


# ============================================================
# Alert History Endpoints
# ============================================================

@router.get("/history/recent", dependencies=[Depends(require_viewer)])
def get_recent_alerts(
    limit: int = Query(20, ge=1, le=100),
    event_type: str | None = Query(None),
    severity: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Get recent alerts across all channels."""
    query = db.query(AlertHistory)
    if event_type:
        query = query.filter(AlertHistory.event_type == event_type)
    if severity:
        query = query.filter(AlertHistory.severity == severity)

    history = query.order_by(AlertHistory.created_at.desc()).limit(limit).all()

    return [{
        "id": h.id,
        "channel_id": h.channel_id,
        "event_type": h.event_type,
        "severity": h.severity,
        "title": h.title,
        "message": h.message,
        "status": h.status,
        "created_at": h.created_at.isoformat() if h.created_at else None,
    } for h in history]


@router.get("/{channel_id}/history", dependencies=[Depends(require_viewer)])
def get_channel_history(
    channel_id: int,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    event_type: str | None = Query(None),
    status: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Get alert history for a specific channel."""
    channel = db.query(AlertChannel).filter(AlertChannel.id == channel_id).first()
    if not channel:
        raise NotFoundError("alert_channel", channel_id)

    query = db.query(AlertHistory).filter(AlertHistory.channel_id == channel_id)
    if event_type:
        query = query.filter(AlertHistory.event_type == event_type)
    if status:
        query = query.filter(AlertHistory.status == status)

    total = query.count()
    history = query.order_by(AlertHistory.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "total": total,
        "history": [{
            "id": h.id,
            "event_type": h.event_type,
            "severity": h.severity,
            "title": h.title,
            "message": h.message,
            "project_id": h.project_id,
            "cluster_id": h.cluster_id,
            "status": h.status,
            "http_status_code": h.http_status_code,
            "error_message": h.error_message,
            "duration_ms": h.duration_ms,
            "created_at": h.created_at.isoformat() if h.created_at else None,
        } for h in history],
    }
