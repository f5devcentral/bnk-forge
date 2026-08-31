"""
BNK resource consumption aggregation.

Pure functions that turn fetched BNK data + pod metrics into the response
shape used by ``GET /api/system/bnk-consumption``.
"""

from typing import Any

from services.bnk_pod_discovery import detect_install_shape

# Roles that belong to the BNK control plane.
_CONTROL_PLANE_ROLES: frozenset[str] = frozenset({"flo", "controller", "analyzer", "crd_installer"})
_DATA_PLANE_ROLES: frozenset[str] = frozenset({"tmm"})


def _empty_plane() -> dict[str, int]:
    return {"count": 0, "cpu_millicores": 0, "memory_bytes": 0}


def _classify_role(role: str | None) -> str:
    """Classify a pod role into 'control_plane', 'data_plane', or 'other'."""
    if role in _DATA_PLANE_ROLES:
        return "data_plane"
    if role in _CONTROL_PLANE_ROLES:
        return "control_plane"
    return "other"


def _build_metrics_lookup(pod_metrics: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Index pod metrics by (namespace, name) for O(1) lookup."""
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for metric in pod_metrics:
        name = metric.get("name")
        namespace = metric.get("namespace")
        if name and namespace:
            lookup[(namespace, name)] = metric
    return lookup


def _aggregate_plane(
    pods: list[dict[str, Any]],
    metrics_lookup: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    """Sum CPU/memory for a list of pods using the metrics lookup."""
    total_cpu = 0
    total_memory = 0
    for pod in pods:
        key = (pod.get("namespace", ""), pod.get("name", ""))
        metric = metrics_lookup.get(key)
        if metric:
            total_cpu += int(metric.get("cpu_millicores", 0) or 0)
            total_memory += int(metric.get("memory_bytes", 0) or 0)
    return {
        "count": len(pods),
        "cpu_millicores": total_cpu,
        "memory_bytes": total_memory,
    }


def _build_top_pods(
    classified_pods: dict[str, list[dict[str, Any]]],
    metrics_lookup: dict[tuple[str, str], dict[str, Any]],
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Return the top BNK pods by CPU usage."""
    scored: list[tuple[int, dict[str, Any]]] = []
    for role, pods in classified_pods.items():
        for pod in pods:
            key = (pod.get("namespace", ""), pod.get("name", ""))
            metric = metrics_lookup.get(key)
            cpu = int(metric.get("cpu_millicores", 0) or 0) if metric else 0
            memory = int(metric.get("memory_bytes", 0) or 0) if metric else 0
            scored.append((cpu, {
                "name": pod.get("name", ""),
                "namespace": pod.get("namespace", ""),
                "role": role,
                "cpu_millicores": cpu,
                "memory_bytes": memory,
            }))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in scored[:limit]]


