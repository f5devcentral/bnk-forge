"""
Analyzer runtime metrics — Prometheus proxy scoped to one F5BigAnalyzer CR.

The F5BigAnalyzer CR declares the Prometheus endpoint(s) it consumes via
``spec.dataSources[]``. This service reads that endpoint, routes queries
through the cluster's K8s API-server service-proxy (so the Prometheus
service doesn't need to be externally exposed), runs a canonical set of
LLM-serving metrics queries, and returns a structured per-backend view
the frontend can render directly.

Design notes:
  - All PromQL queries are well-known patterns for LiteLLM / vLLM / NIM
    metrics. If the cluster's Prometheus doesn't have those series, the
    specific field returns null — no fake fallback.
  - The ``by(model|route)`` dimension maps to HTTPRoute backend names,
    so the UI can align metrics with the Traffic Distribution bars.
  - Errors (Prometheus unreachable, auth failure, malformed query) are
    surfaced as structured error fields rather than exceptions — the UI
    degrades gracefully.
"""

from __future__ import annotations

import logging
import urllib.parse
from datetime import UTC, datetime
from typing import Any

from kubernetes.client.rest import ApiException
from sqlalchemy.orm import Session

from services.kubernetes_service import KubernetesService

logger = logging.getLogger(__name__)


# Canonical query set for LLM-serving metrics. Designed for LiteLLM-compatible
# exporters; works with vLLM/NIM via common labels when series exist.
#
# The template placeholder {lookup} is substituted with the PromQL label
# selector that constrains queries to the specific analyzer's monitored
# backends (e.g. {service=~"claude-sonnet|nova-micro"}).
#
# by(model) aggregates per-backend so the UI can overlay metrics onto the
# Traffic Distribution rows.
_QUERIES: dict[str, str] = {
    # Templates use the sentinel ``__LOOKUP__`` for the backend-selector
    # substitution. We cannot use str.format() because PromQL itself uses
    # literal { } for label selectors.
    "latency_p50_ms": (
        'histogram_quantile(0.50, sum by (le, model) ('
        'rate(litellm_request_latency_seconds_bucket{__LOOKUP__}[5m]))) * 1000'
    ),
    "latency_p95_ms": (
        'histogram_quantile(0.95, sum by (le, model) ('
        'rate(litellm_request_latency_seconds_bucket{__LOOKUP__}[5m]))) * 1000'
    ),
    "tokens_per_second": (
        'sum by (model) (rate(litellm_tokens_generated_total{__LOOKUP__}[1m]))'
    ),
    "requests_per_minute": (
        'sum by (model) (rate(litellm_requests_total{__LOOKUP__}[1m])) * 60'
    ),
    "error_rate_pct": (
        '100 * ('
        'sum by (model) (rate(litellm_requests_total{status=~"5..",__LOOKUP__}[5m])) '
        '/ clamp_min(sum by (model) (rate(litellm_requests_total{__LOOKUP__}[5m])), 1e-9)'
        ')'
    ),
}


# vLLM-shape fallback queries. Runs when the LiteLLM-named series are absent
# (e.g. custom shims / NIM / DeepSeek-compatible exporters that emit vllm:*
# metrics with a ``model_name`` label). label_replace normalises
# ``model_name`` → ``model`` so downstream aggregation and _extract_by_backend
# treat both families identically.
#
# Notes on equivalence:
#   - tokens_per_second uses rate(time_per_output_token_seconds_count), i.e.
#     one histogram observation per generated token — same shape as a
#     tokens counter.
#   - requests_per_minute uses the _count companion of the e2e latency
#     histogram — one observation per request.
#   - error_rate has no direct vllm equivalent (no status-code label), so
#     it is intentionally absent from this set; the UI renders null.
_QUERIES_VLLM: dict[str, str] = {
    "latency_p50_ms": (
        'histogram_quantile(0.50, sum by (le, model) (label_replace('
        'rate(vllm:e2e_request_latency_seconds_bucket{__LOOKUP__}[5m]),'
        '"model","$1","model_name","(.+)"))) * 1000'
    ),
    "latency_p95_ms": (
        'histogram_quantile(0.95, sum by (le, model) (label_replace('
        'rate(vllm:e2e_request_latency_seconds_bucket{__LOOKUP__}[5m]),'
        '"model","$1","model_name","(.+)"))) * 1000'
    ),
    "tokens_per_second": (
        'sum by (model) (label_replace('
        'rate(vllm:time_per_output_token_seconds_count{__LOOKUP__}[1m]),'
        '"model","$1","model_name","(.+)"))'
    ),
    "requests_per_minute": (
        'sum by (model) (label_replace('
        'rate(vllm:e2e_request_latency_seconds_count{__LOOKUP__}[1m]),'
        '"model","$1","model_name","(.+)")) * 60'
    ),
}


