"""Shared boto3 ``Config`` for all forge-side AWS clients.

The RFC's Phase-2 cloud-API integration: rather than wrapping AWS calls in
yet another circuit breaker (which would fight botocore's own retry
accounting — anti-pattern per the boto3 docs), we configure botocore's
**adaptive** retry mode. That ships with built-in circuit-breaker
semantics + token-bucket rate limiting, and is the recommended setting
for foreground / latency-sensitive AWS calls.

Lazy import: callers that never touch AWS shouldn't pay the botocore
import cost (~10MB) just because the symbol is in scope.
"""
from __future__ import annotations

from typing import Any

_cached_config: Any = None


def adaptive_retry_config() -> Any:
    """Return a singleton ``botocore.config.Config`` with adaptive retry.

    ``mode="adaptive"`` enables standard mode's circuit breaker plus a
    token-bucket throttle that backs off on TooManyRequests. ``max_attempts=3``
    bounds a single call to two retries — enough for transient network /
    503 blips, not enough to mask a real outage.
    """
    global _cached_config
    if _cached_config is None:
        from botocore.config import Config
        _cached_config = Config(retries={"mode": "adaptive", "max_attempts": 3})
    return _cached_config
