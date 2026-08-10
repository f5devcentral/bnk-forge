"""
Integration tests for routes/kubernetes.py — backward-compatibility shim.

Verifies the shim module re-exports routers and shared models correctly.
The actual route tests are in test_routes_k8s_clusters.py, test_routes_k8s_resources.py, etc.
This file tests the import shim itself and any routes unique to kubernetes.py.
"""

import pytest


class TestKubernetesShim:
    """Verify routes.kubernetes re-exports all expected symbols."""

    def test_imports_cluster_router(self):
        """clusters_router is re-exported."""
        from routes.kubernetes import clusters_router
        assert clusters_router is not None

    def test_imports_resources_router(self):
        """resources_router is re-exported."""
        from routes.kubernetes import resources_router
        assert resources_router is not None

    def test_imports_f5bnk_router(self):
        """f5bnk_router is re-exported."""
        from routes.kubernetes import f5bnk_router
        assert f5bnk_router is not None

    def test_imports_tunnels_router(self):
        """tunnels_router is re-exported."""
        from routes.kubernetes import tunnels_router
        assert tunnels_router is not None


class TestKubernetesSharedModels:
    """Verify shared Pydantic models are re-exported."""

    def test_imports_serialize_cluster(self):
        """serialize_cluster helper is importable."""
        from routes.kubernetes import serialize_cluster
        assert callable(serialize_cluster)

    def test_imports_run_kubectl(self):
        """run_kubectl helper is importable."""
        from routes.kubernetes import run_kubectl
        assert callable(run_kubectl)

    def test_imports_request_models(self):
        """All request/schema models are importable from the shim."""
        from routes.kubernetes import (
            AdaptiveModuleRequest,
            AnnotateResourceRequest,
            ClusterCreateRequest,
            ClusterUpdateRequest,
            LabelResourceRequest,
            ResourceCreateRequest,
            ResourceDeleteRequest,
            ResourcePatchRequest,
            ResourceUpdateRequest,
            ScaleDeploymentRequest,
        )
        # Just verify they exist as classes
        assert ClusterCreateRequest is not None
        assert ResourceCreateRequest is not None
