"""Reachability subsystem.

Provides a unified probe + breaker + state-cache layer for any external
target (K8s clusters, SSH hosts, Helm repos, cloud APIs, operator). Phase 1
ships clusters only; SDK contract for additional probe types lives in
``probe.Probe``.

Public surface:
    - Probe / ProbeResult            : services.reachability.probe
    - ReachabilityRegistry singleton : services.reachability.registry.registry
    - with_breaker decorator         : services.reachability.breaker
    - categorize_exception           : services.reachability.breaker
"""
from services.reachability.breaker import (
    BreakerState,
    categorize_exception,
    with_breaker,
)
from services.reachability.probe import ErrorCategory, Probe, ProbeResult, ReachabilityState
from services.reachability.registry import ReachabilityRegistry, registry

__all__ = [
    "BreakerState",
    "ErrorCategory",
    "Probe",
    "ProbeResult",
    "ReachabilityRegistry",
    "ReachabilityState",
    "categorize_exception",
    "registry",
    "with_breaker",
]
