"""
BNK data analysis package.

Re-exports all public functions for backward compatibility with existing
imports from ``services.bnk_data_service``. Internal modules can also
be imported directly for finer-grained access.

Modules:
    helpers             — shared K8s dict helpers, severity, constants
    fetch               — parallel CRD + pod fetching (only I/O module)
    health              — health dashboard analysis
    topology            — gateway topology tree
    backends            — service/route cross-reference
    a2a_discovery       — A2A agent discovery from HTTPRoute backends
    palette             — lightweight summaries for Config Builder
    policy_associations — security policy → gateway mapping
"""

from services.bnk.a2a_discovery import discover_a2a_agents
from services.bnk.backends import analyze_backends
from services.bnk.fetch import fetch_all_bnk_data
from services.bnk.health import analyze_health
from services.bnk.helpers import (
    BNK_RESOURCE_TYPES,
    build_route_ref_map,
    calc_severity,
    get_condition_message,
    has_condition,
    make_resource_map,
    resource_key,
    resource_name,
    resource_ns,
    rollup_severity,
    safe_get,
)
from services.bnk.palette import extract_palette_data
from services.bnk.policy_associations import analyze_policy_associations
from services.bnk.topology import analyze_topology
from services.bnk.traffic_stats import analyze_traffic_stats, fetch_tmm_traffic_stats

__all__ = [
    # Fetch
    "fetch_all_bnk_data",
    # Analysis
    "discover_a2a_agents",
    "analyze_health",
    "analyze_topology",
    "analyze_backends",
    "extract_palette_data",
    "analyze_policy_associations",
    "analyze_traffic_stats",
    "fetch_tmm_traffic_stats",
    # Helpers
    "build_route_ref_map",
    "safe_get",
    "has_condition",
    "get_condition_message",
    "calc_severity",
    "rollup_severity",
    "resource_name",
    "resource_ns",
    "resource_key",
    "make_resource_map",
    # Constants
    "BNK_RESOURCE_TYPES",
]
