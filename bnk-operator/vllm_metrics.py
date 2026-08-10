"""
vLLM Prometheus metrics parser — in-operator port of backend_health_task.py.

Parses raw ``/metrics`` Prometheus text from a vLLM-compatible backend pod and
reshapes it into the BackendPodHealth row dict used by the forge frontend.
All logic is a verbatim port from ``backend/tasks/backend_health_task.py``; do
NOT diverge without updating both sides.

Stdlib-only (re).  No backend/ imports — this is a standalone operator module.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Prometheus text-format parser
# ---------------------------------------------------------------------------

_SAMPLE_RE = re.compile(
    r'^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)'
    r'(?:\{(?P<labels>[^}]*)\})?\s+'
    r'(?P<value>-?[0-9eE.+\-]+|NaN|\+?Inf|-Inf)\s*$'
)
_LABEL_RE = re.compile(r'(?P<key>[a-zA-Z_][a-zA-Z0-9_]*)="(?P<val>[^"]*)"')


def _parse_prometheus_text(text: str) -> list[tuple[str, dict[str, str], float]]:
    """Return (metric_name, labels, value) for every non-comment sample line."""
    out: list[tuple[str, dict[str, str], float]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _SAMPLE_RE.match(line)
        if not m:
            continue
        labels: dict[str, str] = {}
        if m.group("labels"):
            for lm in _LABEL_RE.finditer(m.group("labels")):
                labels[lm.group("key")] = lm.group("val")
        try:
            value = float(m.group("value"))
        except ValueError:
            continue
        out.append((m.group("name"), labels, value))
    return out


def _last_value(samples: list[tuple[str, dict[str, str], float]], name: str) -> float | None:
    """Return the value of the last sample matching ``name`` (any labels)."""
    for n, _labels, v in reversed(samples):
        if n == name:
            return v
    return None


def _histogram_p95_seconds(
    samples: list[tuple[str, dict[str, str], float]], histogram_name: str
) -> float | None:
    """
    Compute p95 from a Prometheus histogram's ``_bucket`` series.

    Linearly interpolates within the bucket where the 95th-percentile cutoff
    lands.  Returns seconds, or None if no observations exist.
    """
    bucket_name = f"{histogram_name}_bucket"
    buckets: list[tuple[float, float]] = []  # (le, cumulative_count)
    for n, labels, v in samples:
        if n != bucket_name:
            continue
        le_str = labels.get("le")
        if not le_str:
            continue
        try:
            le = float("inf") if le_str == "+Inf" else float(le_str)
        except ValueError:
            continue
        buckets.append((le, v))
    if not buckets:
        return None
    buckets.sort(key=lambda x: x[0])
    total = buckets[-1][1]
    if total <= 0:
        return None
    target = 0.95 * total
    prev_le = 0.0
    prev_count = 0.0
    for le, count in buckets:
        if count >= target:
            if le == float("inf") or count == prev_count:
                return prev_le if prev_count > 0 else le
            frac = (target - prev_count) / (count - prev_count)
            return prev_le + frac * (le - prev_le)
        prev_le = le
        prev_count = count
    return buckets[-1][0]


def _rate_per_second(
    samples: list[tuple[str, dict[str, str], float]], name: str, _window_seconds: int = 60
) -> float | None:
    """
    Approximate per-second rate from a counter's _sum series in a single snapshot.

    Returns mean tokens per request (sum/count) as a throughput proxy.
    """
    sum_val = _last_value(samples, f"{name}_sum")
    count_val = _last_value(samples, f"{name}_count")
    if sum_val is None or count_val is None or count_val <= 0:
        return None
    return sum_val / count_val


def _pick_metrics_port(ports: list[dict[str, Any]]) -> int | None:
    """
    Pick the scrape port from a Service's ports list.

    Preference: ``chat`` → ``http`` → ``vllm`` → ``http-metrics`` → ``metrics``
    → first available.  Mirrors backend_health_task._pick_metrics_port exactly.
    """
    if not ports:
        return None
    for name_pref in ("chat", "http", "vllm", "http-metrics", "metrics"):
        for p in ports:
            if p.get("name") == name_pref:
                try:
                    return int(p.get("port"))
                except (TypeError, ValueError):
                    continue
    try:
        return int(ports[0].get("port"))
    except (TypeError, ValueError):
        return None


def _detect_backend_kind(samples: list[tuple[str, dict[str, str], float]]) -> str:
    """Pick the backend kind from the metric prefix in the scrape."""
    seen = {n for n, _l, _v in samples}
    if any(n.startswith("vllm:") for n in seen):
        return "vllm"
    if any(n.startswith("sglang:") for n in seen):
        return "sglang"
    if any(n.startswith("tgi_") for n in seen):
        return "tgi"
    if any(n.startswith("lmcache_") for n in seen):
        return "lmcache"
    return "unknown"


def _detect_model_name(samples: list[tuple[str, dict[str, str], float]]) -> str | None:
    """Read ``model_name`` label off any vllm:* sample."""
    for n, labels, _v in samples:
        if n.startswith("vllm:") and labels.get("model_name"):
            return labels["model_name"]
    return None


def _unhealthy_row(pod_name: str, namespace: str, svc_name: str, _reason: str) -> dict[str, Any]:
    return {
        "pod_name": pod_name,
        "namespace": namespace,
        "backend_kind": "unknown",
        "model": svc_name,
        "status": "unhealthy",
        "kv_cache_used_pct": 0,
        "running": 0,
        "waiting": 0,
        "ttft_p95_ms": None,
        "itl_p95_ms": None,
        "output_tps": 0,
        "gpu_util_pct": None,
        "vram_used_gb": None,
        "vram_total_gb": None,
        "gpu_temp_c": None,
    }


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "backends": [],
        "errors": {},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def parse_pod_metrics(text: str, pod_name: str, namespace: str, svc_name: str) -> dict[str, Any]:
    """
    Parse raw Prometheus text from a pod's ``/metrics`` and build a row dict.

    Returns a BackendPodHealth-shape dict.  On empty/unparseable input returns
    an unhealthy row (caller already added the error to ``errors{}``).
    """
    samples = _parse_prometheus_text(text)
    if not samples:
        return _unhealthy_row(pod_name, namespace, svc_name, "no samples in /metrics")

    backend_kind = _detect_backend_kind(samples)
    model = _detect_model_name(samples) or svc_name

    kv_perc = _last_value(samples, "vllm:gpu_cache_usage_perc")
    running = _last_value(samples, "vllm:num_requests_running")
    waiting = _last_value(samples, "vllm:num_requests_waiting")

    itl_p95_s = _histogram_p95_seconds(samples, "vllm:time_per_output_token_seconds")
    e2e_p95_s = _histogram_p95_seconds(samples, "vllm:e2e_request_latency_seconds")
    avg_gen_tokens = _rate_per_second(samples, "vllm:request_generation_tokens")

    return {
        "pod_name": pod_name,
        "namespace": namespace,
        "backend_kind": backend_kind,
        "model": model,
        "status": "healthy",
        "kv_cache_used_pct": int(round((kv_perc or 0.0) * 100)) if kv_perc is not None and kv_perc <= 1.0 else int(round(kv_perc or 0.0)),
        "running": int(running or 0),
        "waiting": int(waiting or 0),
        "ttft_p95_ms": round(e2e_p95_s * 1000, 1) if e2e_p95_s is not None else None,
        "itl_p95_ms": round(itl_p95_s * 1000, 1) if itl_p95_s is not None else None,
        "output_tps": int(round(avg_gen_tokens)) if avg_gen_tokens is not None else 0,
        "gpu_util_pct": None,
        "vram_used_gb": None,
        "vram_total_gb": None,
        "gpu_temp_c": None,
    }
