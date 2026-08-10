"""
Audit Service for BNK-Forge.

Centralizes audit log creation so all parts of the app (middleware, routes, tasks)
use a consistent interface for recording actions.

ENG-006: This service retains its own db.commit() calls because it is used as an
internal cross-cutting concern (called by other services, middleware, and tasks).
Audit logging commits independently to avoid losing audit records if the caller
transaction fails.
"""
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from models import AuditLog, User

logger = logging.getLogger(__name__)


def create_audit_log(
    db: Session,
    action: str,
    resource_type: str,
    *,
    user: str | None = None,
    user_id: int | None = None,
    resource_id: str | None = None,
    resource_name: str | None = None,
    status: str = "success",
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    http_method: str | None = None,
    http_path: str | None = None,
    http_status_code: int | None = None,
    duration_ms: int | None = None,
    request_id: str | None = None,
) -> AuditLog | None:
    """
    Create an audit log entry.

    Args:
        db: Database session
        action: Action performed (create, update, delete, deploy, destroy, plan, init, sync, login, etc.)
        resource_type: Type of resource affected (project, module, stack, credential_template, etc.)
        user: Username string (for display)
        user_id: User ID FK (for joins)
        resource_id: ID of the affected resource
        resource_name: Human-readable name of the affected resource
        status: Result status (success, failed, error)
        details: Additional context dict
        ip_address: Client IP address
        user_agent: Client User-Agent header
        http_method: HTTP method (GET, POST, PUT, DELETE)
        http_path: API path
        http_status_code: Response status code
        duration_ms: Request duration in milliseconds
        request_id: OBS-001 correlation ID for cross-service tracing

    Returns:
        The created AuditLog entry, or None if creation failed
    """
    try:
        audit = AuditLog(
            timestamp=datetime.now(UTC),
            user=user or "system",
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            resource_name=resource_name,
            status=status,
            details=details or {},
            ip_address=ip_address,
            user_agent=user_agent,
            http_method=http_method,
            http_path=http_path,
            http_status_code=http_status_code,
            duration_ms=duration_ms,
            request_id=request_id,
        )
        db.add(audit)
        db.commit()
        return audit
    except Exception as e:
        logger.error(f"Failed to create audit log: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return None


def log_from_request(
    db: Session,
    action: str,
    resource_type: str,
    *,
    request=None,
    user_obj: User | None = None,
    resource_id: str | None = None,
    resource_name: str | None = None,
    status: str = "success",
    details: dict[str, Any] | None = None,
    http_status_code: int | None = None,
    duration_ms: int | None = None,
) -> AuditLog | None:
    """
    Create an audit log entry from a FastAPI Request object.
    Extracts IP, user agent, method, and path automatically.

    Args:
        db: Database session
        action: Action performed
        resource_type: Type of resource affected
        request: FastAPI Request object
        user_obj: Authenticated User object (if available)
        resource_id: ID of the affected resource
        resource_name: Human-readable name
        status: Result status
        details: Additional context
        http_status_code: Response status code
        duration_ms: Request duration in milliseconds
    """
    ip_address = None
    user_agent = None
    http_method = None
    http_path = None

    if request:
        # Get real client IP (check X-Forwarded-For for proxied requests)
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            ip_address = forwarded.split(",")[0].strip()
        else:
            ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("User-Agent", "")[:500]
        http_method = request.method
        http_path = str(request.url.path)

    return create_audit_log(
        db,
        action=action,
        resource_type=resource_type,
        user=str(user_obj.username) if user_obj else None,
        user_id=int(user_obj.id) if user_obj else None,
        resource_id=resource_id,
        resource_name=resource_name,
        status=status,
        details=details,
        ip_address=ip_address,
        user_agent=user_agent,
        http_method=http_method,
        http_path=http_path,
        http_status_code=http_status_code,
        duration_ms=duration_ms,
    )