def _parse_prom_endpoint(endpoint: str) -> tuple[str, str, str, int] | None:
    """
    Parse an in-cluster Prometheus endpoint URL into (scheme, namespace, service, port).

    Accepts forms like:
      http://kube-prometheus-stack-prometheus.monitoring:9090
      http://prometheus.monitoring.svc.cluster.local:9090/prom
      https://prom.observability:9090
    Returns None if the URL doesn't look like an in-cluster service DNS name.
    """
    try:
        parsed = urllib.parse.urlparse(endpoint)
    except Exception:
        return None
    if not parsed.hostname:
        return None
    host = parsed.hostname
    parts = host.split(".")
    if len(parts) < 2:
        # Bare service name with no namespace — can't route
        return None
    service = parts[0]
    namespace = parts[1]
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    scheme = parsed.scheme or "http"
    return scheme, namespace, service, port


def _proxy_path(scheme: str, namespace: str, service: str, port: int, sub_path: str) -> str:
    """
    Build the K8s API-server service-proxy path for a Prometheus query.

    See: https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.28/#proxy-http-request-v1-core
    """
    # scheme prefix tells the proxy whether to tunnel as http or https
    scheme_prefix = "https:" if scheme == "https" else "http:"
    target = f"{scheme_prefix}{service}:{port}"
    # Strip leading slash from sub_path to avoid double slashes
    sp = sub_path.lstrip("/")
    return f"/api/v1/namespaces/{namespace}/services/{target}/proxy/{sp}"


def _extract_by_backend(
    result: dict[str, Any],
    backend_names: set[str],
    label_keys: tuple[str, ...] = ("model", "model_name", "service", "pool", "deployment"),
) -> dict[str, float]:
    """
    Convert a Prometheus vector response to ``{backend_name: value}``.

    Tries each candidate label key in order; first one present wins.
    """
    out: dict[str, float] = {}
    if not isinstance(result, dict) or result.get("status") != "success":
        return out
    data = result.get("data", {})
    if data.get("resultType") != "vector":
        return out
    for row in data.get("result", []) or []:
        metric = row.get("metric", {})
        name: str | None = None
        for key in label_keys:
            v = metric.get(key)
            if v:
                name = str(v)
                break
        if not name:
            # Unlabeled aggregate — skip (we only surface per-backend)
            continue
        if backend_names and name not in backend_names:
            continue
        try:
            val = float(row.get("value", [None, None])[1])
        except (TypeError, ValueError):
            continue
        out[name] = val
    return out


def _render_lookup(backend_names: set[str]) -> str:
    """
    Build a PromQL label selector matching any of the given backend names
    via the ``model`` label (LiteLLM convention). Empty set → empty selector.
    """
    if not backend_names:
        return ""
    pattern = "|".join(sorted(backend_names))
    return f'model=~"{pattern}"'


def _render_lookup_vllm(backend_names: set[str]) -> str:
    """
    Build a PromQL label selector matching any of the given backend names
    via the ``model_name`` label (vLLM / DeepSeek-compatible convention).
    Empty set → empty selector.
    """
    if not backend_names:
        return ""
    pattern = "|".join(sorted(backend_names))
    return f'model_name=~"{pattern}"'


