"""
Component tests for LlmObservabilityService.

Mocks the K8s API-server service-proxy (``api_client.call_api``) so tests
exercise LogQL construction → Loki-response parsing → contract reshaping
without a real cluster or Loki. Canned payloads use the real Loki shapes:
instant vector (stats/rankings), matrix (histogram/provider-usage), and
streams (logs).
"""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client.rest import ApiException

from services.llm_observability_service import LlmObservabilityService

# ---------------------------------------------------------------------------
# Loki response builders (real shapes)
# ---------------------------------------------------------------------------


def _vector(value: float, labels: dict[str, str] | None = None) -> dict[str, Any]:
    return {"metric": labels or {}, "value": [1700000000, str(value)]}


def _instant(*rows: dict[str, Any]) -> dict[str, Any]:
    return {"status": "success", "data": {"resultType": "vector", "result": list(rows)}}


def _matrix(*rows: dict[str, Any]) -> dict[str, Any]:
    return {"status": "success", "data": {"resultType": "matrix", "result": list(rows)}}


def _series(labels: dict[str, str], *points: tuple[float, float]) -> dict[str, Any]:
    return {"metric": labels, "values": [[ts, str(v)] for ts, v in points]}


def _streams(*lines: tuple[int, dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [
                {"stream": {"job": "llm-gateway"}, "values": [[str(ns), json.dumps(rec)] for ns, rec in lines]}
            ],
        },
    }


class _FakeApiClient:
    """Records call_api invocations and returns canned data via a router fn.

    ``router(sub_path, query, params) -> dict`` where sub_path is the trailing
    Loki path (``query`` / ``query_range`` / ``label/model/values`` …).
    """

    def __init__(self, router):
        self._router = router
        self.calls: list[dict[str, Any]] = []

    def call_api(self, resource_path: str, method: str, query_params=None, **kwargs):
        params = dict(query_params or [])
        sub = resource_path.split("/proxy/", 1)[1]
        self.calls.append({"path": resource_path, "sub": sub, "params": params})
        return self._router(sub, params.get("query", ""), params)

    def queries(self) -> list[str]:
        return [c["params"].get("query", "") for c in self.calls]


def _make_service(router) -> tuple[LlmObservabilityService, _FakeApiClient]:
    fake = _FakeApiClient(router)
    with patch("services.llm_observability_service.KubernetesService") as MockK8s:
        MockK8s.return_value.load_kubeconfig.return_value = fake
        svc = LlmObservabilityService(db=MagicMock())
    return svc, fake


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


class TestStats:
    def _router(self, sub: str, query: str, params: dict[str, Any]) -> dict[str, Any]:
        if "count by (model)" in query:
            return _instant(_vector(3))
        if "unwrap latency_ms" in query:
            return _instant(_vector(250.0))
        if "unwrap total_tk" in query:
            return _instant(_vector(5000))
        if "unwrap cost" in query:
            return _instant(_vector(1.25))
        if 'status=~"2.."' in query:
            return _instant(_vector(95))
        return _instant(_vector(100))  # total_requests

    def test_stats_reshapes_and_computes_success_rate(self):
        svc, fake = _make_service(self._router)
        out = svc.stats(cluster_id=1, range_="1h")

        assert out["available"] is True
        assert out["total_requests"] == 100
        assert out["success_rate"] == pytest.approx(0.95)
        assert out["avg_latency_ms"] == pytest.approx(250.0)
        assert out["total_tokens"] == 5000
        assert out["total_cost"] == pytest.approx(1.25)
        assert out["models"] == 3
        assert out["errors"] == {}
        # instant query with the range window baked into the selector
        assert all(c["sub"] == "loki/api/v1/query" for c in fake.calls)
        assert any('count_over_time({job="llm-gateway"}[3600s])' in q for q in fake.queries())
        # every instant query pins an explicit eval time
        assert all("time" in c["params"] for c in fake.calls)

    def test_stats_applies_model_and_status_filters(self):
        svc, fake = _make_service(self._router)
        svc.stats(cluster_id=1, range_="6h", model="gpt-4o", status="200")
        total_q = next(q for q in fake.queries() if "count_over_time" in q and "status=~" not in q)
        assert 'model="gpt-4o"' in total_q
        assert 'status="200"' in total_q
        assert "[21600s]" in total_q

    def test_stats_loki_unreachable_degrades(self):
        def _boom(sub, query, params):
            raise ApiException(status=503, reason="Service Unavailable")

        svc, _ = _make_service(_boom)
        out = svc.stats(cluster_id=1, range_="1h")
        assert out["available"] is False
        assert "503" in out["reason"]
        assert out["endpoint"] == "http://loki.llm-egress:3100"


