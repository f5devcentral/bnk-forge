"""
DPF (DOCA Platform Framework) service package — NVIDIA DPU management.

Provides detection, data fetching, and health analysis for DPF-managed
BlueField DPUs on Kubernetes clusters.

Modules:
    constants   — DPF CRD type keys and group identifiers
    fetch       — parallel K8s API data collection (only I/O module)
    health      — DPF health aggregation (pure analysis)
"""

from services.dpf.constants import DPF_DETECT_TYPES, DPF_RESOURCE_TYPES
from services.dpf.fetch import detect_dpf, fetch_all_dpf_data
from services.dpf.health import analyze_dpf_health

__all__ = [
    "DPF_DETECT_TYPES",
    "DPF_RESOURCE_TYPES",
    "analyze_dpf_health",
    "detect_dpf",
    "fetch_all_dpf_data",
]
