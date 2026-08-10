"""
Unit tests for services.bnk.helpers — K8s dict utilities and severity logic.

Pure functions, no I/O, no mocking needed.
"""

import pytest

from services.bnk.helpers import (
    BNK_RESOURCE_TYPES,
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

# ---------------------------------------------------------------------------
# safe_get
# ---------------------------------------------------------------------------


class TestSafeGet:
    def test_single_key(self):
        assert safe_get({"a": 1}, "a") == 1

    def test_nested_keys(self):
        assert safe_get({"a": {"b": {"c": 42}}}, "a", "b", "c") == 42

    def test_missing_key_returns_default(self):
        assert safe_get({"a": 1}, "b") is None
        assert safe_get({"a": 1}, "b", default="fallback") == "fallback"

    def test_none_value_returns_default(self):
        assert safe_get({"a": None}, "a", default="x") == "x"

    def test_non_dict_intermediate_returns_default(self):
        assert safe_get({"a": "string"}, "a", "b") is None

    def test_non_dict_input_returns_default(self):
        assert safe_get("not a dict", "a") is None
        assert safe_get(42, "a", default="d") == "d"

    def test_empty_keys(self):
        obj = {"a": 1}
        assert safe_get(obj) == obj

    def test_deeply_nested(self):
        obj = {"a": {"b": {"c": {"d": {"e": "deep"}}}}}
        assert safe_get(obj, "a", "b", "c", "d", "e") == "deep"


# ---------------------------------------------------------------------------
# resource_name / resource_ns / resource_key
# ---------------------------------------------------------------------------


class TestResourceAccessors:
    def test_resource_name(self):
        r = {"metadata": {"name": "my-gw", "namespace": "default"}}
        assert resource_name(r) == "my-gw"

    def test_resource_ns(self):
        r = {"metadata": {"name": "my-gw", "namespace": "f5-bnk"}}
        assert resource_ns(r) == "f5-bnk"

    def test_resource_key(self):
        r = {"metadata": {"name": "my-gw", "namespace": "f5-bnk"}}
        assert resource_key(r) == "f5-bnk/my-gw"

    def test_missing_metadata_returns_empty(self):
        assert resource_name({}) == ""
        assert resource_ns({}) == ""
        assert resource_key({}) == "/"

    def test_missing_name_returns_empty(self):
        assert resource_name({"metadata": {}}) == ""

    def test_cluster_scoped_resource(self):
        """Cluster-scoped resources (no namespace) return empty namespace."""
        r = {"metadata": {"name": "f5-bnk"}}
        assert resource_ns(r) == ""
        assert resource_key(r) == "/f5-bnk"


# ---------------------------------------------------------------------------
# has_condition / get_condition_message
# ---------------------------------------------------------------------------


class TestHasCondition:
    def test_condition_present_and_true(self):
        r = {"status": {"conditions": [
            {"type": "Programmed", "status": "True", "message": "OK"},
        ]}}
        assert has_condition(r, "Programmed") is True

    def test_condition_present_but_false(self):
        r = {"status": {"conditions": [
            {"type": "Programmed", "status": "False", "message": "failed"},
        ]}}
        assert has_condition(r, "Programmed") is False

    def test_condition_missing(self):
        r = {"status": {"conditions": [
            {"type": "Accepted", "status": "True"},
        ]}}
        assert has_condition(r, "Programmed") is False

    def test_no_conditions_list(self):
        assert has_condition({"status": {}}, "Programmed") is False
        assert has_condition({}, "Programmed") is False

    def test_null_conditions(self):
        """K8s API can return null for conditions."""
        assert has_condition({"status": {"conditions": None}}, "Programmed") is False

    def test_custom_expected_status(self):
        r = {"status": {"conditions": [
            {"type": "Ready", "status": "False"},
        ]}}
        assert has_condition(r, "Ready", expected="False") is True

    def test_non_dict_input(self):
        assert has_condition("not a dict", "Programmed") is False
        assert has_condition(None, "Programmed") is False

    def test_non_dict_condition_entries_skipped(self):
        r = {"status": {"conditions": ["garbage", 42, None]}}
        assert has_condition(r, "Programmed") is False

    def test_multiple_conditions(self):
        r = {"status": {"conditions": [
            {"type": "Accepted", "status": "True"},
            {"type": "Programmed", "status": "True"},
            {"type": "Ready", "status": "False"},
        ]}}
        assert has_condition(r, "Accepted") is True
        assert has_condition(r, "Programmed") is True
        assert has_condition(r, "Ready") is False


class TestGetConditionMessage:
    def test_message_present(self):
        r = {"status": {"conditions": [
            {"type": "Programmed", "status": "False", "message": "no listeners configured"},
        ]}}
        assert get_condition_message(r, "Programmed") == "no listeners configured"

    def test_condition_without_message_key(self):
        r = {"status": {"conditions": [
            {"type": "Programmed", "status": "True"},
        ]}}
        assert get_condition_message(r, "Programmed") == ""

    def test_missing_condition_type(self):
        r = {"status": {"conditions": [
            {"type": "Accepted", "status": "True", "message": "ok"},
        ]}}
        assert get_condition_message(r, "Programmed") == ""

    def test_empty_resource(self):
        assert get_condition_message({}, "Programmed") == ""

    def test_non_dict_input(self):
        assert get_condition_message("not a dict", "Programmed") == ""


# ---------------------------------------------------------------------------
# calc_severity / rollup_severity
# ---------------------------------------------------------------------------


class TestCalcSeverity:
    def test_all_healthy(self):
        assert calc_severity(3, 3) == "healthy"

    def test_partial(self):
        assert calc_severity(1, 3) == "warning"

    def test_none_healthy(self):
        assert calc_severity(0, 3) == "critical"

    def test_zero_total(self):
        assert calc_severity(0, 0) == "unknown"

    def test_one_of_one(self):
        assert calc_severity(1, 1) == "healthy"


class TestRollupSeverity:
    def test_all_healthy(self):
        assert rollup_severity(["healthy", "healthy", "healthy"]) == "healthy"

    def test_one_warning(self):
        assert rollup_severity(["healthy", "warning", "healthy"]) == "warning"

    def test_one_critical(self):
        assert rollup_severity(["healthy", "warning", "critical"]) == "critical"

    def test_unknown_only(self):
        assert rollup_severity(["unknown", "unknown"]) == "unknown"

    def test_unknown_with_healthy(self):
        """Unknown is worse than healthy."""
        assert rollup_severity(["healthy", "unknown"]) == "unknown"

    def test_empty_list(self):
        assert rollup_severity([]) == "unknown"

    def test_single_element(self):
        assert rollup_severity(["critical"]) == "critical"
        assert rollup_severity(["healthy"]) == "healthy"

    def test_unrecognized_severity_treated_as_unknown(self):
        """Unknown severity strings default to 'unknown' priority."""
        assert rollup_severity(["healthy", "banana"]) == "banana"


# ---------------------------------------------------------------------------
# make_resource_map
# ---------------------------------------------------------------------------


class TestMakeResourceMap:
    def test_builds_lookup(self):
        items = [
            {"metadata": {"name": "gw-1", "namespace": "ns-a"}},
            {"metadata": {"name": "gw-2", "namespace": "ns-b"}},
        ]
        m = make_resource_map(items)
        assert "ns-a/gw-1" in m
        assert "ns-b/gw-2" in m
        assert m["ns-a/gw-1"] is items[0]

    def test_empty_list(self):
        assert make_resource_map([]) == {}

    def test_duplicate_key_last_wins(self):
        """If two resources have the same namespace/name, last one wins."""
        items = [
            {"metadata": {"name": "x", "namespace": "ns"}, "v": 1},
            {"metadata": {"name": "x", "namespace": "ns"}, "v": 2},
        ]
        m = make_resource_map(items)
        assert m["ns/x"]["v"] == 2


# ---------------------------------------------------------------------------
# BNK_RESOURCE_TYPES constant
# ---------------------------------------------------------------------------


class TestBnkResourceTypes:
    def test_is_non_empty_list(self):
        assert isinstance(BNK_RESOURCE_TYPES, list)
        assert len(BNK_RESOURCE_TYPES) > 0

    def test_includes_core_types(self):
        assert "gateway" in BNK_RESOURCE_TYPES
        assert "httproute" in BNK_RESOURCE_TYPES
        assert "service" in BNK_RESOURCE_TYPES
        assert "f5spkvlan" in BNK_RESOURCE_TYPES
        assert "cneinstance" in BNK_RESOURCE_TYPES

    def test_no_duplicates(self):
        assert len(BNK_RESOURCE_TYPES) == len(set(BNK_RESOURCE_TYPES))
