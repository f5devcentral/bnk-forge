"""
BU-001: Unit tests for core.errors module.

Tests all error classes, format_error_response, helper functions,
and the handle_route_errors / handle_service_errors decorators.
"""

import json
import subprocess

import pytest

from core.errors import (
    AppError,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    DecryptionError,
    ForbiddenError,
    InternalError,
    NotFoundError,
    ServiceError,
    TimeoutError,
    UnauthorizedError,
    ValidationError,
    classify_aws_credential_error,
    classify_aws_subprocess_stderr,
    format_error_response,
    handle_exception,
    handle_route_errors,
    handle_service_errors,
    raise_bad_request,
    raise_internal_error,
    raise_not_found,
    raise_service_error,
)

# ── Error class constructors ──────────────────────────────────────────


class TestAppError:
    def test_default_status_code(self):
        err = AppError(code="TEST", message="test error")
        assert err.code == "TEST"
        assert err.message == "test error"
        assert err.status_code == 500
        assert err.details == {}

    def test_custom_status_and_details(self):
        err = AppError(code="CUSTOM", message="custom", status_code=418, details={"tea": True})
        assert err.status_code == 418
        assert err.details == {"tea": True}

    def test_is_exception(self):
        err = AppError(code="X", message="boom")
        assert isinstance(err, Exception)
        assert str(err) == "boom"


class TestNotFoundError:
    def test_fields(self):
        err = NotFoundError("project", 42)
        assert err.code == "PROJECT_NOT_FOUND"
        assert err.status_code == 404
        assert err.message == "Project not found"
        assert err.details == {"project_id": "42"}


class TestValidationError:
    def test_fields(self):
        err = ValidationError("email", "invalid format")
        assert err.code == "VALIDATION_ERROR"
        assert err.status_code == 422
        assert "invalid format" in err.message
        assert err.details["field"] == "email"


class TestUnauthorizedError:
    def test_defaults(self):
        err = UnauthorizedError()
        assert err.status_code == 401
        assert err.code == "UNAUTHORIZED"

    def test_custom_message(self):
        err = UnauthorizedError("Token expired")
        assert err.message == "Token expired"


class TestForbiddenError:
    def test_defaults(self):
        err = ForbiddenError()
        assert err.status_code == 403
        assert err.code == "FORBIDDEN"


class TestConflictError:
    def test_fields(self):
        err = ConflictError("user", "Username already exists")
        assert err.code == "USER_CONFLICT"
        assert err.status_code == 409
        assert err.details["resource"] == "user"


class TestBadRequestError:
    def test_defaults(self):
        err = BadRequestError("invalid input")
        assert err.code == "BAD_REQUEST"
        assert err.status_code == 400

    def test_custom_code(self):
        err = BadRequestError("oops", code="CUSTOM_BAD")
        assert err.code == "CUSTOM_BAD"


class TestInternalError:
    def test_defaults(self):
        err = InternalError("something broke")
        assert err.code == "INTERNAL_ERROR"
        assert err.status_code == 500


class TestDecryptionError:
    def test_defaults(self):
        err = DecryptionError()
        assert err.code == "DECRYPTION_ERROR"
        assert err.status_code == 500

    def test_custom_message(self):
        err = DecryptionError("bad key", details={"key": "x"})
        assert err.message == "bad key"
        assert err.details["key"] == "x"


class TestServiceError:
    def test_fields(self):
        err = ServiceError("docker", "container failed")
        assert err.code == "DOCKER_ERROR"
        assert err.status_code == 500


class TestTimeoutError:
    def test_fields(self):
        err = TimeoutError("helm install")
        assert err.code == "TIMEOUT"
        assert "helm install" in err.message
        assert err.details["operation"] == "helm install"


# ── format_error_response ─────────────────────────────────────────────


