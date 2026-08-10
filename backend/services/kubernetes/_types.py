"""
Type stubs for Kubernetes service mixins.

Provides a Protocol so mypy knows that mixins will be composed with
KubernetesServiceBase (which provides get_cluster / load_kubeconfig / db).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from kubernetes.client import ApiClient
    from sqlalchemy.orm import Session

    from models import KubernetesCluster


class K8sServiceProtocol(Protocol):
    """Methods that every K8s mixin may call on `self`."""

    db: Session

    def get_cluster(self, cluster_id: int) -> KubernetesCluster: ...
    def load_kubeconfig(self, cluster: KubernetesCluster) -> ApiClient: ...
