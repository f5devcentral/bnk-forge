"""Component tests for K8s drift detection service helpers and dispatch."""

from unittest.mock import MagicMock, patch

from services.k8s_drift_service import (
    _diff_dicts,
    _normalize_for_comparison,
    check_helm_drift,
    check_k8s_module_drift,
    check_manifest_drift,
)


class TestNormalizeForComparison:
    def test_strips_status_from_both(self):
        desired = {"metadata": {"name": "x", "namespace": "default"}, "status": {"ready": True}}
        actual = {"metadata": {"name": "x", "namespace": "default"}, "status": {"phase": "Running"}}
        d, a = _normalize_for_comparison(desired, actual)
        assert "status" not in d
        assert "status" not in a

    def test_keeps_name_and_namespace(self):
        desired = {"metadata": {"name": "svc", "namespace": "ns1", "uid": "abc"}}
        actual = {"metadata": {"name": "svc", "namespace": "ns1", "uid": "def", "resourceVersion": "123"}}
        d, a = _normalize_for_comparison(desired, actual)
        assert d["metadata"] == {"name": "svc", "namespace": "ns1"}
        assert a["metadata"] == {"name": "svc", "namespace": "ns1"}

    def test_compares_only_desired_labels(self):
        desired = {"metadata": {"name": "x", "namespace": "ns", "labels": {"app": "web"}}}
        actual = {"metadata": {"name": "x", "namespace": "ns", "labels": {"app": "web", "extra": "label"}}}
        d, a = _normalize_for_comparison(desired, actual)
        assert d["metadata"]["labels"] == {"app": "web"}
        assert a["metadata"]["labels"] == {"app": "web"}


class TestDiffDicts:
    def test_identical_dicts(self):
        assert _diff_dicts({"a": 1}, {"a": 1}) == []

    def test_changed_value(self):
        diffs = _diff_dicts({"replicas": 3}, {"replicas": 2})
        assert len(diffs) == 1
        assert diffs[0]["type"] == "changed"
        assert diffs[0]["path"] == "replicas"

    def test_missing_key_in_actual(self):
        diffs = _diff_dicts({"a": 1, "b": 2}, {"a": 1})
        assert len(diffs) == 1
        assert diffs[0]["type"] == "missing"
        assert diffs[0]["path"] == "b"

    def test_extra_key_in_actual_ignored(self):
        diffs = _diff_dicts({"a": 1}, {"a": 1, "b": 2})
        assert diffs == []

    def test_nested_diff(self):
        diffs = _diff_dicts({"spec": {"replicas": 3}}, {"spec": {"replicas": 1}})
        assert len(diffs) == 1
        assert diffs[0]["path"] == "spec.replicas"

    def test_list_diff(self):
        diffs = _diff_dicts({"ports": [80, 443]}, {"ports": [80, 8080]})
        assert len(diffs) == 1
        assert diffs[0]["path"] == "ports[1]"

    def test_type_coercion_string_vs_int(self):
        diffs = _diff_dicts({"port": 80}, {"port": "80"})
        assert diffs == []


class TestCatalogDriftUnavailable:
    def test_manifest_returns_not_available_without_lib_module(self):
        result = check_manifest_drift("/tmp/kube", "k8s/test", {})
        assert result["drift_detected"] is False
        assert "not available" in result["summary"]
        assert "no catalog metadata" in result["summary"]

    def test_helm_returns_not_available_without_lib_module(self):
        result = check_helm_drift("/tmp/kube", "helm/test", {})
        assert result["drift_detected"] is False
        assert "not available" in result["summary"]
        assert "no catalog metadata" in result["summary"]


class TestCheckK8sModuleDrift:
    def test_module_without_catalog_metadata(self):
        result = check_k8s_module_drift("/tmp/kube", "nope", {})
        assert result["drift_detected"] is False
        assert "no catalog metadata" in result["summary"]

    @patch("services.k8s_drift_service.check_helm_drift")
    def test_dispatches_to_helm_by_deploy_model(self, mock_helm):
        lib_module = MagicMock()
        lib_module.deploy_model = "helm"
        mock_helm.return_value = {"drift_detected": False}

        check_k8s_module_drift("/tmp/kube", "helm/x", {"a": 1}, lib_module=lib_module)
        mock_helm.assert_called_once_with("/tmp/kube", "helm/x", {"a": 1}, lib_module=lib_module)

    @patch("services.k8s_drift_service.check_manifest_drift")
    def test_dispatches_to_manifest_by_default(self, mock_manifest):
        lib_module = MagicMock()
        lib_module.deploy_model = "manifest"
        mock_manifest.return_value = {"drift_detected": True}

        check_k8s_module_drift("/tmp/kube", "k8s/x", {}, lib_module=lib_module)
        mock_manifest.assert_called_once_with("/tmp/kube", "k8s/x", {}, lib_module=lib_module)

    @patch("services.k8s_drift_service.check_manifest_drift")
    def test_dispatches_to_manifest_when_deploy_model_missing(self, mock_manifest):
        lib_module = MagicMock()
        lib_module.deploy_model = None
        mock_manifest.return_value = {"drift_detected": False}

        check_k8s_module_drift("/tmp/kube", "k8s/x", {}, lib_module=lib_module)
        mock_manifest.assert_called_once_with("/tmp/kube", "k8s/x", {}, lib_module=lib_module)