class TestFormatErrorResponse:
    def test_app_error_format(self):
        err = NotFoundError("project", 5)
        result = format_error_response(err, "/api/projects/5")
        assert result["error"]["code"] == "PROJECT_NOT_FOUND"
        assert result["error"]["message"] == "Project not found"
        assert result["error"]["path"] == "/api/projects/5"

    def test_generic_exception_format(self):
        err = RuntimeError("unexpected")
        result = format_error_response(err, "/api/test")
        assert result["error"]["code"] == "INTERNAL_ERROR"
        assert result["error"]["message"] == "unexpected"
        assert result["error"]["details"]["error_type"] == "RuntimeError"


# ── Helper functions ──────────────────────────────────────────────────


class TestRaiseHelpers:
    def test_raise_not_found(self):
        with pytest.raises(NotFoundError) as exc_info:
            raise_not_found("cluster", "abc-123")
        assert exc_info.value.status_code == 404

    def test_raise_bad_request(self):
        with pytest.raises(BadRequestError) as exc_info:
            raise_bad_request("bad input")
        assert exc_info.value.status_code == 400

    def test_raise_internal_error(self):
        with pytest.raises(InternalError):
            raise_internal_error("boom")

    def test_raise_service_error(self):
        with pytest.raises(ServiceError) as exc_info:
            raise_service_error("redis", RuntimeError("connection refused"))
        assert exc_info.value.code == "REDIS_ERROR"


# ── handle_exception mapping ─────────────────────────────────────────


class TestHandleException:
    def test_value_error_maps_to_bad_request(self):
        with pytest.raises(BadRequestError):
            handle_exception(ValueError("bad"), "test op")

    def test_runtime_error_maps_to_service_error(self):
        with pytest.raises(ServiceError):
            handle_exception(RuntimeError("fail"), "test op")

    def test_file_not_found_maps_to_not_found(self):
        with pytest.raises(NotFoundError):
            handle_exception(FileNotFoundError("missing.txt"), "test op")

    def test_json_decode_error_maps_to_bad_request(self):
        """JSONDecodeError is a subclass of ValueError, so it's caught as BadRequestError."""
        err = json.JSONDecodeError("bad json", "", 0)
        with pytest.raises(BadRequestError):
            handle_exception(err, "test op")

    def test_unknown_exception_maps_to_internal(self):
        with pytest.raises(InternalError) as exc_info:
            handle_exception(OSError("disk full"), "test op")
        assert "OSError" in exc_info.value.message


# ── Decorators ────────────────────────────────────────────────────────


class TestHandleRouteErrors:
    def test_sync_function_passes_through(self):
        @handle_route_errors("test")
        def good_fn():
            return "ok"

        assert good_fn() == "ok"

    def test_sync_function_maps_value_error(self):
        @handle_route_errors("test")
        def bad_fn():
            raise ValueError("nope")

        with pytest.raises(BadRequestError):
            bad_fn()

    def test_app_error_propagates_unchanged(self):
        @handle_route_errors("test")
        def fn():
            raise NotFoundError("thing", 1)

        with pytest.raises(NotFoundError):
            fn()

    async def test_async_function_passes_through(self):
        @handle_route_errors("test")
        async def good_fn():
            return "async ok"

        assert await good_fn() == "async ok"

    async def test_async_function_maps_errors(self):
        @handle_route_errors("test")
        async def bad_fn():
            raise ValueError("async nope")

        with pytest.raises(BadRequestError):
            await bad_fn()


class TestHandleServiceErrors:
    def test_sync_maps_errors(self):
        @handle_service_errors("helm install")
        def fn():
            raise RuntimeError("command failed")

        with pytest.raises(ServiceError):
            fn()

    def test_app_error_propagates(self):
        @handle_service_errors("helm install")
        def fn():
            raise ForbiddenError("no access")

        with pytest.raises(ForbiddenError):
            fn()


# ── AWS credential error classification ──────────────────────────────


