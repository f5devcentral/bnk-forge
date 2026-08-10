"""
Tests for K8s-engine drift detection service.

Tests the manifest comparison logic without requiring a live cluster.
"""
import pytest

from services.k8s_drift_service import (
    _diff_dicts,
    _normalize_for_comparison,
)

# ===========================================================================
# _diff_dicts tests
# ===========================================================================

class TestDiffDicts:
    """Test the recursive dict diffing logic."""

    def test_identical_dicts(self):
        d = {"replicas": 3, "image": "nginx:latest"}
        assert _diff_dicts(d, d) == []

    def test_changed_value(self):
        desired = {"replicas": 3}
        actual = {"replicas": 2}
        diffs = _diff_dicts(desired, actual)
        assert len(diffs) == 1
        assert diffs[0]["path"] == "replicas"
        assert diffs[0]["type"] == "changed"
        assert diffs[0]["desired"] == 3
        assert diffs[0]["actual"] == 2

    def test_missing_key_in_actual(self):
        desired = {"replicas": 3, "image": "nginx"}
        actual = {"replicas": 3}
        diffs = _diff_dicts(desired, actual)
        assert len(diffs) == 1
        assert diffs[0]["path"] == "image"
        assert diffs[0]["type"] == "missing"

    def test_extra_key_in_actual_ignored(self):
        """Extra keys in actual are NOT drift — K8s adds defaults."""
        desired = {"replicas": 3}
        actual = {"replicas": 3, "revisionHistoryLimit": 10}
        assert _diff_dicts(desired, actual) == []

    def test_nested_diff(self):
        desired = {"spec": {"containers": [{"image": "nginx:1.25"}]}}
        actual = {"spec": {"containers": [{"image": "nginx:1.24"}]}}
        diffs = _diff_dicts(desired, actual)
        assert len(diffs) == 1
        assert "image" in diffs[0]["path"]
        assert diffs[0]["type"] == "changed"

    def test_nested_extra_ignored(self):
        desired = {"spec": {"replicas": 3}}
        actual = {"spec": {"replicas": 3, "selector": {"matchLabels": {"app": "test"}}}}
        assert _diff_dicts(desired, actual) == []

    def test_list_same_length(self):
        desired = [1, 2, 3]
        actual = [1, 2, 3]
        assert _diff_dicts(desired, actual) == []

    def test_list_different_values(self):
        desired = [1, 2, 3]
        actual = [1, 5, 3]
        diffs = _diff_dicts(desired, actual)
        assert len(diffs) == 1
        assert diffs[0]["desired"] == 2
        assert diffs[0]["actual"] == 5

    def test_list_shorter_actual(self):
        desired = [1, 2, 3]
        actual = [1, 2]
        diffs = _diff_dicts(desired, actual)
        assert len(diffs) == 1
        assert diffs[0]["type"] == "missing"

    def test_list_longer_actual_ignored(self):
        """Extra list elements in actual are NOT drift."""
        desired = [1, 2]
        actual = [1, 2, 3]
        assert _diff_dicts(desired, actual) == []

    def test_type_coercion(self):
        """K8s often returns strings for numeric values."""
        desired = {"port": 80}
        actual = {"port": "80"}
        assert _diff_dicts(desired, actual) == []

    def test_bool_vs_string(self):
        desired = {"enabled": True}
        actual = {"enabled": "True"}
        assert _diff_dicts(desired, actual) == []

    def test_none_vs_none(self):
        desired = {"value": None}
        actual = {"value": None}
        assert _diff_dicts(desired, actual) == []

    def test_empty_dicts(self):
        assert _diff_dicts({}, {}) == []

    def test_deeply_nested(self):
        desired = {"a": {"b": {"c": {"d": 1}}}}
        actual = {"a": {"b": {"c": {"d": 2}}}}
        diffs = _diff_dicts(desired, actual)
        assert len(diffs) == 1
        assert diffs[0]["path"] == "a.b.c.d"


# ===========================================================================
# _normalize_for_comparison tests
# ===========================================================================

class TestNormalizeForComparison:
    """Test manifest normalization logic."""

    def test_strips_status(self):
        desired = {"kind": "Deployment", "metadata": {"name": "test"}, "spec": {}, "status": {"ready": True}}
        actual = {"kind": "Deployment", "metadata": {"name": "test", "uid": "abc"}, "spec": {}, "status": {"ready": False}}
        d, a = _normalize_for_comparison(desired, actual)
        assert "status" not in d
        assert "status" not in a

    def test_strips_k8s_metadata(self):
        desired = {"metadata": {"name": "test", "namespace": "default"}, "spec": {}}
        actual = {
            "metadata": {
                "name": "test",
                "namespace": "default",
                "uid": "abc-123",
                "resourceVersion": "12345",
                "creationTimestamp": "2026-01-01T00:00:00Z",
                "managedFields": [{"manager": "kubectl"}],
            },
            "spec": {},
        }
        d, a = _normalize_for_comparison(desired, actual)
        # Normalized metadata should only have name and namespace
        assert d["metadata"] == {"name": "test", "namespace": "default"}
        assert a["metadata"] == {"name": "test", "namespace": "default"}

    def test_preserves_matching_labels(self):
        desired = {"metadata": {"name": "test", "labels": {"app": "nginx"}}, "spec": {}}
        actual = {
            "metadata": {
                "name": "test",
                "labels": {"app": "nginx", "kubernetes.io/managed-by": "helm"},
            },
            "spec": {},
        }
        d, a = _normalize_for_comparison(desired, actual)
        # Should only compare labels that appear in desired
        assert d["metadata"]["labels"] == {"app": "nginx"}
        assert a["metadata"]["labels"] == {"app": "nginx"}

    def test_labels_diff_detected(self):
        desired = {"metadata": {"name": "test", "labels": {"app": "nginx"}}, "spec": {}}
        actual = {"metadata": {"name": "test", "labels": {"app": "apache"}}, "spec": {}}
        d, a = _normalize_for_comparison(desired, actual)
        assert d["metadata"]["labels"]["app"] == "nginx"
        assert a["metadata"]["labels"]["app"] == "apache"

    def test_preserves_spec(self):
        desired = {"metadata": {"name": "test"}, "spec": {"replicas": 3}}
        actual = {"metadata": {"name": "test"}, "spec": {"replicas": 2, "selector": {}}}
        d, a = _normalize_for_comparison(desired, actual)
        assert d["spec"] == {"replicas": 3}
        assert a["spec"] == {"replicas": 2, "selector": {}}


