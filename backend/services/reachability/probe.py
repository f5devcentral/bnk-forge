"""Probe ABC + ProbeResult dataclass — the SDK contract for reachability surfaces.

A new connectivity surface (Helm, SSH, Vault, etc.) implements ``Probe`` and
registers an instance with ``ReachabilityRegistry``. The registry handles
scheduling, breaker state, Redis caching, and SSE publishing.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session


class ReachabilityState(StrEnum):
    """Three-state model (gRPC Health Checking + K8s startup-probe trichotomy).

    Cold start → UNKNOWN. UNKNOWN is honestly different from REACHABLE — the
    UI shows "checking…" rather than fail-open-as-healthy.
    """

    UNKNOWN = "unknown"
    REACHABLE = "reachable"
    UNREACHABLE = "unreachable"


class ErrorCategory(StrEnum):
    """4-category classification of probe / call failures.

    The category determines whether a failure feeds the breaker:
    - network/timeout/slow/target → counts toward open
    - auth/authz                  → does NOT count (avoids stuck-open on bad token)
    """

    NETWORK = "network"
    AUTH = "auth"
    AUTHZ = "authz"
    TARGET = "target"
    TIMEOUT = "timeout"
    SLOW = "slow"


@dataclass
class ProbeResult:
    """One probe outcome.

    `error_context` is what the frontend renders verbatim. Keys MUST include
    ``target_name`` and ``suggested_action`` when state == UNREACHABLE so the
    UI doesn't have to translate machine codes.
    """

    state: ReachabilityState
    checked_at: datetime
    latency_ms: float | None = None
    error_category: ErrorCategory | None = None
    error_context: dict[str, Any] = field(default_factory=dict)


class Probe(ABC):
    """ABC for a reachability surface.

    Subclasses define probe semantics for one target type (cluster, SSH host,
    Helm repo, etc.). The registry instantiates one ``Probe`` per type and
    invokes ``probe(target_id)`` on a schedule.

    Class-level configuration (override on subclass):
    - ``target_type``: stable identifier used in SSE events + breaker keys
    - ``probe_interval_seconds``: cadence (jitter ±10% applied by registry)
    - ``slow_threshold_ms``: latency above which a "successful" call is treated
      as a failure (resilience4j slowCallDurationThreshold)
    - ``breaker_failure_threshold``: consecutive failures to open
    - ``breaker_sleep_window_seconds``: how long open before half-open probe
    """

    target_type: str = ""
    probe_interval_seconds: int = 30
    slow_threshold_ms: int = 5000
    breaker_failure_threshold: int = 5
    breaker_sleep_window_seconds: int = 30

    @abstractmethod
    async def probe(self, target_id: int) -> ProbeResult:
        """Run a single reachability probe against the target.

        Implementations MUST classify errors via ``ErrorCategory`` and populate
        ``error_context`` with at least ``target_name`` and ``suggested_action``
        for unreachable results. Should not raise — return a ProbeResult with
        state=UNREACHABLE instead.
        """

    @abstractmethod
    def list_targets(self, db: Session) -> list[int]:
        """Return target IDs that should be polled.

        Called once per scheduling cycle so newly-added targets are picked up
        without a backend restart.
        """