# ---------------------------------------------------------------------------
# histogram
# ---------------------------------------------------------------------------


class TestHistogram:
    def test_requests_splits_success_and_error(self):
        def _router(sub, query, params):
            if 'status=~"2.."' in query:
                return _matrix(_series({}, (1700000000, 9), (1700000060, 11)))
            return _matrix(_series({}, (1700000000, 1), (1700000060, 0)))

        svc, fake = _make_service(_router)
        out = svc.histogram(cluster_id=1, range_="1h", metric="requests")

        assert out["available"] is True
        assert out["metric"] == "requests"
        assert out["step_s"] == 60
        names = {s["name"] for s in out["series"]}
        assert names == {"success", "error"}
        success = next(s for s in out["series"] if s["name"] == "success")
        assert success["points"][0]["value"] == 9.0
        assert "ts" in success["points"][0]
        assert all(c["sub"] == "loki/api/v1/query_range" for c in fake.calls)
        # explicit start/end/step on every range query
        for c in fake.calls:
            assert {"start", "end", "step"} <= set(c["params"])

    def test_cost_fans_out_per_model(self):
        def _router(sub, query, params):
            return _matrix(
                _series({"model": "gpt-4o"}, (1700000000, 0.5)),
                _series({"model": "claude-3"}, (1700000000, 0.2)),
            )

        svc, _ = _make_service(_router)
        out = svc.histogram(cluster_id=1, range_="1h", metric="cost")
        names = {s["name"] for s in out["series"]}
        assert names == {"gpt-4o", "claude-3"}

    def test_unknown_metric_degrades(self):
        svc, _ = _make_service(lambda s, q, p: _matrix())
        out = svc.histogram(cluster_id=1, range_="1h", metric="bogus")
        assert out["available"] is False
        assert "bogus" in out["reason"]


# ---------------------------------------------------------------------------
# provider-usage
# ---------------------------------------------------------------------------


class TestProviderUsage:
    def test_folds_models_to_providers(self):
        def _router(sub, query, params):
            return _matrix(
                _series({"model": "gpt-4o"}, (1700000000, 1.0)),
                _series({"model": "gpt-3.5"}, (1700000000, 2.0)),
                _series({"model": "claude-3"}, (1700000000, 4.0)),
            )

        svc, _ = _make_service(_router)
        out = svc.provider_usage(cluster_id=1, range_="1h", metric="cost")
        by_name = {s["name"]: s for s in out["series"]}
        assert set(by_name) == {"openai", "anthropic"}
        # cost sums the two openai models at the shared timestamp
        assert by_name["openai"]["points"][0]["value"] == pytest.approx(3.0)

    def test_latency_folds_by_mean(self):
        def _router(sub, query, params):
            return _matrix(
                _series({"model": "gpt-4o"}, (1700000000, 100.0)),
                _series({"model": "gpt-3.5"}, (1700000000, 300.0)),
            )

        svc, _ = _make_service(_router)
        out = svc.provider_usage(cluster_id=1, range_="1h", metric="latency")
        openai = next(s for s in out["series"] if s["name"] == "openai")
        assert openai["points"][0]["value"] == pytest.approx(200.0)


# ---------------------------------------------------------------------------
# rankings
# ---------------------------------------------------------------------------


