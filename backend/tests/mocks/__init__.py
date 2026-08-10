"""
Shared mocks for BNK-Forge backend tests.

Provides reusable mock objects for external services that should not
be called during unit/integration tests:
- AWS (boto3, SSO, STS)
- Subprocess (tofu, helm, kubectl CLI calls)
- Kubernetes (kr8s, kubernetes client)
- Redis (cache, Celery broker)
"""

from tests.mocks.aws_mock import (
    mock_boto3_session,
    mock_sso_oidc_client,
    mock_sts_client,
)
from tests.mocks.kubernetes_mock import (
    MockKubernetesService,
    mock_kr8s_api,
    mock_kubernetes_client,
)
from tests.mocks.redis_mock import MockCacheService, MockRedis
from tests.mocks.subprocess_mock import (
    mock_helm_subprocess,
    mock_kubectl_subprocess,
    mock_tofu_subprocess,
)

__all__ = [
    # AWS
    "mock_boto3_session",
    "mock_sso_oidc_client",
    "mock_sts_client",
    # Subprocess
    "mock_tofu_subprocess",
    "mock_helm_subprocess",
    "mock_kubectl_subprocess",
    # Kubernetes
    "mock_kubernetes_client",
    "mock_kr8s_api",
    "MockKubernetesService",
    # Redis
    "MockRedis",
    "MockCacheService",
]
