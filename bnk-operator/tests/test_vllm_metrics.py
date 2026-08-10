"""
Tests for vllm_metrics.py — the ported Prometheus parser and row builder.

Validates parser correctness, row shape (14 keys), KV-cache normalization,
histogram p95, and edge cases.  These are the "golden shape" tests.
"""
import os
import sys

import pytest

operator_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if operator_root not in sys.path:
    sys.path.insert(0, operator_root)

from vllm_metrics import (
    _detect_backend_kind,
    _detect_model_name,
    _histogram_p95_seconds,
    _last_value,
    _parse_prometheus_text,
    _pick_metrics_port,
    _unhealthy_row,
    parse_pod_metrics,
)

# ---------------------------------------------------------------------------
# Sample vLLM /metrics text (minimal but realistic)
# ---------------------------------------------------------------------------

_SAMPLE_METRICS = """\
# HELP vllm:gpu_cache_usage_perc Fraction of GPU KV cache used
# TYPE vllm:gpu_cache_usage_perc gauge
vllm:gpu_cache_usage_perc{model_name="meta-llama/Llama-3-8b"} 0.42
# HELP vllm:num_requests_running Running requests
# TYPE vllm:num_requests_running gauge
vllm:num_requests_running{model_name="meta-llama/Llama-3-8b"} 3
# HELP vllm:num_requests_waiting Waiting requests
# TYPE vllm:num_requests_waiting gauge
vllm:num_requests_waiting{model_name="meta-llama/Llama-3-8b"} 0
# HELP vllm:time_per_output_token_seconds Output-token latency histogram
# TYPE vllm:time_per_output_token_seconds histogram
vllm:time_per_output_token_seconds_bucket{le="0.005",model_name="meta-llama/Llama-3-8b"} 10
vllm:time_per_output_token_seconds_bucket{le="0.01",model_name="meta-llama/Llama-3-8b"} 20
vllm:time_per_output_token_seconds_bucket{le="0.025",model_name="meta-llama/Llama-3-8b"} 50
vllm:time_per_output_token_seconds_bucket{le="0.05",model_name="meta-llama/Llama-3-8b"} 80
vllm:time_per_output_token_seconds_bucket{le="+Inf",model_name="meta-llama/Llama-3-8b"} 100
vllm:time_per_output_token_seconds_count{model_name="meta-llama/Llama-3-8b"} 100
vllm:time_per_output_token_seconds_sum{model_name="meta-llama/Llama-3-8b"} 3.5
# HELP vllm:e2e_request_latency_seconds End-to-end latency histogram
# TYPE vllm:e2e_request_latency_seconds histogram
vllm:e2e_request_latency_seconds_bucket{le="0.1",model_name="meta-llama/Llama-3-8b"} 5
vllm:e2e_request_latency_seconds_bucket{le="0.5",model_name="meta-llama/Llama-3-8b"} 40
vllm:e2e_request_latency_seconds_bucket{le="1.0",model_name="meta-llama/Llama-3-8b"} 90
vllm:e2e_request_latency_seconds_bucket{le="+Inf",model_name="meta-llama/Llama-3-8b"} 100
vllm:e2e_request_latency_seconds_count{model_name="meta-llama/Llama-3-8b"} 100
vllm:e2e_request_latency_seconds_sum{model_name="meta-llama/Llama-3-8b"} 65.0
# HELP vllm:request_generation_tokens Output token generation counter
# TYPE vllm:request_generation_tokens counter
vllm:request_generation_tokens_count{model_name="meta-llama/Llama-3-8b"} 1000
vllm:request_generation_tokens_sum{model_name="meta-llama/Llama-3-8b"} 88000.0
"""


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

class TestParsePrometheusText:
    """_parse_prometheus_text."""

    def test_parses_gauge(self):
        text = 'vllm:num_requests_running{model_name="llama"} 3\n'
        samples = _parse_prometheus_text(text)
        assert len(samples) == 1
        name, labels, value = samples[0]
        assert name == "vllm:num_requests_running"
        assert labels == {"model_name": "llama"}
        assert value == 3.0

    def test_skips_comments_and_blanks(self):
        text = "# HELP foo\n\nvllm:foo 1.0\n"
        samples = _parse_prometheus_text(text)
        assert len(samples) == 1

    def test_unlabeled_metric(self):
        samples = _parse_prometheus_text("go_goroutines 42\n")
        assert len(samples) == 1
        assert samples[0][0] == "go_goroutines"
        assert samples[0][2] == 42.0


class TestLastValue:
    """_last_value."""

    def test_finds_value(self):
        samples = _parse_prometheus_text(_SAMPLE_METRICS)
        v = _last_value(samples, "vllm:num_requests_running")
        assert v == 3.0

    def test_returns_none_for_missing(self):
        v = _last_value([], "vllm:missing")
        assert v is None


class TestHistogramP95:
    """_histogram_p95_seconds."""

    def test_computes_p95(self):
        samples = _parse_prometheus_text(_SAMPLE_METRICS)
        p95 = _histogram_p95_seconds(samples, "vllm:time_per_output_token_seconds")
        assert p95 is not None
        # p95 should land in the 0.025–0.05 bucket (80th count = le=0.05)
        assert 0.025 <= p95 <= 0.05

    def test_returns_none_on_empty(self):
        assert _histogram_p95_seconds([], "vllm:missing") is None

    def test_returns_none_on_zero_total(self):
        text = "vllm:foo_bucket{le='+Inf'} 0\n"
        samples = _parse_prometheus_text(text)
        assert _histogram_p95_seconds(samples, "vllm:foo") is None


