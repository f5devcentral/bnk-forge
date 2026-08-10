"""
Component tests for /api/k8s/clusters/{id}/llm-observability/* routes.

Mocks LlmObservabilityService (the Loki I/O boundary) so tests exercise the
HTTP → route → response_model chain: RBAC gating (require_viewer) and the
degradation-envelope response shape from the contract.
"""

from unittest.mock import patch

import pytest

_SVC = "routes.k8s.llm_observability.LlmObservabilityService"


def _stats_payload() -> dict:
    return {
        "available": True,
        "endpoint": "http://loki.llm-egress:3100",
        "updated_at": "2026-07-01T00:00:00+00:00",
        "total_requests": 100,
        "success_rate": 0.95,
        "avg_latency_ms": 250.0,
        "total_tokens": 5000,
        "total_cost": 1.25,
        "models": 3,
        "errors": {},
    }


class TestLlmObservabilityRbac:
    def test_stats_allows_viewer(self, client, viewer_headers, sample_viewer_user, make_k8s_cluster, sample_project):
        cluster = make_k8s_cluster(project=sample_project, name="hgx")
        with patch(_SVC) as MockSvc:
            MockSvc.return_value.stats.return_value = _stats_payload()
            resp = client.get(
                f"/api/k8s/clusters/{cluster.id}/llm-observability/stats?range=1h",
                headers=viewer_headers,
            )
        assert resp.status_code == 200, resp.text
        MockSvc.return_value.stats.assert_called_once_with(cluster.id, "1h", None, None)

    def test_stats_rejects_unauthenticated(self, client, make_k8s_cluster, sample_project):
        cluster = make_k8s_cluster(project=sample_project, name="hgx2")
        resp = client.get(f"/api/k8s/clusters/{cluster.id}/llm-observability/stats")
        assert resp.status_code in (401, 403), resp.text


class TestLlmObservabilityShapes:
    def test_stats_response_shape(self, client, viewer_headers, sample_viewer_user, make_k8s_cluster, sample_project):
        cluster = make_k8s_cluster(project=sample_project, name="c1")
        with patch(_SVC) as MockSvc:
            MockSvc.return_value.stats.return_value = _stats_payload()
            resp = client.get(
                f"/api/k8s/clusters/{cluster.id}/llm-observability/stats",
                headers=viewer_headers,
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        for key in (
            "available", "endpoint", "updated_at", "errors",
            "total_requests", "success_rate", "avg_latency_ms",
            "total_tokens", "total_cost", "models",
        ):
            assert key in body
        assert body["total_requests"] == 100

    def test_histogram_forwards_metric_and_shape(self, client, viewer_headers, sample_viewer_user, make_k8s_cluster, sample_project):
        cluster = make_k8s_cluster(project=sample_project, name="c2")
        payload = {
            "available": True,
            "endpoint": "http://loki.llm-egress:3100",
            "updated_at": "2026-07-01T00:00:00+00:00",
            "metric": "tokens",
            "step_s": 60,
            "series": [{"name": "prompt", "points": [{"ts": "2026-07-01T00:00:00+00:00", "value": 12.0}]}],
            "errors": {},
        }
        with patch(_SVC) as MockSvc:
            MockSvc.return_value.histogram.return_value = payload
            resp = client.get(
                f"/api/k8s/clusters/{cluster.id}/llm-observability/histogram?metric=tokens&range=6h",
                headers=viewer_headers,
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["metric"] == "tokens"
        assert body["series"][0]["name"] == "prompt"
        MockSvc.return_value.histogram.assert_called_once_with(cluster.id, "6h", "tokens", None, None)

    def test_logs_forwards_params_and_shape(self, client, viewer_headers, sample_viewer_user, make_k8s_cluster, sample_project):
        cluster = make_k8s_cluster(project=sample_project, name="c3")
        payload = {
            "available": True,
            "endpoint": "http://loki.llm-egress:3100",
            "updated_at": "2026-07-01T00:00:00+00:00",
            "rows": [{
                "ts": "2026-07-01T00:00:00+00:00", "type": "success", "message": "hi",
                "model": "gpt-4o", "latency_ms": 120.0, "prompt_tk": 10, "comp_tk": 20,
                "total_tk": 30, "cost": 0.01, "status": "200", "req_body": "{}", "resp_body": "{}",
            }],
            "next_end": "1699999999999999999",
            "errors": {},
        }
        with patch(_SVC) as MockSvc:
            MockSvc.return_value.logs.return_value = payload
            resp = client.get(
                f"/api/k8s/clusters/{cluster.id}/llm-observability/logs?range=1h&limit=25&content_search=err",
                headers=viewer_headers,
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["rows"][0]["message"] == "hi"
        assert body["next_end"] == "1699999999999999999"
        MockSvc.return_value.logs.assert_called_once_with(cluster.id, "1h", None, None, 25, "err", None)

    def test_logs_rejects_non_integer_end(self, client, viewer_headers, sample_viewer_user, make_k8s_cluster, sample_project):
        cluster = make_k8s_cluster(project=sample_project, name="c-end")
        resp = client.get(
            f"/api/k8s/clusters/{cluster.id}/llm-observability/logs?end=notanumber",
            headers=viewer_headers,
        )
        # `end` is typed int at the route layer → FastAPI validates it (422)
        assert resp.status_code == 422, resp.text

    def test_unavailable_envelope_serializes(self, client, viewer_headers, sample_viewer_user, make_k8s_cluster, sample_project):
        cluster = make_k8s_cluster(project=sample_project, name="c4")
        with patch(_SVC) as MockSvc:
            MockSvc.return_value.stats.return_value = {
                "available": False,
                "endpoint": "http://loki.llm-egress:3100",
                "reason": "503 Service Unavailable",
                "updated_at": "2026-07-01T00:00:00+00:00",
            }
            resp = client.get(
                f"/api/k8s/clusters/{cluster.id}/llm-observability/stats",
                headers=viewer_headers,
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["available"] is False
        assert body["reason"] == "503 Service Unavailable"
        # defaulted data fields still present (response_model)
        assert body["total_requests"] == 0
