"""
Tests for services.config_export_service — YAML export, config diff, and cluster export.

BC-009: Config export service — pure functions tested directly,
export_cluster_config tested with mocked DB + K8s client.
"""

from unittest.mock import MagicMock, patch

import pytest
import yaml
from kubernetes.client.rest import ApiException

from services.config_export_service import (
    _clean_resource,
    _flatten_resources,
    apply_resources,
    config_to_yaml,
    diff_configs,
    export_cluster_config,
)

# ---------------------------------------------------------------------------
# config_to_yaml
# ---------------------------------------------------------------------------


class TestConfigToYaml:
    """YAML serialization of export configs."""

    def test_nested_dict(self):
        config = {
            "bnk_forge_export": {"version": "1.0"},
            "resources": {"gateway_api": [{"kind": "Gateway", "metadata": {"name": "gw1"}}]},
        }
        result = config_to_yaml(config)
        assert isinstance(result, str)
        parsed = yaml.safe_load(result)
        assert parsed["bnk_forge_export"]["version"] == "1.0"
        assert parsed["resources"]["gateway_api"][0]["kind"] == "Gateway"

    def test_empty_dict(self):
        result = config_to_yaml({})
        assert yaml.safe_load(result) == {}

    def test_unicode_preserved(self):
        config = {"label": "production-\u00e9"}
        result = config_to_yaml(config)
        assert "\u00e9" in result


# ---------------------------------------------------------------------------
# diff_configs
# ---------------------------------------------------------------------------


class TestDiffConfigs:
    """Diffing two exported cluster configurations."""

    @staticmethod
    def _make_config(cluster_name, resources=None, module_config=None):
        return {
            "bnk_forge_export": {"cluster": {"name": cluster_name}},
            "resources": resources or {},
            "module_config": module_config or {},
        }

    def test_identical_configs_no_diffs(self):
        res = {"gateway_api": [
            {"kind": "Gateway", "metadata": {"name": "gw1", "namespace": "ns"}, "spec": {"port": 80}},
        ]}
        a = self._make_config("A", resources=res)
        b = self._make_config("B", resources=res)
        result = diff_configs(a, b)
        assert result["total_diffs"] == 0
        assert "No differences" in result["summary"]

    def test_resource_only_in_a(self):
        a = self._make_config("A", resources={
            "gateway_api": [{"kind": "Gateway", "metadata": {"name": "gw1"}, "spec": {}}],
        })
        b = self._make_config("B", resources={})
        result = diff_configs(a, b)
        assert "Gateway/gw1" in result["resources"]["only_in_a"]
        assert result["total_diffs"] >= 1

    def test_resource_only_in_b(self):
        a = self._make_config("A", resources={})
        b = self._make_config("B", resources={
            "bnk_security": [{"kind": "BNKSecPolicy", "metadata": {"name": "pol1"}, "spec": {}}],
        })
        result = diff_configs(a, b)
        assert "BNKSecPolicy/pol1" in result["resources"]["only_in_b"]

    def test_changed_resource_spec(self):
        a = self._make_config("A", resources={
            "gateway_api": [{"kind": "Gateway", "metadata": {"name": "gw"}, "spec": {"port": 80}}],
        })
        b = self._make_config("B", resources={
            "gateway_api": [{"kind": "Gateway", "metadata": {"name": "gw"}, "spec": {"port": 443}}],
        })
        result = diff_configs(a, b)
        assert len(result["resources"]["changed"]) == 1
        assert result["resources"]["changed"][0]["spec_a"] == {"port": 80}
        assert result["resources"]["changed"][0]["spec_b"] == {"port": 443}

    def test_empty_configs(self):
        result = diff_configs({}, {})
        assert result["total_diffs"] == 0

    def test_module_config_diff(self):
        a = self._make_config("A", module_config={
            "modules/vpc": {"variables": {"cidr": "10.0.0.0/16"}, "status": "applied"},
        })
        b = self._make_config("B", module_config={
            "modules/vpc": {"variables": {"cidr": "10.1.0.0/16"}, "status": "applied"},
        })
        result = diff_configs(a, b)
        assert len(result["modules"]["changed"]) == 1
        assert result["modules"]["changed"][0]["variables_a"] == {"cidr": "10.0.0.0/16"}

    def test_module_only_in_one_side(self):
        a = self._make_config("A", module_config={
            "modules/vpc": {"variables": {}, "status": "applied"},
        })
        b = self._make_config("B", module_config={
            "modules/eks": {"variables": {}, "status": "applied"},
        })
        result = diff_configs(a, b)
        assert "modules/vpc" in result["modules"]["only_in_a"]
        assert "modules/eks" in result["modules"]["only_in_b"]

    def test_different_categories(self):
        a = self._make_config("A", resources={
            "gateway_api": [{"kind": "Gateway", "metadata": {"name": "gw"}, "spec": {}}],
        })
        b = self._make_config("B", resources={
            "bnk_data_plane": [{"kind": "F5SPKVlan", "metadata": {"name": "vlan1"}, "spec": {}}],
        })
        result = diff_configs(a, b)
        assert "Gateway/gw" in result["resources"]["only_in_a"]
        assert "F5SPKVlan/vlan1" in result["resources"]["only_in_b"]


# ---------------------------------------------------------------------------
# export_cluster_config (mocked DB + K8s)
# ---------------------------------------------------------------------------


