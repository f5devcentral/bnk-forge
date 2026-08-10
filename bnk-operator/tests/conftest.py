"""
Shared fixtures for bnk-operator tests.

All tests mock external dependencies (kr8s, helm CLI, websockets, aiohttp)
so they run without a real K8s cluster.
"""

import asyncio
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure the operator package is importable
operator_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if operator_root not in sys.path:
    sys.path.insert(0, operator_root)


# ---------------------------------------------------------------------------
# Mock K8s resource helpers
# ---------------------------------------------------------------------------


class FakeK8sResource:
    """Mimics a kr8s resource object with .metadata, .status, .spec, etc."""

    def __init__(self, kind="Pod", name="test", namespace="default", labels=None,
                 status=None, spec=None):
        self.kind = kind
        self.metadata = {
            "name": name,
            "namespace": namespace,
            "labels": labels or {},
            "creationTimestamp": "2026-01-01T00:00:00Z",
        }
        self.status = status or {}
        self.spec = spec or {}

    async def async_delete(self):
        pass

    async def async_wait(self, condition, timeout=300):
        pass

    async def async_logs(self, container=None, tail_lines=100):
        return "line1\nline2\nline3\n"


def make_node(name="node-1", ready=True):
    """Create a fake K8s node."""
    return FakeK8sResource(
        kind="Node",
        name=name,
        status={
            "conditions": [
                {"type": "Ready", "status": "True" if ready else "False"},
            ]
        },
    )


def make_pod(name="pod-1", ready=True, containers=3):
    """Create a fake K8s pod."""
    return FakeK8sResource(
        kind="Pod",
        name=name,
        namespace="f5-bnk",
        labels={"app": "f5-tmm"},
        status={
            "conditions": [
                {"type": "Ready", "status": "True" if ready else "False"},
            ],
            "containerStatuses": [
                {"name": f"c{i}", "ready": ready} for i in range(containers)
            ],
        },
    )


def make_deployment(name="flo", ready=True, replicas=1):
    """Create a fake K8s deployment."""
    return FakeK8sResource(
        kind="Deployment",
        name=name,
        namespace="f5-bnk",
        labels={"app.kubernetes.io/name": "f5-lifecycle-operator",
                "app.kubernetes.io/version": "1.2.3"},
        status={
            "replicas": replicas,
            "availableReplicas": replicas if ready else 0,
        },
    )


def make_crd(name):
    """Create a fake CRD object."""
    return FakeK8sResource(kind="CustomResourceDefinition", name=name)


def make_gateway(name="gw-1", programmed=True, listeners=2):
    """Create a fake Gateway resource."""
    return FakeK8sResource(
        kind="Gateway",
        name=name,
        namespace="f5-bnk",
        status={
            "conditions": [
                {"type": "Programmed", "status": "True" if programmed else "False"},
            ],
        },
        spec={"listeners": [{}] * listeners},
    )


def make_route(name="route-1"):
    return FakeK8sResource(kind="HTTPRoute", name=name, namespace="default")


# ---------------------------------------------------------------------------
# Mock kr8s API
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_kr8s_api():
    """Create a mock kr8s.asyncio.Api that returns predictable results."""
    api = AsyncMock()

    # Default: empty responses for everything
    api.async_version = AsyncMock(return_value={"gitVersion": "v1.29.0"})
    api.async_get = AsyncMock(return_value=[])
    api.async_apply = AsyncMock(return_value=FakeK8sResource())

    return api


@pytest.fixture
def handlers(mock_kr8s_api):
    """Create CommandHandlers with a mocked kr8s API."""
    from command_handlers import CommandHandlers

    h = CommandHandlers()
    h._api = mock_kr8s_api
    return h
