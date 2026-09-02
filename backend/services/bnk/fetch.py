"""
BNK data fetch — parallel retrieval of all F5 BNK CRD types + pods.

This is the only BNK module with I/O (K8s API calls). All analysis
modules (health, topology, etc.) consume the dict returned by
``fetch_all_bnk_data`` and are pure data transformations.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from kubernetes import client as k8s_client

from core.cache import cache
from services.bnk.helpers import BNK_RESOURCE_TYPES
from services.bnk_pod_discovery import (
    classify_f5_pods,
    discover_f5_pods,
)
from services.kubernetes._metrics import parse_cpu_to_millicores, parse_memory_to_bytes
from services.kubernetes._resources import resolve_resource_type
from services.kubernetes_service import KubernetesService
from services.scanner.nodes import parse_node

# Short-term cache for BNK data fetch. The dashboard polls /f5bnk/data and
# /f5bnk/gateway-topology, and fleet BNK consumption aggregates the same data
# across clusters. A 60-second TTL prevents redundant expensive K8s API bursts
# when the user navigates/polls, while keeping staleness acceptable for views.
_BNK_DATA_CACHE_TTL = 60
_BNK_POD_DISCOVERY_CACHE_TTL = 15

# Shared executor for BNK CRD/pod fetches. A per-request executor with
# max_workers=20 explodes the process thread count when multiple BNK pages
# are open (100+ threads observed on a laptop). Because this pool is shared
# across all requests, we can keep more workers available without thread
# explosion; the limit is network/batch parallelism to the K8s API.
_BNK_FETCH_WORKERS = min(16, (os.cpu_count() or 4) + 4)
_bnk_fetch_executor: ThreadPoolExecutor | None = None


def _get_bnk_fetch_executor() -> ThreadPoolExecutor:
    global _bnk_fetch_executor
    if _bnk_fetch_executor is None:
        _bnk_fetch_executor = ThreadPoolExecutor(
            max_workers=_BNK_FETCH_WORKERS,
            thread_name_prefix="bnk-fetch-",
        )
    return _bnk_fetch_executor

_CRD_INSTALLER_NAMESPACE = "f5-utils"
_CRD_INSTALLER_LABEL = "app=crd-installer"


def _node_enrichment(node) -> dict[str, Any] | None:
    """Extract placement-relevant fields from a V1Node.

    Reuses ``services.scanner.nodes.parse_node`` so the label fallback logic
    for zone and instance-type stays in one place. Also includes allocatable
    and capacity CPU/memory so fleet BNK resources can fall back to node
    capacity when cluster metrics-server is not installed.
    """
    meta = getattr(node, "metadata", None)
    if not meta or not getattr(meta, "name", None):
        return None
    parsed = parse_node(node)
    allocatable = parsed.get("allocatable", {})
    capacity = parsed.get("capacity", {})
    return {
        "name": parsed["name"],
        "zone": parsed.get("zone"),
        "instance_type": parsed.get("instance_type"),
        "labels": parsed.get("labels", {}),
        "allocatable_cpu": parse_cpu_to_millicores(allocatable.get("cpu")),
        "allocatable_memory": parse_memory_to_bytes(allocatable.get("memory")),
        "capacity_cpu": parse_cpu_to_millicores(capacity.get("cpu")),
        "capacity_memory": parse_memory_to_bytes(capacity.get("memory")),
    }


def _fetch_nodes(api_client) -> dict[str, dict[str, Any]]:
    """Fetch cluster nodes and return a name-indexed enrichment map."""
    try:
        v1 = k8s_client.CoreV1Api(api_client)
        nodes = v1.list_node(_request_timeout=10).items or []
        result: dict[str, dict[str, Any]] = {}
        for node in nodes:
            enriched = _node_enrichment(node)
            if enriched:
                result[enriched["name"]] = enriched
        return result
    except Exception:
        return {}


def _bnk_data_cache_key(cluster_id: int, namespace: str | None, include_nodes: bool) -> str:
    return f"bnk:data:{cluster_id}:{namespace or 'all'}:{include_nodes}"


def _cached_discover_f5_pods(
    cluster_id: int,
    api_client,
    extra_namespaces: list[str],
) -> tuple[list[dict], list[dict]]:
    """Discover F5 pods with a short-lived per-cluster cache.

    Pod discovery is I/O-heavy (parallel namespace queries + optional cluster-
    wide sweep). Caching the result for a few seconds removes the duplicate work
    when the BNK page loads multiple insight endpoints in quick succession.
    """
    from core.cache import cache

    cache_key = f"bnk:pods:{cluster_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    result = discover_f5_pods(api_client, extra_namespaces=extra_namespaces)
    cache.set(cache_key, result, ttl_seconds=_BNK_POD_DISCOVERY_CACHE_TTL)
    return result


def fetch_all_bnk_data(
    k8s_service: KubernetesService,
    cluster_id: int,
    namespace: str | None = None,
    *,
    include_nodes: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """
    Fetch all BNK CRD resources + pods in one parallel burst.

    Results are cached for 15 seconds by (cluster_id, namespace, include_nodes).
    Pass ``force=True`` to bypass the cache (used by explicit "Rescan"/refresh
    actions and by operations that need the freshest state).

    Returns a dict with:
      - resources: {resource_type_key: [items...]} for all CRD types
      - pods: {tenant: [...], utils: [...]}
      - classified_pods: {tmm: [...], flo: [...], controller: [...], ...}
      - nodes: {nodeName: {zone, instance_type, labels}} (only if include_nodes=True)
      - cluster_id, namespace
    """
    cache_key = _bnk_data_cache_key(cluster_id, namespace, include_nodes)
    if not force:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)

    # Seed BNK pod discovery with namespaces the scanner has previously
    # observed F5 components in. This lets the fast-path phase find pods
    # in non-standard namespaces without relying on the expensive cluster-
    # wide sweep on every BNK page load.
    persisted_namespaces: list[str] = list(
        getattr(cluster, "discovered_namespaces", None) or []
    )

    def safe_fetch(resource_type_key: str) -> list[dict]:
        try:
            rt = resolve_resource_type(k8s_service.db, cluster_id, resource_type_key)
            items = k8s_service._fetch_from_k8s(api_client, rt, namespace, None)
            return [i for i in items if isinstance(i, dict)]
        except Exception:
            return []

    def fetch_crd_installer_job() -> dict | None:
        """
        Fetch the crd-installer batch/v1 Job status.

        Returns a minimal dict with succeeded/failed/active counts, or None
        if the Job doesn't exist (never-installed). Tries f5-utils first
        (the standard FLO install namespace), then falls back to the
        tenant namespace passed to this fetch — direct-helm installs may
        run crd-installer in the app namespace instead.
        """
        try:
            batch_api = k8s_client.BatchV1Api(api_client)
            candidate_namespaces = [_CRD_INSTALLER_NAMESPACE]
            if namespace and namespace != _CRD_INSTALLER_NAMESPACE:
                candidate_namespaces.append(namespace)

            for ns in candidate_namespaces:
                jobs = batch_api.list_namespaced_job(
                    namespace=ns,
                    label_selector=_CRD_INSTALLER_LABEL,
                    _request_timeout=10,
                ).items
                if jobs:
                    job = jobs[0]
                    status = job.status or type("_", (), {"succeeded": None, "failed": None, "active": None})()
                    return {
                        "succeeded": status.succeeded or 0,
                        "failed": status.failed or 0,
                        "active": status.active or 0,
                    }
            return None
        except Exception:
            return None

    # Fire all CRD fetches + pod discovery + job status + nodes in parallel.
    # Use the module-level shared executor so concurrent BNK page loads do not
    # each spawn 20 threads and overwhelm the backend process.
    executor = _get_bnk_fetch_executor()
    crd_futures = {rt: executor.submit(safe_fetch, rt) for rt in BNK_RESOURCE_TYPES}
    pods_future = executor.submit(
        _cached_discover_f5_pods, cluster_id, api_client, persisted_namespaces
    )
    job_future = executor.submit(fetch_crd_installer_job)
    nodes_future = executor.submit(_fetch_nodes, api_client) if include_nodes else None

    resources = {rt: fut.result() for rt, fut in crd_futures.items()}
    tenant_pods, utils_pods = pods_future.result()
    crd_installer_job = job_future.result()
    nodes = nodes_future.result() if nodes_future is not None else {}

    classified = classify_f5_pods(tenant_pods, utils_pods)

    result = {
        "resources": resources,
        "pods": {"tenant": tenant_pods, "utils": utils_pods},
        "classified_pods": classified,
        "crd_installer_job": crd_installer_job,
        "nodes": nodes,
        "cluster_id": cluster_id,
        "namespace": namespace,
    }
    cache.set(cache_key, result, ttl_seconds=_BNK_DATA_CACHE_TTL)
    return result
