"""What the router did during a benchmark run, read from the model-server pods.

vLLM and the llm-d inference simulator both expose per-pod Prometheus counters for
finished requests and prefix-cache lookups. Forge reads them from every pod behind the
target Service (through the API server's pod proxy) when a run starts and when it
completes; the difference shows how the proxy spread requests over the pods and how
much of each prompt came from the KV cache. Works the same for a simulator and for
real GPUs. Best effort: a scrape failure never fails a run.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from kubernetes import client as k8s_client

logger = logging.getLogger(__name__)

# Counter → field name. Each counter may have several series (labels); they are summed.
COUNTERS = {
    "vllm:request_success_total": "requests",
    "vllm:prefix_cache_queries_total": "prefix_queries",
    "vllm:prefix_cache_hits_total": "prefix_hits",
    "vllm:num_preemptions_total": "preemptions",
}
_TIMEOUT = 5
_SNAPSHOT_DEADLINE = 10.0


def parse_counters(text: str) -> dict[str, float]:
    """Sum the COUNTERS series in a Prometheus text exposition."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name = line.split("{", 1)[0].split(" ", 1)[0]
        field = COUNTERS.get(name)
        if field is None:
            continue
        try:
            out[field] = out.get(field, 0.0) + float(line.rsplit(" ", 1)[-1])
        except ValueError:
            continue
    return out


def _pod_port(svc: Any, svc_port: int, pods: list) -> int | None:
    """The pod port behind ``svc_port`` (named targetPorts resolved from the pods)."""
    for port in svc.spec.ports or []:
        if port.port != svc_port and len(svc.spec.ports) > 1:
            continue
        target = port.target_port if port.target_port is not None else port.port
        if isinstance(target, int) or str(target).isdigit():
            return int(target)
        for pod in pods:
            for container in pod.spec.containers or []:
                for cport in container.ports or []:
                    if cport.name == target:
                        return cport.container_port
    return None


def snapshot(db: Any, target: Any) -> dict | None:
    """Counters of every Running pod behind the target Service, or None."""
    from models import KubernetesCluster
    from services.kubernetes_service import KubernetesService
    from services.proxy_deploy_service import _svc_name, _svc_port

    try:
        cluster = db.get(KubernetesCluster, target.cluster_id) if target.cluster_id else None
        if cluster is None or not target.llm_base_url:
            return None
        core = k8s_client.CoreV1Api(KubernetesService(db).load_kubeconfig(cluster))
        namespace = target.llm_namespace or "default"
        svc = core.read_namespaced_service(_svc_name(target.llm_base_url), namespace, _request_timeout=_TIMEOUT)
        selector = ",".join(f"{k}={v}" for k, v in (svc.spec.selector or {}).items())
        if not selector:
            return None
        pods = [
            p
            for p in core.list_namespaced_pod(namespace, label_selector=selector, _request_timeout=_TIMEOUT).items
            if p.status and p.status.phase == "Running"
        ]
        port = _pod_port(svc, _svc_port(target.llm_base_url), pods)
        if not pods or not port:
            return None
        counters: dict[str, dict] = {}
        deadline = time.monotonic() + _SNAPSHOT_DEADLINE
        for pod in pods:
            if time.monotonic() > deadline:
                logger.warning("model server snapshot deadline exceeded for target %s", getattr(target, "id", "?"))
                break
            name = pod.metadata.name
            try:
                text = core.connect_get_namespaced_pod_proxy_with_path(
                    f"{name}:{port}",
                    namespace,
                    "metrics",
                    _request_timeout=_TIMEOUT,
                )
            except Exception as exc:  # one pod down must not hide the others
                logger.warning("metrics scrape of %s/%s failed: %s", namespace, name, exc)
                continue
            # A fresh pod lists no counter series until its first request: it starts from zero.
            counters[name] = {
                **parse_counters(text if isinstance(text, str) else str(text)),
                "node": pod.spec.node_name,
            }
    except Exception as exc:
        logger.warning("model server snapshot for target %s failed: %s", getattr(target, "id", "?"), exc)
        return None
    if not counters:
        logger.warning("model server snapshot for target %s: no pod answered /metrics", getattr(target, "id", "?"))
        return None
    return {"at": datetime.now(UTC).isoformat(), "pods": counters}


def delta(before: dict | None, after: dict | None) -> dict | None:
    """Per-pod counter differences between two snapshots, with totals.

    A pod whose counters went down restarted during the run; it counts from zero.
    """
    if not after or not any(f in p for p in (after.get("pods") or {}).values() for f in COUNTERS.values()):
        return None  # no vLLM-style counters: not a vLLM / llm-d model server
    start = (before or {}).get("pods") or {}
    pods = []
    for name, now in sorted(after["pods"].items()):
        was = start.get(name)
        restarted = bool(was) and any(now.get(k, 0) < was.get(k, 0) for k in COUNTERS.values())
        base = {} if (was is None or restarted) else was
        row = {"pod": name, "node": now.get("node")}
        for field in COUNTERS.values():
            if field in now:
                row[field] = round(now[field] - base.get(field, 0.0))
        if row.get("prefix_queries"):
            row["hit_pct"] = round(100 * row.get("prefix_hits", 0) / row["prefix_queries"], 1)
        if was is None or restarted:
            row["counted_from"] = "pod start"
        pods.append(row)
    requests = [p.get("requests", 0) for p in pods]
    queries = sum(p.get("prefix_queries", 0) for p in pods)
    hits = sum(p.get("prefix_hits", 0) for p in pods)
    total = sum(requests)
    totals: dict[str, Any] = {"pods": len(pods), "requests": total}
    if queries:
        totals["hit_pct"] = round(100 * hits / queries, 1)
    busy = [r for r in requests if r > 0]
    if total and busy:
        totals["load_spread"] = round(max(requests) / min(busy), 2) if len(busy) == len(requests) else None
        totals["max_share_pct"] = round(100 * max(requests) / total, 1)
    if any("preemptions" in p for p in pods):
        totals["preemptions"] = sum(p.get("preemptions", 0) for p in pods)
    for p in pods:
        if total:
            p["share_pct"] = round(100 * p.get("requests", 0) / total, 1)
    return {
        "source": "model-server /metrics",
        "from": (before or {}).get("at"),
        "to": after.get("at"),
        "pods": pods,
        "totals": totals,
    }
