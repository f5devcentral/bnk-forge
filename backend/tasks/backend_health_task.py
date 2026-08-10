"""
Backend-health Celery task.

Resolves an F5BigAnalyzer's monitored apps to their backend pods, scrapes each
pod's ``/metrics`` via the K8s API server proxy, and parses vLLM-shape series
into a per-pod row schema for the BackendHealthSection panel.

Runs in the celery worker (which has aws CLI for EKS auth). The parse is
in-process — no external binaries, no subprocess fork. We dropped llmtop after
its K8s API server proxy scrape silently failed in our setup; the proxy URL
itself works (verified by direct ``api_client.call_api(...)`` GET returning
the same Prometheus text llmtop couldn't read).

Per-pod metric mapping (matches the bedrock-shim's ``vllm:*`` emission):
  vllm:gpu_cache_usage_perc       → kv_cache_used_pct  (× 100; shim emits 0..1)
  vllm:num_requests_running       → running
  vllm:num_requests_waiting       → waiting
  vllm:time_per_output_token_seconds_bucket{le=...} → itl_p95_ms (histogram p95)
  vllm:request_generation_tokens_count    → output_tps  (sum / count snapshot)
  vllm:e2e_request_latency_seconds (mean as a stand-in for ttft until shim emits a real first-token histogram)

GPU fields (gpu_util_pct / vram / temp) stay null — the bedrock shims have no
GPU. Wiring these requires a DCGM exporter on cluster nodes; surface that as a
separate follow-up, not silent fake values.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from celery_app import celery_app
from database import get_db_context

logger = logging.getLogger(__name__)


# Regex for one Prometheus text-format sample line:
#   metric_name{label1="v1",label2="v2"} 12.5
# Or unlabeled:  metric_name 12.5
_SAMPLE_RE = re.compile(
    r'^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)'
    r'(?:\{(?P<labels>[^}]*)\})?\s+'
    r'(?P<value>-?[0-9eE.+\-]+|NaN|\+?Inf|-Inf)\s*$'
)
_LABEL_RE = re.compile(r'(?P<key>[a-zA-Z_][a-zA-Z0-9_]*)="(?P<val>[^"]*)"')


def _parse_prometheus_text(text: str) -> list[tuple[str, dict[str, str], float]]:
    """Yield (metric_name, labels, value) for every non-comment sample line."""
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
    Compute p95 from a Prometheus histogram's ``_bucket`` series — same shape as
    PromQL ``histogram_quantile(0.95, ...)`` on a single snapshot.

    Buckets are cumulative: ``..._bucket{le="0.5"}`` is "count of observations ≤ 0.5s".
    We linearly interpolate within the bucket where the 95th-percentile cutoff lands.
    Returns seconds, or None if no observations exist.
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
            # Linear interpolate inside this bucket
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

    A real rate needs two samples over time; from one snapshot we can only
    derive the average (sum/count). We surface that as the row's "throughput"
    proxy. For true rate the next iteration of this code can either keep a
    Redis-backed previous-snapshot or lift the rate computation to a PromQL
    query against the analyzer's own dataSource — both are bigger changes.
    """
    sum_val = _last_value(samples, f"{name}_sum")
    count_val = _last_value(samples, f"{name}_count")
    if sum_val is None or count_val is None or count_val <= 0:
        return None
    return sum_val / count_val  # mean tokens per request — not strictly TPS


@celery_app.task(name="tasks.backend_health.fetch_backend_health", bind=True)
def fetch_backend_health(
    self: Any,
    cluster_id: int,
    namespace: str,
    name: str,
) -> dict[str, Any]:
    """Resolve analyzer → backend services → pods → /metrics → row dicts."""
    from kubernetes.client.rest import ApiException

    from services.cluster_utils import get_cluster
    from services.kubernetes_service import KubernetesService

    with get_db_context() as db:
        cluster = get_cluster(db, cluster_id)
        api_client = KubernetesService(db).load_kubeconfig(cluster)

        try:
            analyzer = api_client.call_api(
                resource_path=f"/apis/k8s.f5net.com/v1alpha1/namespaces/{namespace}/f5-big-analyzers/{name}",
                method="GET",
                response_type="object",
                auth_settings=["BearerToken"],
                _return_http_data_only=True,
            )
        except ApiException as e:
            return _unavailable(f"analyzer CR not found: {e.reason}")

        backend_refs = _collect_backend_refs(api_client, analyzer, namespace)
        if not backend_refs:
            return _unavailable("analyzer has no resolvable HTTPRoute backendRefs")

        backends: list[dict[str, Any]] = []
        errors: dict[str, str] = {}

        for ns, svc_name in backend_refs:
            try:
                svc = api_client.call_api(
                    resource_path=f"/api/v1/namespaces/{ns}/services/{svc_name}",
                    method="GET",
                    response_type="object",
                    auth_settings=["BearerToken"],
                    _return_http_data_only=True,
                )
            except ApiException as e:
                errors[f"{ns}/{svc_name}"] = f"service not found: {e.reason}"
                continue

            selector = (svc.get("spec") or {}).get("selector") or {}
            metrics_port = _pick_metrics_port(svc)
            if not selector or metrics_port is None:
                errors[f"{ns}/{svc_name}"] = "service has no selector or metrics port"
                continue
            label_selector = ",".join(f"{k}={v}" for k, v in selector.items())

            try:
                pod_list = api_client.call_api(
                    resource_path=f"/api/v1/namespaces/{ns}/pods",
                    method="GET",
                    query_params=[("labelSelector", label_selector)],
                    response_type="object",
                    auth_settings=["BearerToken"],
                    _return_http_data_only=True,
                )
            except ApiException as e:
                errors[f"{ns}/{svc_name}"] = f"pod list failed: {e.reason}"
                continue

            for pod in (pod_list.get("items") or []):
                pod_name = (pod.get("metadata") or {}).get("name", "")
                if not pod_name:
                    continue
                phase = (pod.get("status") or {}).get("phase")
                if phase != "Running":
                    backends.append(_unhealthy_row(pod_name, ns, svc_name, f"pod phase {phase}"))
                    continue

                row, scrape_err = _scrape_and_parse(
                    api_client, ns, pod_name, metrics_port, svc_name
                )
                if scrape_err:
                    errors[f"{ns}/{pod_name}"] = scrape_err
                    backends.append(_unhealthy_row(pod_name, ns, svc_name, scrape_err))
                else:
                    backends.append(row)

    return {
        "available": True,
        "backends": backends,
        "errors": errors,
        "updated_at": datetime.now(UTC).isoformat(),
    }


