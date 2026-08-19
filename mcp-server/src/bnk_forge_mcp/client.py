"""
HTTP client for communicating with the BNK-Forge FastAPI backend.

Handles authentication (JWT token management), request/response formatting,
and error mapping. All MCP tools delegate to this client.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from .config import MCPConfig

logger = logging.getLogger(__name__)


class APIError(Exception):
    """Raised when the BNK-Forge API returns an error."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        url: str = "",
        *,
        code: str | None = None,
        details: Any = None,
    ):
        self.status_code = status_code
        # Always a human-readable STRING -- backward compatible for every
        # consumer that reads error.detail as text. The structured pieces of a
        # backend error body ride alongside as first-class fields rather than
        # being trapped inside a str(dict) (#67).
        self.detail = detail
        self.code = code
        self.details = details
        self.url = url
        self.error_class = self._classify_error(status_code)
        self.retryable = status_code in {408, 429, 502, 503, 504}
        super().__init__(f"HTTP {status_code} from {url}: {detail}")

    @staticmethod
    def _classify_error(status_code: int) -> str:
        if status_code in {400, 422}:
            return "validation_error"
        if status_code in {401, 403}:
            return "auth_error"
        if status_code == 404:
            return "not_found"
        if status_code in {408, 429, 502, 503, 504}:
            return "transient_error"
        if status_code >= 500:
            return "server_error"
        return "request_error"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status_code": self.status_code,
            "detail": self.detail,
            # Machine-readable backend error code (e.g. PROJECT_NOT_FOUND) and
            # its structured details, parsed from the JSON body. Previously the
            # whole dict was str()'d into `detail` -- a single-quoted Python
            # repr an agent cannot JSON.parse -- so the precise failure code was
            # lost to automation (#67, D-017 structured-error contract).
            "code": self.code,
            "details": self.details,
            "error_class": self.error_class,
            "retryable": self.retryable,
            "url": self.url,
        }


