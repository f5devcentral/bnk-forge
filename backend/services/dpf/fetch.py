"""
DPF fetch — parallel K8s API data collection for DPF CRD resources.

This is the ONLY I/O module in the DPF service package.  All other modules
are pure functions that transform the dicts returned by ``fetch_all_dpf_data``.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from services.dpf.constants import DPF_DETECT_TYPES, DPF_RESOURCE_TYPES
from services.kubernetes._resources import resolve_resource_type

logger = logging.getLogger(__name__)


def _safe_fetch(k8s_service, api_client, cluster_id: int, resource_type_key: str) -> list[dict]:
    """Fetch CRD resources, returning empty list on error."""
    try:
        rt = resolve_resource_type(k8s_service.db, cluster_id, resource_type_key)
        items = k8s_service._fetch_from_k8s(api_client, rt, None, None)
        return [i for i in items if isinstance(i, dict)]
    except Exception:
        return []


def fetch_all_dpf_data(k8s_service, cluster_id: int) -> dict[str, Any]:
    """
    Fetch all DPF CRD resources in one parallel burst.

    Returns a flat dict keyed by resource type (matching RESOURCE_REGISTRY keys).
    Uses 20 concurrent workers to minimize latency.
    """
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {
            rt: executor.submit(_safe_fetch, k8s_service, api_client, cluster_id, rt)
            for rt in DPF_RESOURCE_TYPES
        }

    resources = {rt: fut.result() for rt, fut in futures.items()}

    return {
        "resources": resources,
        "cluster_id": cluster_id,
    }


def detect_dpf(k8s_service, cluster_id: int) -> dict[str, Any]:
    """
    Lightweight DPF detection — fetches only core CRDs to check presence.

    Much faster than fetch_all_dpf_data for the common case where DPF
    is not installed.
    """
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            rt: executor.submit(_safe_fetch, k8s_service, api_client, cluster_id, rt)
            for rt in DPF_DETECT_TYPES
        }

    resources = {rt: fut.result() for rt, fut in futures.items()}

    has_operator = len(resources.get("dpfoperatorconfig", [])) > 0
    has_devices = len(resources.get("dpudevice", [])) > 0

    # Extract version from DPFOperatorConfig if present
    version = None
    for cfg in resources.get("dpfoperatorconfig", []):
        version = cfg.get("status", {}).get("version")
        if version:
            break

    # Check operator readiness
    operator_ready = False
    for cfg in resources.get("dpfoperatorconfig", []):
        conditions = cfg.get("status", {}).get("conditions") or []
        if any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions):
            operator_ready = True
            break

    return {
        "detected": has_operator or has_devices,
        "version": version,
        "operator": {
            "configured": has_operator,
            "ready": operator_ready,
        },
        "devices": {
            "total": len(resources.get("dpudevice", [])),
        },
        "dpuclusters": len(resources.get("dpucluster", [])),
        "cluster_id": cluster_id,
    }
