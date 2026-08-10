"""
Audit Middleware for BNK-Forge.

Automatically logs all mutating API calls (POST, PUT, DELETE, PATCH) with:
- Who: authenticated user (from JWT token)
- What: HTTP method, path, resource type/ID inferred from URL
- When: timestamp, duration
- Result: HTTP status code

Read-only (GET) requests are NOT logged to avoid noise.
"""
import logging
import re
import time
from typing import cast

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

logger = logging.getLogger(__name__)

# Paths to NEVER audit (high-frequency, low-value)
SKIP_PATHS = {
    "/api/system/health",
    "/api/system/performance",
    "/api/system/queue-metrics",
    "/ping",
    "/",
    "/docs",
    "/openapi.json",
    "/redoc",
}

SKIP_PREFIXES = (
    "/ws/",           # WebSocket connections
    "/api/auth/me",   # Frequent polling
    "/api/auth/login",  # Login route can raise its own errors; avoid post-response audit side effects
)

# Map URL patterns to resource types and extract IDs
# Order matters — more specific patterns first
ROUTE_PATTERNS = [
    # Auth
    (re.compile(r"^/api/auth/login$"), "auth", "login", None),
    (re.compile(r"^/api/auth/change-password$"), "auth", "change_password", None),
    (re.compile(r"^/api/auth/users/(\d+)$"), "user", None, 1),
    (re.compile(r"^/api/auth/users$"), "user", "create", None),

    # Projects
    (re.compile(r"^/api/projects/(\d+)/modules/(\d+)/(init|plan|apply|destroy|validate|refresh)"), "module", None, 2),
    (re.compile(r"^/api/projects/(\d+)/modules/(\d+)"), "module", None, 2),
    (re.compile(r"^/api/projects/(\d+)/modules"), "module", None, None),
    (re.compile(r"^/api/projects/(\d+)/deploy-all"), "project", "deploy_all", 1),
    (re.compile(r"^/api/projects/(\d+)/destroy-all"), "project", "destroy_all", 1),
    (re.compile(r"^/api/projects/(\d+)/secrets/(\d+)"), "secret", None, 2),
    (re.compile(r"^/api/projects/(\d+)/secrets"), "secret", None, None),
    (re.compile(r"^/api/projects/(\d+)"), "project", None, 1),
    (re.compile(r"^/api/projects$"), "project", "create", None),

    # Stacks
    (re.compile(r"^/api/stacks/instances/(\d+)/(deploy|destroy|retry)"), "stack", None, 1),
    (re.compile(r"^/api/stacks/instances/(\d+)"), "stack_instance", None, 1),
    (re.compile(r"^/api/stacks/instances$"), "stack_instance", "create", None),
    (re.compile(r"^/api/stacks/templates/(\d+)"), "stack_template", None, 1),
    (re.compile(r"^/api/stacks/templates$"), "stack_template", "create", None),

    # Credential templates
    (re.compile(r"^/api/credential-templates/(\d+)"), "credential_template", None, 1),
    (re.compile(r"^/api/credential-templates$"), "credential_template", "create", None),

    # K8s clusters
    (re.compile(r"^/api/k8s/clusters/(\d+)"), "k8s_cluster", None, 1),
    (re.compile(r"^/api/k8s/clusters$"), "k8s_cluster", "create", None),

    # Helm
    (re.compile(r"^/api/helm/clusters/(\d+)/releases"), "helm_release", None, None),
    (re.compile(r"^/api/helm"), "helm", None, None),

    # Module library
    (re.compile(r"^/api/module-library/sync"), "module_library", "sync", None),
    (re.compile(r"^/api/module-library/(\d+)"), "module_library", None, 1),
    (re.compile(r"^/api/module-library"), "module_library", None, None),

    # Module sources
    (re.compile(r"^/api/module-sources/(\d+)"), "module_source", None, 1),
    (re.compile(r"^/api/module-sources"), "module_source", "create", None),

    # Settings
    (re.compile(r"^/api/settings/(.+)"), "setting", None, None),
    (re.compile(r"^/api/settings$"), "setting", "update", None),
    (re.compile(r"^/api/system/defaults"), "system_defaults", "update", None),

    # System
    (re.compile(r"^/api/system/database/(cleanup|vacuum)"), "system", None, None),
    (re.compile(r"^/api/system/upgrade"), "system", "upgrade", None),
    (re.compile(r"^/api/system/containers"), "container", None, None),

    # Drift & Cost
    (re.compile(r"^/api/drift"), "drift", None, None),
    (re.compile(r"^/api/cost"), "cost", None, None),
]


