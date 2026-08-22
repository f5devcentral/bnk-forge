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


# ── API-token (bnk_) branch: verified + gated via the middleware ─────────────


class TestApiTokenBranchGatedByMiddleware:
    """#186 r5 (bonnyr-f5, Minor): the bnk_ API-token branch had no coverage.

    It is verified in the middleware (not deferred to the route) and runs the
    must-change gate. Both the verify and the gate now run through
    run_in_threadpool so the sync DB session never blocks the event loop; these
    tests exercise that path end to end with a stubbed verifier.
    """

    def _client_with_verifier(self, monkeypatch, user):
        from core import auth_middleware as mw_module
        app = _make_app(require_auth=True)

        @app.get("/api/projects")
        async def _projects(request: Request):  # a dependency-less /api route
            return JSONResponse({"ok": True})

        # monkeypatch auto-restores the real verifier after the test, so this stub
        # never leaks into the other TestMiddlewareVerifiesApiTokens cases.
        monkeypatch.setattr(mw_module, "_verify_api_token", lambda token: user)
        return TestClient(app, raise_server_exceptions=True)

    def test_api_token_must_change_user_is_403(self, monkeypatch):
        class _U:
            must_change_password = True
        client = self._client_with_verifier(monkeypatch, _U())
        resp = client.get("/api/projects", headers={"Authorization": "Bearer bnk_deadbeef"})
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"

    def test_api_token_settled_user_passes(self, monkeypatch):
        class _U:
            must_change_password = False
        client = self._client_with_verifier(monkeypatch, _U())
        resp = client.get("/api/projects", headers={"Authorization": "Bearer bnk_deadbeef"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


# ── JWT branch fails CLOSED on an unresolvable subject (bonnyr-f5 #193 minor) ──


class TestUnresolvableJwtSubjectRefused:
    """A VALID JWT whose user cannot be resolved (deleted/disabled/DB error) must be
    REFUSED with 401 — token_user_state returns None on any such failure and the
    middleware must fail closed, never pass through on the ~32 dependency-less /api
    routes (bonnyr-f5 #186 r2 called the earlier skip a fail-open bypass). Untested
    until now."""

    def _client(self):
        app = _make_app(require_auth=True)

        @app.get("/api/projects")
        async def _projects(request: Request):  # a dependency-less /api route
            return JSONResponse({"ok": True})

        return TestClient(app, raise_server_exceptions=True)

    def test_valid_jwt_unresolvable_user_is_401(self, monkeypatch):
        from services.auth_service import create_access_token

        # decode_token succeeds (real JWT), but the user can't be resolved.
        monkeypatch.setattr("services.auth_service.token_user_state", lambda token: None)
        token = create_access_token(data={"sub": "ghost", "role": "admin"})
        resp = self._client().get(
            "/api/projects", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "UNAUTHORIZED"

    def test_valid_jwt_resolvable_settled_user_passes(self, monkeypatch):
        from services.auth_service import create_access_token

        class _U:
            must_change_password = False

        monkeypatch.setattr("services.auth_service.token_user_state", lambda token: _U())
        token = create_access_token(data={"sub": "real", "role": "admin"})
        resp = self._client().get(
            "/api/projects", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


# ── must-change exempt-path matching is EXACT, not suffix (bonnyr-f5 #193 minor) ──


class TestPasswordChangeExemptPathIsExact:
    """enforce_password_change gates a must-change user off everything but the exempt
    endpoints, and the match is EXACT (path.rstrip('/') in the frozenset) — a security
    gate must not accept an unrelated route that merely ENDS WITH '/auth/me'. Untested
    until now."""

    class _MustChange:
        must_change_password = True

    def test_exact_exempt_paths_are_allowed(self):
        from services.auth_service import enforce_password_change

        # No raise on the exact exempt paths (trailing slash tolerated by rstrip).
        enforce_password_change("/api/auth/me", self._MustChange())
        enforce_password_change("/api/auth/me/", self._MustChange())
        enforce_password_change("/api/auth/change-password", self._MustChange())

    def test_suffix_lookalike_paths_are_not_exempt(self):
        from core.errors import ForbiddenError
        from services.auth_service import enforce_password_change

        for path in ("/api/evil/auth/me", "/api/auth/me/extra", "xxx/api/auth/me"):
            with pytest.raises(ForbiddenError):
                enforce_password_change(path, self._MustChange())

    def test_settled_user_is_never_gated(self):
        from services.auth_service import enforce_password_change

        class _Settled:
            must_change_password = False

        enforce_password_change("/api/anything/at/all", _Settled())  # no raise
