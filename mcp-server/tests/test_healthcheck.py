"""
Unit tests for the MCP auth-probe healthcheck.

Mocks the backend login call — no real backend needed.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from bnk_forge_mcp.healthcheck import probe


@pytest.fixture(autouse=True)
def _inject_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Ensure every test sees valid credentials by default so probe() reaches the
    HTTP-status logic rather than short-circuiting via the no-credentials path.

    Tests that specifically want to exercise the no-credentials path must
    override these values (delenv / setenv) inside their own body.
    """
    monkeypatch.setenv("BNK_FORGE_USERNAME", "mcp")
    monkeypatch.setenv("BNK_FORGE_PASSWORD", "test-pw")
    monkeypatch.delenv("BNK_FORGE_TOKEN", raising=False)


def _mock_response(status_code: int, body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    return resp


def _mock_response_non_json(status_code: int) -> MagicMock:
    """Return a mock response whose .json() raises JSONDecodeError."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.side_effect = json.JSONDecodeError("Expecting value", "", 0)
    return resp


# ------------------------------------------------------------------
# Successful auth
# ------------------------------------------------------------------


def test_probe_returns_0_on_successful_login() -> None:
    """200 + token in body → exit 0 (healthy)."""
    with patch("bnk_forge_mcp.healthcheck.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(200, {"token": "abc123"})
        assert probe() == 0


# ------------------------------------------------------------------
# Auth failures
# ------------------------------------------------------------------


def test_probe_returns_1_on_401() -> None:
    """401 response → exit 1 (unhealthy — password drift)."""
    with patch("bnk_forge_mcp.healthcheck.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(401, {"detail": "Invalid credentials"})
        assert probe() == 1


def test_probe_returns_1_on_200_without_token() -> None:
    """200 but no token in body → exit 1 (auth did not actually succeed)."""
    with patch("bnk_forge_mcp.healthcheck.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(200, {})
        assert probe() == 1


def test_probe_returns_1_on_403() -> None:
    """403 → exit 1."""
    with patch("bnk_forge_mcp.healthcheck.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(403, {"detail": "Forbidden"})
        assert probe() == 1


def test_probe_returns_1_on_500() -> None:
    """Unexpected 500 → exit 1 (not clearly unhealthy but safe default)."""
    with patch("bnk_forge_mcp.healthcheck.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(500, {"detail": "Internal Server Error"})
        assert probe() == 1


# ------------------------------------------------------------------
# Network / connectivity
# ------------------------------------------------------------------


def test_probe_returns_1_when_backend_unreachable() -> None:
    """Connection error (backend down) → exit 1; Docker start_period absorbs startup races."""
    with patch("bnk_forge_mcp.healthcheck.httpx.post", side_effect=Exception("Connection refused")):
        assert probe() == 1


# ------------------------------------------------------------------
# No-credentials case
# ------------------------------------------------------------------


def test_probe_returns_0_for_token_only_deployment() -> None:
    """No username/password but a bearer token IS set → skip the login probe, return 0.

    The probe authenticates via /api/auth/login (username/password only), so it
    cannot exercise a token; a token-only deployment is healthy and validated on
    real tool calls, not here.
    """
    with patch("bnk_forge_mcp.healthcheck.load_config") as mock_cfg:
        cfg = MagicMock()
        cfg.has_credentials = False
        cfg.has_token = True
        mock_cfg.return_value = cfg
        assert probe() == 0


def test_probe_token_only_logs_skip(caplog) -> None:  # type: ignore[no-untyped-def]
    """Token-only → info log is emitted so the skip is greppable."""
    import logging

    with patch("bnk_forge_mcp.healthcheck.load_config") as mock_cfg:
        cfg = MagicMock()
        cfg.has_credentials = False
        cfg.has_token = True
        mock_cfg.return_value = cfg
        with caplog.at_level(logging.INFO, logger="bnk_forge_mcp.healthcheck"):
            result = probe()
    assert result == 0
    assert "token-only" in caplog.text


def test_probe_returns_1_when_no_auth_configured() -> None:
    """Neither password NOR token → MCP cannot authenticate at all → exit 1 (UNHEALTHY).

    bonnyr-f5 #188: MCP_SERVICE_PASSWORD ships empty by default, so a default
    deploy with no token would 401 on every tool call. Report UNHEALTHY, not green.
    """
    with patch("bnk_forge_mcp.healthcheck.load_config") as mock_cfg:
        cfg = MagicMock()
        cfg.has_credentials = False
        cfg.has_token = False
        mock_cfg.return_value = cfg
        assert probe() == 1


def test_probe_no_auth_logs_error(caplog) -> None:  # type: ignore[no-untyped-def]
    """No auth at all → error log names the cause so a typo'd env var is greppable."""
    import logging

    with patch("bnk_forge_mcp.healthcheck.load_config") as mock_cfg:
        cfg = MagicMock()
        cfg.has_credentials = False
        cfg.has_token = False
        mock_cfg.return_value = cfg
        with caplog.at_level(logging.ERROR, logger="bnk_forge_mcp.healthcheck"):
            result = probe()
    assert result == 1
    assert "cannot authenticate" in caplog.text


# ------------------------------------------------------------------
# Non-JSON body (guard json parse)
# ------------------------------------------------------------------


def test_probe_returns_1_on_200_with_non_json_body() -> None:
    """200 response with non-JSON body → exit 1 cleanly (no traceback)."""
    with patch("bnk_forge_mcp.healthcheck.httpx.post") as mock_post:
        mock_post.return_value = _mock_response_non_json(200)
        assert probe() == 1
