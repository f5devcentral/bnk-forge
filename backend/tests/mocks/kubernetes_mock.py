"""
Kubernetes client mock objects for testing.

Provides mocks for:
- kubernetes (official Python client)
- kr8s (lightweight async client)
- KubernetesService (our service layer)

These mocks simulate K8s API responses without requiring a real cluster.
"""

from unittest.mock import AsyncMock, MagicMock, PropertyMock


def mock_kubernetes_client() -> MagicMock:
    """
    Mock the official kubernetes Python client (kubernetes.client).

    Provides mock CoreV1Api, AppsV1Api, and CustomObjectsApi.
    """
    client = MagicMock()

    # CoreV1Api
    core_v1 = MagicMock()

    # Mock pod list response
    mock_pod = MagicMock()
    mock_pod.metadata.name = "test-pod-1"
    mock_pod.metadata.namespace = "default"
    mock_pod.metadata.labels = {"app": "test"}
    mock_pod.status.phase = "Running"
    mock_pod.status.conditions = []
    mock_pod.spec.containers = [
        MagicMock(name="main", image="nginx:latest", ports=[MagicMock(container_port=80)])
    ]

    pod_list = MagicMock()
    pod_list.items = [mock_pod]
    core_v1.list_namespaced_pod.return_value = pod_list

    # Mock namespace list
    mock_ns = MagicMock()
    mock_ns.metadata.name = "default"
    ns_list = MagicMock()
    ns_list.items = [mock_ns]
    core_v1.list_namespace.return_value = ns_list

    # Mock node list
    mock_node = MagicMock()
    mock_node.metadata.name = "test-node-1"
    mock_node.status.conditions = [MagicMock(type="Ready", status="True")]
    mock_node.status.allocatable = {"cpu": "4", "memory": "16Gi"}
    node_list = MagicMock()
    node_list.items = [mock_node]
    core_v1.list_node.return_value = node_list

    client.CoreV1Api.return_value = core_v1

    # AppsV1Api
    apps_v1 = MagicMock()

    mock_deployment = MagicMock()
    mock_deployment.metadata.name = "test-deployment"
    mock_deployment.metadata.namespace = "default"
    mock_deployment.spec.replicas = 3
    mock_deployment.status.ready_replicas = 3
    mock_deployment.status.available_replicas = 3

    deployment_list = MagicMock()
    deployment_list.items = [mock_deployment]
    apps_v1.list_namespaced_deployment.return_value = deployment_list

    client.AppsV1Api.return_value = apps_v1

    # CustomObjectsApi (for F5 BNK CRDs)
    custom = MagicMock()
    custom.list_namespaced_custom_object.return_value = {"items": []}
    custom.get_namespaced_custom_object.return_value = {
        "apiVersion": "k8s.f5net.com/v1",
        "kind": "F5SPKIngressTCP",
        "metadata": {"name": "test-crd", "namespace": "default"},
    }
    client.CustomObjectsApi.return_value = custom

    return client


def mock_kr8s_api() -> MagicMock:
    """
    Mock kr8s async API for lightweight K8s operations.

    Used by kubernetes_engine.py and services/k8s_*.py.
    """
    api = AsyncMock()

    # Mock api.get() for resources
    mock_resource = MagicMock()
    mock_resource.name = "test-resource"
    mock_resource.namespace = "default"
    mock_resource.raw = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": "test-resource", "namespace": "default"},
        "spec": {"type": "ClusterIP", "ports": [{"port": 80}]},
    }
    mock_resource.delete = AsyncMock()
    mock_resource.patch = AsyncMock()

    api.get.return_value = [mock_resource]
    api.async_get.return_value = [mock_resource]

    # Mock api.version for health check
    api.version.return_value = {
        "major": "1",
        "minor": "28",
        "gitVersion": "v1.28.0",
    }

    return api


class MockKubernetesService:
    """
    Mock of services/cluster_management_service.py KubernetesService.

    Provides deterministic return values for all K8s operations without
    requiring a real cluster connection.

    Usage:
        with patch("services.cluster_management_service.KubernetesService",
                    return_value=MockKubernetesService()):
            ...
    """

    def __init__(self, *, healthy: bool = True, resources: list | None = None):
        self.healthy = healthy
        self.resources = resources or []

    def get_cluster_health(self, cluster_id: int) -> dict:
        return {
            "status": "healthy" if self.healthy else "unhealthy",
            "node_count": 3,
            "pod_count": 12,
            "version": "v1.28.0",
        }

    def list_resources(self, cluster_id: int, resource_type: str, namespace: str = "default") -> list:
        return self.resources

    def apply_manifest(self, cluster_id: int, manifest: dict) -> dict:
        kind = manifest.get("kind", "Unknown")
        name = manifest.get("metadata", {}).get("name", "unknown")
        return {"status": "created", "kind": kind, "name": name}

    def delete_resource(self, cluster_id: int, kind: str, name: str, namespace: str = "default") -> dict:
        return {"status": "deleted", "kind": kind, "name": name}
