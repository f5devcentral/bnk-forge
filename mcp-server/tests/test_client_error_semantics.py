"""Focused tests for MCP client error semantics."""

from __future__ import annotations

from bnk_forge_mcp.client import APIError, BNKForgeClient
from bnk_forge_mcp.config import MCPConfig


def _client() -> BNKForgeClient:
    return BNKForgeClient(
        MCPConfig(
            api_base_url="http://test-backend:8000",
            api_token="test-token",
            api_timeout=5,
            verify_ssl=False,
        )
    )


def test_api_error_classification_and_retryability() -> None:
    err = APIError(404, "missing", "/api/example")
    assert err.error_class == "not_found"
    assert err.retryable is False

    transient = APIError(503, "service unavailable", "/api/example")
    assert transient.error_class == "transient_error"
    assert transient.retryable is True


def test_error_envelope_contains_actionable_fields() -> None:
    client = _client()
    err = APIError(403, "forbidden", "/api/secure")

    payload = client._error_payload("GET", "/api/secure", err)

    assert payload["ok"] is False
    assert payload["request"] == {"method": "GET", "path": "/api/secure"}
    assert payload["error"]["status_code"] == 403
    assert payload["error"]["error_class"] == "auth_error"
    assert payload["error"]["retryable"] is False
    assert payload["error"]["next_action"]
