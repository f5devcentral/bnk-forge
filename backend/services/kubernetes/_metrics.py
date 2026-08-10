"""
Metrics: pod and node resource usage (kubectl top equivalent).
"""

import logging
from typing import Any

from kubernetes import client
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)


class MetricsMixin:
    """Mixin for pod/node metrics (requires metrics-server)."""

    def get_pod_metrics(
        self,
        cluster_id: int,
        namespace: str | None = None,
        sort_by: str | None = None
    ) -> dict[str, Any]:
        """Get pod resource usage metrics (CPU and memory). Equivalent to: kubectl top pods."""
        try:
            cluster = self.get_cluster(cluster_id)
            api_client = self.load_kubeconfig(cluster)
            custom_api = client.CustomObjectsApi(api_client)

            try:
                if namespace:
                    metrics_response = custom_api.list_namespaced_custom_object(
                        group="metrics.k8s.io", version="v1beta1", namespace=namespace, plural="pods"
                    )
                else:
                    metrics_response = custom_api.list_cluster_custom_object(
                        group="metrics.k8s.io", version="v1beta1", plural="pods"
                    )

                pod_metrics = []
                for item in metrics_response.get("items", []):
                    metadata = item.get("metadata", {})
                    containers = item.get("containers", [])
                    total_cpu = 0
                    total_memory = 0
                    for container in containers:
                        usage = container.get("usage", {})
                        total_cpu += self._parse_cpu_string(usage.get("cpu", "0"))
                        total_memory += self._parse_memory_string(usage.get("memory", "0"))

                    pod_metrics.append({
                        "name": metadata.get("name"),
                        "namespace": metadata.get("namespace"),
                        "cpu_millicores": total_cpu,
                        "memory_bytes": total_memory,
                        "timestamp": item.get("timestamp")
                    })

                if sort_by == "cpu":
                    pod_metrics.sort(key=lambda x: x["cpu_millicores"], reverse=True)
                elif sort_by == "memory":
                    pod_metrics.sort(key=lambda x: x["memory_bytes"], reverse=True)

                return {"available": True, "metrics": pod_metrics}

            except ApiException as e:
                if e.status == 404:
                    return {"available": False, "error": "Metrics server not installed in cluster."}
                else:
                    raise

        except ApiException as e:
            logger.error(f"Kubernetes API error getting pod metrics: {e}")
            raise ValueError(f"Kubernetes API error: {e.reason}")
        except Exception as e:
            logger.error(f"Unexpected error getting pod metrics: {e}")
            raise

    def get_node_metrics(
        self,
        cluster_id: int,
        sort_by: str | None = None
    ) -> dict[str, Any]:
        """Get node resource usage metrics (CPU and memory). Equivalent to: kubectl top nodes."""
        try:
            cluster = self.get_cluster(cluster_id)
            api_client = self.load_kubeconfig(cluster)
            custom_api = client.CustomObjectsApi(api_client)
            v1 = client.CoreV1Api(api_client)

            try:
                metrics_response = custom_api.list_cluster_custom_object(
                    group="metrics.k8s.io", version="v1beta1", plural="nodes"
                )

                nodes = v1.list_node()
                node_allocatable = {}
                for node in nodes.items:
                    node_allocatable[node.metadata.name] = {
                        "cpu": self._parse_cpu_string(node.status.allocatable.get("cpu", "0")),
                        "memory": self._parse_memory_string(node.status.allocatable.get("memory", "0")),
                        "pods": int(node.status.allocatable.get("pods", "0"))
                    }

                node_metrics = []
                for item in metrics_response.get("items", []):
                    metadata = item.get("metadata", {})
                    usage = item.get("usage", {})
                    node_name = metadata.get("name")

                    cpu_millicores = self._parse_cpu_string(usage.get("cpu", "0"))
                    memory_bytes = self._parse_memory_string(usage.get("memory", "0"))

                    allocatable = node_allocatable.get(node_name, {})
                    allocatable_cpu = allocatable.get("cpu", 0)
                    allocatable_memory = allocatable.get("memory", 0)
                    allocatable_pods = allocatable.get("pods", 0)

                    cpu_percent = (cpu_millicores / allocatable_cpu * 100) if allocatable_cpu > 0 else 0
                    memory_percent = (memory_bytes / allocatable_memory * 100) if allocatable_memory > 0 else 0

                    node_metrics.append({
                        "name": node_name,
                        "cpu_millicores": cpu_millicores,
                        "memory_bytes": memory_bytes,
                        "allocatable_cpu_millicores": allocatable_cpu,
                        "allocatable_memory_bytes": allocatable_memory,
                        "allocatable_pods": allocatable_pods,
                        "cpu_percent": round(cpu_percent, 1),
                        "memory_percent": round(memory_percent, 1),
                        "timestamp": item.get("timestamp")
                    })

                if sort_by == "cpu":
                    node_metrics.sort(key=lambda x: x["cpu_millicores"], reverse=True)
                elif sort_by == "memory":
                    node_metrics.sort(key=lambda x: x["memory_bytes"], reverse=True)

                return {"available": True, "metrics": node_metrics}

            except ApiException as e:
                if e.status == 404:
                    return {"available": False, "error": "Metrics server not installed in cluster."}
                else:
                    raise

        except ApiException as e:
            logger.error(f"Kubernetes API error getting node metrics: {e}")
            raise ValueError(f"Kubernetes API error: {e.reason}")
        except Exception as e:
            logger.error(f"Unexpected error getting node metrics: {e}")
            raise

    def _parse_cpu_string(self, cpu_str: str) -> int:
        """Parse Kubernetes CPU string to millicores."""
        if not cpu_str or cpu_str == "0":
            return 0
        cpu_str = cpu_str.strip()
        if cpu_str.endswith("n"):
            return int(float(cpu_str[:-1]) / 1_000_000)
        if cpu_str.endswith("u"):
            return int(float(cpu_str[:-1]) / 1_000)
        if cpu_str.endswith("m"):
            return int(float(cpu_str[:-1]))
        return int(float(cpu_str) * 1000)

    def _parse_memory_string(self, memory_str: str) -> int:
        """Parse Kubernetes memory string to bytes."""
        if not memory_str or memory_str == "0":
            return 0
        memory_str = memory_str.strip()
        try:
            return int(float(memory_str))
        except ValueError:
            pass

        suffixes = {
            "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4, "Pi": 1024**5, "Ei": 1024**6,
            "k": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4, "P": 1000**5, "E": 1000**6,
        }
        for suffix, multiplier in suffixes.items():
            if memory_str.endswith(suffix):
                return int(float(memory_str[:-len(suffix)]) * multiplier)

        try:
            return int(float(memory_str))
        except ValueError:
            logger.warning(f"Could not parse memory string: {memory_str}")
            return 0