def _make_client_error(code: str, message: str = "test", operation: str = "GetCallerIdentity"):
    """Construct a botocore ClientError the way botocore itself does."""
    try:
        from botocore.exceptions import ClientError
        return ClientError({"Error": {"Code": code, "Message": message}}, operation)
    except ImportError:
        pytest.skip("botocore not installed")


class TestClassifyAwsCredentialError:
    def test_expired_token_returns_authentication_error(self):
        err = _make_client_error("ExpiredToken")
        result = classify_aws_credential_error(err)
        assert result is not None
        assert isinstance(result, AuthenticationError)
        assert result.status_code == 401
        assert result.code == "REMOTE_AUTHENTICATION_FAILED"
        assert result.details["aws_error_code"] == "ExpiredToken"

    def test_expired_token_exception_returns_authentication_error(self):
        err = _make_client_error("ExpiredTokenException", "The security token included in the request is expired")
        result = classify_aws_credential_error(err)
        assert result is not None
        assert result.code == "REMOTE_AUTHENTICATION_FAILED"

    def test_invalid_client_token_returns_authentication_error(self):
        err = _make_client_error("InvalidClientTokenId", "The security token included in the request is invalid")
        result = classify_aws_credential_error(err)
        assert result is not None
        assert isinstance(result, AuthenticationError)
        assert result.details["aws_error_code"] == "InvalidClientTokenId"

    def test_unrecognized_client_returns_authentication_error(self):
        err = _make_client_error("UnrecognizedClientException")
        result = classify_aws_credential_error(err)
        assert result is not None
        assert result.code == "REMOTE_AUTHENTICATION_FAILED"

    def test_non_expiry_throttling_returns_none(self):
        err = _make_client_error("Throttling", "Rate exceeded")
        result = classify_aws_credential_error(err)
        assert result is None

    def test_plain_access_denied_returns_none(self):
        """Generic resource-level AccessDenied must NOT be classified as credential expiry."""
        err = _make_client_error("AccessDeniedException", "User is not authorized to perform: s3:GetObject")
        result = classify_aws_credential_error(err)
        assert result is None

    def test_sso_token_expired_access_denied_returns_authentication_error(self):
        """AccessDeniedException with SSO token message IS a credential error."""
        err = _make_client_error(
            "AccessDeniedException",
            "The SSO token has expired. Please re-authenticate.",
        )
        result = classify_aws_credential_error(err)
        assert result is not None
        assert result.code == "REMOTE_AUTHENTICATION_FAILED"

    def test_no_response_attribute_returns_none(self):
        """Plain exception without response dict must not crash."""
        result = classify_aws_credential_error(RuntimeError("boom"))
        assert result is None


class TestClassifyAwsSubprocessStderr:
    def test_expired_token_in_stderr(self):
        result = classify_aws_subprocess_stderr("An error occurred (ExpiredToken) when calling GetCallerIdentity", "EKS kubeconfig")
        assert result is not None
        assert isinstance(result, AuthenticationError)
        assert result.code == "REMOTE_AUTHENTICATION_FAILED"

    def test_invalid_client_token_in_stderr(self):
        result = classify_aws_subprocess_stderr("error: InvalidClientTokenId", "EKS kubeconfig")
        assert result is not None
        assert result.code == "REMOTE_AUTHENTICATION_FAILED"

    def test_security_token_invalid_in_stderr(self):
        result = classify_aws_subprocess_stderr(
            "The security token included in the request is invalid.", "EKS kubeconfig"
        )
        assert result is not None
        assert result.code == "REMOTE_AUTHENTICATION_FAILED"

    def test_clean_stderr_returns_none(self):
        result = classify_aws_subprocess_stderr("Successfully updated kubeconfig", "EKS kubeconfig")
        assert result is None

    def test_empty_stderr_returns_none(self):
        result = classify_aws_subprocess_stderr("", "EKS kubeconfig")
        assert result is None


