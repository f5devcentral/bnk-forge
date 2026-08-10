"""Unit tests for D-022 P6 Slice D — FleetOperationStrategy + executor extensions.

Pure / no-DB tests:
  - _group_members_into_waves(wave_by="label:<key>") buckets correctly
  - _resolve_wave_concurrency: pct → absolute = max(1, ceil(n*pct/100))
    including edge cases where pct rounds to 0 → must be 1
  - STRATEGY_GATE_KINDS constant contains expected values
  - _validate_strategy_fields: validates each field correctly
"""

import math

import pytest

from models.fleet_targeting import (
    GATE_KIND_APPROVAL,
    GATE_KIND_HEALTH_STABLE,
    GATE_KIND_TIMED_WAIT,
    STRATEGY_GATE_KINDS,
)
from services.fleet_bulkop_service import _group_members_into_waves, _resolve_wave_concurrency

# ---------------------------------------------------------------------------
# Shared fake member
# ---------------------------------------------------------------------------

class _FakeMember:
    """Minimal duck-typed FleetMember for pure-function tests."""

    def __init__(self, discovered=None, assigned=None, fault_domain=None):
        self.discovered_labels = discovered or {}
        self.assigned_labels = assigned or {}
        self.fault_domain = fault_domain
        self.id = 1
        self.member_type = "cluster"
        self.member_id = 1
        self.health_status = None


# ---------------------------------------------------------------------------
# _group_members_into_waves — label:<key> bucketing
# ---------------------------------------------------------------------------

class TestGroupMembersLabelWaveBy:
    """Test _group_members_into_waves with wave_by='label:<key>'."""

    def test_label_env_buckets_by_env_value(self):
        m_prod = _FakeMember(discovered={"env": "prod"})
        m_staging = _FakeMember(discovered={"env": "staging"})
        m_prod2 = _FakeMember(discovered={"env": "prod"})

        waves = _group_members_into_waves([m_prod, m_staging, m_prod2], wave_by="label:env")
        labels = [label for label, _ in waves]
        # prod and staging sorted lexicographically; no "(none)" bucket
        assert set(labels) == {"prod", "staging"}
        assert labels == sorted(labels)

    def test_label_env_prod_bucket_has_two_members(self):
        m_prod = _FakeMember(discovered={"env": "prod"})
        m_staging = _FakeMember(discovered={"env": "staging"})
        m_prod2 = _FakeMember(discovered={"env": "prod"})

        waves = _group_members_into_waves([m_prod, m_staging, m_prod2], wave_by="label:env")
        by_label = {label: members for label, members in waves}
        assert len(by_label["prod"]) == 2
        assert len(by_label["staging"]) == 1

    def test_label_missing_key_goes_to_none_bucket(self):
        m_with = _FakeMember(discovered={"env": "prod"})
        m_without = _FakeMember(discovered={"vendor": "aws"})  # no "env" key

        waves = _group_members_into_waves([m_with, m_without], wave_by="label:env")
        by_label = {label: members for label, members in waves}
        assert "prod" in by_label
        assert "(none)" in by_label
        assert len(by_label["(none)"]) == 1

    def test_label_assigned_wins_over_discovered_on_conflict(self):
        """assigned_labels shadow discovered_labels in the combined dict."""
        m = _FakeMember(
            discovered={"env": "staging"},
            assigned={"env": "prod"},  # assigned wins
        )
        waves = _group_members_into_waves([m], wave_by="label:env")
        labels = [label for label, _ in waves]
        # assigned "prod" should win
        assert labels == ["prod"]

    def test_label_wave_by_empty_key_value_goes_to_none(self):
        m = _FakeMember(discovered={"env": ""})  # empty string value
        waves = _group_members_into_waves([m], wave_by="label:env")
        labels = [label for label, _ in waves]
        assert labels == ["(none)"]

    def test_label_wave_by_all_none_single_bucket(self):
        m1 = _FakeMember(discovered={})
        m2 = _FakeMember(discovered={})
        waves = _group_members_into_waves([m1, m2], wave_by="label:env")
        assert len(waves) == 1
        assert waves[0][0] == "(none)"
        assert len(waves[0][1]) == 2

    def test_label_wave_deterministic_ordering(self):
        m_z = _FakeMember(discovered={"region": "us-west-2"})
        m_a = _FakeMember(discovered={"region": "ap-east-1"})
        m_m = _FakeMember(discovered={"region": "eu-central-1"})

        waves1 = _group_members_into_waves([m_z, m_a, m_m], wave_by="label:region")
        waves2 = _group_members_into_waves([m_m, m_z, m_a], wave_by="label:region")
        labels1 = [lbl for lbl, _ in waves1]
        labels2 = [lbl for lbl, _ in waves2]
        assert labels1 == labels2 == sorted(labels1)

    def test_fault_domain_wave_by_still_works(self):
        """Default fault_domain behaviour is unchanged after the extension."""
        m1 = _FakeMember(fault_domain="rack-1")
        m2 = _FakeMember(fault_domain="rack-2")
        m3 = _FakeMember(fault_domain=None)
        waves = _group_members_into_waves([m1, m2, m3], wave_by="fault_domain")
        labels = [lbl for lbl, _ in waves]
        assert labels == ["rack-1", "rack-2", "(none)"]


# ---------------------------------------------------------------------------
# _resolve_wave_concurrency: pct → absolute worker count
# ---------------------------------------------------------------------------

