"""
Unit tests for WAF Policy Manager registry entries and route helpers.

Verifies the 4 appprotect.f5.com/v1 CRDs (owned by nap-policy-operator's
PLM, unmodified) are correctly registered, and that the route module's
YAML-building helper produces the expected resource shape.
See docs/WAF_POLICY_MANAGER_DESIGN.md.
"""

from core.k8s_resource_registry import RESOURCE_REGISTRY, get_resource_type
from core.k8s_types import ApiGroups, ResourceCategory
from routes.k8s.waf_policies import APSIGNATURES_NAME, _build_resource_yaml, _find_by_name


class TestWafResourceRegistry:

    def test_appolicy_registered(self):
        rt = get_resource_type("appolicy")
        assert rt.kind == "APPolicy"
        assert rt.api_group == ApiGroups.APPPROTECT
        assert rt.api_version == "v1"
        assert rt.plural == "appolicies"
        assert rt.namespaced is True
        assert rt.category == ResourceCategory.WAF

    def test_aplogconf_registered(self):
        rt = get_resource_type("aplogconf")
        assert rt.kind == "APLogConf"
        assert rt.plural == "aplogconfs"
        assert rt.api_group == ApiGroups.APPPROTECT

    def test_apsignatures_registered(self):
        rt = get_resource_type("apsignatures")
        assert rt.kind == "APSignatures"
        assert rt.plural == "apsignatures"
        assert rt.api_group == ApiGroups.APPPROTECT

    def test_apusersig_registered(self):
        rt = get_resource_type("apusersig")
        assert rt.kind == "APUserSig"
        assert rt.plural == "apusersigs"
        assert rt.api_group == ApiGroups.APPPROTECT

    def test_all_four_crds_present_in_registry(self):
        for key in ("appolicy", "aplogconf", "apsignatures", "apusersig"):
            assert key in RESOURCE_REGISTRY, f"{key} not in RESOURCE_REGISTRY"


class TestBuildResourceYaml:

    def test_produces_expected_shape(self):
        rendered = _build_resource_yaml("APPolicy", "my-policy", "default", {"policy": {"name": "my-policy"}})
        assert "apiVersion: appprotect.f5.com/v1" in rendered
        assert "kind: APPolicy" in rendered
        assert "name: my-policy" in rendered
        assert "namespace: default" in rendered

    def test_apsignatures_uses_singleton_name(self):
        rendered = _build_resource_yaml("APSignatures", APSIGNATURES_NAME, "default", {})
        assert f"name: {APSIGNATURES_NAME}" in rendered


class TestFindByName:

    def test_finds_matching_resource(self):
        resources = [
            {"metadata": {"name": "a"}},
            {"metadata": {"name": "b"}},
        ]
        found = _find_by_name(resources, "b")
        assert found == {"metadata": {"name": "b"}}

    def test_returns_none_when_not_found(self):
        resources = [{"metadata": {"name": "a"}}]
        assert _find_by_name(resources, "missing") is None

    def test_returns_none_for_empty_list(self):
        assert _find_by_name([], "anything") is None
