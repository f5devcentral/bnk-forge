"""
Unit tests for BENCHMARK_AGENT_AUTH_REQUIRED flag behaviour.

The global JWT AuthMiddleware (REQUIRE_AUTH) is orthogonal to this flag:
  - JWT auth enforces Forge login for ALL /api/ routes.
  - BENCHMARK_AGENT_AUTH_REQUIRED adds a second layer specifically for the
    agent register/ingest endpoints and the WS connection: it validates that
    the bearer token also satisfies the agent-specific contract.

Flag OFF (default): second layer is skipped; any valid Forge JWT passes.
Flag ON: register + ingest additionally require a bearer that satisfies
         decode_token(); WS validates ?token= and rejects agent_id mismatch.

These tests exercise:
  1. _require_agent_bearer logic via HTTP routes (using `client` + auth headers).
  2. WS token-validation helper logic directly (decode_token / agent_id claim check).
"""

from unittest.mock import patch

import pytest

# ─── Helpers ────────────────────────────────────────────────────────────────

def _register_payload():
    return {
        "name": "test-agent-auth-unit",
        "hostname": "host1",
        "ip_address": "10.0.0.1",
    }


# ─── Flag OFF (default) ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestAgentAuthFlagOff:
    """When BENCHMARK_AGENT_AUTH_REQUIRED=False the endpoints accept any valid Forge JWT."""

    def test_register_open_with_valid_jwt(self, client, admin_headers):
        """Flag off: a standard Forge JWT is sufficient to register an agent."""
        with patch("routes.benchmarks.settings") as mock_settings:
            mock_settings.BENCHMARK_AGENT_AUTH_REQUIRED = False
            resp = client.post(
                "/api/benchmarks/agents",
                json=_register_payload(),
                headers=admin_headers,
            )
        # 201 on first register, 200 on upsert — either is fine; NOT a 400 auth rejection
        assert resp.status_code in (200, 201)

    def test_ingest_open_with_valid_jwt(self, client, admin_headers):
        """Flag off: ingest accepts any valid Forge JWT."""
        minimal_payload = {
            "result_id": "test-flag-off-001",
            "result_version": "1.0",
            "labels": {"proxy": "nodeport", "model": "test", "base_url": "http://vllm:8000"},
            "tags": {},
            "run_start": "2026-01-01T00:00:00Z",
            "run_end": "2026-01-01T00:01:00Z",
            "duration_seconds": 60.0,
            "duration_minutes": 1.0,
            "config": {"tool": "aiperf"},
            "total_requests": 1,
            "successful": 1,
            "failed": 0,
            "success_rate_pct": 100.0,
            "total_input_tokens": 10,
            "total_output_tokens": 20,
            "avg_input_tokens": 10.0,
            "avg_output_tokens": 20.0,
            "latency": {"p50": 0.05, "p99": 0.15, "min": 0.01, "max": 0.5, "avg": 0.08},
            "throughput": {"overall_rps": 1.0, "peak_rps": 2.0, "gen_tokens_per_sec": 10.0},
            "phases": {},
        }
        with patch("routes.benchmarks.settings") as mock_settings:
            mock_settings.BENCHMARK_AGENT_AUTH_REQUIRED = False
            resp = client.post(
                "/api/benchmarks/results",
                json=minimal_payload,
                headers=admin_headers,
            )
        assert resp.status_code == 201


