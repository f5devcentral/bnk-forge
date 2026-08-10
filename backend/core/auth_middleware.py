"""
Authentication middleware for BNK-Forge.
Protects all API routes except auth endpoints, health checks, and WebSocket.
Can be disabled via REQUIRE_AUTH=false for backward compatibility.
"""
import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from jose import JWTError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from core.config import settings
from core.errors import UnauthorizedError

logger = logging.getLogger(__name__)

# Long-lived API tokens (CLI / CI-CD) carry this prefix; they are verified
# against the api_tokens table rather than decoded as a JWT.
API_TOKEN_PREFIX = "bnk_"


def _verify_api_token(token: str) -> None:
    """Raise UnauthorizedError unless ``token`` is a live API token.

    Opens its own session: middleware runs outside FastAPI's dependency
    injection, so ``get_db`` is not available here.
    """
    from database import SessionLocal
    from services.api_token_service import ApiTokenService

    db = SessionLocal()
    try:
        ApiTokenService(db).verify(token)  # raises UnauthorizedError if invalid
        db.commit()  # verify() stamps last_used_at
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# Paths that never require authentication
PUBLIC_PATHS = {
    "/api/auth/login",
    "/ping",
    "/",
    "/docs",
    "/openapi.json",
    "/redoc",
}

# Path prefixes that never require authentication
# (these paths have their own token-based auth, not JWT)
PUBLIC_PREFIXES = (
    "/api/auth/login",
    "/api/system/health",  # Docker healthcheck + proxy healthcheck (no token available)
    "/ws/",        # WebSocket connections (have their own auth via registration token)
    "/api/operators/install/",  # Operator install YAML (auth via token in URL)
    "/api/operators/register-poll",  # Polling operator registration (uses registration token)
)

# Operator polling paths — authenticated via registration token, not JWT
# These are matched by pattern: /api/operators/{id}/poll, /results, /heartbeat
OPERATOR_POLLING_PATTERNS = (
    "/poll",
    "/results/",
    "/heartbeat",
)

# Benchmark agent endpoints — governed by BENCHMARK_AGENT_AUTH_REQUIRED (via
# _require_agent_bearer in routes/benchmarks.py), NOT user JWT.  Mirrors the
# operator registration-token exemption above.
# Only the write/ingest paths are exempt; GET/DELETE agent routes keep JWT.
AGENT_PUBLIC_ENDPOINTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/benchmarks/agents"),
        ("POST", "/api/benchmarks/results"),
        ("POST", "/api/benchmarks/results/aiperf"),
    }
)


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware that enforces JWT authentication on all API routes.

    Behavior:
    - If REQUIRE_AUTH=false: all requests pass through (legacy mode)
    - If REQUIRE_AUTH=true: requests must have valid Bearer token
    - Public paths (login, health, docs) are always exempt
    - OPTIONS requests (CORS preflight) are always exempt
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Skip auth if disabled
        if not settings.REQUIRE_AUTH:
            return await call_next(request)

        # Skip CORS preflight
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path

        # Skip public paths
        if path in PUBLIC_PATHS:
            return await call_next(request)

        # Skip public prefixes
        for prefix in PUBLIC_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        # Skip operator polling paths (they use registration token auth, not JWT)
        # Pattern: /api/operators/{operator_id}/poll, /results/*, /heartbeat
        if path.startswith("/api/operators/") and any(
            pattern in path for pattern in OPERATOR_POLLING_PATTERNS
        ):
            return await call_next(request)

        # Skip benchmark agent registration/ingest — governed by BENCHMARK_AGENT_AUTH_REQUIRED
        if (request.method, path) in AGENT_PUBLIC_ENDPOINTS:
            return await call_next(request)

        # Skip non-API paths (static files, etc.)
        if not path.startswith("/api/"):
            return await call_next(request)

        # Check for Authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "code": "UNAUTHORIZED",
                        "message": "Authentication required. Provide a Bearer token.",
                        "details": {},
                        "path": path,
                    }
                },
            )

        # Validate the token
        token = auth_header.split(" ", 1)[1]
        try:
            if token.startswith(API_TOKEN_PREFIX):
                # Long-lived CLI / CI-CD token — a DB-backed hash, not a JWT, so
                # decode_token() would reject it. 21 /api routes have no auth
                # dependency of their own and rely on this middleware alone, so
                # it must fully verify the token here, not defer to the route.
                _verify_api_token(token)
            else:
                from services.auth_service import decode_token
                decode_token(token)  # Will raise UnauthorizedError if invalid
        except (JWTError, UnauthorizedError):
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "code": "UNAUTHORIZED",
                        "message": "Invalid or expired token. Please login again.",
                        "details": {},
                        "path": path,
                    }
                },
            )
        except Exception:
            logger.exception(f"Unexpected error validating token for {path}")
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "INTERNAL_ERROR",
                        "message": "Authentication service error. Please try again.",
                        "details": {},
                        "path": path,
                    }
                },
            )

        return await call_next(request)
