"""
Cloud-agnostic Kubernetes service for BNK-Forge.
Works with EKS, AKS, GKE, on-prem, and any Kubernetes distribution.

Split into focused modules for maintainability:
  _base.py       — cluster access, kubeconfig, connection testing
  _resources.py  — generic CRUD (get, create, update, delete)
  _describe.py   — describe resource, events
  _pods.py       — pod logs, restart, container listing
  _metrics.py    — pod/node metrics (kubectl top)
  _rollouts.py   — deployment rollout history, status, undo, restart
  _operations.py — patch, label, annotate, scale, cordon, uncordon, drain
"""

from services.kubernetes._base import KubernetesServiceBase
from services.kubernetes._describe import DescribeMixin
from services.kubernetes._metrics import MetricsMixin
from services.kubernetes._operations import OperationsMixin
from services.kubernetes._pods import PodsMixin
from services.kubernetes._resources import ResourcesMixin
from services.kubernetes._rollouts import RolloutsMixin


class KubernetesService(
    KubernetesServiceBase,
    ResourcesMixin,
    DescribeMixin,
    PodsMixin,
    MetricsMixin,
    RolloutsMixin,
    OperationsMixin,
):
    """
    Cloud-agnostic Kubernetes service.
    Uses kubernetes-client library instead of kubectl subprocess calls.

    Assembled from mixins — each mixin lives in its own file for maintainability.
    """
    pass


__all__ = ["KubernetesService"]