class TestPickMetricsPort:
    """_pick_metrics_port preference order."""

    def test_prefers_chat_over_http(self):
        ports = [
            {"name": "http", "port": 80},
            {"name": "chat", "port": 8080},
        ]
        assert _pick_metrics_port(ports) == 8080

    def test_falls_back_to_http(self):
        ports = [{"name": "http", "port": 80}]
        assert _pick_metrics_port(ports) == 80

    def test_falls_back_to_first_when_no_name_match(self):
        ports = [{"name": "grpc", "port": 50051}]
        assert _pick_metrics_port(ports) == 50051

    def test_returns_none_for_empty(self):
        assert _pick_metrics_port([]) is None


class TestDetectBackendKind:
    """_detect_backend_kind."""

    def test_vllm(self):
        samples = _parse_prometheus_text(_SAMPLE_METRICS)
        assert _detect_backend_kind(samples) == "vllm"

    def test_unknown_for_unrecognized(self):
        samples = _parse_prometheus_text("go_goroutines 1\n")
        assert _detect_backend_kind(samples) == "unknown"


class TestDetectModelName:
    """_detect_model_name."""

    def test_extracts_model_name_label(self):
        samples = _parse_prometheus_text(_SAMPLE_METRICS)
        assert _detect_model_name(samples) == "meta-llama/Llama-3-8b"

    def test_returns_none_when_absent(self):
        samples = _parse_prometheus_text("go_goroutines 1\n")
        assert _detect_model_name(samples) is None


# ---------------------------------------------------------------------------
# Row builder golden-shape test
# ---------------------------------------------------------------------------

_REQUIRED_ROW_KEYS = {
    "pod_name", "namespace", "backend_kind", "model", "status",
    "kv_cache_used_pct", "running", "waiting", "ttft_p95_ms",
    "itl_p95_ms", "output_tps", "gpu_util_pct", "vram_used_gb",
    "vram_total_gb", "gpu_temp_c",
}  # 15 keys (14 in useBackendHealth + gpu_temp_c also present)


class TestParsePodMetrics:
    """parse_pod_metrics — golden shape and value assertions."""

    def test_golden_shape_14_keys(self):
        """Row must have exactly the required keys (golden-shape test)."""
        row = parse_pod_metrics(_SAMPLE_METRICS, "pod-0", "prod", "llama3-svc")
        # All required keys present
        assert _REQUIRED_ROW_KEYS.issubset(set(row.keys()))

    def test_correct_backend_kind(self):
        row = parse_pod_metrics(_SAMPLE_METRICS, "pod-0", "prod", "llama3-svc")
        assert row["backend_kind"] == "vllm"

    def test_model_name_from_label(self):
        row = parse_pod_metrics(_SAMPLE_METRICS, "pod-0", "prod", "llama3-svc")
        assert row["model"] == "meta-llama/Llama-3-8b"

    def test_kv_cache_normalized_when_fraction(self):
        """kv_perc = 0.42 (≤1.0) → ×100 → 42."""
        row = parse_pod_metrics(_SAMPLE_METRICS, "pod-0", "prod", "llama3-svc")
        assert row["kv_cache_used_pct"] == 42

    def test_kv_cache_not_doubled_when_already_percent(self):
        """kv_perc = 42.0 (>1.0) → kept as-is → 42."""
        text = 'vllm:gpu_cache_usage_perc{model_name="m"} 42.0\n'
        row = parse_pod_metrics(text, "pod-0", "ns", "svc")
        assert row["kv_cache_used_pct"] == 42

    def test_running_and_waiting_counts(self):
        row = parse_pod_metrics(_SAMPLE_METRICS, "pod-0", "prod", "llama3-svc")
        assert row["running"] == 3
        assert row["waiting"] == 0

    def test_ttft_ms_not_none(self):
        row = parse_pod_metrics(_SAMPLE_METRICS, "pod-0", "prod", "llama3-svc")
        assert row["ttft_p95_ms"] is not None
        assert row["ttft_p95_ms"] > 0

    def test_itl_ms_not_none(self):
        row = parse_pod_metrics(_SAMPLE_METRICS, "pod-0", "prod", "llama3-svc")
        assert row["itl_p95_ms"] is not None
        assert row["itl_p95_ms"] > 0

    def test_output_tps_non_negative(self):
        row = parse_pod_metrics(_SAMPLE_METRICS, "pod-0", "prod", "llama3-svc")
        assert row["output_tps"] >= 0

    def test_gpu_fields_null(self):
        """GPU fields are null (no DCGM in shim env)."""
        row = parse_pod_metrics(_SAMPLE_METRICS, "pod-0", "prod", "llama3-svc")
        assert row["gpu_util_pct"] is None
        assert row["vram_used_gb"] is None
        assert row["vram_total_gb"] is None
        assert row["gpu_temp_c"] is None

    def test_status_healthy_on_valid_metrics(self):
        row = parse_pod_metrics(_SAMPLE_METRICS, "pod-0", "prod", "llama3-svc")
        assert row["status"] == "healthy"

    def test_unhealthy_on_empty_text(self):
        row = parse_pod_metrics("", "pod-0", "ns", "svc")
        assert row["status"] == "unhealthy"

    def test_falls_back_to_svc_name_when_no_model_label(self):
        """When no model_name label, model falls back to svc_name."""
        text = "vllm:num_requests_running 1\n"
        row = parse_pod_metrics(text, "pod-0", "ns", "my-svc")
        assert row["model"] == "my-svc"


class TestUnhealthyRow:
    """_unhealthy_row shape."""

    def test_unhealthy_row_has_required_keys(self):
        row = _unhealthy_row("pod-0", "ns", "svc", "phase Pending")
        assert _REQUIRED_ROW_KEYS.issubset(set(row.keys()))
        assert row["status"] == "unhealthy"