class TestExportClusterConfig:
    """Integration-level tests for export_cluster_config with mocked externals."""

    @patch("services.config_export_service._fetch_resources", return_value=[])
    @patch("services.kubernetes_service.KubernetesService")
    @patch("kubernetes.client")
    def test_basic_export_structure(self, mock_k8s, mock_k8s_svc, mock_fetch, db, make_k8s_cluster, make_project):
        project = make_project(name="export-proj")
        cluster = make_k8s_cluster(name="test-cluster", project=project)

        mock_k8s_svc.return_value.load_kubeconfig.return_value = MagicMock()
        mock_version = MagicMock()
        mock_version.major = "1"
        mock_version.minor = "28"
        mock_k8s.VersionApi.return_value.get_code.return_value = mock_version
        mock_k8s.CustomObjectsApi.return_value = MagicMock()

        result = export_cluster_config(cluster.id, db)

        assert "bnk_forge_export" in result
        assert result["bnk_forge_export"]["version"] == "1.0"
        assert result["bnk_forge_export"]["cluster"]["name"] == "test-cluster"
        assert "resources" in result
        assert "module_config" in result

    def test_cluster_not_found_raises(self, db):
        with pytest.raises(ValueError, match="not found"):
            export_cluster_config(99999, db)

    @patch("services.config_export_service._fetch_resources", return_value=[])
    @patch("services.kubernetes_service.KubernetesService")
    @patch("kubernetes.client")
    def test_export_includes_project_modules(self, mock_k8s, mock_k8s_svc, mock_fetch, db, make_k8s_cluster, make_project, make_project_module, make_module_library):
        project = make_project(name="mod-proj")
        cluster = make_k8s_cluster(name="mod-cluster", project=project)
        lib = make_module_library(name="vpc", path="infra/vpc")
        make_project_module(
            project=project, library_module=lib,
            path_in_project="modules/vpc",
            status="applied",
            variables={"cidr": "10.0.0.0/16"},
        )

        mock_k8s_svc.return_value.load_kubeconfig.return_value = MagicMock()
        mock_version = MagicMock()
        mock_version.major = "1"
        mock_version.minor = "28"
        mock_k8s.VersionApi.return_value.get_code.return_value = mock_version
        mock_k8s.CustomObjectsApi.return_value = MagicMock()

        result = export_cluster_config(cluster.id, db)

        assert "modules/vpc" in result["module_config"]
        assert result["module_config"]["modules/vpc"]["variables"] == {"cidr": "10.0.0.0/16"}


# ---------------------------------------------------------------------------
# apply_resources — extracted from the /bnk/import route (D-034 P0 seam)
# ---------------------------------------------------------------------------


class TestApplyResources:
    """Shared server-side-apply write path — used by both /bnk/import and use-case apply."""

    def _gateway(self, name="gw1", namespace="ns1"):
        return {
            "kind": "Gateway",
            "apiVersion": "gateway.networking.k8s.io/v1",
            "metadata": {"name": name, "namespace": namespace},
            "spec": {},
        }

    def test_applies_namespaced_resource(self, db):
        custom_api = MagicMock()
        resources = {"gateway_api": [self._gateway()]}

        results = apply_resources(db, 1, custom_api, resources)

        assert results["applied"] == [{"kind": "Gateway", "name": "gw1", "namespace": "ns1"}]
        assert results["failed"] == []
        assert results["skipped"] == []
        custom_api.patch_namespaced_custom_object.assert_called_once_with(
            group="gateway.networking.k8s.io", version="v1", namespace="ns1",
            plural="gateways", name="gw1", body=self._gateway(),
            field_manager="bnk-forge", force=True,
        )

    def test_applies_cluster_scoped_resource(self, db):
        custom_api = MagicMock()
        resource = self._gateway(namespace="")
        resource["metadata"].pop("namespace")
        resources = {"gateway_api": [resource]}

        results = apply_resources(db, 1, custom_api, resources)

        assert results["applied"] == [{"kind": "Gateway", "name": "gw1", "namespace": ""}]
        custom_api.patch_cluster_custom_object.assert_called_once()

    def test_core_api_resource_skipped(self, db):
        custom_api = MagicMock()
        resources = {"core": [{"kind": "ConfigMap", "apiVersion": "v1", "metadata": {"name": "cm1"}}]}

        results = apply_resources(db, 1, custom_api, resources)

        assert results["skipped"] == [{
            "kind": "ConfigMap", "name": "cm1", "namespace": "",
            "reason": "Core API import not supported",
        }]

    def test_404_apply_error_is_skipped(self, db):
        custom_api = MagicMock()
        custom_api.patch_namespaced_custom_object.side_effect = ApiException(status=404, reason="Not Found")
        resources = {"gateway_api": [self._gateway()]}

        results = apply_resources(db, 1, custom_api, resources)

        assert len(results["skipped"]) == 1
        assert results["skipped"][0]["reason"] == "CRD not installed: Gateway"
        assert results["applied"] == []

    def test_non_404_apply_error_is_failed(self, db):
        custom_api = MagicMock()
        custom_api.patch_namespaced_custom_object.side_effect = ApiException(status=500, reason="server error")
        resources = {"gateway_api": [self._gateway()]}

        results = apply_resources(db, 1, custom_api, resources)

        assert len(results["failed"]) == 1
        assert results["failed"][0]["error"] == "server error"
