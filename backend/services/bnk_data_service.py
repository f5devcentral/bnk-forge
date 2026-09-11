"""
BNK Data Service — backward-compatible re-export shim.

All logic has been decomposed into the ``services.bnk`` package:
    - ``services.bnk.helpers``              — shared K8s dict utilities
    - ``services.bnk.fetch``                — parallel CRD + pod fetching
    - ``services.bnk.health``               — health dashboard analysis
    - ``services.bnk.topology``             — gateway topology tree
    - ``services.bnk.backends``             — service/route cross-reference
    - ``services.bnk.palette``              — config builder palette
    - ``services.bnk.policy_associations``  — security policy mapping

Existing ``from services.bnk_data_service import ...`` imports continue
to work unchanged. New code should import from ``services.bnk`` directly.
"""

# ruff: noqa: F401 — re-exports for backward compatibility
from services.bnk import (
    BNK_RESOURCE_TYPES,
    analyze_backends,
    analyze_health,
    analyze_policy_associations,
    analyze_topology,
    analyze_traffic_stats,
    calc_severity,
    extract_palette_data,
    fetch_all_bnk_data,
    fetch_tmm_traffic_stats,
    get_condition_message,
    has_condition,
    rollup_severity,
    safe_get,
)