# ===========================================================================
# Integration: full comparison pipeline
# ===========================================================================

class TestFullComparison:
    """Test normalize + diff together on realistic manifests."""

    def test_no_drift_deployment(self):
        desired = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "nginx", "namespace": "demo-apps"},
            "spec": {
                "replicas": 2,
                "selector": {"matchLabels": {"app": "nginx"}},
                "template": {
                    "metadata": {"labels": {"app": "nginx"}},
                    "spec": {
                        "containers": [{"name": "nginx", "image": "nginx:1.25", "ports": [{"containerPort": 80}]}]
                    },
                },
            },
        }
        # Actual has extra K8s-added fields
        actual = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": "nginx",
                "namespace": "demo-apps",
                "uid": "abc",
                "resourceVersion": "999",
                "generation": 3,
                "creationTimestamp": "2026-01-01",
                "managedFields": [],
            },
            "spec": {
                "replicas": 2,
                "selector": {"matchLabels": {"app": "nginx"}},
                "template": {
                    "metadata": {"labels": {"app": "nginx"}},
                    "spec": {
                        "containers": [
                            {
                                "name": "nginx",
                                "image": "nginx:1.25",
                                "ports": [{"containerPort": 80, "protocol": "TCP"}],
                                "terminationMessagePath": "/dev/termination-log",
                                "terminationMessagePolicy": "File",
                                "imagePullPolicy": "IfNotPresent",
                            }
                        ],
                        "restartPolicy": "Always",
                        "terminationGracePeriodSeconds": 30,
                        "dnsPolicy": "ClusterFirst",
                        "schedulerName": "default-scheduler",
                    },
                },
                "revisionHistoryLimit": 10,
                "progressDeadlineSeconds": 600,
                "strategy": {"type": "RollingUpdate", "rollingUpdate": {"maxUnavailable": "25%", "maxSurge": "25%"}},
            },
            "status": {"replicas": 2, "readyReplicas": 2, "availableReplicas": 2},
        }

        d, a = _normalize_for_comparison(desired, actual)
        diffs = _diff_dicts(d.get("spec", {}), a.get("spec", {}), "spec")
        assert len(diffs) == 0, f"Unexpected diffs: {diffs}"

    def test_drift_detected_replicas(self):
        desired = {
            "metadata": {"name": "nginx", "namespace": "default"},
            "spec": {"replicas": 3},
        }
        actual = {
            "metadata": {"name": "nginx", "namespace": "default", "uid": "abc"},
            "spec": {"replicas": 1},
            "status": {},
        }
        d, a = _normalize_for_comparison(desired, actual)
        diffs = _diff_dicts(d.get("spec", {}), a.get("spec", {}), "spec")
        assert len(diffs) == 1
        assert diffs[0]["path"] == "spec.replicas"
        assert diffs[0]["desired"] == 3
        assert diffs[0]["actual"] == 1

    def test_drift_detected_image(self):
        desired = {
            "metadata": {"name": "test"},
            "spec": {"containers": [{"name": "app", "image": "myapp:v2"}]},
        }
        actual = {
            "metadata": {"name": "test", "uid": "x"},
            "spec": {"containers": [{"name": "app", "image": "myapp:v1"}]},
            "status": {},
        }
        d, a = _normalize_for_comparison(desired, actual)
        diffs = _diff_dicts(d.get("spec", {}), a.get("spec", {}), "spec")
        assert len(diffs) == 1
        assert "image" in diffs[0]["path"]

    def test_configmap_data_drift(self):
        """ConfigMap data should be compared."""
        desired = {"metadata": {"name": "cfg"}, "data": {"key1": "valueA"}}
        actual = {"metadata": {"name": "cfg", "uid": "x"}, "data": {"key1": "valueB"}}
        # For ConfigMaps, we compare data separately (done in check_manifest_drift)
        diffs = _diff_dicts(desired.get("data", {}), actual.get("data", {}), "data")
        assert len(diffs) == 1
        assert diffs[0]["path"] == "data.key1"

    def test_namespace_manifest_no_spec(self):
        """Namespace manifests have no spec — should not drift."""
        desired = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "demo-apps"}}
        actual = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": "demo-apps", "uid": "abc", "resourceVersion": "1"},
            "spec": {"finalizers": ["kubernetes"]},
            "status": {"phase": "Active"},
        }
        d, a = _normalize_for_comparison(desired, actual)
        diffs = _diff_dicts(d.get("spec", {}), a.get("spec", {}), "spec")
        assert len(diffs) == 0
