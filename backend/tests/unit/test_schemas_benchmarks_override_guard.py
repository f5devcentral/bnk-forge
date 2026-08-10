"""
CT-override: Negative + positive schema tests for ScenarioRunRequest.no_internal_keys.

Tests that ScenarioRunRequest rejects forbidden override keys (trace_url, _-prefixed)
with ValidationError and accepts benign overrides.
"""

import pytest
from pydantic import ValidationError

from schemas.benchmarks import ScenarioRunRequest  # noqa: E402

# ── Forbidden: trace_url ─────────────────────────────────────────────────────


def test_ScenarioRunRequest_overrides_trace_url_raisesValidationError():
    """trace_url is a forbidden override key — must be rejected at schema layer."""
    with pytest.raises(ValidationError) as exc_info:
        ScenarioRunRequest(
            scenario_key="prefix-cache",
            overrides={"trace_url": "http://evil.example.com/x"},
        )
    assert "trace_url" in str(exc_info.value)


# ── Forbidden: _-prefixed keys ───────────────────────────────────────────────


def test_ScenarioRunRequest_overrides_underscore_prefix_raisesValidationError():
    """_-prefixed keys are Forge-internal metadata — must be rejected at schema layer."""
    with pytest.raises(ValidationError) as exc_info:
        ScenarioRunRequest(
            scenario_key="prefix-cache",
            overrides={"_scenario_key": "injected"},
        )
    assert "_scenario_key" in str(exc_info.value)


# ── Accepted: benign overrides ───────────────────────────────────────────────


def test_ScenarioRunRequest_overrides_benign_key_accepted():
    """A legitimate override (e.g. model) must be accepted without error."""
    req = ScenarioRunRequest(
        scenario_key="prefix-cache",
        overrides={"model": "tinyllama"},
    )
    assert req.overrides == {"model": "tinyllama"}


def test_ScenarioRunRequest_overrides_none_accepted():
    """No overrides (None) is always valid."""
    req = ScenarioRunRequest(scenario_key="prefix-cache", overrides=None)
    assert req.overrides is None


def test_ScenarioRunRequest_overrides_empty_dict_accepted():
    """An empty overrides dict is valid."""
    req = ScenarioRunRequest(scenario_key="prefix-cache", overrides={})
    assert req.overrides == {}