def _collect_backend_refs(
    api_client: Any, analyzer: dict[str, Any], default_ns: str
) -> list[tuple[str, str]]:
    """Walk analyzer.spec.applications[] → HTTPRoute → (namespace, service_name) tuples."""
    from kubernetes.client.rest import ApiException

    apps = (analyzer.get("spec") or {}).get("applications") or []
    refs: list[tuple[str, str]] = []
    for app in apps:
        if not isinstance(app, dict):
            continue
        if not str(app.get("kind", "")).lower().startswith("httproute"):
            continue
        app_name = app.get("name")
        app_ns = app.get("namespace") or default_ns
        if not app_name:
            continue
        try:
            route = api_client.call_api(
                resource_path=f"/apis/gateway.networking.k8s.io/v1/namespaces/{app_ns}/httproutes/{app_name}",
                method="GET",
                response_type="object",
                auth_settings=["BearerToken"],
                _return_http_data_only=True,
            )
        except ApiException as e:
            logger.warning("failed to load HTTPRoute %s/%s: %s", app_ns, app_name, e.reason)
            continue
        for rule in (route.get("spec") or {}).get("rules") or []:
            for ref in rule.get("backendRefs") or []:
                ref_name = ref.get("name")
                if not ref_name:
                    continue
                ref_ns = ref.get("namespace") or app_ns
                refs.append((ref_ns, str(ref_name)))
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for r in refs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _pick_metrics_port(svc: dict[str, Any]) -> int | None:
    """
    Decide which Service port to scrape ``/metrics`` on.

    Preference order: ``chat`` → ``http`` → ``vllm`` → ``http-metrics`` →
    ``metrics`` → first.

    The bedrock-shim mounts ``/metrics`` on its FastAPI app (the ``chat`` port
    at 8080), and its separate ``metrics`` port (9100) is **unbound** — a known
    shim quirk we patched cluster-side via ServiceMonitor port redirect. Our
    scrape uses the same logic: prefer the API-serving port over the
    metrics-named port that may not actually be listening. Real vLLM Helm
    charts typically expose a single port named ``http`` so this still works.
    """
    ports = (svc.get("spec") or {}).get("ports") or []
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


def _scrape_and_parse(
    api_client: Any, ns: str, pod_name: str, port: int, svc_name: str
) -> tuple[dict[str, Any], str | None]:
    """GET /api/v1/.../pods/{pod}:{port}/proxy/metrics, parse, build row dict."""
    from kubernetes.client.rest import ApiException

    try:
        text = api_client.call_api(
            resource_path=f"/api/v1/namespaces/{ns}/pods/{pod_name}:{port}/proxy/metrics",
            method="GET",
            response_type="str",
            auth_settings=["BearerToken"],
            _return_http_data_only=True,
        )
    except ApiException as e:
        return {}, f"proxy GET failed: {e.status} {e.reason}"

    samples = _parse_prometheus_text(str(text))
    if not samples:
        return {}, "no samples in /metrics"

    backend_kind = _detect_backend_kind(samples)
    model = _detect_model_name(samples) or svc_name

    kv_perc = _last_value(samples, "vllm:gpu_cache_usage_perc")
    running = _last_value(samples, "vllm:num_requests_running")
    waiting = _last_value(samples, "vllm:num_requests_waiting")

    itl_p95_s = _histogram_p95_seconds(samples, "vllm:time_per_output_token_seconds")
    e2e_p95_s = _histogram_p95_seconds(samples, "vllm:e2e_request_latency_seconds")
    avg_gen_tokens = _rate_per_second(samples, "vllm:request_generation_tokens")

    row = {
        "pod_name": pod_name,
        "namespace": ns,
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
    return row, None


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
    """Read ``model_name`` label off any vllm:* sample (shim sets it consistently)."""
    for n, labels, _v in samples:
        if n.startswith("vllm:") and labels.get("model_name"):
            return labels["model_name"]
    return None


def _unhealthy_row(pod_name: str, ns: str, svc_name: str, _reason: str) -> dict[str, Any]:
    return {
        "pod_name": pod_name,
        "namespace": ns,
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
        "updated_at": datetime.now(UTC).isoformat(),
    }
