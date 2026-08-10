"""
Unit tests for AuthMiddleware exemption logic.

Uses a minimal Starlette app with stub routes to verify middleware behaviour
without requiring a full app fixture or database.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import JSONResponse

from core.auth_middleware import AuthMiddleware


def _make_app(require_auth: bool = True) -> FastAPI:
    """Build a minimal app with AuthMiddleware and stub routes."""
    app = FastAPI()

    # Monkey-patch settings so the middleware sees our desired value
    from core import auth_middleware as mw_module
    original_settings = mw_module.settings

    class _FakeSettings:
        REQUIRE_AUTH = require_auth

    mw_module.settings = _FakeSettings()  # type: ignore[assignment]

    app.add_middleware(AuthMiddleware)

    @app.post("/api/benchmarks/agents")
    async def _register_agent(request: Request):
        return JSONResponse({"ok": True})

    @app.post("/api/benchmarks/results")
    async def _ingest_results(request: Request):
        return JSONResponse({"ok": True})

    @app.post("/api/benchmarks/results/aiperf")
    async def _ingest_aiperf(request: Request):
        return JSONResponse({"ok": True})

    @app.get("/api/benchmarks/agents")
    async def _list_agents(request: Request):
        return JSONResponse([])

    @app.get("/api/benchmarks/agents/{agent_id}")
    async def _get_agent(request: Request, agent_id: int):
        return JSONResponse({"id": agent_id})

    # Restore after app construction so other tests are unaffected
    # (TestClient is synchronous so the mutation is scoped to this function)
    # We intentionally do NOT restore here — the TestClient uses the patched
    # settings throughout its lifetime.  Each _make_app call is independent.

    return app


@pytest.fixture()
def client():
    """TestClient with REQUIRE_AUTH=true and stub benchmark routes."""
    return TestClient(_make_app(require_auth=True), raise_server_exceptions=True)


# ── Benchmark agent POST endpoints are exempt from user-JWT ──────────────────


class TestAgentPublicEndpointsNotBlockedByMiddleware:
    """POST register/ingest paths must pass through without a JWT."""

    def test_post_benchmarks_agents_no_auth_not_middleware_401(self, client):
        # No Authorization header — middleware must NOT 401; route handles it
        resp = client.post("/api/benchmarks/agents", json={"name": "agent-1"})
        # The stub route returns 200 {"ok": true}, proving middleware let it through
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_post_benchmarks_results_no_auth_not_middleware_401(self, client):
        resp = client.post("/api/benchmarks/results", json={})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_post_benchmarks_results_aiperf_no_auth_not_middleware_401(self, client):
        resp = client.post("/api/benchmarks/results/aiperf", json={})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


# ── Non-exempt benchmark routes still require JWT ────────────────────────────


class TestNonExemptBenchmarkEndpointsStillRequireJWT:
    """GET routes must still be blocked by the middleware when no JWT is supplied."""

    def test_get_benchmarks_agents_no_auth_returns_401(self, client):
        resp = client.get("/api/benchmarks/agents")
        assert resp.status_code == 401
        body = resp.json()
        assert body["error"]["code"] == "UNAUTHORIZED"

    def test_get_benchmark_agent_by_id_no_auth_returns_401(self, client):
        resp = client.get("/api/benchmarks/agents/1")
        assert resp.status_code == 401
