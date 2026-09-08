"""Test isolation for the reachability circuit-breaker registry (#55).

The registry is a process-global singleton keyed by (target_type, target_id).
Running the backend suite as ONE process produced order-dependent failures
that never appeared in CI: an earlier integration test tripped the breaker
for cluster:1 OPEN, nothing reset it, and later tests whose fixtures reused
cluster id 1 short-circuited with BreakerOpenError before their mocked logic
ever ran. CI runs the suites in separate processes, so the leak was
invisible there -- which is exactly why it needs an autouse reset.

These two tests MUST run in file order: the first trips a breaker, the
second asserts it did not leak. pytest collects within a file in definition
order, so this is deterministic without a plugin.
"""

from __future__ import annotations

import pytest

from services.reachability.registry import registry

_TARGET = ("cluster", 999_001)  # an id no fixture uses


def _trip(target_type: str, target_id: int) -> None:
    """Record enough consecutive failures to open the breaker."""
    for _ in range(20):
        registry.record_real_call(target_type, target_id, success=False)


@pytest.mark.unit
def test_a_trips_the_breaker_open() -> None:
    _trip(*_TARGET)
    # Sanity: it really is open NOW, in this test.
    assert registry.allow_call(*_TARGET) is False, (
        "precondition: the breaker did not open; the next test proves nothing"
    )


@pytest.mark.unit
def test_b_next_test_does_not_inherit_the_open_breaker() -> None:
    """Without the autouse reset, this sees the breaker test_a left OPEN."""
    assert registry.allow_call(*_TARGET) is True, (
        "breaker state leaked across tests -- the autouse reset in conftest "
        "is not running (or not clearing _breakers)"
    )


@pytest.mark.unit
def test_reset_clears_every_per_target_map() -> None:
    """The reset must clear ALL per-target state, not just breakers -- a stale
    last-snapshot or last-success time is the same class of cross-test leak."""
    _trip(*_TARGET)
    registry._latest[_TARGET] = {"leaked": True}
    registry._target_names[_TARGET] = "leaked-name"
    from datetime import UTC, datetime
    registry._last_success_wall[_TARGET] = datetime.now(UTC)

    registry.reset_breaker_state()

    assert _TARGET not in registry._breakers
    assert _TARGET not in registry._latest
    assert _TARGET not in registry._target_names
    assert _TARGET not in registry._last_success_wall


@pytest.mark.unit
def test_reset_leaves_app_wiring_alone() -> None:
    """Probes and the session factory are set once at startup; tests that call
    probes depend on them. The reset must not unregister them."""
    before_probes = dict(registry._probes)
    registry.reset_breaker_state()
    assert registry._probes == before_probes