class TestRankings:
    def test_rows_with_trend_deltas(self):
        # rankings fires current + previous window per metric concurrently, so
        # call order is not stable — route on the query's `time` param instead.
        # current window time ~= now; previous window is one range (>=1h) earlier.
        now = time.time_ns()

        def _is_current(params) -> bool:
            return int(params["time"]) >= now - 60_000_000_000

        def _router(sub, query, params):
            if "count_over_time" in query and 'status=~"2.."' in query:
                return _instant(_vector(90, {"model": "gpt-4o"}))
            if "count_over_time" in query:  # requests metric: current 100, previous 50
                return _instant(_vector(100 if _is_current(params) else 50, {"model": "gpt-4o"}))
            if "unwrap total_tk" in query:
                return _instant(_vector(1000, {"model": "gpt-4o"}))
            if "unwrap cost" in query:
                return _instant(_vector(2.0, {"model": "gpt-4o"}))
            if "unwrap latency_ms" in query:
                return _instant(_vector(150.0, {"model": "gpt-4o"}))
            return _instant()

        svc, fake = _make_service(_router)
        out = svc.rankings(cluster_id=1, range_="1h")

        assert out["available"] is True
        assert len(out["rows"]) == 1
        row = out["rows"][0]
        assert row["model"] == "gpt-4o"
        assert row["provider"] == "openai"
        assert row["requests"] == 100
        assert row["success_rate"] == pytest.approx(0.9)
        assert row["tokens"] == 1000
        assert row["cost"] == pytest.approx(2.0)
        assert row["avg_latency_ms"] == pytest.approx(150.0)
        # current 100 vs previous 50 → +1.0 (100%)
        assert row["trend"]["requests"] == pytest.approx(1.0)
        # two eval windows are queried (current + previous)
        times = {c["params"]["time"] for c in fake.calls}
        assert len(times) == 2


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------


class TestLogs:
    def test_parses_lines_and_sets_cursor(self):
        lines = (
            (1700000002000000000, {"model": "gpt-4o", "userq": "hi", "status": "200",
                                   "latency_ms": 120, "prompt_tk": 10, "comp_tk": 20,
                                   "total_tk": 30, "cost": 0.01, "req_body": "{}", "resp_body": "{}"}),
            (1700000001000000000, {"model": "claude-3", "userq": "yo", "status": "500",
                                   "latency_ms": 90, "prompt_tk": 5, "comp_tk": 0,
                                   "total_tk": 5, "cost": 0.0, "req_body": "{}", "resp_body": "{}"}),
        )

        def _router(sub, query, params):
            assert "| json" in query
            assert params["direction"] == "backward"
            return _streams(*lines)

        svc, fake = _make_service(_router)
        out = svc.logs(cluster_id=1, range_="1h", limit=2)

        assert out["available"] is True
        assert len(out["rows"]) == 2
        # newest first
        assert out["rows"][0]["message"] == "hi"
        assert out["rows"][0]["type"] == "success"
        assert out["rows"][1]["type"] == "error"
        assert out["rows"][0]["total_tk"] == 30
        # got == limit rows → cursor points just before the oldest line
        assert out["next_end"] == str(1700000001000000000 - 1)

    def test_content_search_adds_line_filter(self):
        captured: dict[str, str] = {}

        def _router(sub, query, params):
            captured["q"] = query
            return _streams()

        svc, _ = _make_service(_router)
        out = svc.logs(cluster_id=1, range_="1h", content_search="timeout")
        assert '|= "timeout"' in captured["q"]
        assert out["next_end"] is None  # fewer than limit

    def test_end_cursor_used_as_upper_bound(self):
        captured: dict[str, str] = {}

        def _router(sub, query, params):
            captured.update(params)
            return _streams()

        svc, _ = _make_service(_router)
        svc.logs(cluster_id=1, range_="1h", end=1699999999000000000)
        assert captured["end"] == "1699999999000000000"

    def test_content_search_is_escaped(self):
        captured: dict[str, str] = {}

        def _router(sub, query, params):
            captured["q"] = query
            return _streams()

        svc, _ = _make_service(_router)
        # an embedded quote must not break out of the line filter (LogQL injection)
        svc.logs(cluster_id=1, range_="1h", content_search='x" |= "admin')
        assert '|= "x\\" |= \\"admin"' in captured["q"]
        assert '|= "x" |= "admin"' not in captured["q"]

    def test_model_filter_is_escaped(self):
        captured: dict[str, str] = {}

        def _router(sub, query, params):
            captured["q"] = query
            return _streams()

        svc, _ = _make_service(_router)
        svc.logs(cluster_id=1, range_="1h", model='gpt" or x="y')
        assert 'model="gpt\\" or x=\\"y"' in captured["q"]


# ---------------------------------------------------------------------------
# filterdata
# ---------------------------------------------------------------------------


