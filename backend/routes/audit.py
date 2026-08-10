"""
Audit Log API routes for BNK-Forge.

Provides paginated, filterable access to the audit trail.
Admin-only — viewers and operators can see their own logs,
admins can see everything.
"""
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Text, desc
from sqlalchemy.orm import Session

from database import get_db
from models import AuditLog, User
from routes.auth import effective_role, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
def get_audit_logs(
    # Pagination
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page"),
    # Filters
    user: str | None = Query(None, description="Filter by username"),
    action: str | None = Query(None, description="Filter by action (create, update, delete, deploy, etc.)"),
    resource_type: str | None = Query(None, description="Filter by resource type (project, module, stack, etc.)"),
    resource_id: str | None = Query(None, description="Filter by resource ID"),
    status: str | None = Query(None, description="Filter by status (success, failed, error)"),
    since: str | None = Query(None, description="Filter logs after this ISO datetime"),
    until: str | None = Query(None, description="Filter logs before this ISO datetime"),
    search: str | None = Query(None, description="Search in resource_name, http_path, details"),
    # Auth
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get paginated audit logs with optional filtering.

    Admin users see all logs. Non-admin users see only their own logs.
    """
    query = db.query(AuditLog)

    # Non-admin users can only see their own logs
    if effective_role(current_user) != "admin":
        query = query.filter(AuditLog.user == current_user.username)

    # Apply filters
    if user:
        query = query.filter(AuditLog.user == user)
    if action:
        query = query.filter(AuditLog.action == action)
    if resource_type:
        query = query.filter(AuditLog.resource_type == resource_type)
    if resource_id:
        query = query.filter(AuditLog.resource_id == resource_id)
    if status:
        query = query.filter(AuditLog.status == status)

    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            query = query.filter(AuditLog.timestamp >= since_dt)
        except (ValueError, TypeError):
            pass  # Ignore invalid date

    if until:
        try:
            until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
            query = query.filter(AuditLog.timestamp <= until_dt)
        except (ValueError, TypeError):
            pass

    if search:
        search_term = f"%{search}%"
        query = query.filter(
            (AuditLog.resource_name.ilike(search_term)) |
            (AuditLog.http_path.ilike(search_term)) |
            # FEAT-0014 / ERR-0029: the spec says search covers details; cast the
            # JSON column to text so it is matched too (works on SQLite + Postgres).
            (AuditLog.details.cast(Text).ilike(search_term))
        )

    # Get total count before pagination
    total = query.count()

    # Order by most recent first, paginate
    logs = (
        query
        .order_by(desc(AuditLog.timestamp))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "logs": [_audit_to_dict(log) for log in logs],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if total > 0 else 0,
    }


@router.get("/stats")
def get_audit_stats(
    days: int = Query(7, ge=1, le=90, description="Number of days to analyze"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get audit log statistics for dashboard display.

    Returns action counts, active users, and recent activity summary.
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)

    base_query = db.query(AuditLog).filter(AuditLog.timestamp >= cutoff)

    # Non-admin users can only see their own stats
    if effective_role(current_user) != "admin":
        base_query = base_query.filter(AuditLog.user == current_user.username)

    total_events = base_query.count()

    # Action breakdown
    from sqlalchemy import func
    action_counts = (
        base_query
        .with_entities(AuditLog.action, func.count(AuditLog.id))
        .group_by(AuditLog.action)
        .order_by(desc(func.count(AuditLog.id)))
        .all()
    )

    # Resource type breakdown
    resource_counts = (
        base_query
        .with_entities(AuditLog.resource_type, func.count(AuditLog.id))
        .group_by(AuditLog.resource_type)
        .order_by(desc(func.count(AuditLog.id)))
        .all()
    )

    # Active users
    user_counts = (
        base_query
        .filter(AuditLog.user.isnot(None), AuditLog.user != "system")
        .with_entities(AuditLog.user, func.count(AuditLog.id))
        .group_by(AuditLog.user)
        .order_by(desc(func.count(AuditLog.id)))
        .all()
    )

    # Status breakdown
    status_counts = (
        base_query
        .with_entities(AuditLog.status, func.count(AuditLog.id))
        .group_by(AuditLog.status)
        .order_by(desc(func.count(AuditLog.id)))
        .all()
    )

    # Events per day (for chart)
    daily_counts = (
        base_query
        .with_entities(
            func.date(AuditLog.timestamp).label("date"),
            func.count(AuditLog.id).label("count"),
        )
        .group_by(func.date(AuditLog.timestamp))
        .order_by(func.date(AuditLog.timestamp))
        .all()
    )

    return {
        "period_days": days,
        "total_events": total_events,
        "actions": {action: count for action, count in action_counts},
        "resource_types": {rt: count for rt, count in resource_counts},
        "users": {user: count for user, count in user_counts},
        "statuses": {status: count for status, count in status_counts},
        "daily_activity": [
            {"date": str(date), "count": count}
            for date, count in daily_counts
        ],
    }


@router.get("/filters")
def get_audit_filters(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get available filter values for the audit log UI.

    Returns distinct actions, resource types, users, and statuses.
    """
    from sqlalchemy import distinct

    base_query = db.query(AuditLog)
    if effective_role(current_user) != "admin":
        base_query = base_query.filter(AuditLog.user == current_user.username)

    actions = [r[0] for r in base_query.with_entities(distinct(AuditLog.action)).all() if r[0]]
    resource_types = [r[0] for r in base_query.with_entities(distinct(AuditLog.resource_type)).all() if r[0]]
    users = [r[0] for r in base_query.with_entities(distinct(AuditLog.user)).all() if r[0]]
    statuses = [r[0] for r in base_query.with_entities(distinct(AuditLog.status)).all() if r[0]]

    return {
        "actions": sorted(actions),
        "resource_types": sorted(resource_types),
        "users": sorted(users),
        "statuses": sorted(statuses),
    }


def _audit_to_dict(log: AuditLog) -> dict:
    """Convert AuditLog model to dict for API response."""
    return {
        "id": log.id,
        "timestamp": log.timestamp.isoformat() if log.timestamp else None,
        "user": log.user,
        "user_id": log.user_id,
        "action": log.action,
        "resource_type": log.resource_type,
        "resource_id": log.resource_id,
        "resource_name": log.resource_name,
        "status": log.status,
        "details": log.details,
        "ip_address": log.ip_address,
        "http_method": log.http_method,
        "http_path": log.http_path,
        "http_status_code": log.http_status_code,
        "duration_ms": log.duration_ms,
    }