def _parse_route(path: str, method: str) -> tuple[str, str | None, str | None]:
    """
    Parse a URL path to extract resource_type, action, and resource_id.

    Returns:
        (resource_type, action_override, resource_id)
    """
    for pattern, resource_type, action_override, id_group in ROUTE_PATTERNS:
        match = pattern.match(path)
        if match:
            resource_id = match.group(id_group) if id_group and id_group <= len(match.groups()) else None

            # Infer action from HTTP method if not overridden
            if action_override is None:
                # Check if the path ends with an action verb
                path_parts = path.rstrip("/").split("/")
                last_part = path_parts[-1] if path_parts else ""
                if last_part in ("init", "plan", "apply", "destroy", "validate", "refresh",
                                 "deploy", "retry", "sync", "cleanup", "vacuum", "upgrade"):
                    action_override = last_part

            return resource_type, action_override, resource_id

    # Fallback: extract from path structure
    return "api", None, None


def _infer_action(method: str, action_override: str | None) -> str:
    """Infer the audit action from HTTP method and path-based override."""
    if action_override:
        return action_override
    method_map = {
        "POST": "create",
        "PUT": "update",
        "PATCH": "update",
        "DELETE": "delete",
    }
    return method_map.get(method, method.lower())


class AuditMiddleware(BaseHTTPMiddleware):
    """
    Middleware that automatically logs mutating API calls.

    Only logs POST, PUT, DELETE, PATCH requests to /api/ paths.
    Read-only (GET) requests are skipped.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        method = request.method

        # Only audit mutating methods
        if method not in ("POST", "PUT", "DELETE", "PATCH"):
            return cast(Response, await call_next(request))

        # Skip non-API paths
        if not path.startswith("/api/"):
            return cast(Response, await call_next(request))

        # Skip noisy paths
        if path in SKIP_PATHS:
            return cast(Response, await call_next(request))
        for prefix in SKIP_PREFIXES:
            if path.startswith(prefix):
                return cast(Response, await call_next(request))

        # Time the request
        start_time = time.time()

        # Execute the request
        response = cast(Response, await call_next(request))

        duration_ms = int((time.time() - start_time) * 1000)

        # Don't audit in a blocking way — fire and forget
        try:
            _record_audit(request, response, path, method, duration_ms)
        except Exception as e:
            logger.error(f"Audit middleware error: {e}")

        return response


def _record_audit(request: Request, response: Response, path: str, method: str, duration_ms: int) -> None:
    """Record the audit log entry (runs after response is sent)."""
    from database import SessionLocal
    from services.audit_service import create_audit_log

    # Parse route
    resource_type, action_override, resource_id = _parse_route(path, method)
    action = _infer_action(method, action_override)

    # Determine status from HTTP response code
    status_code = response.status_code
    if status_code < 400:
        status = "success"
    elif status_code < 500:
        status = "failed"
    else:
        status = "error"

    # Extract user from JWT (if present)
    username = None
    user_id_val = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        try:
            from services.auth_service import decode_token
            token = auth_header.split(" ", 1)[1]
            payload = decode_token(token)
            username = payload.get("sub")
        except Exception:
            username = None

    # If this is a login attempt, extract username from a different source
    if action == "login" and not username:
        # We can't easily read the request body in middleware after it's consumed,
        # so we'll just mark it as "auth_attempt"
        username = "auth_attempt"

    # Get client IP
    ip_address: str | None = None
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        ip_address = forwarded.split(",")[0].strip()
    elif request.client:
        ip_address = request.client.host

    user_agent = request.headers.get("User-Agent", "")[:500]

    # OBS-001: Include correlation ID in audit log
    from core.correlation import get_request_id
    request_id = get_request_id()

    # Write to DB in a separate session (don't interfere with request session)
    db = SessionLocal()
    try:
        create_audit_log(
            db,
            action=action,
            resource_type=resource_type,
            user=username,
            user_id=user_id_val,
            resource_id=resource_id,
            status=status,
            ip_address=ip_address,
            user_agent=user_agent,
            http_method=method,
            http_path=path,
            http_status_code=status_code,
            duration_ms=duration_ms,
            request_id=request_id,
        )
    finally:
        db.close()