class TestResolveWaveConcurrency:
    """Test the pct-to-absolute concurrency resolution."""

    def _make_wave(self, n: int) -> list[_FakeMember]:
        return [_FakeMember() for _ in range(n)]

    # --- typical cases ---

    def test_20pct_of_10_members_is_2(self):
        wave = self._make_wave(10)
        assert _resolve_wave_concurrency(wave, 20, fallback=5) == 2

    def test_50pct_of_10_members_is_5(self):
        wave = self._make_wave(10)
        assert _resolve_wave_concurrency(wave, 50, fallback=5) == 5

    def test_100pct_of_10_members_is_10(self):
        wave = self._make_wave(10)
        assert _resolve_wave_concurrency(wave, 100, fallback=5) == 10

    # --- ceiling behaviour ---

    def test_33pct_of_10_is_ceil_3_3_eq_4(self):
        wave = self._make_wave(10)
        # ceil(10 * 33/100) = ceil(3.3) = 4
        assert _resolve_wave_concurrency(wave, 33, fallback=5) == 4

    def test_1pct_of_10_is_ceil_0_1_eq_1(self):
        """1% of 10 = 0.1 → ceil = 1; must be at least 1."""
        wave = self._make_wave(10)
        # ceil(10 * 1/100) = ceil(0.1) = 1
        assert _resolve_wave_concurrency(wave, 1, fallback=5) == 1

    def test_pct_that_rounds_to_zero_returns_1(self):
        """If pct is small enough that n*pct/100 < 1, ceil still gives ≥1."""
        wave = self._make_wave(1)  # single member
        # ceil(1 * 1/100) = ceil(0.01) = 1
        assert _resolve_wave_concurrency(wave, 1, fallback=5) == 1

    def test_empty_wave_with_pct_returns_1(self):
        """max(1, ceil(0*pct/100)) = max(1, 0) = 1."""
        wave = self._make_wave(0)
        assert _resolve_wave_concurrency(wave, 50, fallback=5) == 1

    # --- fallback path ---

    def test_none_pct_uses_fallback(self):
        wave = self._make_wave(10)
        assert _resolve_wave_concurrency(wave, None, fallback=7) == 7

    def test_fallback_also_clamped_to_1(self):
        wave = self._make_wave(5)
        assert _resolve_wave_concurrency(wave, None, fallback=0) == 1

    # --- ceil formula verification ---

    def test_ceil_formula_for_various_pcts(self):
        wave = self._make_wave(100)
        for pct in [1, 5, 10, 25, 50, 75, 99, 100]:
            expected = max(1, math.ceil(100 * pct / 100))
            result = _resolve_wave_concurrency(wave, pct, fallback=0)
            assert result == expected, f"pct={pct}: expected {expected}, got {result}"


# ---------------------------------------------------------------------------
# STRATEGY_GATE_KINDS constant
# ---------------------------------------------------------------------------

class TestStrategyGateKinds:
    def test_approval_in_gate_kinds(self):
        assert GATE_KIND_APPROVAL in STRATEGY_GATE_KINDS

    def test_timed_wait_in_gate_kinds(self):
        assert GATE_KIND_TIMED_WAIT in STRATEGY_GATE_KINDS

    def test_health_stable_in_gate_kinds(self):
        assert GATE_KIND_HEALTH_STABLE in STRATEGY_GATE_KINDS

    def test_unknown_not_in_gate_kinds(self):
        assert "unknown_gate" not in STRATEGY_GATE_KINDS

    def test_manual_not_in_strategy_gate_kinds(self):
        """Legacy 'manual' mode is not in STRATEGY_GATE_KINDS (replaced by 'approval')."""
        assert "manual" not in STRATEGY_GATE_KINDS


# ---------------------------------------------------------------------------
# _validate_strategy_fields (route helper) — imported here for unit coverage
# ---------------------------------------------------------------------------

class TestValidateStrategyFields:
    def _import(self):
        from routes.fleet import _validate_strategy_fields
        return _validate_strategy_fields

    def test_valid_fault_domain_approval(self):
        fn = self._import()
        fn("fault_domain", 20, "approval", None)  # no error

    def test_valid_label_wave_by(self):
        fn = self._import()
        fn("label:env", 50, "health_stable", None)

    def test_valid_timed_wait_with_seconds(self):
        fn = self._import()
        fn("fault_domain", 10, "timed_wait", 300)  # no error

    def test_invalid_wave_by(self):
        fn = self._import()
        from core.errors import BadRequestError
        with pytest.raises(BadRequestError):
            fn("invalid_wave_by", 20, "approval", None)

    def test_label_without_key_invalid(self):
        fn = self._import()
        from core.errors import BadRequestError
        with pytest.raises(BadRequestError):
            fn("label:", 20, "approval", None)  # empty key

    def test_pct_below_1_invalid(self):
        fn = self._import()
        from core.errors import BadRequestError
        with pytest.raises(BadRequestError):
            fn("fault_domain", 0, "approval", None)

    def test_pct_above_100_invalid(self):
        fn = self._import()
        from core.errors import BadRequestError
        with pytest.raises(BadRequestError):
            fn("fault_domain", 101, "approval", None)

    def test_unknown_gate_kind_invalid(self):
        fn = self._import()
        from core.errors import BadRequestError
        with pytest.raises(BadRequestError):
            fn("fault_domain", 50, "nonexistent_gate", None)

    def test_timed_wait_requires_gate_wait_seconds(self):
        fn = self._import()
        from core.errors import BadRequestError
        with pytest.raises(BadRequestError, match="gate_wait_seconds"):
            fn("fault_domain", 50, "timed_wait", None)

    def test_timed_wait_zero_seconds_invalid(self):
        fn = self._import()
        from core.errors import BadRequestError
        with pytest.raises(BadRequestError, match="gate_wait_seconds"):
            fn("fault_domain", 50, "timed_wait", 0)
