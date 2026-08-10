"""
Integration tests for system routes.

Tests:
  8.  test_health_no_auth — GET /api/system/health works without auth
  9.  test_system_info_requires_auth — GET /api/system/version requires auth
  10. test_mcp_status_* — GET /api/system/mcp/status derives catalog from live tools/list
"""

import json
from unittest.mock import MagicMock, call, patch

import pytest


class TestSystemRoutes:
    """Route-level integration tests for /api/system."""

    def test_health_no_auth(self, client, db):
        """GET /api/system/health works without authentication (public endpoint)."""
        # Mock the SystemService.get_health to avoid Redis/Celery dependencies
        mock_health = {
            "services": {
                "backend": {"status": "healthy", "response_time_ms": 1.0},
                "database": {"status": "healthy", "response_time_ms": 2.0},
            },
            "timestamp": "2026-02-18T00:00:00",
        }
        with patch("routes.system.SystemService") as MockService:
            MockService.return_value.get_health.return_value = mock_health
            response = client.get("/api/system/health")

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

        data = response.json()
        assert "services" in data
        assert data["services"]["backend"]["status"] == "healthy"

    def test_system_version_requires_auth(self, client):
        """GET /api/system/version requires admin auth — returns 401 without token."""
        response = client.get("/api/system/version")
        assert response.status_code == 401

        data = response.json()
        assert data["error"]["code"] == "UNAUTHORIZED"


# ---------------------------------------------------------------------------
# MCP status tests — mock httpx so the route never dials out
# ---------------------------------------------------------------------------


def _make_httpx_post_mock(tools: list[dict], *, ping_status: int = 200) -> MagicMock:
    """Return a side_effect function for httpx.post that simulates the MCP handshake."""

    def _post(url, *, json=None, headers=None, follow_redirects=False, timeout=None):
        method = (json or {}).get("method", "")
        resp = MagicMock()
        resp.headers = {"content-type": "application/json"}
        resp.status_code = 200

        if method == "ping":
            resp.status_code = ping_status
            resp.json.return_value = {"jsonrpc": "2.0", "result": {}, "id": 1}
        elif method == "initialize":
            resp.json.return_value = {"jsonrpc": "2.0", "result": {"capabilities": {}}, "id": 1}
        elif method == "notifications/initialized":
            resp.json.return_value = {}
        elif method == "tools/list":
            resp.json.return_value = {"jsonrpc": "2.0", "result": {"tools": tools}, "id": 2}

        return resp

    return MagicMock(side_effect=_post)


