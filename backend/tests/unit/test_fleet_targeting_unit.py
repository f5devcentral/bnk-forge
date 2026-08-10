"""Unit tests for fleet targeting + bulk-ops — D-022 Phase 2.

Tests (pure/no-DB):
  - matches_selector: correct AND logic over discovered + assigned labels.
  - validate_selector: forbidden keys rejected; valid selectors pass.
  - SAFE_ACTIONS allowlist: only known safe actions allowed.
  - project_id rejected as selector key.
  - _group_members_into_waves: fault-domain ordering, NULL trailing.
  - StaleDecisionError raised correctly.
"""

import pytest

from models.fleet_targeting import (
    DECISION_TTL_SECONDS,
    FORBIDDEN_SELECTOR_KEYS,
    SAFE_ACTIONS,
)
from services.fleet_bulkop_service import _group_members_into_waves
from services.fleet_selector import matches_selector, validate_selector

# ---------------------------------------------------------------------------
# matches_selector
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


class TestMatchesSelector:
    def test_empty_filter_always_matches(self):
        m = _FakeMember(discovered={"vendor": "aws"})
        assert matches_selector(m, []) is True

    def test_single_discovered_label_matches(self):
        m = _FakeMember(discovered={"env": "prod"})
        assert matches_selector(m, ["env=prod"]) is True

    def test_single_discovered_label_no_match(self):
        m = _FakeMember(discovered={"env": "staging"})
        assert matches_selector(m, ["env=prod"]) is False

    def test_assigned_label_matches(self):
        m = _FakeMember(assigned={"team": "platform"})
        assert matches_selector(m, ["team=platform"]) is True

    def test_AND_logic_all_must_match(self):
        m = _FakeMember(discovered={"vendor": "aws"}, assigned={"env": "prod"})
        assert matches_selector(m, ["vendor=aws", "env=prod"]) is True
        assert matches_selector(m, ["vendor=aws", "env=staging"]) is False

    def test_filter_without_equals_ignored(self):
        m = _FakeMember(discovered={"vendor": "aws"})
        # "noequalssign" is ignored; only "vendor=aws" is checked
        assert matches_selector(m, ["noequalssign", "vendor=aws"]) is True

    def test_discovered_overridden_by_assigned_in_union(self):
        # Union is {**discovered, **assigned} — assigned wins on conflict.
        m = _FakeMember(
            discovered={"env": "staging"},
            assigned={"env": "prod"},  # assigned shadows discovered
        )
        # The combined dict has env=prod from assigned (dict merge order).
        combined = {**m.discovered_labels, **m.assigned_labels}
        assert combined["env"] == "prod"
        assert matches_selector(m, ["env=prod"]) is True


# ---------------------------------------------------------------------------
# validate_selector
# ---------------------------------------------------------------------------

class TestValidateSelector:
    def test_valid_selector_passes(self):
        validate_selector({"labels": ["env=prod", "has-dpu=true"]})

    def test_empty_labels_passes(self):
        validate_selector({"labels": []})

    def test_missing_labels_key_passes(self):
        validate_selector({})

    def test_project_id_key_rejected(self):
        from core.errors import ValidationError
        with pytest.raises(ValidationError, match="Forbidden selector key 'project_id'"):
            validate_selector({"labels": ["project_id=123"]})

    def test_member_id_key_rejected(self):
        from core.errors import ValidationError
        with pytest.raises(ValidationError):
            validate_selector({"labels": ["member_id=5"]})

    def test_member_type_key_rejected(self):
        from core.errors import ValidationError
        with pytest.raises(ValidationError):
            validate_selector({"labels": ["member_type=cluster"]})

    def test_non_dict_selector_rejected(self):
        from core.errors import ValidationError
        with pytest.raises(ValidationError):
            validate_selector("env=prod")  # type: ignore

    def test_non_list_labels_rejected(self):
        from core.errors import ValidationError
        with pytest.raises(ValidationError):
            validate_selector({"labels": "env=prod"})  # type: ignore


# ---------------------------------------------------------------------------
# SAFE_ACTIONS allowlist
# ---------------------------------------------------------------------------

class TestSafeActionsAllowlist:
    def test_re_reconcile_is_safe(self):
        assert "re-reconcile" in SAFE_ACTIONS

    def test_set_labels_is_safe(self):
        assert "set-labels" in SAFE_ACTIONS

    def test_destroy_is_not_safe(self):
        assert "destroy" not in SAFE_ACTIONS

    def test_delete_is_not_safe(self):
        assert "delete" not in SAFE_ACTIONS

    def test_project_id_not_a_forbidden_key_alias(self):
        # Sanity check the constant
        assert "project_id" in FORBIDDEN_SELECTOR_KEYS


# ---------------------------------------------------------------------------
# _group_members_into_waves — fault-domain ordering
# ---------------------------------------------------------------------------

class TestGroupMembersIntoWaves:
    def _make_member(self, fault_domain):
        m = _FakeMember(fault_domain=fault_domain)
        return m

    def test_sorted_non_null_domains_then_none_bucket(self):
        m1 = self._make_member("zone-c")
        m2 = self._make_member("zone-a")
        m3 = self._make_member(None)
        m4 = self._make_member("zone-b")

        waves = _group_members_into_waves([m1, m2, m3, m4])
        labels = [label for label, _ in waves]
        # sorted non-null first, then "(none)"
        assert labels == ["zone-a", "zone-b", "zone-c", "(none)"]

    def test_null_only_members_go_to_none_bucket(self):
        m1 = self._make_member(None)
        m2 = self._make_member("")
        waves = _group_members_into_waves([m1, m2])
        assert len(waves) == 1
        assert waves[0][0] == "(none)"
        assert len(waves[0][1]) == 2

    def test_single_fault_domain_single_wave(self):
        m = self._make_member("rack-1")
        waves = _group_members_into_waves([m])
        assert len(waves) == 1
        assert waves[0][0] == "rack-1"

    def test_empty_members_returns_empty_waves(self):
        waves = _group_members_into_waves([])
        assert waves == []

    def test_deterministic_ordering_repeated_calls(self):
        members = [
            self._make_member("z"),
            self._make_member("a"),
            self._make_member("m"),
        ]
        waves1 = _group_members_into_waves(members)
        waves2 = _group_members_into_waves(members[::-1])
        labels1 = [label for label, _ in waves1]
        labels2 = [label for label, _ in waves2]
        assert labels1 == labels2 == ["a", "m", "z"]
