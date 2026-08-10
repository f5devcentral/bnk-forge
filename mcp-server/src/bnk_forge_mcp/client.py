"""
HTTP client for communicating with the BNK-Forge FastAPI backend.

Handles authentication (JWT token management), request/response formatting,
and error mapping. All MCP tools delegate to this client.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from .config import MCPConfig

logger = logging.getLogger(__name__)


class APIError(Exception):
    """Raised when the BNK-Forge API returns an error."""

    def __init__(self, status_code: int, detail: str, url: str = ""):
        self.status_code = status_code
        self.detail = detail
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
        """Extract the most helpful error message from an API response."""
        detail = resp.text
        try:
            body = resp.json()
        except Exception:
            return detail

        if isinstance(body, dict):
            for key in ("detail", "message", "error"):
                value = body.get(key)
                if value:
                    return str(value)
            return str(body)

        return str(body)

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
            raise APIError(resp.status_code, self._extract_error_detail(resp), path)

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
