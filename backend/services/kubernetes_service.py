"""
Backward-compatibility shim.

KubernetesService has been split into services/kubernetes/ package.
This file re-exports the class so existing imports keep working:
    from services.kubernetes_service import KubernetesService
"""

from services.kubernetes import KubernetesService  # noqa: F401

__all__ = ["KubernetesService"]
