"""
BNK data fetch — parallel retrieval of all F5 BNK CRD types + pods.

This is the only BNK module with I/O (K8s API calls). All analysis
modules (health, topology, etc.) consume the dict returned by
``fetch_all_bnk_data`` and are pure data transformations.
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from kubernetes import client as k8s_client

from services.bnk.helpers import BNK_RESOURCE_TYPES
from services.bnk_pod_discovery import (
    classify_f5_pods,
    discover_f5_pods,
)
from services.kubernetes._resources import resolve_resource_type
from services.kubernetes_service import KubernetesService
from services.scanner.nodes import parse_node

_CRD_INSTALLER_NAMESPACE = "f5-utils"
_CRD_INSTALLER_LABEL = "app=crd-installer"


def _node_enrichment(node) -> dict[str, Any] | None:
    """Extract placement-relevant fields from a V1Node.

    Reuses ``services.scanner.nodes.parse_node`` so the label fallback logic
    for zone and instance-type stays in one place.
    """
    meta = getattr(node, "metadata", None)
    if not meta or not getattr(meta, "name", None):
        return None
    parsed = parse_node(node)
    return {
        "name": parsed["name"],
        "zone": parsed.get("zone"),
        "instance_type": parsed.get("instance_type"),
        "labels": parsed.get("labels", {}),
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


def fetch_all_bnk_data(
    k8s_service: KubernetesService,
    cluster_id: int,
    namespace: str | None = None,
    *,
    include_nodes: bool = False,
) -> dict[str, Any]:
    """
    Fetch all BNK CRD resources + pods in one parallel burst.

    Returns a dict with:
      - resources: {resource_type_key: [items...]} for all CRD types
      - pods: {tenant: [...], utils: [...]}
      - classified_pods: {tmm: [...], flo: [...], controller: [...], ...}
      - nodes: {nodeName: {zone, instance_type, labels}} (only if include_nodes=True)
      - cluster_id, namespace
    """
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)

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

    # Fire all CRD fetches + pod discovery + job status + nodes in parallel
    with ThreadPoolExecutor(max_workers=20) as executor:
        crd_futures = {rt: executor.submit(safe_fetch, rt) for rt in BNK_RESOURCE_TYPES}
        pods_future = executor.submit(discover_f5_pods, api_client)
        job_future = executor.submit(fetch_crd_installer_job)
        nodes_future = executor.submit(_fetch_nodes, api_client) if include_nodes else None

        resources = {rt: fut.result() for rt, fut in crd_futures.items()}
        tenant_pods, utils_pods = pods_future.result()
        crd_installer_job = job_future.result()
        nodes = nodes_future.result() if nodes_future is not None else {}

    classified = classify_f5_pods(tenant_pods, utils_pods)

    return {
        "resources": resources,
        "pods": {"tenant": tenant_pods, "utils": utils_pods},
        "classified_pods": classified,
        "crd_installer_job": crd_installer_job,
        "nodes": nodes,
        "cluster_id": cluster_id,
        "namespace": namespace,
    }
