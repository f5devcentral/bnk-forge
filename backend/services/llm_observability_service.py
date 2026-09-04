"""
AI-gateway observability — live LogQL proxy over the HGX Loki store.

The ``llm-gateway`` job writes one JSON log line per request into an
in-cluster Loki (``llm-egress/loki:3100``). This service builds LogQL,
routes each query through the cluster's K8s API-server service-proxy (so
Loki isn't externally exposed), and reshapes the result into the UI
contract. Mirrors ``analyzer_metrics_service.py`` (Prometheus proxy) one
for one: same proxy-path builder, same ``api_client.call_api`` call, same
graceful-degradation envelope.

Design notes:
  - Loki drops anything older than ~1h unless ``start``/``end`` are set
    explicitly. Every query pins both to nanoseconds derived from ``range``.
  - Cost / tokens / latency / hit% are already top-level fields on each log
    line — we aggregate them, never recompute cost from bodies.
  - A failing Loki call never raises to the UI: per-query errors land in the
    ``errors`` map; if every query for an endpoint fails, the whole response
    degrades to ``available: False`` with a ``reason``.
  - provider is *inferred* from the model name (no identity substrate in the
    stream yet), surfaced as such in the UI.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any, TypeVar

from kubernetes.client.rest import ApiException
from sqlalchemy.orm import Session

from core.config import settings
from models.kubernetes import KubernetesCluster
from services.kubernetes_service import KubernetesService

logger = logging.getLogger(__name__)

# Presets → window length in seconds, and the query_range step used to bucket
# that window into ~100–200 points. Unknown ranges fall back to 1h.
_RANGE_SECONDS: dict[str, int] = {"1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800}
_STEP_SECONDS: dict[str, int] = {"1h": 60, "6h": 300, "24h": 900, "7d": 3600}

_JOB = 'job="llm-gateway"'

_T = TypeVar("_T")


def _esc_logql(value: str) -> str:
    """Escape a user-supplied value for a LogQL double-quoted string literal.

    Neutralizes injection via ``model`` / ``status`` / ``content_search``: a
    value like ``x" |= "admin`` would otherwise break out of the quotes and
    alter query semantics. Escaping ``\\`` and ``"`` keeps it a literal.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _run_parallel(thunks: list[Callable[[], _T]]) -> list[_T]:
    """Run independent blocking Loki calls concurrently, preserving order.

    Each endpoint fans several roundtrips through the K8s API-server proxy;
    firing them together instead of sequentially cuts the worst case
    (``rankings`` = 10 instant queries) from N roundtrips to ~1.
    """
    if len(thunks) <= 1:
        return [t() for t in thunks]
    with ThreadPoolExecutor(max_workers=min(len(thunks), 12)) as ex:
        return list(ex.map(lambda t: t(), thunks))


def _proxy_path(scheme: str, namespace: str, service: str, port: int, sub_path: str) -> str:
    """Build the K8s API-server service-proxy path for a Loki query.

    Identical scheme to analyzer_metrics_service._proxy_path.
    """
    scheme_prefix = "https:" if scheme == "https" else "http:"
    target = f"{scheme_prefix}{service}:{port}"
    sp = sub_path.lstrip("/")
    return f"/api/v1/namespaces/{namespace}/services/{target}/proxy/{sp}"


def _provider_for(model: str) -> str:
    """Infer provider from model name. gpt/o1/o3/o4→openai, claude→anthropic,
    gemini→google, else unknown. Best-effort only — the stream has no identity."""
    m = (model or "").lower()
    if m.startswith(("gpt", "o1", "o3", "o4")):
        return "openai"
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith("gemini"):
        return "google"
    return "unknown"


def _iso(epoch_s: float) -> str:
    return datetime.fromtimestamp(epoch_s, UTC).isoformat()


