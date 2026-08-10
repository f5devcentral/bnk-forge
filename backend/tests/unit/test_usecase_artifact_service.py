"""
Unit tests for services.usecase_artifact_service pure functions — param-lift,
content-hash idempotency, and render (D-034 Phase 0 tracer).
"""

from types import SimpleNamespace

import pytest

from core.errors import BadRequestError
from services.usecase_artifact_service import (
    compute_content_hash,
    lift_params,
    render,
)


def _vlan(name: str, selfips: list[str], namespace: str = "spk") -> dict:
    return {
        "kind": "F5SPKVlan",
        "apiVersion": "k8s.f5net.com/v1",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {"selfip_v4s": selfips, "interfaces": ["p0"]},
    }


class TestLiftParams:
    """param-lift via the _CAPTURE_PATHS registry — iterates, never `if kind == ...`."""

    def test_replaces_selfip_with_token(self):
        templates, schema = lift_params([_vlan("vlan1", ["10.0.0.1/24"])])
        assert templates[0]["spec"]["selfip_v4s"] == "${selfip_v4s}"
        assert templates[0]["spec"]["interfaces"] == ["p0"]  # untouched — not a capture path

    def test_param_schema_entry_shape(self):
        _, schema = lift_params([_vlan("vlan1", ["10.0.0.1/24"])])
        assert len(schema) == 1
        entry = schema[0]
        assert entry["key"] == "selfip_v4s"
        assert entry["type"] == "ip"
        assert entry["kind"] == "assigned"
        assert entry["is_list"] is True
        assert entry["required"] is True
        assert entry["source_paths"] == [{"kind": "F5SPKVlan", "jsonpath": "spec.selfip_v4s"}]

    def test_multiple_resources_share_one_param_entry(self):
        _, schema = lift_params([_vlan("vlan1", ["10.0.0.1/24"]), _vlan("vlan2", ["10.0.0.2/24"])])
        assert len(schema) == 1

    def test_non_matching_kind_untouched(self):
        other = {"kind": "Gateway", "spec": {"selfip_v4s": ["10.0.0.1/24"]}}
        templates, schema = lift_params([other])
        assert templates[0]["spec"]["selfip_v4s"] == ["10.0.0.1/24"]
        assert schema == []

    def test_missing_path_skipped(self):
        vlan = {"kind": "F5SPKVlan", "spec": {"interfaces": ["p0"]}}
        templates, schema = lift_params([vlan])
        assert schema == []
        assert templates[0]["spec"] == {"interfaces": ["p0"]}

    def test_does_not_mutate_input(self):
        original = _vlan("vlan1", ["10.0.0.1/24"])
        lift_params([original])
        assert original["spec"]["selfip_v4s"] == ["10.0.0.1/24"]


class TestContentHash:
    """content_hash covers templated structure only — excludes concrete values."""

    def test_same_shape_different_values_same_hash(self):
        templates_a, schema_a = lift_params([_vlan("vlan1", ["10.0.0.1/24"])])
        templates_b, schema_b = lift_params([_vlan("vlan1", ["192.168.1.1/24"])])
        assert compute_content_hash(templates_a, schema_a) == compute_content_hash(templates_b, schema_b)

    def test_different_shape_different_hash(self):
        templates_a, schema_a = lift_params([_vlan("vlan1", ["10.0.0.1/24"])])
        templates_b, schema_b = lift_params([_vlan("vlan2", ["10.0.0.1/24"])])
        assert compute_content_hash(templates_a, schema_a) != compute_content_hash(templates_b, schema_b)

    def test_hash_is_deterministic(self):
        templates, schema = lift_params([_vlan("vlan1", ["10.0.0.1/24"])])
        assert compute_content_hash(templates, schema) == compute_content_hash(templates, schema)


class TestRender:
    """render() substitutes tokens; missing required params are a hard error."""

    def _version(self, templates, schema):
        return SimpleNamespace(cr_templates=templates, param_schema=schema)

    def test_substitutes_concrete_value(self):
        templates, schema = lift_params([_vlan("vlan1", ["10.0.0.1/24"])])
        version = self._version(templates, schema)
        rendered = render(version, {"selfip_v4s": ["10.9.9.9/24"]})
        assert rendered[0]["spec"]["selfip_v4s"] == ["10.9.9.9/24"]

    def test_missing_required_param_raises(self):
        templates, schema = lift_params([_vlan("vlan1", ["10.0.0.1/24"])])
        version = self._version(templates, schema)
        with pytest.raises(BadRequestError) as exc_info:
            render(version, {})
        assert "selfip_v4s" in str(exc_info.value)

    def test_render_does_not_mutate_stored_templates(self):
        templates, schema = lift_params([_vlan("vlan1", ["10.0.0.1/24"])])
        version = self._version(templates, schema)
        render(version, {"selfip_v4s": ["10.9.9.9/24"]})
        assert version.cr_templates[0]["spec"]["selfip_v4s"] == "${selfip_v4s}"
