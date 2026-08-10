"""
LLM Metrics Collector — periodic in-cluster scrape of vLLM ``/metrics``.

Modeled on ``HealthWatcher``: async loop, configurable interval + enable flag,
runs as a long-lived task alongside the health watcher.

Discovery logic:
  Enumerates F5BigAnalyzer CRs → spec.applications[] → HTTPRoute →
  backendRefs[] → Service selector → pods.  Mirrors ``_collect_backend_refs``
  + the service/pod loop in ``backend/tasks/backend_health_task.py`` (lines
  238-279 + 179-228), translated from raw K8s REST calls to kr8s async_get
  calls (operator's native pattern).

Scrape:
  Direct pod-IP GET (in-cluster, NOT via API-server proxy).  Port selection
  mirrors _pick_metrics_port order: chat→http→vllm→http-metrics→metrics.
  Running pod with no podIP yet → errors{} entry.

Push:
  One ``{type:"llm_metrics", analyzer:{namespace,name}, data:{...}}`` message
  per analyzer, per tick.  No delta suppression — metrics change every tick.
  Interval must be ≥15s.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

import aiohttp

from vllm_metrics import _pick_metrics_port, _unavailable, _unhealthy_row, parse_pod_metrics

logger = logging.getLogger("bnk-operator.llm-metrics")

# ---------------------------------------------------------------------------
# Configuration from env
# ---------------------------------------------------------------------------

LLM_METRICS_ENABLED = os.environ.get("LLM_METRICS_ENABLED", "false").lower() in ("true", "1", "yes")
_raw_interval = os.environ.get("LLM_METRICS_INTERVAL", "30")
try:
    LLM_METRICS_INTERVAL = max(15, int(_raw_interval))
except ValueError:
    logger.warning("Invalid LLM_METRICS_INTERVAL=%r; falling back to 30", _raw_interval)
    LLM_METRICS_INTERVAL = 30

# F5BigAnalyzer CRD group/version/resource
_ANALYZER_RESOURCE = "f5biganalyzers.k8s.f5net.com"
_HTTPROUTE_RESOURCE = "httproutes.gateway.networking.k8s.io"


class LlmMetricsCollector:
    """Periodic in-cluster LLM metrics collector."""

    def __init__(self, api: Any = None):
        """
        Args:
            api: kr8s.asyncio.Api instance. If None, creates one per cycle
                 (legacy; pass the shared api from CommandHandlers for
                 efficiency).
        """
        self._api = api
        self._error_count = 0
        self._cycle_count = 0

    @property
    def error_count(self) -> int:
        return self._error_count

    @property
    def cycle_count(self) -> int:
        return self._cycle_count

    async def collect_and_report(self, client: Any, shutdown_event: asyncio.Event):
        """
        Main loop — collect metrics every LLM_METRICS_INTERVAL seconds.

        Args:
            client: ControlPlaneClient or PollingClient (has send_llm_metrics()).
            shutdown_event: Set when operator is shutting down.
        """
        if not LLM_METRICS_ENABLED:
            logger.info("LLM metrics collector disabled (LLM_METRICS_ENABLED not set)")
            return

        # Wait until the client is connected
        while not client.connected and not shutdown_event.is_set():
            await asyncio.sleep(1)

        logger.info(
            "LLM metrics collector started (interval=%ds)", LLM_METRICS_INTERVAL
        )

        while not shutdown_event.is_set():
            try:
                if client.connected:
                    await self._collect_and_push(client)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._error_count += 1
                logger.error("LLM metrics collection error: %s", e)

            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=LLM_METRICS_INTERVAL)
                break  # Shutdown requested
            except asyncio.TimeoutError:
                pass

        logger.info(
            "LLM metrics collector stopped (cycles=%d, errors=%d)",
            self._cycle_count,
            self._error_count,
        )

    async def _collect_and_push(self, client: Any):
        """One collection cycle: discover → scrape → push per analyzer."""
        self._cycle_count += 1

        api = await self._get_api()
        analyzers = await self._list_analyzers(api)

        if not analyzers:
            logger.debug("LLM metrics: no F5BigAnalyzers found")
            return

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        ) as http:
            for analyzer in analyzers:
                metadata = analyzer.metadata if hasattr(analyzer, "metadata") else {}
                if isinstance(metadata, dict):
                    ns = metadata.get("namespace", "default")
                    an_name = metadata.get("name", "")
                else:
                    ns = getattr(metadata, "namespace", "default")
                    an_name = getattr(metadata, "name", "")

                if not an_name:
                    continue

                data = await self._scrape_analyzer(api, http, analyzer, ns, an_name)
                await client.send_llm_metrics(ns, an_name, data)
                logger.debug(
                    "LLM metrics pushed for %s/%s: available=%s backends=%d",
                    ns,
                    an_name,
                    data.get("available"),
                    len(data.get("backends", [])),
                )

    async def _get_api(self) -> Any:
        if self._api is not None:
            return self._api
        import kr8s.asyncio  # type: ignore[import-untyped]
        return await kr8s.asyncio.api()

    async def _list_analyzers(self, api: Any) -> list[Any]:
        """Return all F5BigAnalyzer CRs in all namespaces."""
        try:
            return list(await api.async_get(_ANALYZER_RESOURCE))
        except Exception as e:
            logger.warning("Failed to list F5BigAnalyzers: %s", e)
            return []

    async def _scrape_analyzer(
        self,
        api: Any,
        http: aiohttp.ClientSession,
        analyzer: Any,
        default_ns: str,
        an_name: str,
    ) -> dict[str, Any]:
        """
        Discover pods for one analyzer and scrape their /metrics.

        Returns a BackendHealthShape dict: {available, backends[], errors, updated_at}.
        """
        spec = analyzer.spec if hasattr(analyzer, "spec") else {}
        if isinstance(spec, dict):
            apps = spec.get("applications") or []
        else:
            apps = getattr(spec, "applications", []) or []

        backend_refs = await self._collect_backend_refs(api, apps, default_ns, an_name)
        if not backend_refs:
            return _unavailable("analyzer has no resolvable HTTPRoute backendRefs")

        backends: list[dict[str, Any]] = []
        errors: dict[str, str] = {}

        for ns, svc_name in backend_refs:
            await self._scrape_service(api, http, ns, svc_name, backends, errors)

        return {
            "available": True,
            "backends": backends,
            "errors": errors,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _collect_backend_refs(
        self,
        api: Any,
        apps: list[Any],
        default_ns: str,
        an_name: str,
    ) -> list[tuple[str, str]]:
        """Walk apps → HTTPRoute → (namespace, service_name) tuples (deduped)."""
        refs: list[tuple[str, str]] = []

        for app in apps:
            if isinstance(app, dict):
                kind = str(app.get("kind", "")).lower()
                app_name = app.get("name")
                app_ns = app.get("namespace") or default_ns
            else:
                kind = str(getattr(app, "kind", "")).lower()
                app_name = getattr(app, "name", None)
                app_ns = getattr(app, "namespace", None) or default_ns

            if not kind.startswith("httproute"):
                continue
            if not app_name:
                continue

            try:
                route_list = await api.async_get(
                    _HTTPROUTE_RESOURCE, app_name, namespace=app_ns
                )
                # async_get by name returns a single resource or list with one item
                if isinstance(route_list, list):
                    route = route_list[0] if route_list else None
                else:
                    route = route_list
            except Exception as e:
                logger.warning(
                    "LLM metrics: failed to load HTTPRoute %s/%s for analyzer %s: %s",
                    app_ns,
                    app_name,
                    an_name,
                    e,
                )
                continue

            if route is None:
                continue

            route_spec = route.spec if hasattr(route, "spec") else {}
            if isinstance(route_spec, dict):
                rules = route_spec.get("rules") or []
            else:
                rules = getattr(route_spec, "rules", []) or []

            for rule in rules:
                if isinstance(rule, dict):
                    backend_refs_list = rule.get("backendRefs") or []
                else:
                    backend_refs_list = getattr(rule, "backendRefs", []) or []

                for ref in backend_refs_list:
                    if isinstance(ref, dict):
                        ref_name = ref.get("name")
                        ref_ns = ref.get("namespace") or app_ns
                    else:
                        ref_name = getattr(ref, "name", None)
                        ref_ns = getattr(ref, "namespace", None) or app_ns

                    if ref_name:
                        refs.append((ref_ns, str(ref_name)))

        # Deduplicate while preserving order
        seen: set[tuple[str, str]] = set()
        out: list[tuple[str, str]] = []
        for r in refs:
            if r not in seen:
                seen.add(r)
                out.append(r)
        return out

    async def _scrape_service(
        self,
        api: Any,
        http: aiohttp.ClientSession,
        ns: str,
        svc_name: str,
        backends: list[dict[str, Any]],
        errors: dict[str, str],
    ) -> None:
        """Resolve service → pods → scrape each pod."""
        try:
            svc_list = await api.async_get("services", svc_name, namespace=ns)
            svc = svc_list[0] if isinstance(svc_list, list) else svc_list
        except Exception as e:
            errors[f"{ns}/{svc_name}"] = f"service not found: {e}"
            return

        svc_spec = svc.spec if hasattr(svc, "spec") else {}
        if isinstance(svc_spec, dict):
            selector = svc_spec.get("selector") or {}
            ports = svc_spec.get("ports") or []
        else:
            selector = getattr(svc_spec, "selector", {}) or {}
            ports = getattr(svc_spec, "ports", []) or []

        # Normalise to dicts; kr8s may return attribute-style objects
        if ports and not isinstance(ports[0], dict):
            ports = [
                {"name": getattr(p, "name", None), "port": getattr(p, "port", None)}
                for p in ports
            ]

        metrics_port = _pick_metrics_port(ports)

        if not selector or metrics_port is None:
            errors[f"{ns}/{svc_name}"] = "service has no selector or metrics port"
            return

        if isinstance(selector, dict):
            label_selector = ",".join(f"{k}={v}" for k, v in selector.items())
        else:
            label_selector = str(selector)

        try:
            pod_list = await api.async_get("pods", namespace=ns, label_selector=label_selector)
        except Exception as e:
            errors[f"{ns}/{svc_name}"] = f"pod list failed: {e}"
            return

        for pod in (pod_list or []):
            pod_meta = pod.metadata if hasattr(pod, "metadata") else {}
            pod_status = pod.status if hasattr(pod, "status") else {}

            if isinstance(pod_meta, dict):
                pod_name = pod_meta.get("name", "")
            else:
                pod_name = getattr(pod_meta, "name", "")

            if not pod_name:
                continue

            if isinstance(pod_status, dict):
                phase = pod_status.get("phase")
                pod_ip = pod_status.get("podIP")
            else:
                phase = getattr(pod_status, "phase", None)
                pod_ip = getattr(pod_status, "podIP", None)

            if phase != "Running":
                backends.append(_unhealthy_row(pod_name, ns, svc_name, f"pod phase {phase}"))
                continue

            if not pod_ip:
                errors[f"{ns}/{pod_name}"] = "pod Running but has no podIP yet"
                backends.append(_unhealthy_row(pod_name, ns, svc_name, "no podIP"))
                continue

            row, scrape_err = await self._scrape_pod(http, pod_ip, metrics_port, pod_name, ns, svc_name)
            if scrape_err:
                errors[f"{ns}/{pod_name}"] = scrape_err
                backends.append(_unhealthy_row(pod_name, ns, svc_name, scrape_err))
            else:
                backends.append(row)

    async def _scrape_pod(
        self,
        http: aiohttp.ClientSession,
        pod_ip: str,
        port: int,
        pod_name: str,
        namespace: str,
        svc_name: str,
    ) -> tuple[dict[str, Any], str | None]:
        """GET http://{pod_ip}:{port}/metrics and parse."""
        url = f"http://{pod_ip}:{port}/metrics"
        try:
            async with http.get(url) as resp:
                if resp.status != 200:
                    return {}, f"scrape HTTP {resp.status}"
                text = await resp.text()
        except asyncio.TimeoutError:
            return {}, "scrape timeout"
        except Exception as e:
            return {}, f"scrape failed: {e}"

        row = parse_pod_metrics(text, pod_name, namespace, svc_name)
        if row.get("status") == "unhealthy" and not text.strip():
            return {}, "no samples in /metrics"
        return row, None
