"""
BNK backends analysis — cross-references Services with route backends.

Produces a sorted list of backend entries showing which Services are
mapped to routes (via topology backendRefs) and which are unmapped.

Pure data transformation: consumes the resources dict from
``fetch_all_bnk_data`` and the topology list from ``analyze_topology``.
"""

from typing import Any

from services.bnk.helpers import build_route_ref_map


def analyze_backends(data: dict[str, Any], topology: list[dict]) -> list[dict]:
    """
    Cross-reference K8s Services with route backendRefs from topology.

    Returns a list of backend entries with mapped/unmapped status and
    which routes reference each Service.
    """
    services = data["resources"].get("service", [])

    # Build a lookup: (namespace, name) → list of route references
    route_ref_map = build_route_ref_map(topology)

    backends = [
        _build_backend_entry(svc, route_ref_map)
        for svc in services
    ]

    # Sort: mapped first, then alphabetically by namespace/name
    backends.sort(key=lambda b: (not b["mapped"], b["namespace"], b["name"]))
    return backends


def _build_backend_entry(
    svc: dict,
    route_ref_map: dict[tuple[str, str], list[dict]],
) -> dict[str, Any]:
    """Build a single backend entry from a K8s Service."""
    meta = svc.get("metadata", {})
    spec = svc.get("spec", {})
    svc_name = meta.get("name", "")
    svc_ns = meta.get("namespace", "")
    route_refs = route_ref_map.get((svc_ns, svc_name), [])

    return {
        "name": svc_name,
        "namespace": svc_ns,
        "type": spec.get("type", "ClusterIP"),
        "clusterIP": spec.get("clusterIP"),
        "ports": [
            {
                "port": p.get("port"),
                "targetPort": p.get("targetPort"),
                "protocol": p.get("protocol", "TCP"),
                "name": p.get("name"),
            }
            for p in (spec.get("ports") or [])  # K8s returns null for some Service types
        ],
        "mapped": len(route_refs) > 0,
        "routeRefs": route_refs,
        "selector": spec.get("selector"),
        "createdAt": meta.get("creationTimestamp"),
    }
