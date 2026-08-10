"""
Backward-compatibility shim for routes.kubernetes.

The monolithic kubernetes.py (2,926 lines) has been split into:
  routes/k8s/clusters.py   — Cluster CRUD, test, refresh, scan, adaptive modules
  routes/k8s/resources.py  — Generic K8s resource CRUD, pod/node ops, metrics
  routes/k8s/f5bnk.py      — F5 BNK gateways, topology, health
  routes/k8s/tunnels.py    — SSH tunnel management

This shim re-exports the routers for any code that imports from routes.kubernetes.
"""

# Re-export shared models and helpers for any external consumers
from routes.k8s._shared import (
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
    run_kubectl,
    serialize_cluster,
)
from routes.k8s.clusters import router as clusters_router
from routes.k8s.f5bnk import router as f5bnk_router
from routes.k8s.resources import router as resources_router
from routes.k8s.tunnels import router as tunnels_router

__all__ = [
    "clusters_router",
    "resources_router",
    "f5bnk_router",
    "tunnels_router",
    "serialize_cluster",
    "run_kubectl",
    "ClusterCreateRequest",
    "ClusterUpdateRequest",
    "AdaptiveModuleRequest",
    "ResourceCreateRequest",
    "ResourceUpdateRequest",
    "ResourceDeleteRequest",
    "ScaleDeploymentRequest",
    "ResourcePatchRequest",
    "LabelResourceRequest",
    "AnnotateResourceRequest",
]
