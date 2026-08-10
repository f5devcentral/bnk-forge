"""
OBS-001: Request and Job Correlation Strategy

Provides request-scoped correlation IDs that propagate across:
- API request → response (X-Request-ID header)
- API request → Celery tasks (via task headers)
- API request → audit logs (correlation_id field)
- API request → structured log lines (request_id in JSON)

Usage:
    from core.correlation import get_request_id, correlation_middleware

    # In any code running within a request context:
    request_id = get_request_id()

    # In logs (automatic via CorrelationLogFilter):
    logger.info("Processing request")  # request_id injected automatically
"""

import contextvars
import logging
import uuid
from typing import cast

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

# Context variable for request-scoped correlation ID
_request_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

# Header name — standard convention
REQUEST_ID_HEADER = "X-Request-ID"


def get_request_id() -> str | None:
    """Get the current request's correlation ID from context."""
    return _request_id_ctx.get()


def set_request_id(request_id: str) -> contextvars.Token[str | None]:
    """Set the correlation ID for the current context. Returns reset token."""
    return _request_id_ctx.set(request_id)


def generate_request_id() -> str:
    """Generate a short, unique request ID (12 hex chars)."""
    return uuid.uuid4().hex[:12]


class CorrelationMiddleware(BaseHTTPMiddleware):
    """
    Middleware that generates or accepts a correlation ID for every request.

    - If the client sends X-Request-ID, it is reused (truncated to 36 chars).
    - Otherwise, a new 12-char hex ID is generated.
    - The ID is set in contextvars for use anywhere in the request lifecycle.
    - The ID is returned in the X-Request-ID response header.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Accept client-provided ID or generate one
        incoming_id = request.headers.get(REQUEST_ID_HEADER)
        if incoming_id:
            request_id = incoming_id[:36]  # Cap length for safety
        else:
            request_id = generate_request_id()

        # Set in contextvars for downstream use
        token = set_request_id(request_id)

        # Store on request.state for middleware that can't use contextvars
        request.state.request_id = request_id

        try:
            response = cast(Response, await call_next(request))
            # Echo correlation ID in response
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            _request_id_ctx.reset(token)


class CorrelationLogFilter(logging.Filter):
    """
    Log filter that injects the current request_id into every log record.

    When added to the root logger, this ensures all JSON log lines include
    the request_id field for correlation — even from third-party libraries.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        return True


def get_celery_correlation_headers() -> dict[str, str]:
    """
    Get correlation headers to propagate to Celery tasks.

    Usage in task dispatch:
        from core.correlation import get_celery_correlation_headers
        task.apply_async(args=[...], headers=get_celery_correlation_headers())
    """
    request_id = get_request_id()
    if request_id:
        return {"request_id": request_id}
    return {}