class TestHandleExceptionAwsBotocore:
    def test_expired_token_maps_to_authentication_error_via_handle_exception(self):
        err = _make_client_error("ExpiredToken", "Token expired")
        with pytest.raises(AuthenticationError) as exc_info:
            handle_exception(err, "list EKS clusters")
        exc = exc_info.value
        assert exc.status_code == 401
        assert exc.code == "REMOTE_AUTHENTICATION_FAILED"
        assert "Credential Templates" in exc.details.get("suggested_action", "")

    def test_invalid_client_token_maps_to_authentication_error(self):
        err = _make_client_error("InvalidClientTokenId", "The security token is invalid")
        with pytest.raises(AuthenticationError):
            handle_exception(err, "describe cluster")

    def test_throttling_does_not_map_to_authentication_error(self):
        """Non-expiry ClientErrors must NOT become AuthenticationError."""
        err = _make_client_error("Throttling", "Rate exceeded")
        with pytest.raises(InternalError):
            handle_exception(err, "list resources")

    def test_credential_unavailable_error_maps_to_authentication_error(self):
        """CredentialUnavailableError from credentials_service must classify as AuthenticationError."""
        try:
            from services.credentials_service import CredentialUnavailableError
        except ImportError:
            pytest.skip("credentials_service not importable in this context")

        err = CredentialUnavailableError(
            "SSO credentials for template 'prod-aws' have expired.", template_name="prod-aws"
        )
        with pytest.raises(AuthenticationError) as exc_info:
            handle_exception(err, "get EKS token")
        exc = exc_info.value
        assert exc.status_code == 401
        assert exc.code == "REMOTE_AUTHENTICATION_FAILED"

    def test_credential_unavailable_subclass_maps_to_authentication_error(self):
        """Subclass of CredentialUnavailableError is also caught by the name-match (MRO walk)."""
        try:
            from services.credentials_service import CredentialUnavailableError
        except ImportError:
            pytest.skip("credentials_service not importable in this context")

        class SpecificCredentialUnavailableError(CredentialUnavailableError):
            pass

        err = SpecificCredentialUnavailableError("role-chained token expired.", template_name="cross-account")
        with pytest.raises(AuthenticationError) as exc_info:
            handle_exception(err, "assume role")
        exc = exc_info.value
        assert exc.status_code == 401
        assert exc.code == "REMOTE_AUTHENTICATION_FAILED"

    def test_credential_unavailable_name_match_works_without_import(self):
        """Name-match approach catches any exception named CredentialUnavailableError, even from a stub."""

        class CredentialUnavailableError(Exception):
            pass

        err = CredentialUnavailableError("stub unavailable")
        with pytest.raises(AuthenticationError) as exc_info:
            handle_exception(err, "stub operation")
        exc = exc_info.value
        assert exc.status_code == 401
        assert exc.code == "REMOTE_AUTHENTICATION_FAILED"

    def test_subprocess_expired_token_in_stderr_maps_to_authentication_error(self):
        """CalledProcessError with AWS expiry markers in stderr → AuthenticationError."""
        err = subprocess.CalledProcessError(
            returncode=255,
            cmd=["aws", "eks", "update-kubeconfig"],
            stderr="An error occurred (ExpiredToken) when calling GetCallerIdentity",
        )
        with pytest.raises(AuthenticationError) as exc_info:
            handle_exception(err, "EKS kubeconfig generation")
        exc = exc_info.value
        assert exc.status_code == 401
        assert exc.code == "REMOTE_AUTHENTICATION_FAILED"

    def test_subprocess_clean_stderr_maps_to_internal_error(self):
        """CalledProcessError without AWS markers → InternalError as before."""
        err = subprocess.CalledProcessError(
            returncode=1,
            cmd=["helm", "install"],
            stderr="Error: cannot re-use a name that is still in use",
        )
        with pytest.raises(InternalError):
            handle_exception(err, "helm install")
