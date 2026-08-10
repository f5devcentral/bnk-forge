"""
DPF health analysis — aggregate DPF resource data into a health summary.

Pure function (dict in → dict out) with no I/O.
"""

from typing import Any


def analyze_dpf_health(data: dict[str, Any]) -> dict[str, Any]:
    """
    Analyze DPF health from fetched resource data.

    Returns a structured summary suitable for the DPU Infrastructure tab
    and fleet health dashboard.
    """
    resources = data.get("resources", {})

    operator_configs = resources.get("dpfoperatorconfig", [])
    devices = resources.get("dpudevice", [])
    dpusets = resources.get("dpuset", [])
    dpuclusters = resources.get("dpucluster", [])
    dpus = resources.get("dpu", [])
    bfbs = resources.get("bfb", [])
    dpuflavors = resources.get("dpuflavor", [])
    dpuservices = resources.get("dpuservice", [])
    dpudeployments = resources.get("dpudeployment", [])
    service_chains = resources.get("dpuservicechain", [])
    service_interfaces = resources.get("dpuserviceinterface", [])

    # Operator health
    operator = _analyze_operator(operator_configs)

    # Device inventory
    device_summary = _analyze_devices(devices)

    # DPU lifecycle
    dpu_summary = _analyze_dpus(dpus)

    # Cluster health
    cluster_summary = _analyze_clusters(dpuclusters)

    # BFB images
    bfb_summary = _analyze_bfbs(bfbs)

    # Services
    service_summary = _analyze_services(dpuservices)

    # Overall health status
    overall_status = _determine_overall_status(
        operator, device_summary, dpu_summary, cluster_summary
    )

    return {
        "status": overall_status,
        "operator": operator,
        "devices": device_summary,
        "dpus": dpu_summary,
        "clusters": cluster_summary,
        "bfbs": bfb_summary,
        "flavors": {"total": len(dpuflavors)},
        "dpusets": {"total": len(dpusets)},
        "services": service_summary,
        "deployments": {"total": len(dpudeployments)},
        "serviceChains": {"total": len(service_chains)},
        "serviceInterfaces": {"total": len(service_interfaces)},
    }


def _analyze_operator(configs: list[dict]) -> dict[str, Any]:
    """Analyze DPF operator status from DPFOperatorConfig resources."""
    if not configs:
        return {"configured": False, "ready": False, "version": None, "conditions": []}

    cfg = configs[0]
    conditions = cfg.get("status", {}).get("conditions") or []
    version = cfg.get("status", {}).get("version")
    ready = any(
        c.get("type") == "Ready" and c.get("status") == "True" for c in conditions
    )
    return {
        "configured": True,
        "ready": ready,
        "version": version,
        "conditions": [
            {
                "type": c.get("type", ""),
                "status": c.get("status", ""),
                "reason": c.get("reason", ""),
                "message": c.get("message", ""),
            }
            for c in conditions
        ],
    }


def _analyze_devices(devices: list[dict]) -> dict[str, Any]:
    """Analyze DPU device inventory."""
    by_condition: dict[str, int] = {}
    by_type: dict[str, int] = {}

    for d in devices:
        status = d.get("status", {})
        conditions = status.get("conditions") or []
        dpu_type = status.get("dpuType", "Unknown")
        by_type[dpu_type] = by_type.get(dpu_type, 0) + 1

        # Track the most advanced condition that is True
        for cond in conditions:
            if cond.get("status") == "True":
                ctype = cond.get("type", "Unknown")
                by_condition[ctype] = by_condition.get(ctype, 0) + 1

    ready = by_condition.get("Ready", 0)
    return {
        "total": len(devices),
        "ready": ready,
        "byCondition": by_condition,
        "byType": by_type,
    }


def _analyze_dpus(dpus: list[dict]) -> dict[str, Any]:
    """Analyze individual DPU lifecycle objects."""
    by_phase: dict[str, int] = {}
    for dpu in dpus:
        phase = dpu.get("status", {}).get("phase", "Unknown")
        by_phase[phase] = by_phase.get(phase, 0) + 1

    return {
        "total": len(dpus),
        "byPhase": by_phase,
    }


def _analyze_clusters(clusters: list[dict]) -> dict[str, Any]:
    """Analyze DPU cluster control planes."""
    summaries = []
    for c in clusters:
        name = c.get("metadata", {}).get("name", "")
        namespace = c.get("metadata", {}).get("namespace", "")
        conditions = c.get("status", {}).get("conditions") or []
        ready = any(
            cond.get("type") == "Ready" and cond.get("status") == "True"
            for cond in conditions
        )
        cluster_type = c.get("spec", {}).get("type", "kamaji")
        summaries.append({
            "name": name,
            "namespace": namespace,
            "type": cluster_type,
            "ready": ready,
        })

    return {
        "total": len(clusters),
        "ready": sum(1 for s in summaries if s["ready"]),
        "clusters": summaries,
    }


def _analyze_bfbs(bfbs: list[dict]) -> dict[str, Any]:
    """Analyze BFB (BlueField Boot) images."""
    summaries = []
    for b in bfbs:
        name = b.get("metadata", {}).get("name", "")
        conditions = b.get("status", {}).get("conditions") or []
        ready = any(
            cond.get("type") == "Ready" and cond.get("status") == "True"
            for cond in conditions
        )
        url = b.get("spec", {}).get("url", "")
        summaries.append({
            "name": name,
            "url": url,
            "ready": ready,
        })

    return {
        "total": len(bfbs),
        "ready": sum(1 for s in summaries if s["ready"]),
        "images": summaries,
    }


def _analyze_services(services: list[dict]) -> dict[str, Any]:
    """Analyze DPU services."""
    ready_count = 0
    for svc in services:
        conditions = svc.get("status", {}).get("conditions") or []
        if any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions):
            ready_count += 1

    return {
        "total": len(services),
        "ready": ready_count,
    }


def _determine_overall_status(
    operator: dict, devices: dict, dpus: dict, clusters: dict
) -> str:
    """Determine overall DPF health status."""
    if not operator["configured"]:
        return "not_installed"

    if not operator["ready"]:
        return "degraded"

    if devices["total"] == 0:
        return "no_devices"

    if devices["ready"] < devices["total"]:
        return "partial"

    if clusters["total"] > 0 and clusters["ready"] < clusters["total"]:
        return "partial"

    return "healthy"