class TestMCPStatus:
    """Tests for GET /api/system/mcp/status — live tool catalog derivation."""

    def test_mcp_status_groupsByModule_whenToolsHaveKnownModules(self, client, admin_headers, sample_user):
        """Tools are grouped into the correct display category by their _meta.module."""
        tools = [
            {"name": "system_health", "description": "Health check", "_meta": {"module": "system"}},
            {"name": "list_clusters", "description": "List clusters", "_meta": {"module": "cluster_management"}},
            {"name": "helm_install", "description": "Install chart", "_meta": {"module": "helm"}},
        ]
        mock_post = _make_httpx_post_mock(tools)

        with patch("routes.system.httpx") as mock_httpx:
            mock_httpx.post = mock_post
            mock_httpx.ConnectError = ConnectionError
            mock_httpx.TimeoutException = TimeoutError
            response = client.get("/api/system/mcp/status", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "healthy"
        assert data["total_tools"] == 3

        # Verify shape keys are all present
        for key in ("status", "latency_ms", "error", "endpoint", "port", "transport", "total_tools", "tool_catalog"):
            assert key in data

        catalog = {cat["category"]: cat for cat in data["tool_catalog"]}
        assert "System" in catalog
        assert catalog["System"]["icon"] == "Server"
        assert any(t["name"] == "system_health" for t in catalog["System"]["tools"])

        assert "Cluster Management" in catalog
        assert any(t["name"] == "list_clusters" for t in catalog["Cluster Management"]["tools"])

        assert "Helm" in catalog
        assert any(t["name"] == "helm_install" for t in catalog["Helm"]["tools"])

        # No "Other" bucket when all tools have known modules
        assert "Other" not in catalog

    def test_mcp_status_unknownModuleFallsIntoOther(self, client, admin_headers, sample_user):
        """A tool with an unrecognised or missing module key lands in the 'Other' category."""
        tools = [
            {"name": "mystery_tool", "description": "Unknown module", "_meta": {"module": "new_unknown_module"}},
            {"name": "no_meta_tool", "description": "No meta at all"},
        ]
        mock_post = _make_httpx_post_mock(tools)

        with patch("routes.system.httpx") as mock_httpx:
            mock_httpx.post = mock_post
            mock_httpx.ConnectError = ConnectionError
            mock_httpx.TimeoutException = TimeoutError
            response = client.get("/api/system/mcp/status", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()

        assert data["total_tools"] == 2
        catalog = {cat["category"]: cat for cat in data["tool_catalog"]}
        assert "Other" in catalog
        assert catalog["Other"]["icon"] == "Wrench"
        other_names = {t["name"] for t in catalog["Other"]["tools"]}
        assert "mystery_tool" in other_names
        assert "no_meta_tool" in other_names

    def test_mcp_status_toolsSortedByName(self, client, admin_headers, sample_user):
        """Tools within a category are sorted alphabetically by name."""
        tools = [
            {"name": "z_tool", "description": "Z", "_meta": {"module": "system"}},
            {"name": "a_tool", "description": "A", "_meta": {"module": "system"}},
            {"name": "m_tool", "description": "M", "_meta": {"module": "system"}},
        ]
        mock_post = _make_httpx_post_mock(tools)

        with patch("routes.system.httpx") as mock_httpx:
            mock_httpx.post = mock_post
            mock_httpx.ConnectError = ConnectionError
            mock_httpx.TimeoutException = TimeoutError
            response = client.get("/api/system/mcp/status", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()
        catalog = {cat["category"]: cat for cat in data["tool_catalog"]}
        names = [t["name"] for t in catalog["System"]["tools"]]
        assert names == sorted(names)

    def test_mcp_status_offline_returnEmptyCatalog(self, client, admin_headers, sample_user):
        """When the MCP server is unreachable, status=offline and catalog is empty."""
        with patch("routes.system.httpx") as mock_httpx:
            mock_httpx.ConnectError = ConnectionError
            mock_httpx.TimeoutException = TimeoutError
            mock_httpx.post.side_effect = ConnectionError("refused")
            response = client.get("/api/system/mcp/status", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "offline"
        assert data["total_tools"] == 0
        assert data["tool_catalog"] == []
        assert data["error"] is not None

    def test_mcp_status_handshakeFailure_returnsDegraded(self, client, admin_headers, sample_user):
        """If ping succeeds but tools/list handshake throws, status degrades gracefully."""
        call_count = 0

        def _post(url, *, json=None, headers=None, follow_redirects=False, timeout=None):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.headers = {"content-type": "application/json"}
            method = (json or {}).get("method", "")
            if method == "ping":
                resp.status_code = 200
                resp.json.return_value = {"jsonrpc": "2.0", "result": {}, "id": 1}
                return resp
            raise RuntimeError("handshake exploded")

        with patch("routes.system.httpx") as mock_httpx:
            mock_httpx.ConnectError = ConnectionError
            mock_httpx.TimeoutException = TimeoutError
            mock_httpx.post.side_effect = _post
            response = client.get("/api/system/mcp/status", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "degraded"
        assert data["total_tools"] == 0
        assert data["tool_catalog"] == []
        assert "unavailable" in (data["error"] or "").lower()

    def test_mcp_status_sseResponse_parsedCorrectly(self, client, admin_headers, sample_user):
        """SSE-wrapped tools/list response (text/event-stream) is parsed correctly."""
        tools = [{"name": "sse_tool", "description": "Via SSE", "_meta": {"module": "system"}}]
        sse_body = f"data: {json.dumps({'jsonrpc': '2.0', 'result': {'tools': tools}, 'id': 2})}\n\n"

        def _post(url, *, json=None, headers=None, follow_redirects=False, timeout=None):
            method = (json or {}).get("method", "")
            resp = MagicMock()
            resp.headers = {"content-type": "application/json"}
            resp.status_code = 200
            if method == "ping":
                resp.json.return_value = {"jsonrpc": "2.0", "result": {}, "id": 1}
            elif method == "initialize":
                resp.json.return_value = {"jsonrpc": "2.0", "result": {}, "id": 1}
            elif method == "notifications/initialized":
                resp.json.return_value = {}
            elif method == "tools/list":
                resp.headers = {"content-type": "text/event-stream"}
                resp.text = sse_body
            return resp

        with patch("routes.system.httpx") as mock_httpx:
            mock_httpx.ConnectError = ConnectionError
            mock_httpx.TimeoutException = TimeoutError
            mock_httpx.post.side_effect = _post
            response = client.get("/api/system/mcp/status", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["total_tools"] == 1
        catalog = {cat["category"]: cat for cat in data["tool_catalog"]}
        assert "System" in catalog