def aggregate_cluster_consumption(
    cluster_id: int,
    cluster_name: str,
    node_count: int | None,
    status: str,
    bnk_data: dict[str, Any] | None,
    pod_metrics_response: dict[str, Any] | None,
    dpf_summary: dict[str, Any] | None,
    reachable: bool = True,
) -> dict[str, Any]:
    """
    Build a per-cluster consumption dict from BNK data + metrics.

    Pure function — all I/O must be performed by the caller.
    """
    metrics_response = pod_metrics_response or {}
    metrics_available = bool(metrics_response.get("available"))
    metrics_error = metrics_response.get("error") if not metrics_available else None
    pod_metrics = metrics_response.get("metrics", []) if metrics_available else []
    metrics_lookup = _build_metrics_lookup(pod_metrics)

    dpf = dpf_summary or {"detected": False, "dpu_count": 0}

    if not reachable or bnk_data is None:
        return {
            "cluster_id": cluster_id,
            "cluster_name": cluster_name,
            "reachable": False,
            "bnk_installed": False,
            "bnk_version": None,
            "status": "offline" if not reachable else status,
            "node_count": node_count,
            "control_plane": _empty_plane(),
            "data_plane": _empty_plane(),
            "total": _empty_plane(),
            "metrics_available": metrics_available,
            "metrics_error": metrics_error,
            "dpf": {"detected": bool(dpf.get("detected")), "dpu_count": int(dpf.get("dpu_count", 0))},
            "top_pods": [],
        }

    classified = bnk_data.get("classified_pods", {}) or {}
    install_shape = detect_install_shape(classified)
    bnk_installed = install_shape in ("flo", "helm")

    control_pods: list[dict[str, Any]] = []
    data_pods: list[dict[str, Any]] = []
    for role, pods in classified.items():
        for pod in pods:
            if role in _DATA_PLANE_ROLES:
                data_pods.append(pod)
            elif role in _CONTROL_PLANE_ROLES:
                control_pods.append(pod)

    control_plane = _aggregate_plane(control_pods, metrics_lookup)
    data_plane = _aggregate_plane(data_pods, metrics_lookup)
    total = {
        "count": control_plane["count"] + data_plane["count"],
        "cpu_millicores": control_plane["cpu_millicores"] + data_plane["cpu_millicores"],
        "memory_bytes": control_plane["memory_bytes"] + data_plane["memory_bytes"],
    }

    # Derive BNK version from pod images (reuses fleet health heuristic)
    bnk_version = _extract_bnk_version(classified)

    return {
        "cluster_id": cluster_id,
        "cluster_name": cluster_name,
        "reachable": True,
        "bnk_installed": bnk_installed,
        "bnk_version": bnk_version,
        "status": status,
        "node_count": node_count,
        "control_plane": control_plane,
        "data_plane": data_plane,
        "total": total,
        "metrics_available": metrics_available,
        "metrics_error": metrics_error,
        "dpf": {"detected": bool(dpf.get("detected")), "dpu_count": int(dpf.get("dpu_count", 0))},
        "top_pods": _build_top_pods(classified, metrics_lookup),
    }


def aggregate_fleet_summary(clusters: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll up per-cluster consumption into fleet totals."""
    total_clusters = len(clusters)
    reachable_clusters = sum(1 for c in clusters if c.get("reachable"))
    bnk_installed_clusters = sum(1 for c in clusters if c.get("bnk_installed"))
    dpf_detected_clusters = sum(1 for c in clusters if c.get("dpf", {}).get("detected"))
    dpu_count = sum(c.get("dpf", {}).get("dpu_count", 0) for c in clusters)

    total_bnk_pods = 0
    control_plane_pods = 0
    data_plane_pods = 0
    total_cpu = 0
    total_memory = 0

    for cluster in clusters:
        total_plane = cluster.get("total", {})
        control_plane = cluster.get("control_plane", {})
        data_plane = cluster.get("data_plane", {})
        total_bnk_pods += int(total_plane.get("count", 0))
        control_plane_pods += int(control_plane.get("count", 0))
        data_plane_pods += int(data_plane.get("count", 0))
        total_cpu += int(total_plane.get("cpu_millicores", 0))
        total_memory += int(total_plane.get("memory_bytes", 0))

    return {
        "total_clusters": total_clusters,
        "reachable_clusters": reachable_clusters,
        "bnk_installed_clusters": bnk_installed_clusters,
        "total_bnk_pods": total_bnk_pods,
        "control_plane_pods": control_plane_pods,
        "data_plane_pods": data_plane_pods,
        "total_cpu_millicores": total_cpu,
        "total_memory_bytes": total_memory,
        "dpf_detected_clusters": dpf_detected_clusters,
        "dpu_count": dpu_count,
    }


def _extract_bnk_version(classified_pods: dict[str, list[dict[str, Any]]]) -> str | None:
    """Extract BNK version from TMM/FLO/controller container images."""
    import re

    for role in ("tmm", "flo", "controller"):
        for pod in classified_pods.get(role, []):
            for container in pod.get("containers", []):
                image = container.get("image", "")
                match = re.search(r":v?(\d+\.\d+\.\d+)", image)
                if match:
                    return match.group(1)
    return None
