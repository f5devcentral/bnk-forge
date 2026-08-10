"""
Integration tests for ALL benchmark API routes (35 HTTP endpoints).

Covers:
  - Result ingestion (2 endpoints)
  - Config CRUD (5 endpoints)
  - Run lifecycle (5 endpoints)
  - Agent CRUD (4 endpoints)
  - Comparison & Summary (2 endpoints)
  - Target CRUD + validate (6 endpoints)
  - Target discovery (1 endpoint)
  - Proxy discovery (1 endpoint)
  - Proxy CRUD + deploy/redeploy/delete/task-status (8 endpoints)
  - Trigger run (1 endpoint)

Auth rules (global AuthMiddleware enforces JWT on ALL /api/ routes):
  - All endpoints require a valid JWT token (any role) — no truly public endpoints.
  - Endpoints with `dependencies=[Depends(require_viewer)]` additionally enforce
    the viewer role; endpoints without that decorator work with any valid token.
  - "No route-level dep" endpoints: use operator_headers (any valid token).
  - "Viewer dep" endpoints: use viewer_headers + all_test_users fixture.
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from models import User
from models.benchmark import (
    BenchmarkAgent,
    BenchmarkConfig,
    BenchmarkRun,
    BenchmarkRunGroup,
    BenchmarkTarget,
    ProxyDeployment,
)
from services.auth_service import hash_password

# ---------------------------------------------------------------------------
# Real aiperf 0.10.0 fixture (loaded once at module import)
# ---------------------------------------------------------------------------
_FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "fixtures",
    "sample_aiperf_profile_export.json",
)
with open(_FIXTURE_PATH) as _f:
    _REAL_AIPERF_EXPORT = json.load(_f)


@pytest.fixture(autouse=True)
def _operator_user_exists(request, db):
    """Ensure the `testoperator` user (used by operator_headers) exists in the DB.

    Mutating benchmark routes now carry Depends(require_operator) (M1), which does
    a DB lookup of the JWT subject and enforces the operator role. Without a real
    row the lookup 401s before the route runs, so every operator-token test needs
    this user present.

    Skipped when the test also requests `all_test_users` (which itself creates
    testoperator) — that fixture runs after this autouse one and would hit a
    UNIQUE collision if we'd already inserted the row.
    """
    if "all_test_users" in request.fixturenames:
        return
    if not db.query(User).filter(User.username == "testoperator").first():
        db.add(
            User(
                username="testoperator",
                email="testoperator@bnk-forge.test",
                hashed_password=hash_password("testpassword123"),
                role="operator",
                is_active=True,
                must_change_password=False,
            )
        )
        db.commit()

# ---------------------------------------------------------------------------
# Helper factories — create DB rows directly for test setup
# ---------------------------------------------------------------------------

def _make_config(db, **overrides):
    config = BenchmarkConfig(
        name=overrides.get("name", "test-config"),
        tool=overrides.get("tool", "aiperf"),
        config_json=overrides.get("config_json", {"url": "http://localhost:8000", "request_count": 10}),
        created_by=overrides.get("created_by", "admin"),
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


def _make_run(db, **overrides):
    run = BenchmarkRun(
        tool=overrides.get("tool", "aiperf"),
        proxy=overrides.get("proxy", "nodeport"),
        model=overrides.get("model", "tinyllama"),
        base_url=overrides.get("base_url", "http://vllm:8000"),
        status=overrides.get("status", "completed"),
        config_id=overrides.get("config_id", None),
        agent_id=overrides.get("agent_id", None),
        target_id=overrides.get("target_id", None),
        latency_p50=overrides.get("latency_p50", 0.05),
        latency_p99=overrides.get("latency_p99", 0.15),
        overall_rps=overrides.get("overall_rps", 100.0),
        total_requests=overrides.get("total_requests", 1000),
        successful_requests=overrides.get("successful_requests", 990),
        failed_requests=overrides.get("failed_requests", 10),
        success_rate_pct=overrides.get("success_rate_pct", 99.0),
        duration_seconds=overrides.get("duration_seconds", 10.0),
        result_json=overrides.get("result_json", None),
        run_group_id=overrides.get("run_group_id", None),
        scenario_key=overrides.get("scenario_key", None),
        variant_label=overrides.get("variant_label", None),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _make_agent(db, **overrides):
    agent = BenchmarkAgent(
        name=overrides.get("name", "agent-1"),
        hostname=overrides.get("hostname", "bench-host"),
        ip_address=overrides.get("ip_address", "10.0.0.1"),
        status=overrides.get("status", "connected"),
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


def _make_target(db, cluster_id, **overrides):
    target = BenchmarkTarget(
        name=overrides.get("name", "target-a"),
        cluster_id=cluster_id,
        llm_base_url=overrides.get("llm_base_url", "http://vllm.default:8000"),
        llm_model=overrides.get("llm_model", "tinyllama"),
        llm_namespace=overrides.get("llm_namespace", "default"),
        llm_endpoint=overrides.get("llm_endpoint", "/v1/chat/completions"),
        proxy_namespace=overrides.get("proxy_namespace", "perf-proxies"),
        status=overrides.get("status", "active"),
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    return target


def _make_proxy(db, target_id, **overrides):
    proxy = ProxyDeployment(
        target_id=target_id,
        proxy_type=overrides.get("proxy_type", "envoy"),
        status=overrides.get("status", "ready"),
        status_message=overrides.get("status_message", "Proxy ready"),
        proxy_url=overrides.get("proxy_url", "http://envoy.perf-proxies:10080"),
        celery_task_id=overrides.get("celery_task_id", None),
    )
    db.add(proxy)
    db.commit()
    db.refresh(proxy)
    return proxy


# ---------------------------------------------------------------------------
# Minimal valid BenchmarkResultPush payload
# ---------------------------------------------------------------------------

def _result_push_payload(**overrides):
    """Return a minimal valid payload for POST /api/benchmarks/results."""
    base = {
        "result_id": "test-result-001",
        "result_version": "1.0",
        "labels": {"proxy": "envoy", "model": "tinyllama", "base_url": "http://vllm:8000"},
        "tags": {},
        "run_start": "2026-03-19T00:00:00Z",
        "run_end": "2026-03-19T00:01:00Z",
        "duration_seconds": 60.0,
        "duration_minutes": 1.0,
        "config": {"tool": "aiperf", "url": "http://vllm:8000", "model": "tinyllama"},
        "total_requests": 100,
        "successful": 95,
        "failed": 5,
        "success_rate_pct": 95.0,
        "total_input_tokens": 5000,
        "total_output_tokens": 10000,
        "avg_input_tokens": 50.0,
        "avg_output_tokens": 100.0,
        "latency": {"p50": 0.05, "p99": 0.15, "min": 0.01, "max": 0.5, "avg": 0.08},
        "throughput": {"overall_rps": 10.0, "peak_rps": 15.0, "gen_tokens_per_sec": 500.0},
        "phases": {"profiling": {"requests": 100, "successful": 95, "failed": 5, "duration_seconds": 60.0}},
    }
    base.update(overrides)
    return base


def _aiperf_raw_payload():
    """Return a minimal raw aiperf export JSON for POST /api/benchmarks/results/aiperf."""
    return {
        "benchmark_id": "aiperf-raw-001",
        "schema_version": "1.0",
        "request_latency": {"avg": 50.0, "min": 10.0, "p25": 30.0, "p50": 45.0, "p75": 60.0, "p90": 70.0, "p95": 80.0, "p99": 150.0, "max": 200.0},
        "request_throughput": {"avg": 20.0},
        "output_token_throughput": {"avg": 300.0},
        "request_count": {"avg": 50},
        "time_to_first_token": {"avg": 0.01},
        "inter_token_latency": {"avg": 0.005},
        "output_sequence_length": {"avg": 100},
        "input_sequence_length": {"avg": 50},
        "benchmark_duration": {"avg": 30.0},
        "config": {"url": "http://vllm:8000", "model": "tinyllama"},
    }


# ============================================================================
# 1. Result Ingestion
# ============================================================================

class TestIngestBenchmarkResult:
    """POST /api/benchmarks/results — ingest canonical result push.

    No route-level auth dep — any authenticated user can push results.
    """

    def test_happy_path(self, client, operator_headers, db):
        payload = _result_push_payload()
        resp = client.post("/api/benchmarks/results", json=payload, headers=operator_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data
        assert "run_id" in data
        assert data["proxy"] == "envoy"
        assert data["model"] == "tinyllama"
        assert data["status"] == "completed"

    def test_requires_valid_token(self, client, db):
        """Global auth middleware rejects unauthenticated requests."""
        resp = client.post("/api/benchmarks/results", json=_result_push_payload())
        assert resp.status_code == 401

    def test_response_contract(self, client, operator_headers, db):
        resp = client.post("/api/benchmarks/results", json=_result_push_payload(), headers=operator_headers)
        data = resp.json()
        expected_keys = {"id", "run_id", "proxy", "model", "status"}
        assert expected_keys <= set(data.keys())

    def test_config_id_linkage_sets_on_run(self, client, operator_headers, db):
        """Pushing a result with a valid config_id links the run to that config."""
        cfg = _make_config(db, name="linkage-cfg")
        payload = _result_push_payload(config_id=cfg.id)
        resp = client.post("/api/benchmarks/results", json=payload, headers=operator_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["config_id"] == cfg.id

    def test_stale_target_id_is_nulled_defensively(self, client, operator_headers, db):
        """Pushing with a non-existent target_id must NOT error — target_id is silently set to None."""
        payload = _result_push_payload(target_id=999999)
        resp = client.post("/api/benchmarks/results", json=payload, headers=operator_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["target_id"] is None


class TestIngestAiperfResult:
    """POST /api/benchmarks/results/aiperf — ingest raw aiperf JSON."""

    def test_happy_path(self, client, operator_headers, db):
        resp = client.post(
            "/api/benchmarks/results/aiperf?proxy=envoy&model=tinyllama",
            json=_aiperf_raw_payload(),
            headers=operator_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["proxy"] == "envoy"
        assert data["model"] == "tinyllama"
        assert data["status"] == "completed"

    def test_requires_valid_token(self, client, db):
        resp = client.post("/api/benchmarks/results/aiperf", json=_aiperf_raw_payload())
        assert resp.status_code == 401

    def test_response_contract(self, client, operator_headers, db):
        resp = client.post("/api/benchmarks/results/aiperf", json=_aiperf_raw_payload(), headers=operator_headers)
        data = resp.json()
        expected_keys = {"id", "run_id", "proxy", "model", "status"}
        assert expected_keys <= set(data.keys())


class TestIngestAiperfResultRealExport:
    """POST /api/benchmarks/results/aiperf — exercises the REAL aiperf 0.10.0 fixture
    and the 4 new linkage query params (target_id, config_id, proxy_deployment_id,
    dataset_name).
    """

    def test_real_export_model_url_timestamps_populated(self, client, operator_headers, db):
        """Fix latent transform bug: input_config/start_time/end_time keys must be read.

        The legacy payload uses "config"/"run_start"/"run_end"; the real 0.10.0 export
        uses "input_config"/"start_time"/"end_time". After the fix, model, url, and
        timestamps must all be non-empty/non-"unknown".
        """
        resp = client.post(
            "/api/benchmarks/results/aiperf?proxy=bnk",
            json=_REAL_AIPERF_EXPORT,
            headers=operator_headers,
        )
        assert resp.status_code == 201
        run_id = resp.json()["run_id"]

        run = db.query(BenchmarkRun).filter(BenchmarkRun.id == run_id).first()
        assert run is not None

        # model comes from input_config.models.items[0].name
        assert run.model == "llama3"

        # base_url comes from input_config.endpoint.urls[0]
        assert run.base_url == "http://10.0.10.108"

        # started_at / completed_at populated from start_time / end_time
        assert run.started_at is not None
        assert run.completed_at is not None

        # result_json must have correct timestamps (not empty strings)
        rj = run.result_json or {}
        assert rj.get("run_start", "") != ""
        assert rj.get("run_end", "") != ""

    def test_real_export_http_timing_block_present(self, client, operator_headers, db):
        """http_timing block is built from the 5 http_req_* distribution fields."""
        resp = client.post(
            "/api/benchmarks/results/aiperf",
            json=_REAL_AIPERF_EXPORT,
            headers=operator_headers,
        )
        assert resp.status_code == 201
        run = db.query(BenchmarkRun).filter(
            BenchmarkRun.id == resp.json()["run_id"]
        ).first()
        rj = run.result_json or {}
        assert "http_timing" in rj
        ht = rj["http_timing"]
        for key in ("http_req_blocked", "http_req_dns_lookup", "http_req_connecting",
                    "http_req_waiting", "http_req_sending"):
            assert key in ht

    def test_real_export_telemetry_block_present(self, client, operator_headers, db):
        """telemetry block is populated from telemetry_data in the export."""
        resp = client.post(
            "/api/benchmarks/results/aiperf",
            json=_REAL_AIPERF_EXPORT,
            headers=operator_headers,
        )
        assert resp.status_code == 201
        run = db.query(BenchmarkRun).filter(
            BenchmarkRun.id == resp.json()["run_id"]
        ).first()
        rj = run.result_json or {}
        assert "telemetry" in rj
        assert "summary" in rj["telemetry"]

    def test_real_export_usage_block_present(self, client, operator_headers, db):
        """usage block built from usage_prompt_tokens / usage_completion_tokens."""
        resp = client.post(
            "/api/benchmarks/results/aiperf",
            json=_REAL_AIPERF_EXPORT,
            headers=operator_headers,
        )
        assert resp.status_code == 201
        run = db.query(BenchmarkRun).filter(
            BenchmarkRun.id == resp.json()["run_id"]
        ).first()
        rj = run.result_json or {}
        assert "usage" in rj
        assert "prompt_tokens" in rj["usage"]
        assert "completion_tokens" in rj["usage"]

    def test_linkage_params_set_fk_columns_when_rows_exist(
        self, client, operator_headers, make_k8s_cluster, db
    ):
        """target_id / config_id / proxy_deployment_id are written to the run row
        when the referenced rows exist, and dataset_name lands in result_json.
        """
        cluster = make_k8s_cluster(name="aiperf-link-cluster")
        target = _make_target(db, cluster.id, name="aiperf-link-target")
        cfg = _make_config(db, name="aiperf-link-config")
        proxy = _make_proxy(db, target.id, proxy_type="bnk")

        url = (
            f"/api/benchmarks/results/aiperf"
            f"?proxy=bnk"
            f"&target_id={target.id}"
            f"&config_id={cfg.id}"
            f"&proxy_deployment_id={proxy.id}"
            f"&dataset_name=synthetic-512-128"
        )
        resp = client.post(url, json=_REAL_AIPERF_EXPORT, headers=operator_headers)
        assert resp.status_code == 201
        run_id = resp.json()["run_id"]

        db.expire_all()
        run = db.query(BenchmarkRun).filter(BenchmarkRun.id == run_id).first()
        assert run.target_id == target.id
        assert run.config_id == cfg.id
        assert run.proxy_deployment_id == proxy.id

        # dataset_name stored in result_json
        rj = run.result_json or {}
        assert rj.get("dataset_name") == "synthetic-512-128"

    def test_linkage_params_gracefully_null_when_rows_missing(
        self, client, operator_headers, db
    ):
        """Non-existent FK IDs are silently dropped — ingestion must succeed."""
        url = (
            "/api/benchmarks/results/aiperf"
            "?proxy=bnk"
            "&target_id=99999"
            "&config_id=99999"
            "&proxy_deployment_id=99999"
            "&dataset_name=ghost-dataset"
        )
        resp = client.post(url, json=_REAL_AIPERF_EXPORT, headers=operator_headers)
        assert resp.status_code == 201
        run_id = resp.json()["run_id"]

        db.expire_all()
        run = db.query(BenchmarkRun).filter(BenchmarkRun.id == run_id).first()
        # FK columns nulled out by defensive resolution
        assert run.target_id is None
        assert run.config_id is None
        assert run.proxy_deployment_id is None
        # dataset_name still persisted in result_json (it's just a string, no FK)
        rj = run.result_json or {}
        assert rj.get("dataset_name") == "ghost-dataset"

    def test_old_format_still_works_via_fallback(self, client, operator_headers, db):
        """Legacy payload with 'config'/'run_start'/'run_end' still ingests correctly."""
        resp = client.post(
            "/api/benchmarks/results/aiperf?proxy=envoy&model=tinyllama",
            json=_aiperf_raw_payload(),
            headers=operator_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["model"] == "tinyllama"
        assert data["status"] == "completed"


# ============================================================================
# 2. Config CRUD
# ============================================================================

class TestListBenchmarkConfigs:
    """GET /api/benchmarks/configs — list saved configs (require_viewer)."""

    def test_happy_path(self, client, viewer_headers, all_test_users, db):
        _make_config(db, name="cfg-1")
        _make_config(db, name="cfg-2")
        resp = client.get("/api/benchmarks/configs", headers=viewer_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 2

    def test_requires_auth(self, client):
        resp = client.get("/api/benchmarks/configs")
        assert resp.status_code == 401

    def test_filter_by_tool(self, client, viewer_headers, all_test_users, db):
        _make_config(db, name="cfg-aiperf", tool="aiperf")
        _make_config(db, name="cfg-llmbench", tool="llm-bench")
        resp = client.get("/api/benchmarks/configs?tool=aiperf", headers=viewer_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert all(c["tool"] == "aiperf" for c in data)

    def test_response_contract(self, client, viewer_headers, all_test_users, db):
        _make_config(db, name="cfg-contract")
        resp = client.get("/api/benchmarks/configs", headers=viewer_headers)
        item = resp.json()[0]
        for key in ("id", "name", "tool", "config_json", "created_at", "updated_at"):
            assert key in item


class TestGetBenchmarkConfig:
    """GET /api/benchmarks/configs/{config_id} (require_viewer)."""

    def test_happy_path(self, client, viewer_headers, all_test_users, db):
        cfg = _make_config(db, name="cfg-get")
        resp = client.get(f"/api/benchmarks/configs/{cfg.id}", headers=viewer_headers)
        assert resp.status_code == 200
        assert resp.json()["id"] == cfg.id
        assert resp.json()["name"] == "cfg-get"

    def test_requires_auth(self, client, db):
        cfg = _make_config(db, name="cfg-get-noauth")
        resp = client.get(f"/api/benchmarks/configs/{cfg.id}")
        assert resp.status_code == 401

    def test_not_found(self, client, viewer_headers, all_test_users, db):
        resp = client.get("/api/benchmarks/configs/99999", headers=viewer_headers)
        assert resp.status_code == 404


class TestCreateBenchmarkConfig:
    """POST /api/benchmarks/configs — no route-level auth dep."""

    def test_happy_path(self, client, operator_headers, db):
        payload = {
            "name": "new-config",
            "tool": "aiperf",
            "config_json": {"url": "http://localhost:8000"},
        }
        resp = client.post("/api/benchmarks/configs", json=payload, headers=operator_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "new-config"
        assert data["tool"] == "aiperf"
        assert "id" in data

    def test_requires_valid_token(self, client, db):
        payload = {"name": "pub-config", "config_json": {"url": "http://x"}}
        resp = client.post("/api/benchmarks/configs", json=payload)
        assert resp.status_code == 401

    def test_duplicate_name_conflict(self, client, operator_headers, db):
        _make_config(db, name="dup-cfg")
        payload = {"name": "dup-cfg", "config_json": {"url": "http://x"}}
        resp = client.post("/api/benchmarks/configs", json=payload, headers=operator_headers)
        assert resp.status_code == 409

    def test_response_contract(self, client, operator_headers, db):
        payload = {"name": "contract-cfg", "config_json": {"url": "http://x"}}
        resp = client.post("/api/benchmarks/configs", json=payload, headers=operator_headers)
        data = resp.json()
        for key in ("id", "name", "tool", "config_json", "created_at", "updated_at"):
            assert key in data


class TestUpdateBenchmarkConfig:
    """PUT /api/benchmarks/configs/{config_id} — no route-level auth dep."""

    def test_happy_path(self, client, operator_headers, db):
        cfg = _make_config(db, name="cfg-update")
        resp = client.put(
            f"/api/benchmarks/configs/{cfg.id}",
            json={"name": "cfg-updated", "config_json": {"url": "http://new"}},
            headers=operator_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "cfg-updated"

    def test_requires_valid_token(self, client, db):
        cfg = _make_config(db, name="cfg-update-noauth")
        resp = client.put(f"/api/benchmarks/configs/{cfg.id}", json={"description": "hi"})
        assert resp.status_code == 401

    def test_not_found(self, client, operator_headers, db):
        resp = client.put("/api/benchmarks/configs/99999", json={"name": "x"}, headers=operator_headers)
        assert resp.status_code == 404


class TestDeleteBenchmarkConfig:
    """DELETE /api/benchmarks/configs/{config_id} — no route-level auth dep."""

    def test_happy_path(self, client, operator_headers, db):
        cfg = _make_config(db, name="cfg-del")
        resp = client.delete(f"/api/benchmarks/configs/{cfg.id}", headers=operator_headers)
        assert resp.status_code == 204

    def test_requires_valid_token(self, client, db):
        cfg = _make_config(db, name="cfg-del-noauth")
        resp = client.delete(f"/api/benchmarks/configs/{cfg.id}")
        assert resp.status_code == 401

    def test_not_found(self, client, operator_headers, db):
        resp = client.delete("/api/benchmarks/configs/99999", headers=operator_headers)
        assert resp.status_code == 404


# ============================================================================
# 3. Run Endpoints
# ============================================================================

class TestListBenchmarkRuns:
    """GET /api/benchmarks/runs (require_viewer)."""

    def test_happy_path(self, client, viewer_headers, all_test_users, db):
        _make_run(db)
        resp = client.get("/api/benchmarks/runs", headers=viewer_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "runs" in data
        assert "total" in data
        assert "limit" in data
        assert "offset" in data
        assert len(data["runs"]) >= 1

    def test_requires_auth(self, client):
        resp = client.get("/api/benchmarks/runs")
        assert resp.status_code == 401

    def test_filter_by_proxy(self, client, viewer_headers, all_test_users, db):
        _make_run(db, proxy="envoy", model="m1")
        _make_run(db, proxy="nginx", model="m2")
        resp = client.get("/api/benchmarks/runs?proxy=envoy", headers=viewer_headers)
        data = resp.json()
        assert all(r["proxy"] == "envoy" for r in data["runs"])

    def test_response_contract(self, client, viewer_headers, all_test_users, db):
        _make_run(db)
        resp = client.get("/api/benchmarks/runs", headers=viewer_headers)
        data = resp.json()
        assert set(data.keys()) == {"runs", "total", "limit", "offset"}
        if data["runs"]:
            run = data["runs"][0]
            for key in ("id", "tool", "proxy", "model", "base_url", "status",
                        "latency_p50", "latency_p99", "overall_rps", "created_at"):
                assert key in run


class TestGetBenchmarkRun:
    """GET /api/benchmarks/runs/{run_id} (require_viewer)."""

    def test_happy_path(self, client, viewer_headers, all_test_users, db):
        run = _make_run(db)
        resp = client.get(f"/api/benchmarks/runs/{run.id}", headers=viewer_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == run.id
        assert data["proxy"] == "nodeport"

    def test_requires_auth(self, client, db):
        run = _make_run(db)
        resp = client.get(f"/api/benchmarks/runs/{run.id}")
        assert resp.status_code == 401

    def test_not_found(self, client, viewer_headers, all_test_users, db):
        resp = client.get("/api/benchmarks/runs/99999", headers=viewer_headers)
        assert resp.status_code == 404

    def test_detail_response_contract(self, client, viewer_headers, all_test_users, db):
        run = _make_run(db, result_json={"some": "data"})
        resp = client.get(f"/api/benchmarks/runs/{run.id}", headers=viewer_headers)
        data = resp.json()
        assert "result_json" in data
        assert "config_snapshot" in data

    def test_linkage_fk_fields_exposed_in_get_response(
        self, client, viewer_headers, all_test_users, make_k8s_cluster, db
    ):
        """GET /api/benchmarks/runs/{id} must expose target_id and proxy_deployment_id.

        The FK columns are populated by the aiperf ingest route (via linkage query
        params). This test verifies that the run response schema serialises them so
        the linkage is visible to the UI / API consumers — the root cause of PRD-11
        linkage being invisible despite the DB columns being set.
        """
        cluster = make_k8s_cluster(name="run-linkage-cluster")
        target = _make_target(db, cluster.id, name="run-linkage-target")
        proxy = _make_proxy(db, target.id, proxy_type="bnk")

        run = _make_run(db, target_id=target.id)
        # Patch proxy_deployment_id directly — _make_run doesn't expose it yet
        run.proxy_deployment_id = proxy.id
        db.commit()
        db.refresh(run)

        resp = client.get(f"/api/benchmarks/runs/{run.id}", headers=viewer_headers)
        assert resp.status_code == 200
        data = resp.json()

        assert data["target_id"] == target.id
        assert data["proxy_deployment_id"] == proxy.id


class TestCreateBenchmarkRun:
    """POST /api/benchmarks/runs — no route-level auth dep."""

    def test_happy_path(self, client, operator_headers, db):
        payload = {
            "tool": "aiperf",
            "proxy": "envoy",
            "model": "tinyllama",
            "base_url": "http://vllm:8000",
        }
        resp = client.post("/api/benchmarks/runs", json=payload, headers=operator_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["model"] == "tinyllama"
        assert data["status"] == "pending"

    def test_requires_valid_token(self, client, db):
        payload = {"model": "t", "base_url": "http://x"}
        resp = client.post("/api/benchmarks/runs", json=payload)
        assert resp.status_code == 401

    def test_response_contract(self, client, operator_headers, db):
        payload = {"model": "t2", "base_url": "http://x"}
        resp = client.post("/api/benchmarks/runs", json=payload, headers=operator_headers)
        data = resp.json()
        for key in ("id", "tool", "proxy", "model", "base_url", "status", "created_at"):
            assert key in data


class TestCancelBenchmarkRun:
    """POST /api/benchmarks/runs/{run_id}/cancel — no route-level auth dep."""

    def test_happy_path(self, client, operator_headers, db):
        run = _make_run(db, status="pending")
        resp = client.post(f"/api/benchmarks/runs/{run.id}/cancel", headers=operator_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_requires_valid_token(self, client, db):
        run = _make_run(db, status="running")
        resp = client.post(f"/api/benchmarks/runs/{run.id}/cancel")
        assert resp.status_code == 401

    def test_cancel_terminal_run_fails(self, client, operator_headers, db):
        run = _make_run(db, status="completed")
        resp = client.post(f"/api/benchmarks/runs/{run.id}/cancel", headers=operator_headers)
        assert resp.status_code == 400

    def test_not_found(self, client, operator_headers, db):
        resp = client.post("/api/benchmarks/runs/99999/cancel", headers=operator_headers)
        assert resp.status_code == 404

    def test_cancel_dispatches_cancel_command_to_agent(self, client, operator_headers, db):
        # Regression: cancel must terminate the agent's aiperf, not just flip the
        # DB row — otherwise the run shows "cancelled" while load keeps running.
        agent = _make_agent(db, name="cancel-agent", status="connected")
        run = _make_run(db, status="running", agent_id=agent.id)
        with patch("routes.benchmarks.dispatch_to_agent", return_value=True) as mock_dispatch:
            resp = client.post(f"/api/benchmarks/runs/{run.id}/cancel", headers=operator_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"
        mock_dispatch.assert_called_once_with(agent.id, {"type": "cancel", "run_id": run.id})

    def test_cancel_run_without_agent_does_not_dispatch(self, client, operator_headers, db):
        run = _make_run(db, status="pending", agent_id=None)
        with patch("routes.benchmarks.dispatch_to_agent") as mock_dispatch:
            resp = client.post(f"/api/benchmarks/runs/{run.id}/cancel", headers=operator_headers)
        assert resp.status_code == 200
        mock_dispatch.assert_not_called()


class TestDeleteBenchmarkRun:
    """DELETE /api/benchmarks/runs/{run_id} — no route-level auth dep."""

    def test_happy_path(self, client, operator_headers, db):
        run = _make_run(db, status="completed")
        resp = client.delete(f"/api/benchmarks/runs/{run.id}", headers=operator_headers)
        assert resp.status_code == 204

    def test_requires_valid_token(self, client, db):
        run = _make_run(db, status="failed")
        resp = client.delete(f"/api/benchmarks/runs/{run.id}")
        assert resp.status_code == 401

    def test_cannot_delete_running(self, client, operator_headers, db):
        run = _make_run(db, status="running")
        resp = client.delete(f"/api/benchmarks/runs/{run.id}", headers=operator_headers)
        assert resp.status_code == 400

    def test_not_found(self, client, operator_headers, db):
        resp = client.delete("/api/benchmarks/runs/99999", headers=operator_headers)
        assert resp.status_code == 404


# ============================================================================
# 4. Agent Endpoints
# ============================================================================

class TestRegisterBenchmarkAgent:
    """POST /api/benchmarks/agents — no route-level auth dep."""

    def test_happy_path(self, client, operator_headers, db):
        payload = {"name": "new-agent", "hostname": "host-01", "ip_address": "10.0.0.5"}
        resp = client.post("/api/benchmarks/agents", json=payload, headers=operator_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "new-agent"
        assert data["status"] == "connected"

    def test_requires_valid_token(self, client, db):
        resp = client.post("/api/benchmarks/agents", json={"name": "noauth-agent"})
        assert resp.status_code == 401

    def test_upsert_existing_agent(self, client, operator_headers, db):
        _make_agent(db, name="upsert-agent", hostname="old-host", status="disconnected")
        payload = {"name": "upsert-agent", "hostname": "new-host"}
        resp = client.post("/api/benchmarks/agents", json=payload, headers=operator_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["hostname"] == "new-host"
        assert data["status"] == "connected"

    def test_response_contract(self, client, operator_headers, db):
        resp = client.post("/api/benchmarks/agents", json={"name": "contract-agent"}, headers=operator_headers)
        data = resp.json()
        for key in ("id", "name", "hostname", "ip_address", "status", "created_at", "updated_at"):
            assert key in data


class TestListBenchmarkAgents:
    """GET /api/benchmarks/agents (require_viewer)."""

    def test_happy_path(self, client, viewer_headers, all_test_users, db):
        _make_agent(db, name="agent-list-1")
        _make_agent(db, name="agent-list-2")
        resp = client.get("/api/benchmarks/agents", headers=viewer_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 2

    def test_requires_auth(self, client):
        resp = client.get("/api/benchmarks/agents")
        assert resp.status_code == 401

    def test_response_contract(self, client, viewer_headers, all_test_users, db):
        _make_agent(db, name="agent-contract")
        resp = client.get("/api/benchmarks/agents", headers=viewer_headers)
        item = resp.json()[0]
        for key in ("id", "name", "status", "created_at"):
            assert key in item


class TestGetBenchmarkAgent:
    """GET /api/benchmarks/agents/{agent_id} (require_viewer)."""

    def test_happy_path(self, client, viewer_headers, all_test_users, db):
        agent = _make_agent(db, name="agent-get")
        resp = client.get(f"/api/benchmarks/agents/{agent.id}", headers=viewer_headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "agent-get"

    def test_requires_auth(self, client, db):
        agent = _make_agent(db, name="agent-get-noauth")
        resp = client.get(f"/api/benchmarks/agents/{agent.id}")
        assert resp.status_code == 401

    def test_not_found(self, client, viewer_headers, all_test_users, db):
        resp = client.get("/api/benchmarks/agents/99999", headers=viewer_headers)
        assert resp.status_code == 404


class TestDeleteBenchmarkAgent:
    """DELETE /api/benchmarks/agents/{agent_id} — no route-level auth dep."""

    def test_happy_path(self, client, operator_headers, db):
        agent = _make_agent(db, name="agent-del")
        resp = client.delete(f"/api/benchmarks/agents/{agent.id}", headers=operator_headers)
        assert resp.status_code == 204

    def test_requires_valid_token(self, client, db):
        agent = _make_agent(db, name="agent-del-noauth")
        resp = client.delete(f"/api/benchmarks/agents/{agent.id}")
        assert resp.status_code == 401

    def test_not_found(self, client, operator_headers, db):
        resp = client.delete("/api/benchmarks/agents/99999", headers=operator_headers)
        assert resp.status_code == 404


# ============================================================================
# 5. Comparison & Summary
# ============================================================================

class TestCompareBenchmarkRuns:
    """POST /api/benchmarks/compare (require_viewer)."""

    def test_happy_path(self, client, viewer_headers, all_test_users, db):
        r1 = _make_run(db, proxy="envoy", model="m1")
        r2 = _make_run(db, proxy="nginx", model="m2")
        resp = client.post(
            "/api/benchmarks/compare",
            json={"run_ids": [r1.id, r2.id]},
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "runs" in data
        assert "winners" in data
        assert len(data["runs"]) == 2

    def test_requires_auth(self, client, db):
        r1 = _make_run(db, model="c1")
        r2 = _make_run(db, model="c2")
        resp = client.post(
            "/api/benchmarks/compare",
            json={"run_ids": [r1.id, r2.id]},
        )
        assert resp.status_code == 401

    def test_missing_run_returns_404(self, client, viewer_headers, all_test_users, db):
        r1 = _make_run(db, model="c3")
        resp = client.post(
            "/api/benchmarks/compare",
            json={"run_ids": [r1.id, 99999]},
            headers=viewer_headers,
        )
        assert resp.status_code == 404

    def test_response_contract(self, client, viewer_headers, all_test_users, db):
        r1 = _make_run(db, proxy="envoy", model="mc1")
        r2 = _make_run(db, proxy="nginx", model="mc2")
        resp = client.post(
            "/api/benchmarks/compare",
            json={"run_ids": [r1.id, r2.id]},
            headers=viewer_headers,
        )
        data = resp.json()
        run_entry = data["runs"][0]
        for key in ("run_id", "proxy", "model", "tool", "status",
                     "latency_p50", "latency_p99", "overall_rps"):
            assert key in run_entry


class TestBenchmarkSummary:
    """GET /api/benchmarks/summary (require_viewer)."""

    def test_happy_path(self, client, viewer_headers, all_test_users, db):
        _make_run(db)
        resp = client.get("/api/benchmarks/summary", headers=viewer_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_runs"] >= 1

    def test_requires_auth(self, client):
        resp = client.get("/api/benchmarks/summary")
        assert resp.status_code == 401

    def test_empty_db_returns_zeroes(self, client, viewer_headers, all_test_users, db):
        resp = client.get("/api/benchmarks/summary", headers=viewer_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_runs"] >= 0

    def test_response_contract(self, client, viewer_headers, all_test_users, db):
        resp = client.get("/api/benchmarks/summary", headers=viewer_headers)
        data = resp.json()
        for key in ("total_runs", "completed_runs", "failed_runs", "running_count",
                     "avg_latency_p50", "avg_rps", "avg_success_rate", "last_run_at",
                     "runs_last_7d", "runs_by_proxy", "runs_by_tool"):
            assert key in data


# ============================================================================
# 6. Benchmark Target Endpoints
# ============================================================================

class TestListBenchmarkTargets:
    """GET /api/benchmarks/targets (require_viewer)."""

    def test_happy_path(self, client, viewer_headers, all_test_users, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="tgt-list-cluster")
        _make_target(db, cluster.id, name="tgt-list-1")
        _make_target(db, cluster.id, name="tgt-list-2")
        resp = client.get("/api/benchmarks/targets", headers=viewer_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "targets" in data
        assert "total" in data
        assert data["total"] >= 2

    def test_requires_auth(self, client):
        resp = client.get("/api/benchmarks/targets")
        assert resp.status_code == 401

    def test_filter_by_cluster_id(self, client, viewer_headers, all_test_users, make_k8s_cluster, db):
        c1 = make_k8s_cluster(name="tgt-filter-c1")
        c2 = make_k8s_cluster(name="tgt-filter-c2")
        _make_target(db, c1.id, name="tgt-c1")
        _make_target(db, c2.id, name="tgt-c2")
        resp = client.get(f"/api/benchmarks/targets?cluster_id={c1.id}", headers=viewer_headers)
        data = resp.json()
        assert all(t["cluster_id"] == c1.id for t in data["targets"])

    def test_response_contract(self, client, viewer_headers, all_test_users, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="tgt-contract-cluster")
        _make_target(db, cluster.id, name="tgt-contract")
        resp = client.get("/api/benchmarks/targets", headers=viewer_headers)
        item = resp.json()["targets"][0]
        for key in ("id", "name", "cluster_id", "llm_base_url", "llm_model",
                     "status", "created_at", "updated_at"):
            assert key in item


class TestGetBenchmarkTarget:
    """GET /api/benchmarks/targets/{target_id} (require_viewer)."""

    def test_happy_path(self, client, viewer_headers, all_test_users, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="tgt-get-cluster")
        target = _make_target(db, cluster.id, name="tgt-get")
        resp = client.get(f"/api/benchmarks/targets/{target.id}", headers=viewer_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == target.id
        assert data["name"] == "tgt-get"

    def test_requires_auth(self, client, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="tgt-get-noauth-cluster")
        target = _make_target(db, cluster.id, name="tgt-get-noauth")
        resp = client.get(f"/api/benchmarks/targets/{target.id}")
        assert resp.status_code == 401

    def test_not_found(self, client, viewer_headers, all_test_users, db):
        resp = client.get("/api/benchmarks/targets/99999", headers=viewer_headers)
        assert resp.status_code == 404

    def test_detail_includes_proxy_deployments(self, client, viewer_headers, all_test_users, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="tgt-detail-cluster")
        target = _make_target(db, cluster.id, name="tgt-detail")
        _make_proxy(db, target.id, proxy_type="envoy")
        resp = client.get(f"/api/benchmarks/targets/{target.id}", headers=viewer_headers)
        data = resp.json()
        assert "proxy_deployments" in data
        assert len(data["proxy_deployments"]) >= 1


class TestCreateBenchmarkTarget:
    """POST /api/benchmarks/targets — no route-level auth dep."""

    def test_happy_path(self, client, operator_headers, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="tgt-create-cluster")
        payload = {
            "name": "new-target",
            "cluster_id": cluster.id,
            "llm_base_url": "http://vllm:8000",
            "llm_model": "tinyllama",
        }
        resp = client.post("/api/benchmarks/targets", json=payload, headers=operator_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "new-target"
        assert data["cluster_id"] == cluster.id

    def test_requires_valid_token(self, client, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="tgt-noauth-cluster")
        payload = {
            "name": "noauth-target",
            "cluster_id": cluster.id,
            "llm_base_url": "http://x",
            "llm_model": "m",
        }
        resp = client.post("/api/benchmarks/targets", json=payload)
        assert resp.status_code == 401

    def test_duplicate_name_conflict(self, client, operator_headers, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="tgt-dup-cluster")
        _make_target(db, cluster.id, name="dup-target")
        payload = {
            "name": "dup-target",
            "cluster_id": cluster.id,
            "llm_base_url": "http://x",
            "llm_model": "m",
        }
        resp = client.post("/api/benchmarks/targets", json=payload, headers=operator_headers)
        assert resp.status_code == 409

    def test_invalid_cluster_id(self, client, operator_headers, db):
        payload = {
            "name": "bad-cluster-target",
            "cluster_id": 99999,
            "llm_base_url": "http://x",
            "llm_model": "m",
        }
        resp = client.post("/api/benchmarks/targets", json=payload, headers=operator_headers)
        assert resp.status_code == 404

    def test_response_contract(self, client, operator_headers, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="tgt-contract-create-cluster")
        payload = {
            "name": "contract-target",
            "cluster_id": cluster.id,
            "llm_base_url": "http://x",
            "llm_model": "m",
        }
        resp = client.post("/api/benchmarks/targets", json=payload, headers=operator_headers)
        data = resp.json()
        for key in ("id", "name", "cluster_id", "llm_base_url", "llm_model",
                     "llm_namespace", "llm_endpoint", "proxy_namespace", "status",
                     "created_at", "updated_at"):
            assert key in data


class TestUpdateBenchmarkTarget:
    """PUT /api/benchmarks/targets/{target_id} — no route-level auth dep."""

    def test_happy_path(self, client, operator_headers, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="tgt-update-cluster")
        target = _make_target(db, cluster.id, name="tgt-update")
        resp = client.put(
            f"/api/benchmarks/targets/{target.id}",
            json={"name": "tgt-updated", "llm_model": "llama-70b"},
            headers=operator_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "tgt-updated"
        assert data["llm_model"] == "llama-70b"

    def test_requires_valid_token(self, client, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="tgt-update-noauth-cluster")
        target = _make_target(db, cluster.id, name="tgt-update-noauth")
        resp = client.put(
            f"/api/benchmarks/targets/{target.id}",
            json={"description": "updated"},
        )
        assert resp.status_code == 401

    def test_not_found(self, client, operator_headers, db):
        resp = client.put("/api/benchmarks/targets/99999", json={"name": "x"}, headers=operator_headers)
        assert resp.status_code == 404


class TestDeleteBenchmarkTarget:
    """DELETE /api/benchmarks/targets/{target_id} — no route-level auth dep."""

    def test_happy_path(self, client, operator_headers, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="tgt-del-cluster")
        target = _make_target(db, cluster.id, name="tgt-del")
        resp = client.delete(f"/api/benchmarks/targets/{target.id}", headers=operator_headers)
        assert resp.status_code == 204

    def test_requires_valid_token(self, client, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="tgt-del-noauth-cluster")
        target = _make_target(db, cluster.id, name="tgt-del-noauth")
        resp = client.delete(f"/api/benchmarks/targets/{target.id}")
        assert resp.status_code == 401

    def test_not_found(self, client, operator_headers, db):
        resp = client.delete("/api/benchmarks/targets/99999", headers=operator_headers)
        assert resp.status_code == 404


class TestValidateBenchmarkTarget:
    """POST /api/benchmarks/targets/{target_id}/validate — no route-level auth dep."""

    def test_happy_path(self, client, operator_headers, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="tgt-validate-cluster")
        target = _make_target(db, cluster.id, name="tgt-validate")
        # Target validation probes the LLM endpoint via HTTP + TCP. In the test
        # container the hostname (vllm.default) doesn't resolve, so stub the
        # TCP fallback to succeed.
        with patch("socket.create_connection", return_value=MagicMock()):
            resp = client.post(f"/api/benchmarks/targets/{target.id}/validate", headers=operator_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "active"
        assert data["last_validated"] is not None

    def test_requires_valid_token(self, client, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="tgt-validate-noauth-cluster")
        target = _make_target(db, cluster.id, name="tgt-validate-noauth")
        resp = client.post(f"/api/benchmarks/targets/{target.id}/validate")
        assert resp.status_code == 401

    def test_not_found(self, client, operator_headers, db):
        resp = client.post("/api/benchmarks/targets/99999/validate", headers=operator_headers)
        assert resp.status_code == 404


# ============================================================================
# 7. Target Discovery
# ============================================================================

class TestDiscoverTargets:
    """POST /api/benchmarks/discover-targets — no route-level auth dep.

    Uses lazy import of TargetDiscoveryService inside the route function,
    so we patch at the module where it's imported from.
    """

    @patch("services.target_discovery_service.TargetDiscoveryService")
    def test_happy_path(self, mock_svc_cls, client, operator_headers, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="discover-cluster")
        mock_svc = mock_svc_cls.return_value
        mock_svc.discover_targets.return_value = {
            "cluster_id": cluster.id,
            "cluster_name": "discover-cluster",
            "discovered_services": [],
            "discovered_count": 0,
            "created_targets": [],
            "proxy_results": [],
            "created_configs": [],
        }
        payload = {"cluster_id": cluster.id}
        resp = client.post("/api/benchmarks/discover-targets", json=payload, headers=operator_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["cluster_id"] == cluster.id
        assert "discovered_services" in data
        assert "discovered_count" in data

    @patch("services.target_discovery_service.TargetDiscoveryService")
    def test_requires_valid_token(self, mock_svc_cls, client, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="discover-noauth-cluster")
        resp = client.post("/api/benchmarks/discover-targets", json={"cluster_id": cluster.id})
        assert resp.status_code == 401

    @patch("services.target_discovery_service.TargetDiscoveryService")
    def test_response_contract(self, mock_svc_cls, client, operator_headers, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="discover-contract-cluster")
        mock_svc = mock_svc_cls.return_value
        mock_svc.discover_targets.return_value = {
            "cluster_id": cluster.id,
            "cluster_name": "discover-contract-cluster",
            "discovered_services": [
                {
                    "service_name": "vllm-svc",
                    "namespace": "default",
                    "base_url": "http://vllm:8000",
                    "port": 8000,
                    "confidence": "high",
                    "reason": "Name contains vllm",
                    "model_hint": "tinyllama",
                    "gpu_count": 1,
                    "image": "vllm/vllm:latest",
                }
            ],
            "discovered_count": 1,
            "created_targets": [],
            "proxy_results": [],
            "created_configs": [],
        }
        resp = client.post("/api/benchmarks/discover-targets", json={"cluster_id": cluster.id}, headers=operator_headers)
        data = resp.json()
        for key in ("cluster_id", "cluster_name", "discovered_services",
                     "discovered_count", "created_targets", "proxy_results"):
            assert key in data


# ============================================================================
# 8. Proxy Discovery
# ============================================================================

class TestDiscoverProxies:
    """POST /api/benchmarks/targets/{target_id}/discover-proxies — no route-level auth dep.

    Uses lazy import of ProxyDiscoveryService, and also instantiates
    BenchmarkTargetService at module level. We need to patch both.
    """

    @patch("services.proxy_discovery_service.ProxyDiscoveryService")
    @patch("routes.benchmarks.BenchmarkTargetService")
    def test_happy_path(self, mock_target_svc_cls, mock_disc_cls, client, operator_headers, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="proxy-disc-cluster")
        target = _make_target(db, cluster.id, name="proxy-disc-target")

        mock_target_svc = mock_target_svc_cls.return_value
        mock_target_svc.get_target.return_value = target

        mock_disc = mock_disc_cls.return_value
        mock_disc.discover_all.return_value = [
            {"proxy_type": "envoy", "found": True, "proxy_url": "http://envoy:10080"},
            {"proxy_type": "nginx", "found": False},
        ]

        resp = client.post(f"/api/benchmarks/targets/{target.id}/discover-proxies", headers=operator_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["target_id"] == target.id
        assert data["discovered_count"] == 1
        assert data["total_scanned"] == 2

    @patch("services.proxy_discovery_service.ProxyDiscoveryService")
    @patch("routes.benchmarks.BenchmarkTargetService")
    def test_requires_valid_token(self, mock_target_svc_cls, mock_disc_cls, client, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="proxy-disc-noauth-cluster")
        target = _make_target(db, cluster.id, name="proxy-disc-noauth")
        resp = client.post(f"/api/benchmarks/targets/{target.id}/discover-proxies")
        assert resp.status_code == 401

    @patch("services.proxy_discovery_service.ProxyDiscoveryService")
    @patch("routes.benchmarks.BenchmarkTargetService")
    def test_response_contract(self, mock_target_svc_cls, mock_disc_cls, client, operator_headers, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="proxy-disc-contract-cluster")
        target = _make_target(db, cluster.id, name="proxy-disc-contract-target")

        mock_target_svc = mock_target_svc_cls.return_value
        mock_target_svc.get_target.return_value = target

        mock_disc = mock_disc_cls.return_value
        mock_disc.discover_all.return_value = []

        resp = client.post(f"/api/benchmarks/targets/{target.id}/discover-proxies", headers=operator_headers)
        data = resp.json()
        for key in ("target_id", "target_name", "cluster_id", "results",
                     "discovered_count", "total_scanned"):
            assert key in data


# ============================================================================
# 9. Proxy Deployment Endpoints
# ============================================================================

class TestListProxyDeployments:
    """GET /api/benchmarks/targets/{target_id}/proxies (require_viewer)."""

    def test_happy_path(self, client, viewer_headers, all_test_users, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="proxy-list-cluster")
        target = _make_target(db, cluster.id, name="proxy-list-target")
        _make_proxy(db, target.id, proxy_type="envoy")
        _make_proxy(db, target.id, proxy_type="nginx")
        resp = client.get(
            f"/api/benchmarks/targets/{target.id}/proxies",
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 2

    def test_requires_auth(self, client, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="proxy-list-noauth-cluster")
        target = _make_target(db, cluster.id, name="proxy-list-noauth")
        resp = client.get(f"/api/benchmarks/targets/{target.id}/proxies")
        assert resp.status_code == 401

    def test_response_contract(self, client, viewer_headers, all_test_users, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="proxy-contract-cluster")
        target = _make_target(db, cluster.id, name="proxy-contract-target")
        _make_proxy(db, target.id)
        resp = client.get(
            f"/api/benchmarks/targets/{target.id}/proxies",
            headers=viewer_headers,
        )
        item = resp.json()[0]
        for key in ("id", "target_id", "proxy_type", "status", "proxy_url",
                     "created_at", "updated_at"):
            assert key in item


class TestGetProxyDeployment:
    """GET /api/benchmarks/targets/{target_id}/proxies/{proxy_id} (require_viewer)."""

    def test_happy_path(self, client, viewer_headers, all_test_users, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="proxy-get-cluster")
        target = _make_target(db, cluster.id, name="proxy-get-target")
        proxy = _make_proxy(db, target.id)
        resp = client.get(
            f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}",
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == proxy.id

    def test_requires_auth(self, client, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="proxy-get-noauth-cluster")
        target = _make_target(db, cluster.id, name="proxy-get-noauth")
        proxy = _make_proxy(db, target.id)
        resp = client.get(f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}")
        assert resp.status_code == 401

    def test_not_found(self, client, viewer_headers, all_test_users, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="proxy-get-404-cluster")
        target = _make_target(db, cluster.id, name="proxy-get-404")
        resp = client.get(
            f"/api/benchmarks/targets/{target.id}/proxies/99999",
            headers=viewer_headers,
        )
        assert resp.status_code == 404


class TestDeployProxy:
    """POST /api/benchmarks/targets/{target_id}/proxies — no route-level auth dep.

    The route does a lazy import: `from tasks.proxy_deploy_tasks import deploy_proxy_task`
    We patch at the source module path.
    """

    @patch("tasks.proxy_deploy_tasks.deploy_proxy_task")
    def test_happy_path(self, mock_task, client, operator_headers, make_k8s_cluster, db):
        mock_task.delay.return_value = MagicMock(id="celery-deploy-001")
        cluster = make_k8s_cluster(name="proxy-deploy-cluster")
        target = _make_target(db, cluster.id, name="proxy-deploy-target")
        payload = {"proxy_type": "envoy"}
        resp = client.post(
            f"/api/benchmarks/targets/{target.id}/proxies",
            json=payload,
            headers=operator_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["proxy_type"] == "envoy"
        assert data["status"] == "pending"
        assert data["celery_task_id"] == "celery-deploy-001"

    @patch("tasks.proxy_deploy_tasks.deploy_proxy_task")
    def test_requires_valid_token(self, mock_task, client, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="proxy-deploy-noauth-cluster")
        target = _make_target(db, cluster.id, name="proxy-deploy-noauth")
        resp = client.post(
            f"/api/benchmarks/targets/{target.id}/proxies",
            json={"proxy_type": "nginx"},
        )
        assert resp.status_code == 401

    @patch("tasks.proxy_deploy_tasks.deploy_proxy_task")
    def test_duplicate_proxy_type_conflict(self, mock_task, client, operator_headers, make_k8s_cluster, db):
        mock_task.delay.return_value = MagicMock(id="celery-dup")
        cluster = make_k8s_cluster(name="proxy-dup-cluster")
        target = _make_target(db, cluster.id, name="proxy-dup-target")
        _make_proxy(db, target.id, proxy_type="envoy")
        resp = client.post(
            f"/api/benchmarks/targets/{target.id}/proxies",
            json={"proxy_type": "envoy"},
            headers=operator_headers,
        )
        assert resp.status_code == 409

    @patch("tasks.proxy_deploy_tasks.deploy_proxy_task")
    def test_response_contract(self, mock_task, client, operator_headers, make_k8s_cluster, db):
        mock_task.delay.return_value = MagicMock(id="celery-contract")
        cluster = make_k8s_cluster(name="proxy-contract-deploy-cluster")
        target = _make_target(db, cluster.id, name="proxy-contract-deploy")
        resp = client.post(
            f"/api/benchmarks/targets/{target.id}/proxies",
            json={"proxy_type": "haproxy"},
            headers=operator_headers,
        )
        data = resp.json()
        for key in ("id", "target_id", "proxy_type", "status", "helm_release",
                     "helm_chart", "created_at", "updated_at"):
            assert key in data

    @patch("tasks.proxy_deploy_tasks.deploy_proxy_task")
    def test_envoy_ai_gateway_uses_default_chart(self, mock_task, client, operator_headers, make_k8s_cluster, db):
        from services.benchmark_target_service import DEFAULT_HELM_CHARTS
        mock_task.delay.return_value = MagicMock(id="celery-aigw")
        cluster = make_k8s_cluster(name="proxy-aigw-cluster")
        target = _make_target(db, cluster.id, name="proxy-aigw-target")
        resp = client.post(
            f"/api/benchmarks/targets/{target.id}/proxies",
            json={"proxy_type": "envoy-ai-gateway"},
            headers=operator_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["proxy_type"] == "envoy-ai-gateway"
        assert data["status"] == "pending"
        assert data["celery_task_id"] == "celery-aigw"
        defaults = DEFAULT_HELM_CHARTS["envoy-ai-gateway"]
        assert data["helm_chart"] == defaults["chart"]
        assert data["helm_version"] == defaults["version"]

    @patch("tasks.proxy_deploy_tasks.deploy_proxy_task")
    def test_llm_d_router_uses_default_chart(self, mock_task, client, operator_headers, make_k8s_cluster, db):
        from services.benchmark_target_service import DEFAULT_HELM_CHARTS
        mock_task.delay.return_value = MagicMock(id="celery-llmd")
        cluster = make_k8s_cluster(name="proxy-llmd-cluster")
        target = _make_target(db, cluster.id, name="proxy-llmd-target")
        resp = client.post(
            f"/api/benchmarks/targets/{target.id}/proxies",
            json={"proxy_type": "llm-d-router"},
            headers=operator_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["proxy_type"] == "llm-d-router"
        assert data["status"] == "pending"
        assert data["celery_task_id"] == "celery-llmd"
        defaults = DEFAULT_HELM_CHARTS["llm-d-router"]
        assert data["helm_chart"] == defaults["chart"]
        assert data["helm_version"] == defaults["version"]


class TestUpdateProxyDeployment:
    """PUT /api/benchmarks/targets/{target_id}/proxies/{proxy_id} — no route-level auth dep."""

    def test_happy_path(self, client, operator_headers, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="proxy-update-cluster")
        target = _make_target(db, cluster.id, name="proxy-update-target")
        proxy = _make_proxy(db, target.id)
        resp = client.put(
            f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}",
            json={"proxy_url": "http://new-envoy:10080", "status": "ready"},
            headers=operator_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["proxy_url"] == "http://new-envoy:10080"

    def test_requires_valid_token(self, client, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="proxy-update-noauth-cluster")
        target = _make_target(db, cluster.id, name="proxy-update-noauth")
        proxy = _make_proxy(db, target.id)
        resp = client.put(
            f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}",
            json={"status_message": "Updated"},
        )
        assert resp.status_code == 401

    def test_not_found(self, client, operator_headers, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="proxy-update-404-cluster")
        target = _make_target(db, cluster.id, name="proxy-update-404")
        resp = client.put(
            f"/api/benchmarks/targets/{target.id}/proxies/99999",
            json={"status": "ready"},
            headers=operator_headers,
        )
        assert resp.status_code == 404


class TestDeleteProxyDeployment:
    """DELETE /api/benchmarks/targets/{target_id}/proxies/{proxy_id} — no route-level auth dep.

    Returns 202 Accepted. Pending/uninstalled proxies deleted immediately;
    ready proxies dispatch async Helm uninstall.
    """

    def test_pending_proxy_deleted_immediately(self, client, operator_headers, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="proxy-del-pending-cluster")
        target = _make_target(db, cluster.id, name="proxy-del-pending")
        proxy = _make_proxy(db, target.id, status="pending")
        resp = client.delete(
            f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}",
            headers=operator_headers,
        )
        assert resp.status_code == 202

    @patch("tasks.proxy_deploy_tasks.undeploy_proxy_task")
    def test_ready_proxy_dispatches_undeploy(self, mock_task, client, operator_headers, make_k8s_cluster, db):
        mock_task.delay.return_value = MagicMock(id="celery-undeploy-001")
        cluster = make_k8s_cluster(name="proxy-del-ready-cluster")
        target = _make_target(db, cluster.id, name="proxy-del-ready")
        proxy = _make_proxy(db, target.id, status="ready")
        resp = client.delete(
            f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}",
            headers=operator_headers,
        )
        assert resp.status_code == 202
        mock_task.delay.assert_called_once_with(proxy.id)

    def test_uninstalled_proxy_deleted_immediately(self, client, operator_headers, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="proxy-del-uninstalled-cluster")
        target = _make_target(db, cluster.id, name="proxy-del-uninstalled")
        proxy = _make_proxy(db, target.id, status="uninstalled")
        resp = client.delete(
            f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}",
            headers=operator_headers,
        )
        assert resp.status_code == 202

    def test_requires_valid_token(self, client, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="proxy-del-noauth-cluster")
        target = _make_target(db, cluster.id, name="proxy-del-noauth")
        proxy = _make_proxy(db, target.id, status="pending")
        resp = client.delete(f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}")
        assert resp.status_code == 401

    def test_not_found(self, client, operator_headers, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="proxy-del-404-cluster")
        target = _make_target(db, cluster.id, name="proxy-del-404")
        resp = client.delete(
            f"/api/benchmarks/targets/{target.id}/proxies/99999",
            headers=operator_headers,
        )
        assert resp.status_code == 404


class TestRedeployProxy:
    """POST /api/benchmarks/targets/{target_id}/proxies/{proxy_id}/redeploy — no route-level auth dep."""

    @patch("tasks.proxy_deploy_tasks.deploy_proxy_task")
    def test_happy_path(self, mock_task, client, operator_headers, make_k8s_cluster, db):
        mock_task.delay.return_value = MagicMock(id="celery-redeploy-001")
        cluster = make_k8s_cluster(name="proxy-redeploy-cluster")
        target = _make_target(db, cluster.id, name="proxy-redeploy-target")
        proxy = _make_proxy(db, target.id, status="ready")
        resp = client.post(
            f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}/redeploy",
            headers=operator_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pending"
        assert data["celery_task_id"] == "celery-redeploy-001"

    @patch("tasks.proxy_deploy_tasks.deploy_proxy_task")
    def test_requires_valid_token(self, mock_task, client, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="proxy-redeploy-noauth-cluster")
        target = _make_target(db, cluster.id, name="proxy-redeploy-noauth")
        proxy = _make_proxy(db, target.id)
        resp = client.post(
            f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}/redeploy",
        )
        assert resp.status_code == 401

    @patch("tasks.proxy_deploy_tasks.deploy_proxy_task")
    def test_not_found(self, mock_task, client, operator_headers, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="proxy-redeploy-404-cluster")
        target = _make_target(db, cluster.id, name="proxy-redeploy-404")
        resp = client.post(
            f"/api/benchmarks/targets/{target.id}/proxies/99999/redeploy",
            headers=operator_headers,
        )
        assert resp.status_code == 404


# ============================================================================
# 10. Proxy Task Status
# ============================================================================

class TestBenchmarkProxyTaskStatus:
    """GET /api/benchmarks/targets/{target_id}/proxies/{proxy_id}/task-status (require_viewer)."""

    @patch("routes.benchmarks.BenchmarkTargetService")
    @patch("celery_app.celery_app.AsyncResult")
    def test_returns_contract_shape_with_celery_state(
        self,
        mock_async_result,
        mock_service_cls,
        client,
        viewer_headers,
        all_test_users,
        make_k8s_cluster,
        db,
    ):
        cluster = make_k8s_cluster(name="task-status-cluster-a")
        target = _make_target(db, cluster.id, name="task-status-target-a")
        proxy = _make_proxy(
            db, target.id, status="deploying",
            status_message="Deploying chart",
            proxy_url=None,
            celery_task_id="celery-task-123",
        )

        svc = mock_service_cls.return_value
        svc.get_proxy_deployment.return_value = proxy

        mock_async_result.return_value.state = "STARTED"

        resp = client.get(
            f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}/task-status",
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        data = resp.json()

        assert set(data.keys()) == {
            "proxy_id", "status", "status_message",
            "proxy_url", "celery_task_id", "celery_state",
        }
        assert data["proxy_id"] == proxy.id
        assert data["status"] == "deploying"
        assert data["celery_state"] == "STARTED"

    @patch("routes.benchmarks.BenchmarkTargetService")
    def test_returns_null_celery_state_when_no_task_id(
        self,
        mock_service_cls,
        client,
        viewer_headers,
        all_test_users,
        make_k8s_cluster,
        db,
    ):
        cluster = make_k8s_cluster(name="task-status-cluster-b")
        target = _make_target(db, cluster.id, name="task-status-target-b")
        proxy = _make_proxy(
            db, target.id,
            status="ready",
            status_message="Proxy ready",
            proxy_url="http://envoy.perf-proxies:10080",
            celery_task_id=None,
        )

        svc = mock_service_cls.return_value
        svc.get_proxy_deployment.return_value = proxy

        resp = client.get(
            f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}/task-status",
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["celery_task_id"] is None
        assert data["celery_state"] is None
        assert data["status"] == "ready"

    def test_requires_auth(self, client):
        resp = client.get("/api/benchmarks/targets/1/proxies/1/task-status")
        assert resp.status_code == 401


# ============================================================================
# 11. Trigger Benchmark Run
# ============================================================================

class TestTriggerBenchmarkRun:
    """POST /api/benchmarks/targets/{target_id}/proxies/{proxy_id}/run — no route-level auth dep."""

    @patch("routes.benchmarks.dispatch_to_agent")
    def test_happy_path(self, mock_send, client, operator_headers, make_k8s_cluster, db):
        """Trigger run with a connected agent and ready proxy."""
        import asyncio

        mock_send.return_value = True

        cluster = make_k8s_cluster(name="trigger-cluster")
        target = _make_target(db, cluster.id, name="trigger-target")
        proxy = _make_proxy(db, target.id, status="ready")
        agent = _make_agent(db, name="trigger-agent", status="connected")

        resp = client.post(
            f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}/run",
            json={"agent_id": agent.id},
            headers=operator_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "run_id" in data
        assert "agent_id" in data
        assert "proxy_id" in data
        assert "target_id" in data
        assert "status" in data
        assert "message" in data
        assert data["agent_id"] == agent.id
        assert data["proxy_id"] == proxy.id

    @patch("routes.benchmarks.dispatch_to_agent")
    def test_ws_send_fails_run_stays_pending(self, mock_send, client, operator_headers, make_k8s_cluster, db):
        """When WS send fails, run is created with pending status."""
        mock_send.return_value = False

        cluster = make_k8s_cluster(name="trigger-pending-cluster")
        target = _make_target(db, cluster.id, name="trigger-pending-target")
        proxy = _make_proxy(db, target.id, status="ready")
        agent = _make_agent(db, name="trigger-pending-agent", status="connected")

        resp = client.post(
            f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}/run",
            json={"agent_id": agent.id},
            headers=operator_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "pending"

    def test_requires_valid_token(self, client, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="trigger-noauth-cluster")
        target = _make_target(db, cluster.id, name="trigger-noauth-target")
        proxy = _make_proxy(db, target.id, status="ready")
        agent = _make_agent(db, name="trigger-noauth-agent", status="connected")
        resp = client.post(
            f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}/run",
            json={"agent_id": agent.id},
        )
        assert resp.status_code == 401

    def test_proxy_not_ready_returns_400(self, client, operator_headers, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="trigger-notready-cluster")
        target = _make_target(db, cluster.id, name="trigger-notready-target")
        proxy = _make_proxy(db, target.id, status="pending")
        agent = _make_agent(db, name="trigger-notready-agent", status="connected")

        resp = client.post(
            f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}/run",
            json={"agent_id": agent.id},
            headers=operator_headers,
        )
        assert resp.status_code == 400

    def test_no_connected_agents_returns_400(self, client, operator_headers, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="trigger-noagent-cluster")
        target = _make_target(db, cluster.id, name="trigger-noagent-target")
        proxy = _make_proxy(db, target.id, status="ready")

        resp = client.post(
            f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}/run",
            json={},
            headers=operator_headers,
        )
        assert resp.status_code == 400

    def test_disconnected_agent_returns_400(self, client, operator_headers, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="trigger-disconn-cluster")
        target = _make_target(db, cluster.id, name="trigger-disconn-target")
        proxy = _make_proxy(db, target.id, status="ready")
        agent = _make_agent(db, name="trigger-disconn-agent", status="disconnected")

        resp = client.post(
            f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}/run",
            json={"agent_id": agent.id},
            headers=operator_headers,
        )
        assert resp.status_code == 400

    @patch("routes.benchmarks.dispatch_to_agent")
    def test_response_contract(self, mock_send, client, operator_headers, make_k8s_cluster, db):
        mock_send.return_value = True

        cluster = make_k8s_cluster(name="trigger-contract-cluster")
        target = _make_target(db, cluster.id, name="trigger-contract-target")
        proxy = _make_proxy(db, target.id, status="discovered")
        agent = _make_agent(db, name="trigger-contract-agent", status="connected")

        resp = client.post(
            f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}/run",
            json={"agent_id": agent.id, "total_requests": 50, "max_tokens": 256},
            headers=operator_headers,
        )
        data = resp.json()
        for key in ("run_id", "agent_id", "proxy_id", "target_id", "status", "message"):
            assert key in data


# ============================================================================
# 12. Scenario Catalog
# ============================================================================

class TestListBenchmarkScenarios:
    """GET /api/benchmarks/scenarios (require_viewer)."""

    def test_happy_path(self, client, viewer_headers, all_test_users, db):
        resp = client.get("/api/benchmarks/scenarios", headers=viewer_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "scenarios" in data
        assert "total" in data
        assert data["total"] >= 1
        keys = {s["key"] for s in data["scenarios"]}
        assert "prefix-cache" in keys
        assert "mooncake" in keys

    def test_requires_auth(self, client):
        resp = client.get("/api/benchmarks/scenarios")
        assert resp.status_code == 401

    def test_response_contract(self, client, viewer_headers, all_test_users, db):
        resp = client.get("/api/benchmarks/scenarios", headers=viewer_headers)
        item = resp.json()["scenarios"][0]
        for key in ("key", "name", "description", "trace_driven", "child_run_count", "tags"):
            assert key in item

    def test_mooncake_is_trace_driven_single_child(self, client, viewer_headers, all_test_users, db):
        resp = client.get("/api/benchmarks/scenarios", headers=viewer_headers)
        mooncake = next(s for s in resp.json()["scenarios"] if s["key"] == "mooncake")
        assert mooncake["trace_driven"] is True
        assert mooncake["child_run_count"] == 1


# ============================================================================
# 13. Run Scenario — creates a run-group + N child runs
# ============================================================================

class TestRunBenchmarkScenario:
    """POST /api/benchmarks/targets/{target_id}/proxies/{proxy_id}/run-scenario."""

    @patch("routes.benchmarks.dispatch_to_agent")
    def test_creates_group_and_children(self, mock_send, client, operator_headers, make_k8s_cluster, db):
        mock_send.return_value = True

        cluster = make_k8s_cluster(name="scenario-cluster")
        target = _make_target(db, cluster.id, name="scenario-target")
        proxy = _make_proxy(db, target.id, status="ready")
        agent = _make_agent(db, name="scenario-agent", status="connected")

        resp = client.post(
            f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}/run-scenario",
            json={"scenario_key": "baseline", "agent_id": agent.id},
            headers=operator_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["scenario_key"] == "baseline"
        assert data["total_runs"] == 4  # baseline sweep of 4
        # Gated dispatch: only the FIRST child is sent now; the rest are dispatched
        # one at a time as each completes (strictly sequential, never concurrent).
        assert data["dispatched_runs"] == 1
        assert data["status"] == "running"

        # All child runs exist and link to the group (only one is RUNNING yet).
        children = db.query(BenchmarkRun).filter(BenchmarkRun.run_group_id == data["run_group_id"]).all()
        assert len(children) == 4
        assert all(c.scenario_key == "baseline" for c in children)
        running = [c for c in children if c.status == "running"]
        assert len(running) == 1
        # WS dispatched exactly one command (the first child) at trigger time.
        assert mock_send.call_count == 1

    @patch("routes.benchmarks.dispatch_to_agent")
    def test_mooncake_single_child_trace(self, mock_send, client, operator_headers, make_k8s_cluster, db):
        mock_send.return_value = True

        cluster = make_k8s_cluster(name="scenario-mooncake-cluster")
        target = _make_target(db, cluster.id, name="scenario-mooncake-target")
        proxy = _make_proxy(db, target.id, status="ready")
        agent = _make_agent(db, name="scenario-mooncake-agent", status="connected")

        resp = client.post(
            f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}/run-scenario",
            json={"scenario_key": "mooncake", "agent_id": agent.id},
            headers=operator_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["total_runs"] == 1
        child = db.query(BenchmarkRun).filter(BenchmarkRun.run_group_id == data["run_group_id"]).first()
        snap = child.config_snapshot
        assert snap["custom_dataset_type"] == "mooncake_trace"
        assert snap["fixed_schedule"] is True
        assert "trace_url" in snap

    @patch("routes.benchmarks.dispatch_to_agent")
    def test_unknown_scenario_returns_400(self, mock_send, client, operator_headers, make_k8s_cluster, db):
        mock_send.return_value = True

        cluster = make_k8s_cluster(name="scenario-bad-cluster")
        target = _make_target(db, cluster.id, name="scenario-bad-target")
        proxy = _make_proxy(db, target.id, status="ready")
        agent = _make_agent(db, name="scenario-bad-agent", status="connected")

        resp = client.post(
            f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}/run-scenario",
            json={"scenario_key": "nope", "agent_id": agent.id},
            headers=operator_headers,
        )
        assert resp.status_code == 400

    def test_requires_valid_token(self, client, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="scenario-noauth-cluster")
        target = _make_target(db, cluster.id, name="scenario-noauth-target")
        proxy = _make_proxy(db, target.id, status="ready")
        resp = client.post(
            f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}/run-scenario",
            json={"scenario_key": "baseline"},
        )
        assert resp.status_code == 401

    def test_proxy_not_ready_returns_400(self, client, operator_headers, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="scenario-notready-cluster")
        target = _make_target(db, cluster.id, name="scenario-notready-target")
        proxy = _make_proxy(db, target.id, status="pending")
        agent = _make_agent(db, name="scenario-notready-agent", status="connected")
        resp = client.post(
            f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}/run-scenario",
            json={"scenario_key": "baseline", "agent_id": agent.id},
            headers=operator_headers,
        )
        assert resp.status_code == 400

    @patch("routes.benchmarks.dispatch_to_agent")
    def test_response_contract(self, mock_send, client, operator_headers, make_k8s_cluster, db):
        mock_send.return_value = True

        cluster = make_k8s_cluster(name="scenario-contract-cluster")
        target = _make_target(db, cluster.id, name="scenario-contract-target")
        proxy = _make_proxy(db, target.id, status="discovered")
        agent = _make_agent(db, name="scenario-contract-agent", status="connected")

        resp = client.post(
            f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}/run-scenario",
            json={"scenario_key": "prefix-cache", "agent_id": agent.id},
            headers=operator_headers,
        )
        data = resp.json()
        for key in ("run_group_id", "scenario_key", "agent_id", "proxy_id",
                     "target_id", "status", "total_runs", "dispatched_runs", "message"):
            assert key in data
        assert data["total_runs"] == 4  # prefix-cache: 4 (concurrency, ISL) pairs


# ============================================================================
# 14. Get Run-Group
# ============================================================================

def _make_run_group(db, **overrides):
    group = BenchmarkRunGroup(
        scenario_key=overrides.get("scenario_key", "baseline"),
        scenario_name=overrides.get("scenario_name", "Baseline"),
        run_label=overrides.get("run_label", "baseline-envoy"),
        target_id=overrides.get("target_id"),
        proxy=overrides.get("proxy", "envoy"),
        model=overrides.get("model", "tinyllama"),
        base_url=overrides.get("base_url", "http://envoy:10080"),
        status=overrides.get("status", "running"),
        total_runs=overrides.get("total_runs", 2),
        completed_runs=overrides.get("completed_runs", 0),
        failed_runs=overrides.get("failed_runs", 0),
    )
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


class TestGetBenchmarkRunGroup:
    """GET /api/benchmarks/run-groups/{group_id} (require_viewer)."""

    def test_happy_path(self, client, viewer_headers, all_test_users, db):
        group = _make_run_group(db)
        _make_run(db, run_group_id=group.id, variant_label="c50", status="completed")
        _make_run(db, run_group_id=group.id, variant_label="c100", status="running")
        resp = client.get(f"/api/benchmarks/run-groups/{group.id}", headers=viewer_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == group.id
        assert data["scenario_key"] == "baseline"
        assert len(data["runs"]) == 2
        labels = {r["variant_label"] for r in data["runs"]}
        assert labels == {"c50", "c100"}

    def test_requires_auth(self, client, db):
        group = _make_run_group(db)
        resp = client.get(f"/api/benchmarks/run-groups/{group.id}")
        assert resp.status_code == 401

    def test_not_found(self, client, viewer_headers, all_test_users, db):
        resp = client.get("/api/benchmarks/run-groups/99999", headers=viewer_headers)
        assert resp.status_code == 404

    def test_response_contract(self, client, viewer_headers, all_test_users, db):
        group = _make_run_group(db)
        resp = client.get(f"/api/benchmarks/run-groups/{group.id}", headers=viewer_headers)
        data = resp.json()
        for key in ("id", "scenario_key", "scenario_name", "run_label", "status",
                     "total_runs", "completed_runs", "failed_runs", "aggregate_json",
                     "avg_latency_p50", "peak_rps", "runs", "created_at"):
            assert key in data


# ============================================================================
# 15. Authorization — mutating routes require operator (M1)
# ============================================================================

class TestMutatingRoutesForbidViewer:
    """A viewer token must get 403 on mutating benchmark routes (M1).

    Pre-fix these routes had no role dep, so the global AuthMiddleware (token
    validity only) let a viewer trigger sweeps, deploy proxies, and delete data.
    """

    def test_create_run_forbidsViewer(self, client, viewer_headers, all_test_users, db):
        payload = {"model": "tinyllama", "base_url": "http://vllm:8000"}
        resp = client.post("/api/benchmarks/runs", json=payload, headers=viewer_headers)
        assert resp.status_code == 403

    def test_delete_run_forbidsViewer(self, client, viewer_headers, all_test_users, db):
        run = _make_run(db, status="completed")
        resp = client.delete(f"/api/benchmarks/runs/{run.id}", headers=viewer_headers)
        assert resp.status_code == 403

    def test_cancel_run_forbidsViewer(self, client, viewer_headers, all_test_users, db):
        run = _make_run(db, status="pending")
        resp = client.post(f"/api/benchmarks/runs/{run.id}/cancel", headers=viewer_headers)
        assert resp.status_code == 403

    def test_create_target_forbidsViewer(self, client, viewer_headers, all_test_users, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="viewer-forbid-cluster")
        payload = {
            "name": "viewer-forbid-target",
            "cluster_id": cluster.id,
            "llm_base_url": "http://x",
            "llm_model": "m",
        }
        resp = client.post("/api/benchmarks/targets", json=payload, headers=viewer_headers)
        assert resp.status_code == 403

    def test_delete_target_forbidsViewer(self, client, viewer_headers, all_test_users, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="viewer-forbid-del-cluster")
        target = _make_target(db, cluster.id, name="viewer-forbid-del-target")
        resp = client.delete(f"/api/benchmarks/targets/{target.id}", headers=viewer_headers)
        assert resp.status_code == 403

    def test_deploy_proxy_forbidsViewer(self, client, viewer_headers, all_test_users, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="viewer-forbid-deploy-cluster")
        target = _make_target(db, cluster.id, name="viewer-forbid-deploy-target")
        resp = client.post(
            f"/api/benchmarks/targets/{target.id}/proxies",
            json={"proxy_type": "envoy"},
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    def test_delete_proxy_forbidsViewer(self, client, viewer_headers, all_test_users, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="viewer-forbid-undeploy-cluster")
        target = _make_target(db, cluster.id, name="viewer-forbid-undeploy-target")
        proxy = _make_proxy(db, target.id, status="ready")
        resp = client.delete(
            f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}",
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    def test_trigger_run_forbidsViewer(self, client, viewer_headers, all_test_users, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="viewer-forbid-trigger-cluster")
        target = _make_target(db, cluster.id, name="viewer-forbid-trigger-target")
        proxy = _make_proxy(db, target.id, status="ready")
        agent = _make_agent(db, name="viewer-forbid-trigger-agent", status="connected")
        resp = client.post(
            f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}/run",
            json={"agent_id": agent.id},
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    def test_run_scenario_forbidsViewer(self, client, viewer_headers, all_test_users, make_k8s_cluster, db):
        cluster = make_k8s_cluster(name="viewer-forbid-scenario-cluster")
        target = _make_target(db, cluster.id, name="viewer-forbid-scenario-target")
        proxy = _make_proxy(db, target.id, status="ready")
        agent = _make_agent(db, name="viewer-forbid-scenario-agent", status="connected")
        resp = client.post(
            f"/api/benchmarks/targets/{target.id}/proxies/{proxy.id}/run-scenario",
            json={"scenario_key": "baseline", "agent_id": agent.id},
            headers=viewer_headers,
        )
        assert resp.status_code == 403


# ============================================================================
# 16. Cancel propagation across a run-group (C1)
# ============================================================================

class TestCancelRunGroupPropagation:
    """Cancelling a group child must tear down the WHOLE group (C1).

    Pre-fix: cancel flipped one child to CANCELLED but never finalized the group
    or cancelled siblings, so the group wedged RUNNING forever and pending children
    stalled. Decided semantics: cancel-the-whole-group.
    """

    def _group_with_children(self, db):
        group = _make_run_group(db, status="running", total_runs=3)
        running = _make_run(db, run_group_id=group.id, variant_label="c1", status="running")
        pending_a = _make_run(db, run_group_id=group.id, variant_label="c2", status="pending")
        pending_b = _make_run(db, run_group_id=group.id, variant_label="c3", status="pending")
        return group, running, pending_a, pending_b

    def test_cancel_pending_child_cancelsWholeGroup(self, client, operator_headers, db):
        group, running, pending_a, pending_b = self._group_with_children(db)
        with patch("routes.benchmarks.dispatch_to_agent", return_value=True):
            resp = client.post(f"/api/benchmarks/runs/{pending_a.id}/cancel", headers=operator_headers)
        assert resp.status_code == 200
        db.expire_all()
        for child in (running, pending_a, pending_b):
            db.refresh(child)
            assert child.status == "cancelled"
        db.refresh(group)
        assert group.status == "cancelled"
        assert group.completed_at is not None

    def test_cancel_running_child_dispatchesCancelToRunningAgent(self, client, operator_headers, db):
        # Cancel a PENDING child; the cancel command must still target the agent
        # running the live child (different run_id), so the in-flight aiperf stops.
        agent = _make_agent(db, name="cancel-group-agent", status="connected")
        group = _make_run_group(db, status="running", total_runs=2)
        running = _make_run(db, run_group_id=group.id, variant_label="r1",
                            status="running", agent_id=agent.id)
        pending = _make_run(db, run_group_id=group.id, variant_label="p1",
                            status="pending", agent_id=agent.id)
        with patch("routes.benchmarks.dispatch_to_agent", return_value=True) as mock_dispatch:
            resp = client.post(f"/api/benchmarks/runs/{pending.id}/cancel", headers=operator_headers)
        assert resp.status_code == 200
        # Cancel targets the RUNNING child's agent + run_id, not the pending one.
        mock_dispatch.assert_called_once_with(agent.id, {"type": "cancel", "run_id": running.id})

    def test_cancel_standalone_run_unaffected(self, client, operator_headers, db):
        # No run_group_id → legacy behavior: only this run is cancelled.
        agent = _make_agent(db, name="standalone-cancel-agent", status="connected")
        run = _make_run(db, status="running", agent_id=agent.id, run_group_id=None)
        with patch("routes.benchmarks.dispatch_to_agent", return_value=True) as mock_dispatch:
            resp = client.post(f"/api/benchmarks/runs/{run.id}/cancel", headers=operator_headers)
        assert resp.status_code == 200
        db.refresh(run)
        assert run.status == "cancelled"
        mock_dispatch.assert_called_once_with(agent.id, {"type": "cancel", "run_id": run.id})


# ============================================================================
# 17. Agent WebSocket — auth + result-spoof ownership guard (M2)
# ============================================================================

class TestAgentWebSocketAuth:
    """The agent WS validates a JWT (M2) and rejects cross-agent result spoofing."""

    def test_missing_token_rejected(self, client, all_test_users, db):
        agent = _make_agent(db, name="ws-noauth-agent", status="connected")
        with pytest.raises(Exception):
            with client.websocket_connect(f"/ws/benchmarks/agents/{agent.id}"):
                pass

    def test_invalid_token_rejected(self, client, all_test_users, db):
        agent = _make_agent(db, name="ws-badauth-agent", status="connected")
        with pytest.raises(Exception):
            with client.websocket_connect(f"/ws/benchmarks/agents/{agent.id}?token=garbage"):
                pass

    def test_valid_token_accepts(self, client, all_test_users, db):
        from services.auth_service import create_access_token

        token = create_access_token(data={"sub": "testoperator", "role": "operator"})
        agent = _make_agent(db, name="ws-okauth-agent", status="connected")
        with client.websocket_connect(f"/ws/benchmarks/agents/{agent.id}?token={token}") as ws:
            ws.send_json({"type": "heartbeat", "status": "connected"})
            # No exception means the handshake + accept succeeded.

    def test_run_completed_from_wrong_agent_isIgnored(self, client, all_test_users, db):
        # Run is owned by agent A; agent B (authenticated) tries to report its result.
        # The ownership guard must skip the mutation — run stays RUNNING, not COMPLETED.
        from services.auth_service import create_access_token

        token = create_access_token(data={"sub": "testoperator", "role": "operator"})
        owner = _make_agent(db, name="ws-owner-agent", status="connected")
        attacker = _make_agent(db, name="ws-attacker-agent", status="connected")
        run = _make_run(db, status="running", agent_id=owner.id)

        with client.websocket_connect(f"/ws/benchmarks/agents/{attacker.id}?token={token}") as ws:
            ws.send_json({
                "type": "run_completed",
                "run_id": run.id,
                "result": {
                    "request_count": {"avg": 10},
                    "request_latency": {"p50": 1.0, "p99": 2.0, "avg": 1.5},
                    "request_throughput": {"avg": 5.0},
                    "output_token_throughput": {"avg": 50.0},
                    "output_sequence_length": {"avg": 100},
                    "input_sequence_length": {"avg": 50},
                    "benchmark_duration": {"avg": 5.0},
                },
            })
            # Send a heartbeat afterward and wait for it to round-trip-ish by closing.
            ws.send_json({"type": "heartbeat", "status": "connected"})

        db.expire_all()
        db.refresh(run)
        assert run.status == "running"  # mutation was skipped by the spoof guard


# ============================================================================
# 18. Gated dispatch — atomic claim-before-dispatch (H1)
# ============================================================================

class TestGatedDispatchClaim:
    """`_dispatch_next_group_child` claims the next child atomically before
    dispatch, so concurrent terminal events can't double-dispatch the same run."""

    async def test_dispatch_claimsAndSendsOneChild(self, db):
        from routes.benchmarks import _dispatch_next_group_child
        from services.benchmark_service import BenchmarkService

        group = _make_run_group(db, status="running", total_runs=2)
        agent = _make_agent(db, name="gated-agent", status="connected")
        nxt = _make_run(db, run_group_id=group.id, variant_label="c2",
                        status="pending", agent_id=agent.id)
        svc = BenchmarkService(db)

        sent = []

        async def _fake_send(agent_id, command):
            sent.append((agent_id, command))
            return True

        with patch("routes.benchmarks.send_command_to_agent", _fake_send):
            await _dispatch_next_group_child(svc, agent.id, group.id)
        db.commit()

        db.refresh(nxt)
        assert nxt.status == "running"
        assert len(sent) == 1
        assert sent[0][1]["run_id"] == nxt.id

    async def test_dispatch_secondConcurrentCallNoOps(self, db):
        # Two terminal events both try to advance the sweep. The first claims +
        # sends the lowest-id pending child; the second finds it already RUNNING
        # and must NOT send a duplicate (single aiperf invocation guarantee).
        from routes.benchmarks import _dispatch_next_group_child
        from services.benchmark_service import BenchmarkService

        group = _make_run_group(db, status="running", total_runs=2)
        agent = _make_agent(db, name="gated-race-agent", status="connected")
        nxt = _make_run(db, run_group_id=group.id, variant_label="only-pending",
                        status="pending", agent_id=agent.id)
        svc = BenchmarkService(db)

        sends = []

        async def _fake_send(agent_id, command):
            sends.append(command["run_id"])
            return True

        with patch("routes.benchmarks.send_command_to_agent", _fake_send):
            await _dispatch_next_group_child(svc, agent.id, group.id)
            await _dispatch_next_group_child(svc, agent.id, group.id)
        db.commit()

        db.refresh(nxt)
        assert nxt.status == "running"
        assert sends == [nxt.id]  # dispatched exactly once

    async def test_dispatch_revertsClaimWhenSendFails(self, db):
        # A winning claim whose WS send fails must revert the child to PENDING so
        # a later terminal event / reconnect can re-dispatch it (not wedge RUNNING).
        from routes.benchmarks import _dispatch_next_group_child
        from services.benchmark_service import BenchmarkService

        group = _make_run_group(db, status="running", total_runs=2)
        agent = _make_agent(db, name="gated-fail-agent", status="connected")
        nxt = _make_run(db, run_group_id=group.id, variant_label="c2",
                        status="pending", agent_id=agent.id)
        svc = BenchmarkService(db)

        async def _fake_send(agent_id, command):
            return False  # WS send failed

        with patch("routes.benchmarks.send_command_to_agent", _fake_send):
            await _dispatch_next_group_child(svc, agent.id, group.id)
        db.commit()

        db.refresh(nxt)
        assert nxt.status == "pending"
        assert nxt.started_at is None


