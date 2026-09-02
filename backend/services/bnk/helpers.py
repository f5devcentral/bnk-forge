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


def has_condition(resource: dict, cond_type: str, expected: str | bool = "True") -> bool:
    """Check if a K8s resource (or status/conditions container) has a condition of given type with expected status."""
    if not isinstance(resource, dict):
        return False
    conditions = resource.get("conditions")
    if conditions is None:
        conditions = safe_get(resource, "status", "conditions", default=[])
    if not isinstance(conditions, list):
        return False
    exp_str = str(expected).lower()
    return any(
        isinstance(c, dict)
        and str(c.get("type", "")).lower() == cond_type.lower()
        and str(c.get("status", "")).lower() == exp_str
        for c in conditions
    )


def get_condition_message(resource: dict, cond_type: str) -> str:
    """Get the message string from a K8s resource condition."""
    if not isinstance(resource, dict):
        return ""
    conditions = resource.get("conditions")
    if conditions is None:
        conditions = safe_get(resource, "status", "conditions", default=[])
    if not isinstance(conditions, list):
        return ""
    type_lower = cond_type.lower()
    for c in conditions:
        if isinstance(c, dict) and str(c.get("type", "")).lower() == type_lower:
            msg: str = c.get("message", "") or c.get("reason", "")
            return msg
    return ""


def get_policy_operational_status(resource: dict) -> dict[str, Any]:
    """Derive resolved/programmed operational state from a BNK policy resource (BNKSecPolicy, BNKNetPolicy)."""
    if not isinstance(resource, dict):
        return {"resolved": False, "programmed": False, "messages": {"resolved": "", "programmed": ""}}

    status = resource.get("status", {}) or {}
    ancestors = status.get("ancestors")
    descendants = status.get("descendants")

    if (isinstance(ancestors, list) and len(ancestors) > 0) or (isinstance(descendants, list) and len(descendants) > 0):
        ancestors_list = ancestors if isinstance(ancestors, list) else []
        descendants_list = descendants if isinstance(descendants, list) else []

        ancestor_errors: list[str] = []
        descendant_errors: list[str] = []

        for a in ancestors_list:
            if not isinstance(a, dict):
                continue
            a_conds = a.get("conditions", [])
            is_ok = any(
                isinstance(c, dict)
                and str(c.get("status", "")).lower() == "true"
                and c.get("reason") != "RefNotFound"
                for c in a_conds
            )
            if not is_ok:
                for c in a_conds:
                    if isinstance(c, dict) and c.get("message"):
                        ancestor_errors.append(str(c.get("message")))
                        break

        for d in descendants_list:
            if not isinstance(d, dict):
                continue
            d_conds = d.get("conditions", [])
            is_ok = any(
                isinstance(c, dict)
                and str(c.get("status", "")).lower() == "true"
                and c.get("reason") != "RefNotFound"
                for c in d_conds
            )
            if not is_ok:
                for c in d_conds:
                    if isinstance(c, dict) and c.get("message"):
                        descendant_errors.append(str(c.get("message")))
                        break

        all_ok = len(ancestor_errors) == 0 and len(descendant_errors) == 0
        resolved_msg = "; ".join(ancestor_errors) if ancestor_errors else ""
        programmed_msg = "; ".join(descendant_errors) if descendant_errors else ""

        return {
            "resolved": all_ok or (len(ancestor_errors) == 0 and len(ancestors_list) > 0),
            "programmed": all_ok,
            "messages": {
                "resolved": resolved_msg,
                "programmed": programmed_msg,
            },
        }

    resolved = (
        has_condition(resource, "Resolved")
        or has_condition(resource, "ResolvedRefs")
        or has_condition(resource, "Accepted")
        or has_condition(resource, "Ready")
    )
    has_programmed_false = has_condition(resource, "Programmed", "False")
    programmed = (
        has_condition(resource, "Programmed")
        or (has_condition(resource, "Ready") and not has_programmed_false)
        or (resolved and not has_programmed_false and not get_condition_message(resource, "Programmed"))
    )
    return {
        "resolved": resolved,
        "programmed": programmed,
        "messages": {
            "resolved": get_condition_message(resource, "Resolved") or get_condition_message(resource, "ResolvedRefs") or get_condition_message(resource, "Accepted"),
            "programmed": get_condition_message(resource, "Programmed"),
        },
    }


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
