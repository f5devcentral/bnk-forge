"""
Centralized error handling for BNK-Forge
Provides structured error responses with error codes for better debugging
"""
import logging
from collections.abc import Callable
from enum import StrEnum
from typing import Any, NoReturn, TypeVar, cast

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

F = TypeVar("F", bound=Callable[..., Any])

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base application error with error code"""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 500,
        details: dict[str, Any] | None = None,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(self.message)


class NotFoundError(AppError):
    """Resource not found (404)"""

    def __init__(self, resource: str, identifier: Any):
        super().__init__(
            code=f"{resource.upper()}_NOT_FOUND",
            message=f"{resource.title()} not found",
            status_code=404,
            details={f"{resource}_id": str(identifier)},
        )


class ValidationError(AppError):
    """Input validation failed (422)"""

    def __init__(self, field: str, message: str):
        super().__init__(
            code="VALIDATION_ERROR",
            message=f"Validation failed: {message}",
            status_code=422,
            details={"field": field, "error": message},
        )


class UnauthorizedError(AppError):
    """Authentication required (401)"""

    def __init__(self, message: str = "Authentication required"):
        super().__init__(
            code="UNAUTHORIZED", message=message, status_code=401, details={}
        )


class ForbiddenError(AppError):
    """Insufficient permissions (403)"""

    def __init__(self, message: str = "Insufficient permissions"):
        super().__init__(
            code="FORBIDDEN", message=message, status_code=403, details={}
        )


class ConflictError(AppError):
    """Resource conflict (409)"""

    def __init__(self, resource: str, message: str, details: dict | None = None):
        # `details` is optional and merged over the resource key, so a caller can
        # return the specific conflicting objects (e.g. which modules are still
        # undestroyed) rather than only a message the client has to parse.
        #
        # Merge order: `details` wins. Passing details={"resource": ...} therefore
        # overrides the value derived from the `resource` argument -- deliberate,
        # so a caller can name the conflicting resource more precisely, but worth
        # knowing before you pass that key by accident.
        super().__init__(
            code=f"{resource.upper()}_CONFLICT",
            message=message,
            status_code=409,
            details={"resource": resource, **(details or {})},
        )


class BadRequestError(AppError):
    """Bad request (400) - general validation or business logic errors"""

    def __init__(self, message: str, code: str = "BAD_REQUEST", details: dict[str, Any] | None = None):
        super().__init__(
            code=code,
            message=message,
            status_code=400,
            details=details or {},
        )


class ReleaseNotFoundError(NotFoundError):
    """A specific Helm release does not exist (helm's "release: not found").

    Distinct from generic backend failures (API-server unreachable, auth expiry,
    helm timeout). Callers probing for a release's existence (e.g. singleton
    control-plane installs) must catch ONLY this — a transient error must NOT be
    interpreted as "release absent" or it would trigger a spurious reinstall over
    a control plane someone else may own.
    """

    def __init__(self, release_name: str):
        super().__init__("release", release_name)


class InternalError(AppError):
    """Internal server error (500) with optional custom code"""

    def __init__(self, message: str, code: str = "INTERNAL_ERROR", details: dict[str, Any] | None = None):
        super().__init__(
            code=code,
            message=message,
            status_code=500,
            details=details or {},
        )


class DecryptionError(AppError):
    """Failed to decrypt a value (500) — key mismatch, corrupted data, or wrong key file"""

    def __init__(self, message: str = "Failed to decrypt value", details: dict[str, Any] | None = None):
        super().__init__(
            code="DECRYPTION_ERROR",
            message=message,
            status_code=500,
            details=details or {},
        )


class ServiceError(AppError):
    """External service error (500) - docker, registry, k8s, etc."""

    def __init__(self, service: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(
            code=f"{service.upper()}_ERROR",
            message=message,
            status_code=500,
            details=details or {"service": service},
        )


class TimeoutError(AppError):
    """Operation timed out (500)"""

    def __init__(self, operation: str, details: dict[str, Any] | None = None):
        super().__init__(
            code="TIMEOUT",
            message=f"{operation} timed out",
            status_code=500,
            details=details or {"operation": operation},
        )


# =============================================================================
# Connectivity / reachability errors (RFC: unified connectivity architecture)
# =============================================================================

class NetworkUnreachableError(AppError):
    """Target is not reachable over the network (503).

    Carries enough context for the frontend to render a contextual offline UI
    without translating machine codes — `suggested_action` is a user-facing
    string the UI shows verbatim.
    """

    def __init__(
        self,
        target_type: str,
        target_id: int,
        target_name: str,
        suggested_action: str,
        last_success_at: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        ctx = {
            "target_type": target_type,
            "target_id": target_id,
            "target_name": target_name,
            "last_success_at": last_success_at,
            "suggested_action": suggested_action,
        }
        if details:
            ctx.update(details)
        super().__init__(
            code="NETWORK_UNREACHABLE",
            message=f"{target_type.title()} '{target_name}' is unreachable",
            status_code=503,
            details=ctx,
        )


class BreakerOpenError(NetworkUnreachableError):
    """The circuit breaker for this target is open — fail-fast (503).

    Subclass of NetworkUnreachableError so existing catch-NetworkUnreachableError
    handlers still work.
    """

    def __init__(
        self,
        target_type: str,
        target_id: int,
        target_name: str,
        suggested_action: str,
        last_success_at: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            target_type=target_type,
            target_id=target_id,
            target_name=target_name,
            suggested_action=suggested_action,
            last_success_at=last_success_at,
            details=details,
        )
        self.code = "BREAKER_OPEN"


class AuthenticationError(AppError):
    """Authentication against a remote target failed (401).

    Distinct from UnauthorizedError (which is about the forge user's session) —
    this is about credentials we use to talk to a downstream system. Auth
    failures must NOT trip the breaker.
    """

    def __init__(
        self,
        target_type: str,
        target_id: int,
        target_name: str,
        suggested_action: str,
        details: dict[str, Any] | None = None,
    ):
        ctx = {
            "target_type": target_type,
            "target_id": target_id,
            "target_name": target_name,
            "suggested_action": suggested_action,
        }
        if details:
            ctx.update(details)
        super().__init__(
            code="REMOTE_AUTHENTICATION_FAILED",
            message=f"Authentication to {target_type} '{target_name}' failed",
            status_code=401,
            details=ctx,
        )


class BackupErrorCode(StrEnum):
    """Error codes for backup/restore operations."""

    INVALID_PASSPHRASE = "invalid_passphrase"
    INCOMPATIBLE_FORMAT = "incompatible_format"
    INVALID_ARCHIVE = "invalid_archive"
    RESTORE_IN_PROGRESS = "restore_in_progress"
    BACKUP_IN_PROGRESS = "backup_in_progress"
    DUMP_FAILED = "dump_failed"
    RESTORE_FAILED = "restore_failed"
    MIGRATION_FAILED = "migration_failed"


class BackupError(AppError):
    """Error during backup/restore operations."""

    def __init__(self, code: BackupErrorCode, message: str, details: dict[str, Any] | None = None):
        # Map codes to HTTP status
        status_map: dict[BackupErrorCode, int] = {
            BackupErrorCode.INVALID_PASSPHRASE: 400,
            BackupErrorCode.INCOMPATIBLE_FORMAT: 400,
            BackupErrorCode.INVALID_ARCHIVE: 400,
            BackupErrorCode.RESTORE_IN_PROGRESS: 409,
            BackupErrorCode.BACKUP_IN_PROGRESS: 409,
            BackupErrorCode.DUMP_FAILED: 500,
            BackupErrorCode.RESTORE_FAILED: 500,
            BackupErrorCode.MIGRATION_FAILED: 500,
        }
        super().__init__(
            message=message,
            status_code=status_map.get(code, 500),
            code=code.value,
            details=details,
        )


def scrub_secrets(text: str) -> str:
    """
    SEC-GOV-003: Scrub known secret patterns from error messages before
    including them in API responses. Prevents accidental credential leaks.
    """
    import re
    # AWS Access Key IDs (AKIA...)
    text = re.sub(r'AKIA[A-Z0-9]{16}', 'AKIA***REDACTED***', text)
    # AWS Secret Access Keys (40-char base64)
    text = re.sub(r'(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])', '***SECRET_REDACTED***', text)
    # Bearer tokens
    text = re.sub(r'Bearer\s+[A-Za-z0-9._-]{20,}', 'Bearer ***REDACTED***', text)
    # Private key blocks
    text = re.sub(r'-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----',
                  '***PRIVATE_KEY_REDACTED***', text)
    # Generic password= or token= in query strings or config
    text = re.sub(r'(password|token|secret|api_key|apikey|access_key)([=:]\s*)[^\s,;&"\']+',
                  r'\1\2***REDACTED***', text, flags=re.IGNORECASE)
    return text


def format_error_response(error: Exception, request: Any = None) -> dict[str, Any]:
    """Format error as structured JSON response

    Args:
        error: Either an AppError instance or a generic Exception
        request: Request object or string describing the operation
    """
    # OBS-001: Include correlation ID in error responses for support tracing
    from core.correlation import get_request_id
    request_id = get_request_id()

    # Handle AppError instances
    if isinstance(error, AppError):
        # SEC-GOV-003: Scrub secrets from error messages before sending to client
        resp: dict[str, Any] = {
            "error": {
                "code": error.code,
                "message": scrub_secrets(error.message),
                "details": {k: scrub_secrets(str(v)) if isinstance(v, str) else v
                            for k, v in error.details.items()},
                "path": str(request.url.path) if hasattr(request, 'url') else str(request),
            }
        }
        if request_id:
            resp["error"]["request_id"] = request_id
        return resp

    # Handle generic exceptions
    resp = {
        "error": {
            "code": "INTERNAL_ERROR",
            "message": scrub_secrets(str(error)),
            "details": {"error_type": type(error).__name__},
            "path": str(request.url.path) if hasattr(request, 'url') else str(request),
        }
    }
    if request_id:
        resp["error"]["request_id"] = request_id
    return resp


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Global exception handler for AppError"""
    logger.error(
        f"AppError: {exc.code} - {exc.message}",
        extra={"code": exc.code, "details": exc.details, "path": request.url.path},
    )

    return JSONResponse(
        status_code=exc.status_code, content=format_error_response(exc, request)
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Handler for FastAPI HTTPException (converts to structured format)"""
    error = AppError(
        code="HTTP_ERROR",
        message=exc.detail if isinstance(exc.detail, str) else str(exc.detail),
        status_code=exc.status_code,
        details={},
    )

    return JSONResponse(
        status_code=exc.status_code, content=format_error_response(error, request)
    )


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handler for unexpected exceptions"""
    logger.exception(f"Unhandled exception: {exc}", extra={"path": request.url.path})

    error = AppError(
        code="INTERNAL_SERVER_ERROR",
        message="An unexpected error occurred",
        status_code=500,
        details={"error_type": type(exc).__name__} if logger.level == logging.DEBUG else {},
    )

    return JSONResponse(status_code=500, content=format_error_response(error, request))


# Helper function for common pattern: get entity or raise 404
def get_or_404(db: Any, model: Any, **filters: Any) -> Any:
    """
    Query for an entity, raise NotFoundError if not found

    Usage:
        project = get_or_404(db, Project, id=project_id)
    """
    obj = db.query(model).filter_by(**filters).first()
    if not obj:
        # Get first filter key as resource name
        resource = model.__tablename__.rstrip("s")  # Remove trailing 's'
        identifier = list(filters.values())[0]
        raise NotFoundError(resource, identifier)
    return obj


def raise_not_found(resource: str, identifier: Any) -> None:
    """
    Raise NotFoundError for a resource.

    Usage:
        if not project:
            raise_not_found("project", project_id)
    """
    raise NotFoundError(resource, identifier)


def raise_bad_request(message: str, code: str = "BAD_REQUEST", details: dict[str, Any] | None = None) -> None:
    """
    Raise BadRequestError with a message.

    Usage:
        raise_bad_request("Invalid configuration provided")
    """
    raise BadRequestError(message, code, details)


def raise_internal_error(message: str, code: str = "INTERNAL_ERROR", details: dict[str, Any] | None = None) -> None:
    """
    Raise InternalError with a message.

    Usage:
        raise_internal_error("Failed to process request")
    """
    raise InternalError(message, code, details)


def raise_service_error(service: str, error: Exception) -> None:
    """
    Raise ServiceError from an exception.

    Usage:
        except DockerException as e:
            raise_service_error("docker", e)
    """
    raise ServiceError(service, str(error), {"error_type": type(error).__name__})


# =============================================================================
# Route Error Handler Decorator (R4-003)
# =============================================================================

def handle_route_errors(operation: str) -> Callable[[F], F]:
    """
    Decorator that replaces the blanket except-Exception pattern in routes.

    Instead of:
        try:
            ...
        except AppError:
            raise
        except Exception as e:
            raise InternalError(f"Failed to do X: {str(e)}")

    Use:
        @handle_route_errors("create project")
        def create_project(...):
            ...

    Behavior:
      - AppError subclasses propagate unchanged (global handler catches them)
      - IntegrityError → ConflictError (409)
      - ValueError → BadRequestError (400)
      - FileNotFoundError → NotFoundError (404)
      - json.JSONDecodeError → BadRequestError (400)
      - All other exceptions → InternalError (500) WITH logger.exception()
        so the full traceback is preserved in logs
    """
    import functools
    import inspect

    def decorator(fn: F) -> F:
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await fn(*args, **kwargs)
                except AppError:
                    raise
                except Exception as e:
                    handle_exception(e, operation)
            return cast(F, async_wrapper)
        else:
            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return fn(*args, **kwargs)
                except AppError:
                    raise
                except Exception as e:
                    handle_exception(e, operation)
            return cast(F, wrapper)
    return decorator


def handle_service_errors(operation: str) -> Callable[[F], F]:
    """
    Decorator for service-layer methods. Same exception mapping as
    handle_route_errors but intended for service classes.

    Usage:
        class HelmService:
            @handle_service_errors("install Helm chart")
            def install(self, ...):
                ...

    Behavior identical to handle_route_errors — AppError subclasses propagate
    unchanged, known exception types are mapped to typed AppErrors, and
    unexpected exceptions get full traceback logging.
    """
    import functools
    import inspect

    def decorator(fn: F) -> F:
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await fn(*args, **kwargs)
                except AppError:
                    raise
                except Exception as e:
                    handle_exception(e, operation)
            return cast(F, async_wrapper)
        else:
            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return fn(*args, **kwargs)
                except AppError:
                    raise
                except Exception as e:
                    handle_exception(e, operation)
            return cast(F, wrapper)
    return decorator


# AWS error codes that unambiguously indicate an expired/invalid credential token.
# Kept conservative: plain IAM resource-level AccessDenied is NOT included here
# because it does not mean "your token is expired", just "you lack permission".
_AWS_EXPIRY_CODES: frozenset[str] = frozenset(
    {
        "ExpiredToken",
        "ExpiredTokenException",
        "RequestExpired",
        "InvalidClientTokenId",
        "UnrecognizedClientException",
        "UnauthorizedSSOTokenError",
        "TokenRefreshRequired",
        "AuthFailure",
    }
)

# Substrings in subprocess stderr that signal AWS credential expiry.
_AWS_EXPIRY_STDERR_MARKERS: tuple[str, ...] = (
    "ExpiredToken",
    "InvalidClientTokenId",
    "UnrecognizedClientException",
    "Token has expired",
    "security token included in the request is invalid",
    "security token has expired",
    "UnauthorizedSSOTokenError",
    "sso session",
    "SSO token",
)


def classify_aws_credential_error(e: Exception) -> "AuthenticationError | None":
    """Return an AuthenticationError if *e* is a botocore ClientError for an expired/invalid
    credential, or None if it is a non-expiry ClientError that should fall through."""
    try:
        response = getattr(e, "response", None) or {}
        code = response.get("Error", {}).get("Code", "") if isinstance(response, dict) else ""
        message = response.get("Error", {}).get("Message", str(e)) if isinstance(response, dict) else str(e)
    except Exception:
        return None

    if code not in _AWS_EXPIRY_CODES:
        # Check for AccessDeniedException ONLY when the message clearly refers to an SSO/STS token
        if code == "AccessDeniedException":
            msg_lower = message.lower()
            sso_token_expired = (
                "token" in msg_lower and (
                    "expired" in msg_lower or "invalid" in msg_lower or "sso" in msg_lower
                )
            )
            if not sso_token_expired:
                return None
        else:
            return None

    return AuthenticationError(
        target_type="aws",
        target_id=0,
        target_name="AWS",
        suggested_action=(
            "Refresh your AWS credentials in Project Settings → Credential Templates, then retry."
        ),
        details={"aws_error_code": code, "aws_error_message": message},
    )


def classify_aws_subprocess_stderr(stderr: str, operation: str) -> "AuthenticationError | None":
    """Return an AuthenticationError if *stderr* from a subprocess contains AWS expiry markers."""
    for marker in _AWS_EXPIRY_STDERR_MARKERS:
        if marker.lower() in stderr.lower():
            return AuthenticationError(
                target_type="aws",
                target_id=0,
                target_name="AWS",
                suggested_action=(
                    "Refresh your AWS credentials in Project Settings → Credential Templates, then retry."
                ),
                details={"aws_error": f"AWS CLI credential error during {operation}", "stderr_hint": marker},
            )
    return None


def handle_exception(e: Exception, operation: str) -> NoReturn:
    """
    Map known exception types to appropriate AppError subclasses.
    Unknown exceptions get logged with full traceback and re-raised as InternalError.
    """
    import json

    # SQLAlchemy IntegrityError (duplicate key, FK violation)
    try:
        from sqlalchemy.exc import IntegrityError, OperationalError
        if isinstance(e, IntegrityError):
            detail = str(e.orig) if hasattr(e, 'orig') else str(e)
            raise ConflictError("resource", f"Database constraint violation: {detail}")
        if isinstance(e, OperationalError):
            logger.exception(f"Database operational error during {operation}")
            raise InternalError(f"Database error during {operation}: {type(e).__name__}")
    except (ImportError, AppError):
        if isinstance(e, AppError):
            raise
        pass

    # ValueError → BadRequestError
    if isinstance(e, ValueError):
        raise BadRequestError(str(e))

    # RuntimeError → ServiceError (e.g., Helm/Docker command failures)
    if isinstance(e, RuntimeError):
        raise ServiceError("runtime", str(e))

    # FileNotFoundError → NotFoundError
    if isinstance(e, FileNotFoundError):
        raise NotFoundError("file", str(e))

    # json.JSONDecodeError → BadRequestError
    if isinstance(e, json.JSONDecodeError):
        raise BadRequestError(f"Invalid JSON: {str(e)}")

    # botocore ClientError (AWS credential expiry, invalid token, etc.)
    # Import-guarded so non-AWS installations are not affected.
    _boto_classified: AuthenticationError | None = None
    try:
        from botocore.exceptions import ClientError as BotoCoreClientError
        if isinstance(e, BotoCoreClientError):
            _boto_classified = classify_aws_credential_error(e)
    except ImportError:
        pass
    if _boto_classified is not None:
        raise _boto_classified
    # If e was a BotoCoreClientError but NOT expiry-classified, fall through to
    # generic InternalError (do not classify Throttling/etc. as auth failures).

    # CredentialUnavailableError (SSO credentials absent or expired — raised by
    # credentials_service.get_cloud_credentials_env with strict=True, and by the
    # EKS token generator in services/kubernetes/_base.py)
    # Match by class name to avoid a core→services import that breaks mypy strict.
    if any(t.__name__ == "CredentialUnavailableError" for t in type(e).__mro__):
        raise AuthenticationError(
            target_type="aws",
            target_id=0,
            target_name="AWS",
            suggested_action=(
                "Refresh your AWS credentials in Project Settings → Credential Templates, then retry."
            ),
            details={"aws_error": str(e)},
        )

    # subprocess.CalledProcessError
    try:
        import subprocess
        if isinstance(e, subprocess.CalledProcessError):
            stderr = (e.stderr or "")[:500]
            aws_auth = classify_aws_subprocess_stderr(stderr, operation)
            if aws_auth is not None:
                raise aws_auth
            raise InternalError(
                f"Command failed during {operation}: exit code {e.returncode}",
                details={"stderr": stderr},
            )
    except ImportError:
        pass

    # K8s API exceptions
    try:
        from kubernetes.client.exceptions import ApiException
        if isinstance(e, ApiException):
            if e.status == 404:
                raise NotFoundError("kubernetes resource", str(e.reason))
            elif e.status == 409:
                raise ConflictError("kubernetes resource", str(e.reason))
            elif e.status == 422:
                raise BadRequestError(f"Kubernetes validation error: {e.reason}")
            else:
                raise ServiceError("kubernetes", f"K8s API error ({e.status}): {e.reason}")
    except (ImportError, AppError):
        if isinstance(e, AppError):
            raise
        pass

    # requests connection errors
    try:
        from requests.exceptions import ConnectionError as RequestsConnectionError
        if isinstance(e, RequestsConnectionError):
            raise ServiceError("http", f"Connection failed: {str(e)}")
    except (ImportError, AppError):
        if isinstance(e, AppError):
            raise
        pass

    # Fallback: log FULL traceback and raise generic InternalError
    logger.exception(f"Unexpected error during {operation}: {type(e).__name__}: {e}")
    raise InternalError(f"Unexpected error during {operation}: {type(e).__name__}")