def test_complete_run_counts_aiperf_error_records_as_failures(db):
    """Regression: aiperf request_count is SUCCESSFUL records; error_request_count
    must be counted as failures. Previously failed was hardcoded 0 / success 100%,
    masking real upstream errors (e.g. truncated streams)."""
    from services.benchmark_service import BenchmarkService

    run = _make_run(db, status="running", total_requests=0,
                    successful_requests=0, failed_requests=0, success_rate_pct=0.0)
    db.commit()

    raw = {
        "request_count": {"avg": 886.0},          # successful records
        "error_request_count": {"avg": 114.0},    # error records (truncated/4xx/5xx)
        "request_latency": {"p50": 2000.0, "p99": 5000.0, "avg": 2500.0},
        "request_throughput": {"avg": 24.0},
        "output_token_throughput": {"avg": 3000.0},
        "output_sequence_length": {"avg": 128.0},
        "input_sequence_length": {"avg": 5000.0},
        "benchmark_duration": {"avg": 36.5},
    }
    BenchmarkService(db).complete_run_with_aiperf_result(run.id, raw)
    db.refresh(run)

    assert run.successful_requests == 886
    assert run.failed_requests == 114
    assert run.total_requests == 1000
    assert run.success_rate_pct == 88.6
    assert run.result_json["failed"] == 114
    assert run.result_json["success_rate_pct"] == 88.6
