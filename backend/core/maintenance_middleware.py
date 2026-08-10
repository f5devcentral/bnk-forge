"""
Middleware to block requests during maintenance mode.

Allows through:
- /api/system/health (liveness check)
- /api/system/maintenance (status check)
- /api/system/restore (the restore endpoint itself)
- /docs, /openapi.json (API docs)
"""
import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from core.maintenance import get_maintenance_status

logger = logging.getLogger(__name__)

# Paths that bypass maintenance mode
ALLOWED_PATHS = [
    "/api/system/health",
    "/api/system/maintenance",
    "/api/system/restore",
    "/docs",
    "/openapi.json",
    "/redoc",
]


class MaintenanceMiddleware(BaseHTTPMiddleware):
    """Reject requests during maintenance mode (except allowed paths)."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Check if path is allowed
        path = request.url.path
        if any(path.startswith(allowed) for allowed in ALLOWED_PATHS):
            return await call_next(request)

        # Check maintenance mode
        status = get_maintenance_status()
        if status:
            logger.debug(f"Blocked request to {path} during maintenance mode")
            return JSONResponse(
                status_code=503,
                content={
                    "detail": status.get("message", "System maintenance in progress"),
                    "maintenance_mode": True,
                    "started_at": status.get("started_at"),
                },
            )

        return await call_next(request)