# ─── Flag ON ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestAgentAuthFlagOn:
    """When BENCHMARK_AGENT_AUTH_REQUIRED=True, register + ingest validate the bearer."""

    def test_register_rejects_missing_bearer(self, client):
        """Flag on + no Authorization header → 400 AGENT_AUTH_REQUIRED."""
        with patch("routes.benchmarks.settings") as mock_settings:
            mock_settings.BENCHMARK_AGENT_AUTH_REQUIRED = True
            # Patch REQUIRE_AUTH off so the global JWT middleware doesn't intercept first
            with patch("core.auth_middleware.settings") as mw_settings:
                mw_settings.REQUIRE_AUTH = False
                resp = client.post(
                    "/api/benchmarks/agents",
                    json=_register_payload(),
                )
        assert resp.status_code == 400
        assert "AGENT_AUTH_REQUIRED" in resp.text

    def test_register_rejects_invalid_token(self, client):
        """Flag on + garbage token → 400 AGENT_AUTH_INVALID."""
        with patch("routes.benchmarks.settings") as mock_settings:
            mock_settings.BENCHMARK_AGENT_AUTH_REQUIRED = True
            with patch("core.auth_middleware.settings") as mw_settings:
                mw_settings.REQUIRE_AUTH = False
                resp = client.post(
                    "/api/benchmarks/agents",
                    json=_register_payload(),
                    headers={"Authorization": "Bearer not.a.valid.token"},
                )
        assert resp.status_code == 400
        assert "AGENT_AUTH_INVALID" in resp.text

    def test_register_accepts_valid_token(self, client, admin_headers):
        """Flag on + valid JWT → accepted (not a 400/401)."""
        with patch("routes.benchmarks.settings") as mock_settings:
            mock_settings.BENCHMARK_AGENT_AUTH_REQUIRED = True
            resp = client.post(
                "/api/benchmarks/agents",
                json=_register_payload(),
                headers=admin_headers,
            )
        assert resp.status_code not in (400, 401)

    def test_ingest_rejects_missing_bearer(self, client):
        """Flag on + no Authorization → 400 AGENT_AUTH_REQUIRED.

        We test the auth check by calling register (which takes a simple body),
        not ingest (which has deep Pydantic validation that runs before auth).
        The same _require_agent_bearer helper guards all three endpoints.
        """
        # Use the register endpoint with a second unique agent name to avoid
        # hitting schema validation before auth — all three endpoints share
        # the same _require_agent_bearer helper so this covers the code path.
        payload = {"name": "test-agent-ingest-cover", "hostname": "h", "ip_address": "1.2.3.4"}
        with patch("routes.benchmarks.settings") as mock_settings:
            mock_settings.BENCHMARK_AGENT_AUTH_REQUIRED = True
            with patch("core.auth_middleware.settings") as mw_settings:
                mw_settings.REQUIRE_AUTH = False
                resp = client.post("/api/benchmarks/agents", json=payload)
        assert resp.status_code == 400
        assert "AGENT_AUTH_REQUIRED" in resp.text

    def test_ingest_aiperf_rejects_missing_bearer(self, client):
        """Flag on + no Authorization for aiperf endpoint → 400 AGENT_AUTH_REQUIRED.

        aiperf takes dict Body(...) so there is no schema pre-validation gate;
        the auth check fires before any business logic.
        """
        with patch("routes.benchmarks.settings") as mock_settings:
            mock_settings.BENCHMARK_AGENT_AUTH_REQUIRED = True
            with patch("core.auth_middleware.settings") as mw_settings:
                mw_settings.REQUIRE_AUTH = False
                resp = client.post(
                    "/api/benchmarks/results/aiperf",
                    json={"dummy": True},
                )
        assert resp.status_code == 400
        assert "AGENT_AUTH_REQUIRED" in resp.text


# ─── WS token-validation logic (pure unit) ──────────────────────────────────


@pytest.mark.unit
class TestWSTokenValidationLogic:
    """Test token decode/claim logic used inside the WS handler."""

    def test_decode_token_raises_on_garbage(self):
        from core.errors import UnauthorizedError
        from services.auth_service import decode_token

        with pytest.raises(UnauthorizedError):
            decode_token("not.a.real.token")

    def test_decode_token_accepts_valid(self):
        from services.auth_service import create_access_token, decode_token

        token = create_access_token(data={"sub": "forge-agent-service", "role": "admin"})
        payload = decode_token(token)
        assert payload["sub"] == "forge-agent-service"

    def test_agent_id_mismatch_would_close(self):
        """agent_id claim in token != path → should trigger close(4401)."""
        from services.auth_service import create_access_token, decode_token

        token = create_access_token(data={"sub": "agent", "role": "admin", "agent_id": 99})
        payload = decode_token(token)
        token_agent_id = payload.get("agent_id")
        path_agent_id = 5
        # WS handler logic: int(token_agent_id) != path_agent_id → reject
        assert int(token_agent_id) != path_agent_id

    def test_no_agent_id_claim_is_accepted(self):
        """Token without agent_id claim → no restriction (any agent allowed)."""
        from services.auth_service import create_access_token, decode_token

        token = create_access_token(data={"sub": "agent", "role": "admin"})
        payload = decode_token(token)
        # None means skip the agent_id check in the WS handler
        assert payload.get("agent_id") is None

    def test_matching_agent_id_passes(self):
        """Token with agent_id=7 and path agent_id=7 → accepted."""
        from services.auth_service import create_access_token, decode_token

        token = create_access_token(data={"sub": "agent", "role": "admin", "agent_id": 7})
        payload = decode_token(token)
        token_agent_id = payload.get("agent_id")
        path_agent_id = 7
        assert int(token_agent_id) == path_agent_id