class TestFilterData:
    def test_returns_distinct_labels(self):
        def _router(sub, query, params):
            assert {"start", "end"} <= set(params)
            if sub.endswith("label/model/values"):
                return {"status": "success", "data": ["gpt-4o", "claude-3"]}
            return {"status": "success", "data": ["200", "500"]}

        svc, _ = _make_service(_router)
        out = svc.filterdata(cluster_id=1, range_="24h")
        assert out["available"] is True
        assert out["models"] == ["claude-3", "gpt-4o"]
        assert out["statuses"] == ["200", "500"]

    def test_all_label_calls_fail_degrades(self):
        def _boom(sub, query, params):
            raise ApiException(status=502, reason="Bad Gateway")

        svc, _ = _make_service(_boom)
        out = svc.filterdata(cluster_id=1, range_="1h")
        assert out["available"] is False
        assert "502" in out["reason"]


# ---------------------------------------------------------------------------
# multi-cluster aggregation & latency breakdown
# ---------------------------------------------------------------------------


class TestMultiClusterObservability:
    def test_multi_cluster_histogram_latency_returns_per_cluster_series(self):
        def _router(sub, query, params):
            # Canned latency response with avg series
            return _matrix(
                _series({"__name__": "avg"}, (1700000000, 120.0), (1700000060, 140.0))
            )

        svc, _ = _make_service(_router)
        c1 = MagicMock()
        c1.id = 1
        c1.name = "us-east-cluster"
        c2 = MagicMock()
        c2.id = 2
        c2.name = "eu-west-cluster"
        svc._active_clusters = MagicMock(return_value=[c1, c2])

        out = svc.histogram(cluster_id=None, range_="1h", metric="latency")
        assert out["available"] is True
        assert out["metric"] == "latency"
        assert len(out["series"]) == 2
        names = [s["name"] for s in out["series"]]
        assert "us-east-cluster" in names
        assert "eu-west-cluster" in names
        east = next(s for s in out["series"] if s["name"] == "us-east-cluster")
        assert len(east["points"]) == 2
        assert east["points"][0]["value"] == 120.0

    def test_multi_cluster_histogram_requests_sums_clusters(self):
        def _router(sub, query, params):
            if 'status=~"2.."' in query:
                return _matrix(_series({}, (1700000000, 10.0)))
            return _matrix(_series({}, (1700000000, 2.0)))

        svc, _ = _make_service(_router)
        c1 = MagicMock()
        c1.id = 1
        c1.name = "us-east"
        c2 = MagicMock()
        c2.id = 2
        c2.name = "eu-west"
        svc._active_clusters = MagicMock(return_value=[c1, c2])

        out = svc.histogram(cluster_id=None, range_="1h", metric="requests")
        assert out["available"] is True
        success = next(s for s in out["series"] if s["name"] == "success")
        error = next(s for s in out["series"] if s["name"] == "error")
        # 10 + 10 = 20
        assert success["points"][0]["value"] == 20.0
        # 2 + 2 = 4
        assert error["points"][0]["value"] == 4.0

    def test_multi_cluster_provider_usage_latency_calculates_exact_mean(self):
        call_count = 0

        def _router(sub, query, params):
            nonlocal call_count
            call_count += 1
            # cluster 1 returns 100ms, cluster 2 returns 300ms for openai
            val = 100.0 if call_count % 2 == 1 else 300.0
            return _matrix(_series({"model": "gpt-4o"}, (1700000000, val)))

        svc, _ = _make_service(_router)
        c1 = MagicMock()
        c1.id = 1
        c1.name = "us-east"
        c2 = MagicMock()
        c2.id = 2
        c2.name = "eu-west"
        svc._active_clusters = MagicMock(return_value=[c1, c2])

        out = svc.provider_usage(cluster_id=None, range_="1h", metric="latency")
        assert out["available"] is True
        openai = next(s for s in out["series"] if s["name"] == "openai")
        # (100 + 300) / 2 = 200
        assert openai["points"][0]["value"] == pytest.approx(200.0)

    def test_multi_cluster_stats_sums_requests_and_models_across_fleet(self):
        """Fleet stats sums per-cluster totals; `models` is an upper bound.

        Per-cluster `models` is a COUNT of distinct models on that cluster, so
        the fleet value is the SUM of those counts (an upper bound of models in
        use across the fleet), NOT max() — which understated the fleet whenever
        clusters ran disjoint model sets. c1 has 3 models / 100 reqs, c2 has 4
        models / 50 reqs, so the fleet reports 7 models (3+4), not 4 (max).
        """
        def _router_for(models_count: int, requests: int, tokens: int, cost: float):
            def _router(sub, query, params):
                if "count by (model)" in query:
                    return _instant(_vector(models_count))
                if "unwrap latency_ms" in query:
                    return _instant(_vector(200.0))
                if "unwrap total_tk" in query:
                    return _instant(_vector(tokens))
                if "unwrap cost" in query:
                    return _instant(_vector(cost))
                if 'status=~"2.."' in query:
                    return _instant(_vector(requests))  # all successful
                return _instant(_vector(requests))  # total_requests
            return _router

        svc, _ = _make_service(_router_for(3, 100, 5000, 1.0))
        c1 = MagicMock()
        c1.id = 1
        c1.name = "us-east"
        c2 = MagicMock()
        c2.id = 2
        c2.name = "eu-west"
        svc._active_clusters = MagicMock(return_value=[c1, c2])
        svc._k8s.get_cluster = lambda cid: c1 if cid == 1 else c2
        fake1 = _FakeApiClient(_router_for(3, 100, 5000, 1.0))
        fake2 = _FakeApiClient(_router_for(4, 50, 2000, 0.5))
        svc._client = lambda cid: fake1 if cid == 1 else fake2

        out = svc.stats(cluster_id=None, range_="1h")
        assert out["available"] is True
        # SUM across the fleet — proves it is not max() (which would be 4) and
        # not a single cluster (3).
        assert out["models"] == 7
        assert out["total_requests"] == 150
        assert out["total_tokens"] == 7000
        assert out["total_cost"] == pytest.approx(1.5)

    def test_multi_cluster_stats_all_unavailable_degrades(self):
        """When every cluster's Loki is unreachable, the fleet stat degrades."""
        def _boom(sub, query, params):
            raise ApiException(status=503, reason="Service Unavailable")

        svc, _ = _make_service(_boom)
        c1 = MagicMock()
        c1.id = 1
        c1.name = "us-east"
        svc._active_clusters = MagicMock(return_value=[c1])
        svc._k8s.get_cluster = lambda cid: c1
        svc._client = lambda cid: _FakeApiClient(_boom)

        out = svc.stats(cluster_id=None, range_="1h")
        assert out["available"] is False
        assert "503" in out["reason"]

    def test_multi_cluster_logs_merges_sorts_and_annotates_clusters(self):
        lines_c1 = (
            (1700000002000000000, {"model": "gpt-4o", "userq": "q from c1", "status": "200",
                                   "latency_ms": 100, "prompt_tk": 5, "comp_tk": 10,
                                   "total_tk": 15, "cost": 0.01, "req_body": "{}", "resp_body": "{}"}),
        )
        lines_c2 = (
            (1700000003000000000, {"model": "claude-3", "userq": "q from c2", "status": "200",
                                   "latency_ms": 150, "prompt_tk": 8, "comp_tk": 12,
                                   "total_tk": 20, "cost": 0.02, "req_body": "{}", "resp_body": "{}"}),
        )

        def _router(sub, query, params):
            # sub path proxy contains cluster id via the api_client proxy call
            # Or we inspect which client is calling
            return _streams(*(lines_c1 if "c1" not in query else lines_c2))

        svc, fake = _make_service(_router)
        c1 = MagicMock()
        c1.id = 1
        c1.name = "us-east"
        c2 = MagicMock()
        c2.id = 2
        c2.name = "eu-west"
        svc._active_clusters = MagicMock(return_value=[c1, c2])
        svc._k8s.get_cluster = lambda cid: c1 if cid == 1 else c2
        # Mock client per cluster
        fake1 = _FakeApiClient(lambda s, q, p: _streams(*lines_c1))
        fake2 = _FakeApiClient(lambda s, q, p: _streams(*lines_c2))
        svc._client = lambda cid: fake1 if cid == 1 else fake2

        out = svc.logs(cluster_id=None, range_="1h", limit=10)
        assert out["available"] is True
        assert len(out["rows"]) == 2
        # newest first (c2 timestamp is 1700000003... > c1 timestamp 1700000002...)
        assert out["rows"][0]["message"] == "q from c2"
        assert out["rows"][0]["cluster_name"] == "eu-west"
        assert out["rows"][0]["cluster_id"] == 2
        assert out["rows"][1]["message"] == "q from c1"
        assert out["rows"][1]["cluster_name"] == "us-east"
        assert out["rows"][1]["cluster_id"] == 1