def get_analyzer_runtime_metrics(
    db: Session,
    cluster_id: int,
    namespace: str,
    name: str,
) -> dict[str, Any]:
    """
    Fetch runtime metrics for one F5BigAnalyzer CR by proxying PromQL
    queries to the Prometheus endpoint declared in ``spec.dataSources``.

    Returns a dict shaped for direct UI consumption:
      {
        available: bool,
        endpoint: str | None,
        backends: [str],
        per_backend: { backend: { metric: value | None } },
        errors: { metric: str },        # any PromQL errors
        updated_at: ISO-8601,
      }
    """
    k8s_service = KubernetesService(db)
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)

    # --- 1. Load analyzer CR -------------------------------------------------
    try:
        analyzer = api_client.call_api(
            resource_path=f"/apis/k8s.f5net.com/v1alpha1/namespaces/{namespace}/f5-big-analyzers/{name}",
            method="GET",
            response_type="object",
            auth_settings=["BearerToken"],
            _return_http_data_only=True,
        )
    except ApiException as e:
        return {
            "available": False,
            "reason": f"analyzer CR not found: {e.reason}",
            "updated_at": datetime.now(UTC).isoformat(),
        }

    sources = (analyzer.get("spec") or {}).get("dataSources") or []
    if not sources:
        return {
            "available": False,
            "reason": "analyzer has no dataSources configured",
            "updated_at": datetime.now(UTC).isoformat(),
        }

    endpoint = sources[0].get("endpoint")
    parsed = _parse_prom_endpoint(endpoint) if endpoint else None
    if not parsed:
        return {
            "available": False,
            "reason": f"dataSource endpoint not recognisable as an in-cluster service: {endpoint}",
            "endpoint": endpoint,
            "updated_at": datetime.now(UTC).isoformat(),
        }

    scheme, ns, svc, port = parsed

    # --- 2. Discover backend names from spec.applications --------------------
    # For HTTPRoute applications, we'd need to resolve the route's backendRefs
    # to get the actual service/pod names used as metric labels. Keep simple
    # for now — pass the application names directly as potential label values.
    apps = (analyzer.get("spec") or {}).get("applications") or []
    backend_names: set[str] = set()
    for app in apps:
        if not isinstance(app, dict):
            continue
        app_name = app.get("name")
        app_ns = app.get("namespace", namespace)
        if not app_name:
            continue
        if not str(app.get("kind", "")).lower().startswith("httproute"):
            continue
        # Load the HTTPRoute and collect backend names
        try:
            route = api_client.call_api(
                resource_path=f"/apis/gateway.networking.k8s.io/v1/namespaces/{app_ns}/httproutes/{app_name}",
                method="GET",
                response_type="object",
                auth_settings=["BearerToken"],
                _return_http_data_only=True,
            )
        except ApiException:
            continue
        for rule in (route.get("spec") or {}).get("rules") or []:
            for ref in rule.get("backendRefs") or []:
                if ref.get("name"):
                    backend_names.add(str(ref["name"]))

    lookup_litellm = _render_lookup(backend_names)
    lookup_vllm = _render_lookup_vllm(backend_names)

    def _render(template: str, lookup: str) -> str:
        # Strip the empty-selector comma that appears when lookup is absent
        # (e.g. {status=~"5..",__LOOKUP__} → {status=~"5.."}). When lookup
        # is present, substitute as-is.
        if lookup:
            return template.replace("__LOOKUP__", lookup)
        return template.replace(",__LOOKUP__", "").replace("__LOOKUP__", "")

    query_path = _proxy_path(scheme, ns, svc, port, "api/v1/query")

    def _run(promql: str) -> tuple[dict[str, float] | None, str | None]:
        try:
            response = api_client.call_api(
                resource_path=query_path,
                method="GET",
                query_params=[("query", promql)],
                response_type="object",
                auth_settings=["BearerToken"],
                _return_http_data_only=True,
            )
        except ApiException as e:
            return None, f"{e.status} {e.reason}"
        except Exception as e:  # noqa: BLE001 — wide catch is the point
            return None, f"proxy error: {e}"
        return _extract_by_backend(response, backend_names), None

    # --- 3. Run PromQL queries via K8s service-proxy -------------------------
    #
    # Strategy: try the LiteLLM-shape query first. If it produces no data for
    # a backend, try the vLLM-shape equivalent (if one exists for that
    # metric). LiteLLM wins on ties — it's the canonical gateway shape; vLLM
    # fills in for custom shims that expose vllm:* series natively.
    per_backend: dict[str, dict[str, float | None]] = {b: {} for b in backend_names}
    errors: dict[str, str] = {}

    for metric_key, template in _QUERIES.items():
        merged: dict[str, float] = {}

        # 1. LiteLLM shape
        result, err = _run(_render(template, lookup_litellm))
        if err:
            errors[metric_key] = err
        elif result:
            merged.update(result)

        # 2. vLLM-shape fallback — fills gaps for backends LiteLLM didn't cover
        vllm_template = _QUERIES_VLLM.get(metric_key)
        if vllm_template is not None:
            vllm_result, vllm_err = _run(_render(vllm_template, lookup_vllm))
            if vllm_err and metric_key not in errors:
                errors[metric_key] = f"vllm: {vllm_err}"
            elif vllm_result:
                for backend_name, value in vllm_result.items():
                    merged.setdefault(backend_name, value)

        for backend_name in backend_names:
            per_backend.setdefault(backend_name, {})[metric_key] = merged.get(backend_name)

    return {
        "available": True,
        "endpoint": endpoint,
        "backends": sorted(backend_names),
        "per_backend": per_backend,
        "errors": errors,
        "updated_at": datetime.now(UTC).isoformat(),
    }
