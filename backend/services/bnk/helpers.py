"""
Shared helpers for BNK data analysis.

Pure utility functions for traversing K8s resource dicts, checking
conditions, and computing severity rollups. Used across all BNK
analysis modules (health, topology, backends, policy associations).

Also used by ``runbook_service`` — these are the canonical versions
of ``has_condition`` / ``get_condition_message``.
"""

from typing import Any

# ---------------------------------------------------------------------------
# Dict traversal
# ---------------------------------------------------------------------------


def safe_get(obj: Any, *keys: str, default: Any = None) -> Any:
    """Safely traverse nested dicts: ``safe_get(d, "a", "b") == d["a"]["b"]``."""
    for key in keys:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(key)
        if obj is None:
            return default
    return obj


def resource_name(resource: dict) -> str:
    """Extract ``metadata.name`` from a K8s resource dict."""
    result: str = safe_get(resource, "metadata", "name", default="")
    return result


def resource_ns(resource: dict) -> str:
    """Extract ``metadata.namespace`` from a K8s resource dict."""
    result: str = safe_get(resource, "metadata", "namespace", default="")
    return result


def resource_key(resource: dict) -> str:
    """Return ``namespace/name`` key for a K8s resource dict."""
    return f"{resource_ns(resource)}/{resource_name(resource)}"


# ---------------------------------------------------------------------------
# K8s condition helpers
# ---------------------------------------------------------------------------


def has_condition(resource: dict, cond_type: str, expected: str = "True") -> bool:
    """Check if a K8s resource has a condition of given type with expected status."""
    if not isinstance(resource, dict):
        return False
    conditions = safe_get(resource, "status", "conditions", default=[]) or []
    return any(
        isinstance(c, dict) and c.get("type") == cond_type and c.get("status") == expected
        for c in conditions
    )


def get_condition_message(resource: dict, cond_type: str) -> str:
    """Get the message string from a K8s resource condition."""
    if not isinstance(resource, dict):
        return ""
    conditions = safe_get(resource, "status", "conditions", default=[]) or []
    for c in conditions:
        if isinstance(c, dict) and c.get("type") == cond_type:
            msg: str = c.get("message", "")
            return msg
    return ""


# ---------------------------------------------------------------------------
# Severity helpers
# ---------------------------------------------------------------------------

from models.enums import HealthSeverity

# Legacy ordering — kept for backward compatibility with code that still
# uses the old "critical"/"warning" vocabulary.  New code should use
# HealthSeverity.ordering() directly.
_SEVERITY_ORDER = {"critical": 0, "unhealthy": 0, "warning": 1, "degraded": 1, "unknown": 2, "healthy": 3}

# Mapping from legacy BNK severity terms to canonical HealthSeverity.
# BNK health analysis historically used "critical" and "warning";
# the canonical model uses "unhealthy" and "degraded".
_LEGACY_TO_CANONICAL: dict[str, HealthSeverity] = {
    "critical": HealthSeverity.UNHEALTHY,
    "warning": HealthSeverity.DEGRADED,
    "healthy": HealthSeverity.HEALTHY,
    "unknown": HealthSeverity.UNKNOWN,
    # Canonical values map to themselves
    "unhealthy": HealthSeverity.UNHEALTHY,
    "degraded": HealthSeverity.DEGRADED,
}


def calc_severity(healthy_count: int, total_count: int) -> str:
    """Map healthy/total counts to a severity string.

    Returns legacy vocabulary ("critical"/"warning") for backward compatibility.
    Use ``HealthSeverity.from_counts()`` for canonical values in new code.
    """
    if total_count == 0:
        return "unknown"
    if healthy_count == total_count:
        return "healthy"
    if healthy_count == 0:
        return "critical"
    return "warning"


def rollup_severity(severities: list[str]) -> str:
    """Combine multiple severity values into a single worst-case severity.

    Accepts both legacy ("critical"/"warning") and canonical ("unhealthy"/"degraded")
    terms. Returns in the same vocabulary as the input (legacy by default).
    Use ``HealthSeverity.worst()`` for canonical values in new code.
    """
    if not severities:
        return "unknown"
    return min(severities, key=lambda s: _SEVERITY_ORDER.get(s, 2))


def to_canonical_severity(legacy: str) -> HealthSeverity:
    """Convert a legacy BNK severity string to canonical HealthSeverity."""
    return _LEGACY_TO_CANONICAL.get(legacy, HealthSeverity.UNKNOWN)


def make_resource_map(items: list[dict]) -> dict[str, dict]:
    """Create a ``namespace/name → resource`` lookup dict."""
    return {resource_key(r): r for r in items}


def resolve_list_refs(
    names: set[str] | list[str] | None,
    resource_map: dict[str, dict],
    namespace: str,
    spec_key: str,
) -> list[dict]:
    """Resolve address/port list references to their spec data."""
    if not names:
        return []
    resolved = []
    for name in names:
        res = resource_map.get(f"{namespace}/{name}") or {}
        spec = res.get("spec") or {}
        raw_items = spec.get(spec_key) or []
        if spec_key == "ports":
            items = [str(p) for p in raw_items if p is not None]
        else:
            items = list(raw_items)
        resolved.append({
            "name": name,
            spec_key: items,
        })
    return resolved


# ---------------------------------------------------------------------------
# Topology traversal
# ---------------------------------------------------------------------------


def build_route_ref_map(topology: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """
    Build a map of (namespace, name) → route references from the topology tree.

    Walks gateways → listeners → routes → backends to produce a lookup from
    backend service coordinates to the routes that reference them. Used by both
    ``backends`` (service cross-reference) and ``a2a_discovery`` (agent scanning).
    """
    ref_map: dict[tuple[str, str], list[dict]] = {}
    for gw in topology:
        for listener in gw.get("listeners", []):
            for route in listener.get("routes", []):
                for backend in route.get("backends", []):
                    backend_ns = backend.get("namespace") or route.get("namespace", "")
                    key = (backend_ns, backend.get("name", ""))
                    ref_map.setdefault(key, []).append({
                        "kind": route.get("kind", "HTTPRoute"),
                        "name": route.get("name", ""),
                        "namespace": route.get("namespace", ""),
                        "gatewayName": gw.get("name", ""),
                        "listenerName": listener.get("name", ""),
                        "port": backend.get("port"),
                        "weight": backend.get("weight"),
                    })
    return ref_map


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# CRD types fetched for all BNK insight views.
BNK_RESOURCE_TYPES: list[str] = [
    # Gateway API
    "gatewayclass", "gateway", "httproute", "grpcroute",
    "tcproute", "udproute", "tlsroute", "l4route", "referencegrant",
    # BNK policies
    "bnksecpolicy", "bnknetpolicy",
    # F5 CRDs
    "f5bigfwpolicy", "f5bigcneirule", "f5biganalyzer",
    "f5bigcneaddresslist", "f5bigcneportlist",
    # Data plane
    "f5spkvlan", "cneinstance", "f5spkstaticroute",
    "f5spksnatpool", "f5spkegress",
    # Logging
    "f5bigloghslpub", "f5biglogprofile",
    # Core K8s
    "service",
]
