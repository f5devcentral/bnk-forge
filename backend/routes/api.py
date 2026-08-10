"""
General API routes.

Thin HTTP handlers delegating to ApiService.
"""
import logging
import os
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.config import settings
from core.errors import ForbiddenError, NotFoundError, handle_route_errors
from database import get_db
from routes.auth import require_admin, require_viewer
from schemas.system import (
    AWSAuthMethodRequest,
    SettingsBatchUpdate,
    SettingsResponse,
    VersionResponse,
)
from services.api_service import ApiService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["api"])

LOGS_DIR = "/tmp/bnk-forge-logs"
os.makedirs(LOGS_DIR, exist_ok=True)


# ============================================================================
# Static / No-DB Endpoints
# ============================================================================

@router.get("/", dependencies=[Depends(require_viewer)])
def root():
    """Root endpoint"""
    return {"message": "BNK-Forge API", "version": settings.VERSION, "docs": "/docs"}


@router.get("/health", dependencies=[Depends(require_viewer)])
def health_check(db: Session = Depends(get_db)):
    """Health check endpoint with component status and warnings (PLAT-REL-004)."""
    return _build_health_response(db)


@router.get("/api/health", dependencies=[Depends(require_viewer)])
def health_check_api(db: Session = Depends(get_db)):
    """Health check endpoint with component status and warnings (PLAT-REL-004)."""
    return _build_health_response(db)


def _build_health_response(db: Session) -> dict:
    """
    PLAT-REL-004: Enhanced health response with component checks and warnings.

    Returns operational status for monitoring integrations, including:
    - Individual component health (database, redis, workers)
    - Warnings for degraded but non-fatal conditions
    """
    from sqlalchemy import text

    checks: dict[str, str] = {}
    warnings: list[dict[str, str]] = []
    overall = "healthy"

    # Check database
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unhealthy"
        overall = "unhealthy"

    # Check Redis
    try:
        import redis
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        r = redis.from_url(redis_url, socket_connect_timeout=2)
        r.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unhealthy"
        overall = "degraded" if overall == "healthy" else overall

    # Check workers (via heartbeat in Redis)
    try:
        from core.worker_heartbeat import heartbeat
        worker_status = heartbeat.get_all_workers()
        active_count = sum(1 for w in worker_status.values() if w.get("status") != "offline")
        checks["workers"] = "ok" if active_count > 0 else "degraded"
        if active_count == 0:
            warnings.append({
                "type": "worker_offline",
                "message": "No active Celery workers detected — background tasks will not process",
            })
            overall = "degraded" if overall == "healthy" else overall
    except Exception:
        checks["workers"] = "unknown"

    response: dict = {
        "status": overall,
        "timestamp": datetime.now(UTC).isoformat(),
        "version": settings.VERSION,
        "checks": checks,
    }
    if warnings:
        response["warnings"] = warnings
    return response


@router.get("/api/logs", dependencies=[Depends(require_viewer)])
@handle_route_errors("list logs")
def list_logs():
    """List all execution logs"""
    logs = []
    if os.path.exists(LOGS_DIR):
        for log_file in os.listdir(LOGS_DIR):
            if log_file.endswith(".log"):
                full_path = os.path.join(LOGS_DIR, log_file)
                logs.append({
                    "name": log_file, "path": full_path,
                    "size": os.path.getsize(full_path),
                    "created": datetime.fromtimestamp(os.path.getctime(full_path)).isoformat()
                })
    logs.sort(key=lambda x: x["created"], reverse=True)
    return {"logs": logs, "count": len(logs)}


@router.get("/api/logs/{log_name}", dependencies=[Depends(require_viewer)])
@handle_route_errors("get log")
def get_log(log_name: str):
    """Get specific log file content"""
    safe_logs_dir = os.path.abspath(LOGS_DIR)
    requested_path = os.path.abspath(os.path.join(LOGS_DIR, log_name))
    if os.path.commonpath([safe_logs_dir, requested_path]) != safe_logs_dir:
        raise ForbiddenError("Access denied")
    if not os.path.exists(requested_path):
        raise NotFoundError("log_file", log_name)
    with open(requested_path) as f:
        content = f.read()
    return {"name": log_name, "content": content}


@router.get("/api/version", response_model=VersionResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("get versions")
def get_versions():
    """Get OpenTofu version"""
    import subprocess
    result = subprocess.run(["tofu", "--version"], capture_output=True, text=True, timeout=10)
    opentofu_version = result.stdout.strip().split('\n')[0] if result.returncode == 0 else "Not installed"
    return {"opentofu": opentofu_version}


# ============================================================================
# Service-Backed Endpoints
# ============================================================================

@router.get("/api/providers", dependencies=[Depends(require_viewer)])
@handle_route_errors("list providers")
def list_providers(db: Session = Depends(get_db)):
    """Get summary of cloud providers configured in projects"""
    return ApiService(db).get_provider_summary()


@router.get("/api/sync/status", dependencies=[Depends(require_viewer)])
@handle_route_errors("get sync status")
def get_sync_status(db: Session = Depends(get_db)):
    """Get current sync status and recent job history."""
    return ApiService(db).get_sync_status()


@router.get("/api/settings", response_model=SettingsResponse, dependencies=[Depends(require_admin)])
@handle_route_errors("get settings")
def get_settings(db: Session = Depends(get_db)):
    """Get all application settings"""
    return ApiService(db).get_settings()


@router.put("/api/settings/{key}", dependencies=[Depends(require_admin)])
@handle_route_errors("update setting")
def update_setting(key: str, value: str, db: Session = Depends(get_db)):
    """Update an application setting"""
    result = ApiService(db).update_setting(key, value)
    db.commit()
    return result


@router.put("/api/settings", dependencies=[Depends(require_admin)])
@handle_route_errors("batch update settings")
def batch_update_settings(body: SettingsBatchUpdate, db: Session = Depends(get_db)):
    """Batch update multiple application settings"""
    result = ApiService(db).batch_update_settings(body.settings)
    db.commit()
    return result


@router.post("/api/aws/auth-method", dependencies=[Depends(require_admin)])
@handle_route_errors("set AWS auth method")
def set_aws_auth_method(body: AWSAuthMethodRequest, db: Session = Depends(get_db)):
    """Set AWS authentication method and credentials."""
    # Convert to dict for service layer (service expects dict for backward compat)
    request_dict = body.model_dump()
    request_dict['credentials'] = body.credentials.model_dump(exclude_none=True)
    result = ApiService(db).set_aws_auth_method(request_dict)
    db.commit()
    return result


@router.get("/api/aws/auth-method", dependencies=[Depends(require_admin)])
@handle_route_errors("get AWS auth method")
def get_aws_auth_method(db: Session = Depends(get_db)):
    """Get current AWS authentication method and configuration status"""
    return ApiService(db).get_aws_auth_method()


@router.get("/api/database/stats", dependencies=[Depends(require_admin)])
@handle_route_errors("get database stats")
def get_database_stats(db: Session = Depends(get_db)):
    """Get database statistics"""
    return ApiService(db).get_database_stats()
