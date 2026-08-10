"""
Tests for the BNK-Forge API client.

Uses respx to mock httpx requests — no real backend needed.
"""

from __future__ import annotations

import pytest
from httpx import Response

respx = pytest.importorskip("respx", reason="respx package not installed in this environment")

from bnk_forge_mcp.client import APIError, BNKForgeClient
from bnk_forge_mcp.config import MCPConfig


@pytest.fixture
def config() -> MCPConfig:
    return MCPConfig(
        api_base_url="http://test-backend:8000",
        api_token="test-token-123",
        api_username="admin",
        api_password="admin",
        api_timeout=5,
        verify_ssl=False,
    )


@pytest.fixture
def config_no_token() -> MCPConfig:
    return MCPConfig(
        api_base_url="http://test-backend:8000",
        api_token="",
        api_username="admin",
        api_password="admin",
        api_timeout=5,
        verify_ssl=False,
    )


@pytest.fixture
def client(config: MCPConfig) -> BNKForgeClient:
    return BNKForgeClient(config)


@pytest.fixture
def client_no_token(config_no_token: MCPConfig) -> BNKForgeClient:
    return BNKForgeClient(config_no_token)


# ------------------------------------------------------------------
# GET requests
# ------------------------------------------------------------------


@respx.mock
async def test_get_success(client: BNKForgeClient) -> None:
    """GET request returns parsed JSON."""
    route = respx.get("http://test-backend:8000/api/system/health").mock(
        return_value=Response(200, json={"status": "healthy"})
    )

    result = await client.get("/api/system/health")

    assert result == {"status": "healthy"}
    assert route.called


@respx.mock
async def test_get_sends_auth_header(client: BNKForgeClient) -> None:
    """GET request includes Bearer token."""
    route = respx.get("http://test-backend:8000/api/clusters").mock(
        return_value=Response(200, json=[])
    )

    await client.get("/api/clusters")

    assert route.calls[0].request.headers["Authorization"] == "Bearer test-token-123"


@respx.mock
async def test_get_with_params(client: BNKForgeClient) -> None:
    """GET request passes query parameters."""
    route = respx.get("http://test-backend:8000/api/resources").mock(
        return_value=Response(200, json=[])
    )

    await client.get("/api/resources", params={"namespace": "default", "type": "pods"})

    assert route.calls[0].request.url.params["namespace"] == "default"
    assert route.calls[0].request.url.params["type"] == "pods"


# ------------------------------------------------------------------
# POST requests
# ------------------------------------------------------------------


@respx.mock
async def test_post_success(client: BNKForgeClient) -> None:
    """POST request sends JSON body and returns parsed response."""
    route = respx.post("http://test-backend:8000/api/clusters/1/test").mock(
        return_value=Response(200, json={"connected": True})
    )

    result = await client.post("/api/clusters/1/test", json={"timeout": 10})

    assert result == {"connected": True}
    assert route.called


@respx.mock
async def test_post_204_no_content(client: BNKForgeClient) -> None:
    """POST returning 204 No Content returns status ok."""
    respx.post("http://test-backend:8000/api/something").mock(
        return_value=Response(204)
    )

    result = await client.post("/api/something")

    assert result == {"status": "ok"}


# ------------------------------------------------------------------
# Error handling
# ------------------------------------------------------------------


@respx.mock
async def test_api_error_raised(client: BNKForgeClient) -> None:
    """Non-2xx response returns structured error envelope."""
    respx.get("http://test-backend:8000/api/missing").mock(
        return_value=Response(404, json={"detail": "Not found"})
    )

    result = await client.get("/api/missing")
    assert result["ok"] is False
    assert result["error"]["status_code"] == 404
    assert "Not found" in result["error"]["detail"]


@respx.mock
async def test_api_error_plain_text(client: BNKForgeClient) -> None:
    """Non-JSON error body is still captured in envelope."""
    respx.get("http://test-backend:8000/api/broken").mock(
        return_value=Response(500, text="Internal Server Error")
    )

    result = await client.get("/api/broken")
    assert result["ok"] is False
    assert result["error"]["status_code"] == 500


# ------------------------------------------------------------------
# Auto-login
# ------------------------------------------------------------------


