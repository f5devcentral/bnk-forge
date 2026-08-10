"""
Component tests for Kubernetes Finalizer Cleanup Service.

Tests cover: cleanup_namespace_finalizers, force_delete_namespace,
get_stuck_namespaces, cleanup_finalizers_for_module, and edge cases.
All K8s API calls are mocked.
"""

from unittest.mock import MagicMock, call, patch

import pytest
from kubernetes.client.rest import ApiException

from services.finalizer_cleanup_service import (
    KNOWN_SAFE_FINALIZERS,
    FinalizerCleanupService,
    cleanup_finalizers_for_module,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _mock_namespace(name="test-ns", phase="Terminating", finalizers=None):
    """Build a mock Namespace object."""
    ns = MagicMock()
    ns.metadata.name = name
    ns.metadata.deletion_timestamp = None
    ns.status.phase = phase
    ns.status.conditions = []
    ns.spec = MagicMock()
    ns.spec.finalizers = finalizers or []
    return ns


def _api_exception(status=404, reason="Not Found"):
    """Create a Kubernetes ApiException."""
    exc = ApiException(status=status, reason=reason)
    return exc


# ── cleanup_namespace_finalizers ─────────────────────────────────────


class TestCleanupNamespaceFinalizers:
    @patch("services.finalizer_cleanup_service.KubernetesService")
    @patch("services.finalizer_cleanup_service.client")
    def test_namespace_not_terminating_returns_error(self, mock_client, mock_k8s_cls, db):
        mock_k8s = MagicMock()
        mock_k8s_cls.return_value = mock_k8s

        v1 = MagicMock()
        mock_client.CoreV1Api.return_value = v1
        v1.read_namespace.return_value = _mock_namespace(phase="Active")

        svc = FinalizerCleanupService(db)
        result = svc.cleanup_namespace_finalizers(cluster_id=1, namespace="test-ns")

        assert result["success"] is False
        assert any("not Terminating" in e for e in result["errors"])

    @patch("services.finalizer_cleanup_service.KubernetesService")
    @patch("services.finalizer_cleanup_service.client")
    def test_namespace_already_deleted(self, mock_client, mock_k8s_cls, db):
        mock_k8s = MagicMock()
        mock_k8s_cls.return_value = mock_k8s

        v1 = MagicMock()
        mock_client.CoreV1Api.return_value = v1
        v1.read_namespace.side_effect = _api_exception(404)

        svc = FinalizerCleanupService(db)
        result = svc.cleanup_namespace_finalizers(cluster_id=1, namespace="gone-ns")

        assert result["success"] is True
        assert result["namespace_deleted"] is True

    @patch("services.finalizer_cleanup_service.KubernetesService")
    @patch("services.finalizer_cleanup_service.client")
    def test_cleans_f5_crds_with_safe_finalizers(self, mock_client, mock_k8s_cls, db):
        mock_k8s = MagicMock()
        mock_k8s_cls.return_value = mock_k8s

        v1 = MagicMock()
        mock_client.CoreV1Api.return_value = v1
        v1.read_namespace.return_value = _mock_namespace(phase="Terminating")

        custom_api = MagicMock()
        mock_client.CustomObjectsApi.return_value = custom_api

        # Simulate a CRD with a known safe finalizer
        custom_api.list_namespaced_custom_object.return_value = {
            "items": [{
                "metadata": {
                    "name": "my-crd",
                    "finalizers": ["k8s.f5net.com/uninstall"],
                }
            }]
        }

        # After cleanup, namespace gets deleted
        v1.read_namespace.side_effect = [
            _mock_namespace(phase="Terminating"),  # initial check (already returned above, but for _wait)
            _api_exception(404),  # namespace deleted
        ]

        svc = FinalizerCleanupService(db)
        result = svc.cleanup_namespace_finalizers(cluster_id=1, namespace="f5-bnk")

        assert result["success"] is True
        assert len(result["resources_cleaned"]) >= 1

    @patch("services.finalizer_cleanup_service.KubernetesService")
    @patch("services.finalizer_cleanup_service.client")
    def test_generic_exception_captured(self, mock_client, mock_k8s_cls, db):
        mock_k8s = MagicMock()
        mock_k8s_cls.return_value = mock_k8s
        mock_k8s.get_cluster.side_effect = Exception("cluster not found")

        svc = FinalizerCleanupService(db)
        result = svc.cleanup_namespace_finalizers(cluster_id=999, namespace="ns")

        assert result["success"] is False
        assert any("cluster not found" in e for e in result["errors"])


# ── force_delete_namespace ───────────────────────────────────────────


class TestForceDeleteNamespace:
    @patch.object(FinalizerCleanupService, "cleanup_namespace_finalizers")
    @patch.object(FinalizerCleanupService, "_wait_for_namespace_deletion")
    @patch("services.finalizer_cleanup_service.KubernetesService")
    @patch("services.finalizer_cleanup_service.client")
    def test_force_delete_after_cleanup_succeeds(self, mock_client, mock_k8s_cls, mock_wait, mock_cleanup, db):
        mock_k8s = MagicMock()
        mock_k8s_cls.return_value = mock_k8s

        # First cleanup attempt doesn't delete the namespace
        mock_cleanup.return_value = {
            "success": True,
            "namespace_deleted": False,
            "resources_cleaned": [],
            "errors": [],
        }

        v1 = MagicMock()
        mock_client.CoreV1Api.return_value = v1
        v1.read_namespace.return_value = _mock_namespace(
            phase="Terminating", finalizers=["kubernetes/finalizer"]
        )
        mock_wait.return_value = True

        svc = FinalizerCleanupService(db)
        result = svc.force_delete_namespace(cluster_id=1, namespace="stuck-ns")

        assert result["success"] is True
        v1.patch_namespace.assert_called_once()

    @patch.object(FinalizerCleanupService, "cleanup_namespace_finalizers")
    @patch("services.finalizer_cleanup_service.KubernetesService")
    @patch("services.finalizer_cleanup_service.client")
    def test_force_delete_when_cleanup_already_deleted(self, mock_client, mock_k8s_cls, mock_cleanup, db):
        mock_k8s = MagicMock()
        mock_k8s_cls.return_value = mock_k8s

        mock_cleanup.return_value = {
            "success": True,
            "namespace_deleted": True,
            "resources_cleaned": [],
            "errors": [],
        }

        svc = FinalizerCleanupService(db)
        result = svc.force_delete_namespace(cluster_id=1, namespace="ns")

        assert result["success"] is True


# ── get_stuck_namespaces ─────────────────────────────────────────────


class TestGetStuckNamespaces:
    @patch("services.finalizer_cleanup_service.KubernetesService")
    @patch("services.finalizer_cleanup_service.client")
    def test_returns_terminating_namespaces(self, mock_client, mock_k8s_cls, db):
        mock_k8s = MagicMock()
        mock_k8s_cls.return_value = mock_k8s

        v1 = MagicMock()
        mock_client.CoreV1Api.return_value = v1

        active_ns = _mock_namespace("active-ns", "Active")
        stuck_ns = _mock_namespace("stuck-ns", "Terminating", finalizers=["k8s.f5.com/finalizer"])
        v1.list_namespace.return_value.items = [active_ns, stuck_ns]

        svc = FinalizerCleanupService(db)
        result = svc.get_stuck_namespaces(cluster_id=1)

        assert len(result) == 1
        assert result[0]["name"] == "stuck-ns"
        assert result[0]["phase"] == "Terminating"

    @patch("services.finalizer_cleanup_service.KubernetesService")
    @patch("services.finalizer_cleanup_service.client")
    def test_returns_empty_when_none_stuck(self, mock_client, mock_k8s_cls, db):
        mock_k8s = MagicMock()
        mock_k8s_cls.return_value = mock_k8s

        v1 = MagicMock()
        mock_client.CoreV1Api.return_value = v1
        v1.list_namespace.return_value.items = [_mock_namespace("ns1", "Active")]

        svc = FinalizerCleanupService(db)
        result = svc.get_stuck_namespaces(cluster_id=1)
        assert result == []


# ── cleanup_finalizers_for_module (convenience function) ─────────────


class TestCleanupFinalizersForModule:
    @patch("services.finalizer_cleanup_service.FinalizerCleanupService")
    def test_iterates_default_namespaces(self, mock_cls, db):
        mock_svc = MagicMock()
        mock_cls.return_value = mock_svc
        mock_svc.cleanup_namespace_finalizers.return_value = {
            "success": True,
            "resources_cleaned": [],
            "namespace_deleted": False,
            "errors": [],
        }

        result = cleanup_finalizers_for_module(db, cluster_id=1, module_path="bnk/gateway")

        assert result["module_path"] == "bnk/gateway"
        # Should call cleanup for each default namespace
        assert mock_svc.cleanup_namespace_finalizers.call_count == 4  # f5-bnk, f5-utils, bnk-gw, observability

    @patch("services.finalizer_cleanup_service.FinalizerCleanupService")
    def test_uses_custom_namespaces(self, mock_cls, db):
        mock_svc = MagicMock()
        mock_cls.return_value = mock_svc
        mock_svc.cleanup_namespace_finalizers.return_value = {
            "success": True,
            "resources_cleaned": [],
            "namespace_deleted": False,
            "errors": [],
        }

        result = cleanup_finalizers_for_module(
            db, cluster_id=1, module_path="custom/mod", namespaces=["ns1", "ns2"]
        )
        assert mock_svc.cleanup_namespace_finalizers.call_count == 2