def _scalar(data: dict[str, Any] | None) -> float:
    """First sample of an instant-vector response, as a float (0.0 if empty)."""
    if not isinstance(data, dict):
        return 0.0
    result = (data.get("data") or {}).get("result") or []
    if not result:
        return 0.0
    try:
        return float(result[0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return 0.0


def _by_label(data: dict[str, Any] | None, label: str = "model") -> dict[str, float]:
    """Instant vector → {label_value: float}, dropping unlabeled/NaN rows."""
    out: dict[str, float] = {}
    if not isinstance(data, dict):
        return out
    for row in (data.get("data") or {}).get("result") or []:
        name = (row.get("metric") or {}).get(label)
        if not name:
            continue
        try:
            out[str(name)] = float(row["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return out


def _matrix(data: dict[str, Any] | None) -> list[tuple[dict[str, str], list[dict[str, float]]]]:
    """query_range matrix → [(labels, [{ts, value}, ...]), ...]."""
    out: list[tuple[dict[str, str], list[dict[str, float]]]] = []
    if not isinstance(data, dict):
        return out
    for row in (data.get("data") or {}).get("result") or []:
        labels = row.get("metric") or {}
        points: list[dict[str, float]] = []
        for pair in row.get("values") or []:
            try:
                points.append({"ts": _iso(float(pair[0])), "value": float(pair[1])})
            except (IndexError, TypeError, ValueError):
                continue
        out.append((labels, points))
    return out


class LlmObservabilityService:
    """Live LogQL proxy for the ``llm-gateway`` request stream in one cluster."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self._k8s = KubernetesService(db)
        self._ns = settings.LOKI_NAMESPACE
        self._svc = settings.LOKI_SERVICE
        self._port = settings.LOKI_PORT
        self._scheme = settings.LOKI_SCHEME

    def _active_clusters(self) -> list[KubernetesCluster]:
        return (
            self.db.query(KubernetesCluster)
            .filter(KubernetesCluster.status == "active")
            .order_by(KubernetesCluster.name.asc())
            .all()
        )

    # -- endpoint / time helpers ------------------------------------------

    @property
    def _endpoint(self) -> str:
        return f"{self._scheme}://{self._svc}.{self._ns}:{self._port}"

    @staticmethod
    def _range_s(range_: str) -> int:
        return _RANGE_SECONDS.get(range_, 3600)

    @staticmethod
    def _step_s(range_: str) -> int:
        return _STEP_SECONDS.get(range_, 60)

    def _selector(
        self,
        model: str | None = None,
        status: str | None = None,
        status_re: str | None = None,
        not_status_re: str | None = None,
    ) -> str:
        parts = [_JOB]
        if model:
            parts.append(f'model="{_esc_logql(model)}"')
        if status:
            parts.append(f'status="{_esc_logql(status)}"')
        if status_re:
            parts.append(f'status=~"{status_re}"')
        if not_status_re:
            parts.append(f'status!~"{not_status_re}"')
        return "{" + ", ".join(parts) + "}"

    # -- Loki proxy calls -------------------------------------------------

    def _client(self, cluster_id: int) -> Any:
        cluster = self._k8s.get_cluster(cluster_id)
        return self._k8s.load_kubeconfig(cluster)

    def _call(
        self, api_client: Any, sub_path: str, params: list[tuple[str, str]]
    ) -> tuple[dict[str, Any] | None, str | None]:
        path = _proxy_path(self._scheme, self._ns, self._svc, self._port, sub_path)
        try:
            data = api_client.call_api(
                resource_path=path,
                method="GET",
                query_params=params,
                response_type="object",
                auth_settings=["BearerToken"],
                _return_http_data_only=True,
            )
        except ApiException as e:
            return None, f"{e.status} {e.reason}"
        except Exception as e:  # noqa: BLE001 — wide catch is the point; never raise to UI
            return None, f"proxy error: {e}"
        return data, None

    def _instant(
        self, api_client: Any, query: str, time_ns: int
    ) -> tuple[dict[str, Any] | None, str | None]:
        return self._call(
            api_client,
            "loki/api/v1/query",
            [("query", query), ("time", str(time_ns))],
        )

    def _range(
        self,
        api_client: Any,
        query: str,
        start_ns: int,
        end_ns: int,
        step_s: int,
        extra: list[tuple[str, str]] | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        params = [
            ("query", query),
            ("start", str(start_ns)),
            ("end", str(end_ns)),
            ("step", str(step_s)),
        ]
        if extra:
            params.extend(extra)
        return self._call(api_client, "loki/api/v1/query_range", params)

    def _unavailable(self, reason: str) -> dict[str, Any]:
        return {
            "available": False,
            "endpoint": self._endpoint,
            "reason": reason,
            "updated_at": datetime.now(UTC).isoformat(),
        }

    def _ok(self, **data: Any) -> dict[str, Any]:
        return {
            "available": True,
            "endpoint": self._endpoint,
            "updated_at": datetime.now(UTC).isoformat(),
            **data,
        }

    # -- endpoints --------------------------------------------------------

    def stats(
        self, cluster_id: int | None, range_: str, model: str | None = None, status: str | None = None
    ) -> dict[str, Any]:
        if cluster_id is None:
            clusters = self._active_clusters()
            if not clusters:
                return self._unavailable("No active Kubernetes clusters available")
            results = _run_parallel([
                lambda c=c: self.stats(c.id, range_, model, status)
                for c in clusters
            ])
            valid = [r for r in results if r.get("available")]
            if not valid:
                err = next((r.get("reason") for r in results if r.get("reason")), "All clusters unavailable")
                return self._unavailable(err)
            total_requests = sum(v.get("total_requests", 0) for v in valid)
            total_tokens = sum(v.get("total_tokens", 0) for v in valid)
            total_cost = sum(v.get("total_cost", 0.0) for v in valid)
            total_models = max((v.get("models", 0) for v in valid), default=0)
            if total_requests > 0:
                success_rate = sum(v.get("success_rate", 0.0) * v.get("total_requests", 0) for v in valid) / total_requests
                avg_latency_ms = sum(v.get("avg_latency_ms", 0.0) * v.get("total_requests", 0) for v in valid) / total_requests
            else:
                success_rate = 0.0
                avg_latency_ms = 0.0
            return self._ok(
                total_requests=int(total_requests),
                success_rate=success_rate,
                avg_latency_ms=avg_latency_ms,
                total_tokens=int(total_tokens),
                total_cost=total_cost,
                models=int(total_models),
                errors={},
            )

        api_client = self._client(cluster_id)
        end_ns = time.time_ns()
        rng = f"{self._range_s(range_)}s"
        sel = self._selector(model, status)
        # success numerator = the filtered slice AND 2xx, so success_rate reflects
        # the caller's status filter instead of the whole window.
        sel_ok = self._selector(model, status, status_re="2..")

        queries: dict[str, str] = {
            "total_requests": f"sum(count_over_time({sel}[{rng}]))",
            "success": f"sum(count_over_time({sel_ok}[{rng}]))",
            "avg_latency_ms": f"avg(avg_over_time({sel} | json | unwrap latency_ms [{rng}]))",
            "total_tokens": f"sum(sum_over_time({sel} | json | unwrap total_tk [{rng}]))",
            "total_cost": f"sum(sum_over_time({sel} | json | unwrap cost [{rng}]))",
            "models": f"count(count by (model)(count_over_time({sel}[{rng}])))",
        }

        keys = list(queries)
        results = _run_parallel(
            [lambda q=queries[k]: self._instant(api_client, q, end_ns) for k in keys]
        )
        errors: dict[str, str] = {}
        values: dict[str, float] = {}
        for key, (data, err) in zip(keys, results, strict=True):
            if err:
                errors[key] = err
            else:
                values[key] = _scalar(data)

        if len(errors) == len(queries):
            return self._unavailable(next(iter(errors.values())))

        total = values.get("total_requests", 0.0)
        success = values.get("success", 0.0)
        return self._ok(
            total_requests=int(total),
            success_rate=(success / total) if total else 0.0,
            avg_latency_ms=values.get("avg_latency_ms", 0.0),
            total_tokens=int(values.get("total_tokens", 0.0)),
            total_cost=values.get("total_cost", 0.0),
            models=int(values.get("models", 0.0)),
            errors=errors,
        )

    def histogram(
        self,
        cluster_id: int | None,
        range_: str,
        metric: str,
        model: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        if cluster_id is None:
            clusters = self._active_clusters()
            if not clusters:
                return self._unavailable("No active Kubernetes clusters available")
            results = _run_parallel([
                lambda c=c: self.histogram(c.id, range_, metric, model, status)
                for c in clusters
            ])
            valid = [r for r in results if r.get("available")]
            if not valid:
                err = next((r.get("reason") for r in results if r.get("reason")), "All clusters unavailable")
                return self._unavailable(err)
            step_s = valid[0].get("step_s", self._step_s(range_))
            series_map: dict[str, dict[str, float]] = {}
            for r in valid:
                for s in r.get("series", []):
                    s_name = s.get("name", "")
                    if s_name not in series_map:
                        series_map[s_name] = {}
                    for p in s.get("points", []):
                        ts = p.get("ts", "")
                        if metric == "latency":
                            curr = series_map[s_name].get(ts, 0.0)
                            series_map[s_name][ts] = (curr + p.get("value", 0.0)) / (2 if curr else 1)
                        else:
                            series_map[s_name][ts] = series_map[s_name].get(ts, 0.0) + p.get("value", 0.0)
            merged_series = []
            for s_name, points_dict in series_map.items():
                points = [{"ts": ts, "value": val} for ts, val in sorted(points_dict.items())]
                merged_series.append({"name": s_name, "points": points})
            return self._ok(metric=metric, step_s=step_s, series=merged_series, errors={})

        api_client = self._client(cluster_id)
        end_ns = time.time_ns()
        start_ns = end_ns - self._range_s(range_) * 1_000_000_000
        step_s = self._step_s(range_)
        step = f"{step_s}s"
        sel = self._selector(model, status)

        # Each metric maps to one or more named LogQL series.
        specs: dict[str, list[tuple[str, str]]] = {
            "requests": [
                ("success", f"sum(count_over_time({self._selector(model, status_re='2..')}[{step}]))"),
                ("error", f"sum(count_over_time({self._selector(model, not_status_re='2..')}[{step}]))"),
            ],
            "tokens": [
                ("prompt", f"sum(sum_over_time({sel} | json | unwrap prompt_tk [{step}]))"),
                ("completion", f"sum(sum_over_time({sel} | json | unwrap comp_tk [{step}]))"),
                ("cached", f"sum(sum_over_time({sel} | json | unwrap cached [{step}]))"),
            ],
            "cost": [("__by_model__", f"sum by (model)(sum_over_time({sel} | json | unwrap cost [{step}]))")],
            "models": [("__by_model__", f"count by (model)(count_over_time({sel}[{step}]))")],
            "latency": [
                ("avg", f"avg(avg_over_time({sel} | json | unwrap latency_ms [{step}]))"),
                ("p90", f"quantile_over_time(0.9, {sel} | json | unwrap latency_ms [{step}])"),
                ("p95", f"quantile_over_time(0.95, {sel} | json | unwrap latency_ms [{step}])"),
                ("p99", f"quantile_over_time(0.99, {sel} | json | unwrap latency_ms [{step}])"),
            ],
        }
        if metric not in specs:
            return self._unavailable(f"unknown metric: {metric}")

        series, errors = self._collect_series(api_client, specs[metric], start_ns, end_ns, step_s)
        if series is None:
            return self._unavailable(next(iter(errors.values())))
        return self._ok(metric=metric, step_s=step_s, series=series, errors=errors)

    def _collect_series(
        self,
        api_client: Any,
        specs: list[tuple[str, str]],
        start_ns: int,
        end_ns: int,
        step_s: int,
        fold_provider: bool = False,
        fold_mean: bool = False,
    ) -> tuple[list[dict[str, Any]] | None, dict[str, str]]:
        """Run each (name, query) as a query_range and reshape to series.

        ``__by_model__`` fans the matrix out to one series per label (model, or
        provider when ``fold_provider``). ``fold_mean`` averages folded points
        instead of summing (latency). Returns (None, errors) when every query
        failed so the caller can degrade the whole response.
        """
        series: list[dict[str, Any]] = []
        errors: dict[str, str] = {}
        call_results = _run_parallel(
            [lambda q=query: self._range(api_client, q, start_ns, end_ns, step_s) for _, query in specs]
        )
        for (name, _query), (data, err) in zip(specs, call_results, strict=True):
            if err:
                errors[name] = err
                continue
            matrix = _matrix(data)
            if name == "__by_model__":
                series.extend(self._fold(matrix, fold_provider, fold_mean))
            else:
                points = matrix[0][1] if matrix else []
                series.append({"name": name, "points": points})

        if errors and not series:
            return None, errors
        return series, errors

    @staticmethod
    def _fold(
        matrix: list[tuple[dict[str, str], list[dict[str, float]]]],
        by_provider: bool,
        mean: bool,
    ) -> list[dict[str, Any]]:
        """Fold per-model matrix rows into per-model or per-provider series."""
        # key -> ts -> list of values (list so we can mean or sum)
        agg: dict[str, dict[str, list[float]]] = {}
        for labels, points in matrix:
            model = labels.get("model", "unknown")
            key = _provider_for(model) if by_provider else model
            bucket = agg.setdefault(key, {})
            for p in points:
                bucket.setdefault(p["ts"], []).append(p["value"])
        out: list[dict[str, Any]] = []
        for key in sorted(agg):
            pts = [
                {"ts": ts, "value": (sum(vals) / len(vals)) if mean else sum(vals)}
                for ts, vals in sorted(agg[key].items())
            ]
            out.append({"name": key, "points": pts})
        return out

    def provider_usage(
        self,
        cluster_id: int | None,
        range_: str,
        metric: str,
        model: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        if cluster_id is None:
            clusters = self._active_clusters()
            if not clusters:
                return self._unavailable("No active Kubernetes clusters available")
            results = _run_parallel([
                lambda c=c: self.provider_usage(c.id, range_, metric, model, status)
                for c in clusters
            ])
            valid = [r for r in results if r.get("available")]
            if not valid:
                err = next((r.get("reason") for r in results if r.get("reason")), "All clusters unavailable")
                return self._unavailable(err)
            step_s = valid[0].get("step_s", self._step_s(range_))
            series_map: dict[str, dict[str, float]] = {}
            for r in valid:
                for s in r.get("series", []):
                    s_name = s.get("name", "")
                    if s_name not in series_map:
                        series_map[s_name] = {}
                    for p in s.get("points", []):
                        ts = p.get("ts", "")
                        if metric == "latency":
                            curr = series_map[s_name].get(ts, 0.0)
                            series_map[s_name][ts] = (curr + p.get("value", 0.0)) / (2 if curr else 1)
                        else:
                            series_map[s_name][ts] = series_map[s_name].get(ts, 0.0) + p.get("value", 0.0)
            merged_series = []
            for s_name, points_dict in series_map.items():
                points = [{"ts": ts, "value": val} for ts, val in sorted(points_dict.items())]
                merged_series.append({"name": s_name, "points": points})
            return self._ok(metric=metric, step_s=step_s, series=merged_series, errors={})

        api_client = self._client(cluster_id)
        end_ns = time.time_ns()
        start_ns = end_ns - self._range_s(range_) * 1_000_000_000
        step_s = self._step_s(range_)
        step = f"{step_s}s"
        sel = self._selector(model, status)

        by_model = {
            "cost": f"sum by (model)(sum_over_time({sel} | json | unwrap cost [{step}]))",
            "tokens": f"sum by (model)(sum_over_time({sel} | json | unwrap total_tk [{step}]))",
            "latency": f"avg by (model)(avg_over_time({sel} | json | unwrap latency_ms [{step}]))",
        }
        if metric not in by_model:
            return self._unavailable(f"unknown metric: {metric}")

        # latency folds by mean across models of a provider; cost/tokens sum.
        series, errors = self._collect_series(
            api_client,
            [("__by_model__", by_model[metric])],
            start_ns,
            end_ns,
            step_s,
            fold_provider=True,
            fold_mean=(metric == "latency"),
        )
        if series is None:
            return self._unavailable(next(iter(errors.values())))
        return self._ok(metric=metric, step_s=step_s, series=series, errors=errors)

    def rankings(
        self, cluster_id: int | None, range_: str, model: str | None = None, status: str | None = None
    ) -> dict[str, Any]:
        if cluster_id is None:
            clusters = self._active_clusters()
            if not clusters:
                return self._unavailable("No active Kubernetes clusters available")
            results = _run_parallel([
                lambda c=c: self.rankings(c.id, range_, model, status)
                for c in clusters
            ])
            valid = [r for r in results if r.get("available")]
            if not valid:
                err = next((r.get("reason") for r in results if r.get("reason")), "All clusters unavailable")
                return self._unavailable(err)
            models_acc: dict[str, dict[str, Any]] = {}
            for r in valid:
                for row in r.get("rows", []):
                    m = row["model"]
                    if m not in models_acc:
                        models_acc[m] = {
                            "model": m,
                            "provider": row["provider"],
                            "requests": 0,
                            "tokens": 0,
                            "cost": 0.0,
                            "total_latency_weighted": 0.0,
                            "success_weighted": 0.0,
                            "trend": row.get("trend", {}),
                        }
                    acc = models_acc[m]
                    reqs = row.get("requests", 0)
                    acc["requests"] += reqs
                    acc["tokens"] += row.get("tokens", 0)
                    acc["cost"] += row.get("cost", 0.0)
                    acc["total_latency_weighted"] += row.get("avg_latency_ms", 0.0) * reqs
                    acc["success_weighted"] += row.get("success_rate", 0.0) * reqs
            rows = []
            for m, acc in models_acc.items():
                reqs = acc["requests"]
                rows.append({
                    "model": m,
                    "provider": acc["provider"],
                    "requests": reqs,
                    "success_rate": (acc["success_weighted"] / reqs) if reqs else 0.0,
                    "tokens": acc["tokens"],
                    "cost": acc["cost"],
                    "avg_latency_ms": (acc["total_latency_weighted"] / reqs) if reqs else 0.0,
                    "trend": acc["trend"],
                })
            rows.sort(key=lambda x: x["requests"], reverse=True)
            return self._ok(rows=rows, errors={})

        api_client = self._client(cluster_id)
        rng_s = self._range_s(range_)
        rng = f"{rng_s}s"
        cur_ns = time.time_ns()
        prev_ns = cur_ns - rng_s * 1_000_000_000  # previous window ends where current starts
        sel = self._selector(model, status)
        sel_ok = self._selector(model, status, status_re="2..")

        metrics = {
            "requests": f"sum by (model)(count_over_time({sel}[{rng}]))",
            "tokens": f"sum by (model)(sum_over_time({sel} | json | unwrap total_tk [{rng}]))",
            "cost": f"sum by (model)(sum_over_time({sel} | json | unwrap cost [{rng}]))",
            "latency": f"avg by (model)(avg_over_time({sel} | json | unwrap latency_ms [{rng}]))",
            "success": f"sum by (model)(count_over_time({sel_ok}[{rng}]))",
        }

        # Fire current + previous window for every metric concurrently (10 calls).
        keys = list(metrics)
        cur_res = _run_parallel(
            [lambda q=metrics[k]: self._instant(api_client, q, cur_ns) for k in keys]
        )
        prev_res = _run_parallel(
            [lambda q=metrics[k]: self._instant(api_client, q, prev_ns) for k in keys]
        )
        errors: dict[str, str] = {}
        cur: dict[str, dict[str, float]] = {}
        prev: dict[str, dict[str, float]] = {}
        for key, (cdata, cerr) in zip(keys, cur_res, strict=True):
            if cerr:
                errors[key] = cerr
            else:
                cur[key] = _by_label(cdata)
        for key, (_pdata, perr) in zip(keys, prev_res, strict=True):
            if not perr:
                prev[key] = _by_label(_pdata)

        if len(errors) == len(metrics):
            return self._unavailable(next(iter(errors.values())))

        rows = self._rank_rows(cur, prev)
        return self._ok(rows=rows, errors=errors)

    @staticmethod
    def _rank_rows(
        cur: dict[str, dict[str, float]], prev: dict[str, dict[str, float]]
    ) -> list[dict[str, Any]]:
        def _trend(model: str, key: str) -> float | None:
            c = cur.get(key, {}).get(model, 0.0)
            p = prev.get(key, {}).get(model, 0.0)
            return ((c - p) / p) if p else None

        models = sorted(cur.get("requests", {}))
        rows: list[dict[str, Any]] = []
        for m in models:
            requests = cur.get("requests", {}).get(m, 0.0)
            success = cur.get("success", {}).get(m, 0.0)
            rows.append(
                {
                    "model": m,
                    "provider": _provider_for(m),
                    "requests": int(requests),
                    "success_rate": (success / requests) if requests else 0.0,
                    "tokens": int(cur.get("tokens", {}).get(m, 0.0)),
                    "cost": cur.get("cost", {}).get(m, 0.0),
                    "avg_latency_ms": cur.get("latency", {}).get(m, 0.0),
                    "trend": {
                        "requests": _trend(m, "requests"),
                        "tokens": _trend(m, "tokens"),
                        "cost": _trend(m, "cost"),
                    },
                }
            )
        rows.sort(key=lambda r: r["requests"], reverse=True)
        return rows

    def logs(
        self,
        cluster_id: int | None,
        range_: str,
        model: str | None = None,
        status: str | None = None,
        limit: int = 50,
        content_search: str | None = None,
        end: int | None = None,
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 1000))
        if cluster_id is None:
            clusters = self._active_clusters()
            if not clusters:
                return self._unavailable("No active Kubernetes clusters available")
            results = _run_parallel([
                lambda c=c: self.logs(c.id, range_, model, status, limit, content_search, end)
                for c in clusters
            ])
            valid = [r for r in results if r.get("available")]
            if not valid:
                err = next((r.get("reason") for r in results if r.get("reason")), "All clusters unavailable")
                return self._unavailable(err)
            all_rows: list[dict[str, Any]] = []
            for r in valid:
                all_rows.extend(r.get("rows", []))
            all_rows.sort(key=lambda x: x.get("ts", ""), reverse=True)
            trimmed = all_rows[:limit]
            return self._ok(rows=trimmed, next_end=None, errors={})

        api_client = self._client(cluster_id)
        cluster = self._k8s.get_cluster(cluster_id)
        cluster_name = cluster.name if cluster else f"cluster-{cluster_id}"
        end_ns = end if end else time.time_ns()
        start_ns = end_ns - self._range_s(range_) * 1_000_000_000

        sel = self._selector(model, status)
        line_filter = f' |= "{_esc_logql(content_search)}"' if content_search else ""
        query = f"{sel}{line_filter} | json"

        data, err = self._range(
            api_client,
            query,
            start_ns,
            end_ns,
            self._step_s(range_),
            extra=[("limit", str(limit)), ("direction", "backward")],
        )
        if err:
            return self._unavailable(err)

        rows, oldest_ns = self._parse_log_lines(data)
        for r in rows:
            r["cluster_id"] = cluster_id
            r["cluster_name"] = cluster_name
        next_end = str(oldest_ns - 1) if (oldest_ns is not None and len(rows) >= limit) else None
        return self._ok(rows=rows, next_end=next_end, errors={})

    @staticmethod
    def _parse_log_lines(data: dict[str, Any] | None) -> tuple[list[dict[str, Any]], int | None]:
        """Flatten Loki streams into request rows, newest first. Returns
        (rows, oldest_ts_ns) — the cursor for 'load older'."""
        entries: list[tuple[int, str, dict]] = []
        if isinstance(data, dict):
            for stream in (data.get("data") or {}).get("result") or []:
                stream_labels = stream.get("stream", {})
                for pair in stream.get("values") or []:
                    try:
                        entries.append((int(pair[0]), pair[1], stream_labels))
                    except (IndexError, TypeError, ValueError):
                        continue
        entries.sort(key=lambda e: e[0], reverse=True)  # newest first

        rows: list[dict[str, Any]] = []
        for ts_ns, line, stream_labels in entries:
            try:
                rec = json.loads(line)
            except (TypeError, ValueError):
                continue
            model = str(rec.get("model") or stream_labels.get("model") or "")
            status = str(rec.get("status") or stream_labels.get("status") or "")
            rows.append(
                {
                    "ts": _iso(ts_ns / 1_000_000_000),
                    "type": "success" if status.startswith("2") else "error",
                    "message": str(rec.get("userq", "")),
                    "model": model,
                    "latency_ms": float(rec.get("latency_ms", 0) or 0),
                    "prompt_tk": int(rec.get("prompt_tk", 0) or 0),
                    "comp_tk": int(rec.get("comp_tk", 0) or 0),
                    "total_tk": int(rec.get("total_tk", 0) or 0),
                    "cost": float(rec.get("cost", 0) or 0),
                    "status": status,
                    "req_body": str(rec.get("req_body", "")),
                    "resp_body": str(rec.get("resp_body", "")),
                }
            )
        oldest_ns = entries[-1][0] if entries else None
        return rows, oldest_ns

    def filterdata(self, cluster_id: int | None, range_: str) -> dict[str, Any]:
        if cluster_id is None:
            clusters = self._active_clusters()
            if not clusters:
                return self._unavailable("No active Kubernetes clusters available")
            results = _run_parallel([
                lambda c=c: self.filterdata(c.id, range_)
                for c in clusters
            ])
            valid = [r for r in results if r.get("available")]
            if not valid:
                err = next((r.get("reason") for r in results if r.get("reason")), "All clusters unavailable")
                return self._unavailable(err)
            models = sorted(set(m for r in valid for m in r.get("models", [])))
            statuses = sorted(set(s for r in valid for s in r.get("statuses", [])))
            return self._ok(models=models, statuses=statuses, errors={})

        api_client = self._client(cluster_id)
        end_ns = time.time_ns()
        start_ns = end_ns - self._range_s(range_) * 1_000_000_000
        bounds = [("start", str(start_ns)), ("end", str(end_ns))]

        labels = ("model", "status")
        call_results = _run_parallel(
            [lambda lb=lb: self._call(api_client, f"loki/api/v1/label/{lb}/values", bounds) for lb in labels]
        )
        errors: dict[str, str] = {}
        result: dict[str, list[str]] = {}
        for label, (data, err) in zip(labels, call_results, strict=True):
            if err:
                errors[label] = err
            else:
                values = data.get("data") if isinstance(data, dict) else None
                result[label] = sorted(str(v) for v in (values or []))

        if len(errors) == 2:
            return self._unavailable(next(iter(errors.values())))
        return self._ok(
            models=result.get("model", []),
            statuses=result.get("status", []),
            errors=errors,
        )