@respx.mock
async def test_auto_login_when_no_token(client_no_token: BNKForgeClient) -> None:
    """Client logs in automatically when no token is configured."""
    login_route = respx.post("http://test-backend:8000/api/auth/login").mock(
        return_value=Response(200, json={"token": "new-token-456", "user": {"role": "admin"}})
    )
    data_route = respx.get("http://test-backend:8000/api/clusters").mock(
        return_value=Response(200, json=[{"id": 1}])
    )

    result = await client_no_token.get("/api/clusters")

    assert login_route.called
    assert data_route.called
    # The data request should use the new token
    assert data_route.calls[0].request.headers["Authorization"] == "Bearer new-token-456"
    assert result == [{"id": 1}]


@respx.mock
async def test_auto_relogin_on_401(client: BNKForgeClient) -> None:
    """Client re-authenticates when token is expired (401)."""
    # First call returns 401 (expired token)
    data_route = respx.get("http://test-backend:8000/api/clusters")
    data_route.side_effect = [
        Response(401, json={"detail": "Token expired"}),
        Response(200, json=[{"id": 1}]),
    ]

    # Login succeeds
    respx.post("http://test-backend:8000/api/auth/login").mock(
        return_value=Response(200, json={"token": "refreshed-token"})
    )

    result = await client.get("/api/clusters")

    assert result == [{"id": 1}]


# ------------------------------------------------------------------
# Health check (no auth)
# ------------------------------------------------------------------


@respx.mock
async def test_health_check_no_auth(client: BNKForgeClient) -> None:
    """Health check does NOT send auth headers."""
    route = respx.get("http://test-backend:8000/api/system/health").mock(
        return_value=Response(200, json={"status": "healthy"})
    )

    result = await client.health_check()

    assert result == {"status": "healthy"}
    # Health check should NOT include auth header
    assert "Authorization" not in route.calls[0].request.headers


@respx.mock
async def test_get_returns_error_envelope_on_not_found(client: BNKForgeClient) -> None:
    """Client returns structured envelope for API errors."""
    respx.get("http://test-backend:8000/api/missing").mock(
        return_value=Response(404, json={"detail": "Not found"})
    )

    result = await client.get("/api/missing")

    assert result["ok"] is False
    assert result["request"] == {"method": "GET", "path": "/api/missing"}
    assert result["error"]["status_code"] == 404
    assert result["error"]["error_class"] == "not_found"
    assert result["error"]["retryable"] is False
    assert "next_action" in result["error"]


@respx.mock
async def test_put_supports_query_params(client: BNKForgeClient) -> None:
    """PUT request passes query parameters for route-aligned APIs."""
    route = respx.put("http://test-backend:8000/api/k8s/1/helm/releases/r/upgrade").mock(
        return_value=Response(200, json={"success": True})
    )

    result = await client.put(
        "/api/k8s/1/helm/releases/r/upgrade",
        json={"chart": "repo/chart"},
        params={"namespace": "kube-system"},
    )

    assert result == {"success": True}
    assert route.calls[0].request.url.params["namespace"] == "kube-system"


@respx.mock
async def test_delete_supports_query_params(client: BNKForgeClient) -> None:
    """DELETE request passes query parameters for route-aligned APIs."""
    route = respx.delete("http://test-backend:8000/api/k8s/1/helm/releases/r").mock(
        return_value=Response(200, json={"success": True})
    )

    result = await client.delete(
        "/api/k8s/1/helm/releases/r",
        params={"namespace": "default", "keep_history": "false"},
    )

    assert result == {"success": True}
    assert route.calls[0].request.url.params["namespace"] == "default"


@respx.mock
async def test_client_logs_structured_success_without_payloads(
    client: BNKForgeClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="bnk_forge_mcp.client")
    respx.get("http://test-backend:8000/api/system/health").mock(
        return_value=Response(200, json={"status": "healthy"})
    )

    await client.get("/api/system/health")

    assert "mcp_client_request method=GET path=/api/system/health success=True" in caplog.text


@respx.mock
async def test_client_logs_structured_failure_without_payloads(
    client: BNKForgeClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="bnk_forge_mcp.client")
    respx.post("http://test-backend:8000/api/secure").mock(
        return_value=Response(403, json={"detail": "forbidden", "token": "dont-log-me"})
    )

    await client.post("/api/secure", json={"password": "super-secret"})

    assert "mcp_client_request method=POST path=/api/secure success=False" in caplog.text
    assert "error_class=auth_error" in caplog.text
    assert "super-secret" not in caplog.text
    assert "dont-log-me" not in caplog.text