class BNKForgeClient:
    """
    Async HTTP client for the BNK-Forge API.

    Manages JWT authentication, auto-login, and provides typed request methods.
    Designed to be created once at server startup and shared across all tool handlers.
    """

    def __init__(self, config: MCPConfig) -> None:
        self._config = config
        self._token: str = config.api_token
        self._http = httpx.AsyncClient(
            base_url=config.api_base_url,
            timeout=httpx.Timeout(config.api_timeout),
            verify=config.verify_ssl,
        )

    async def _ensure_auth(self) -> None:
        """Ensure we have a valid JWT token, logging in if needed."""
        if self._token:
            return

        if not self._config.has_credentials:
            raise APIError(
                401, "No token or credentials configured. Set BNK_FORGE_TOKEN or BNK_FORGE_USERNAME/PASSWORD."
            )

        logger.info("No token configured, logging in with credentials...")
        resp = await self._http.post(
            "/api/auth/login",
            json={"username": self._config.api_username, "password": self._config.api_password},
        )

        if resp.status_code != 200:
            raise APIError(resp.status_code, f"Login failed: {resp.text}", "/api/auth/login")

        data = resp.json()
        self._token = data.get("token", "")
        if not self._token:
            raise APIError(401, "Login succeeded but no token returned")

        logger.info("Successfully authenticated with BNK-Forge API")

    def _auth_headers(self) -> dict[str, str]:
        """Return authorization headers."""
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}

    @staticmethod
    def _extract_error_detail(resp: httpx.Response) -> str:
        """Extract the most helpful human-readable error message.

        Backward-compatible wrapper: callers that only want text keep getting
        text. Use _parse_error_body for the structured pieces.
        """
        return BNKForgeClient._parse_error_body(resp)["detail"]

    @staticmethod
    def _parse_error_body(resp: httpx.Response) -> dict[str, Any]:
        """Split a backend error response into {detail, code, details}.

        The backend's structured error shape (core.errors) is
        ``{code, message, details, path, request_id}`` -- sometimes nested one
        level under ``detail`` by FastAPI's HTTPException. ``detail`` here is
        always a plain string for humans; ``code``/``details`` carry the
        machine-readable parts as real values. Before this, a dict body was
        returned via str(value), i.e. a Python repr with single quotes that
        no JSON parser accepts (#67).
        """
        text = resp.text
        try:
            body = resp.json()
        except Exception:
            return {"detail": text, "code": None, "details": None}

        if not isinstance(body, dict):
            return {"detail": str(body), "code": None, "details": None}

        # FastAPI wraps HTTPException payloads as {"detail": <payload>}; the
        # payload may itself be the structured dict. Unwrap one level.
        inner = body.get("detail")
        structured = inner if isinstance(inner, dict) else body

        code = structured.get("code")
        details = structured.get("details")
        message = None
        for key in ("message", "detail", "error"):
            value = structured.get(key)
            if isinstance(value, str) and value:
                message = value
                break
        if message is None and isinstance(inner, str) and inner:
            message = inner
        if message is None:
            # No human string anywhere -- fall back to a JSON rendering, never
            # a Python repr.
            try:
                message = json.dumps(structured, sort_keys=True)
            except Exception:
                message = text

        return {
            "detail": message,
            "code": str(code) if code is not None else None,
            "details": details,
        }

    @staticmethod
    def _next_action_for(error_class: str) -> str:
        suggestions = {
            "validation_error": "Check request arguments for missing or invalid fields and retry.",
            "auth_error": "Re-authenticate and verify credentials/permissions for this operation.",
            "not_found": "Verify the target ID/name exists and that the route/path parameters are correct.",
            "transient_error": "Retry after a short delay; upstream dependency may be temporarily unavailable.",
            "server_error": "Check backend logs and health endpoints for service failures.",
            "request_error": "Review request inputs and endpoint mapping, then retry.",
        }
        return suggestions.get(error_class, "Review backend error detail and retry if safe.")

    def _error_payload(self, method: str, path: str, err: APIError) -> dict[str, Any]:
        return {
            "ok": False,
            "request": {
                "method": method,
                "path": path,
            },
            "error": {
                **err.to_dict(),
                "next_action": self._next_action_for(err.error_class),
            },
        }

    def _log_client_event(
        self,
        *,
        method: str,
        path: str,
        duration_ms: int,
        success: bool,
        error_class: str | None = None,
        status_code: int | None = None,
    ) -> None:
        """Emit bounded structured client observability without request payloads."""
        logger.info(
            "mcp_client_request method=%s path=%s success=%s duration_ms=%s error_class=%s status_code=%s",
            method,
            path,
            success,
            duration_ms,
            error_class,
            status_code,
        )

    async def _request_with_error_envelope(self, method: str, path: str, **kwargs: Any) -> Any:
        """Return API result or a structured MCP-friendly error envelope."""
        start = time.perf_counter()
        try:
            result = await self._request(method, path, **kwargs)
            duration_ms = int((time.perf_counter() - start) * 1000)
            self._log_client_event(method=method, path=path, duration_ms=duration_ms, success=True)
            return result
        except APIError as err:
            logger.warning("API request failed: %s %s -> %s", method, path, err)
            duration_ms = int((time.perf_counter() - start) * 1000)
            self._log_client_event(
                method=method,
                path=path,
                duration_ms=duration_ms,
                success=False,
                error_class=err.error_class,
                status_code=err.status_code,
            )
            return self._error_payload(method, path, err)

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """
        Make an authenticated request to the BNK-Forge API.

        Returns the parsed JSON response body.
        Raises APIError on non-2xx responses.
        """
        await self._ensure_auth()

        resp = await self._http.request(
            method,
            path,
            headers=self._auth_headers(),
            **kwargs,
        )

        # Handle token expiry — re-login once and retry
        if resp.status_code == 401 and self._config.has_credentials:
            logger.info("Token expired, re-authenticating...")
            self._token = ""
            await self._ensure_auth()
            resp = await self._http.request(
                method,
                path,
                headers=self._auth_headers(),
                **kwargs,
            )

        if resp.status_code >= 400:
            parsed = self._parse_error_body(resp)
            raise APIError(
                resp.status_code, parsed["detail"], path,
                code=parsed["code"], details=parsed["details"],
            )

        # 204 No Content
        if resp.status_code == 204:
            return {"status": "ok"}

        try:
            return resp.json()
        except Exception:
            return {"raw": resp.text}

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET request."""
        return await self._request_with_error_envelope("GET", path, params=params)

    async def post(self, path: str, json: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> Any:
        """POST request."""
        return await self._request_with_error_envelope("POST", path, json=json, params=params)

    async def put(self, path: str, json: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> Any:
        """PUT request."""
        return await self._request_with_error_envelope("PUT", path, json=json, params=params)

    async def patch(self, path: str, json: dict[str, Any] | None = None) -> Any:
        """PATCH request."""
        return await self._request_with_error_envelope("PATCH", path, json=json)

    async def delete(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """DELETE request."""
        return await self._request_with_error_envelope("DELETE", path, params=params)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Health check (useful for lifespan validation)
    # ------------------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        """Check BNK-Forge API health (does NOT require auth)."""
        resp = await self._http.get("/api/system/health")
        return resp.json()
