"""
Pod operations: logs, restart, container listing.
"""

import logging
from typing import Any

from kubernetes import client
from kubernetes.client.rest import ApiException

from services.reachability import with_breaker

logger = logging.getLogger(__name__)


class PodsMixin:
    """Mixin for pod-specific operations."""

    @with_breaker("cluster", target_id_arg="cluster_id")
    def get_pod_logs(
        self,
        cluster_id: int,
        pod_name: str,
        namespace: str,
        container: str | None = None,
        tail_lines: int = 100,
        follow: bool = False
    ) -> str:
        """Get logs from a pod."""
        cluster = self.get_cluster(cluster_id)
        api_client = self.load_kubeconfig(cluster)

        if not namespace:
            namespace = cluster.default_namespace or "default"

        try:
            v1 = client.CoreV1Api(api_client)
            logs = v1.read_namespaced_pod_log(
                name=pod_name, namespace=namespace,
                container=container, tail_lines=tail_lines, follow=follow
            )
            return logs
        except ApiException as e:
            logger.error(f"Failed to get logs for pod {pod_name}: {e}")
            raise ValueError(f"Kubernetes API error: {e.reason}")
        except Exception as e:
            logger.error(f"Unexpected error getting logs for pod {pod_name}: {e}")
            raise

    def restart_pod(self, cluster_id: int, pod_name: str, namespace: str) -> dict[str, Any]:
        """Restart a pod by deleting it (will be recreated by controller)."""
        cluster = self.get_cluster(cluster_id)
        api_client = self.load_kubeconfig(cluster)

        if not namespace:
            namespace = cluster.default_namespace or "default"

        try:
            v1 = client.CoreV1Api(api_client)
            v1.delete_namespaced_pod(name=pod_name, namespace=namespace)
            return {"success": True, "message": f"Pod {pod_name} deleted (will be recreated by controller)"}
        except ApiException as e:
            logger.error(f"Failed to restart pod {pod_name}: {e}")
            raise ValueError(f"Kubernetes API error: {e.reason}")
        except Exception as e:
            logger.error(f"Unexpected error restarting pod {pod_name}: {e}")
            raise

    def get_pod_containers(self, cluster_id: int, pod_name: str, namespace: str) -> dict[str, Any]:
        """Get list of containers in a pod."""
        cluster = self.get_cluster(cluster_id)
        api_client = self.load_kubeconfig(cluster)

        if not namespace:
            namespace = cluster.default_namespace or "default"

        try:
            v1 = client.CoreV1Api(api_client)
            pod = v1.read_namespaced_pod(name=pod_name, namespace=namespace)

            containers = []
            init_containers = []

            if pod.spec.containers:
                for container in pod.spec.containers:
                    container_status = None
                    if pod.status.container_statuses:
                        for status in pod.status.container_statuses:
                            if status.name == container.name:
                                container_status = status
                                break
                    containers.append({
                        "name": container.name,
                        "image": container.image,
                        "ready": container_status.ready if container_status else False,
                        "restart_count": container_status.restart_count if container_status else 0,
                        "state": self._parse_container_state(container_status) if container_status else "unknown"
                    })

            if pod.spec.init_containers:
                for container in pod.spec.init_containers:
                    container_status = None
                    if pod.status.init_container_statuses:
                        for status in pod.status.init_container_statuses:
                            if status.name == container.name:
                                container_status = status
                                break
                    init_containers.append({
                        "name": container.name,
                        "image": container.image,
                        "ready": container_status.ready if container_status else False,
                        "restart_count": container_status.restart_count if container_status else 0,
                        "state": self._parse_container_state(container_status) if container_status else "unknown"
                    })

            return {
                "pod_name": pod_name,
                "namespace": namespace,
                "containers": containers,
                "init_containers": init_containers
            }

        except ApiException as e:
            logger.error(f"Failed to get pod containers: {e}")
            raise ValueError(f"Kubernetes API error: {e.reason}")
        except Exception as e:
            logger.error(f"Unexpected error getting pod containers: {e}")
            raise

    def _parse_container_state(self, container_status) -> str:
        """Parse container state from status."""
        if not container_status or not container_status.state:
            return "unknown"
        if container_status.state.running:
            return "running"
        elif container_status.state.waiting:
            return f"waiting: {container_status.state.waiting.reason}"
        elif container_status.state.terminated:
            return f"terminated: {container_status.state.terminated.reason}"
        else:
            return "unknown"
