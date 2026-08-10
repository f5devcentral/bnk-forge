"""
Resource operations: patch, label, annotate, scale, cordon, uncordon, drain.
"""

import logging
from typing import Any

from kubernetes import client
from kubernetes.client.rest import ApiException

from services.kubernetes._resources import API_GROUP_CLIENTS, _kind_to_snake, resolve_resource_type

logger = logging.getLogger(__name__)


class OperationsMixin:
    """Mixin for resource patch/label/annotate and node operations."""

    def scale_deployment(
        self,
        cluster_id: int,
        deployment_name: str,
        namespace: str,
        replicas: int
    ) -> dict[str, Any]:
        """Scale a deployment to a specific number of replicas."""
        cluster = self.get_cluster(cluster_id)
        api_client = self.load_kubeconfig(cluster)

        if not namespace:
            namespace = cluster.default_namespace or "default"

        try:
            apps_v1 = client.AppsV1Api(api_client)
            deployment = apps_v1.read_namespaced_deployment(deployment_name, namespace)
            deployment.spec.replicas = replicas
            result = apps_v1.patch_namespaced_deployment(name=deployment_name, namespace=namespace, body=deployment)
            return {"success": True, "message": f"Deployment scaled to {replicas} replicas", "replicas": result.spec.replicas}
        except ApiException as e:
            logger.error(f"Failed to scale deployment {deployment_name}: {e}")
            raise ValueError(f"Kubernetes API error: {e.reason}")
        except Exception as e:
            logger.error(f"Unexpected error scaling deployment {deployment_name}: {e}")
            raise

    def patch_resource(
        self,
        cluster_id: int,
        resource_type_key: str,
        resource_name: str,
        patch_data: dict[str, Any],
        namespace: str | None = None,
        patch_type: str = "strategic"
    ) -> dict[str, Any]:
        """Patch a Kubernetes resource (partial update)."""
        resource_type = resolve_resource_type(self.db, cluster_id, resource_type_key)
        cluster = self.get_cluster(cluster_id)
        api_client = self.load_kubeconfig(cluster)

        if resource_type.namespaced and not namespace:
            namespace = cluster.default_namespace or "default"

        try:
            if resource_type.api_group not in API_GROUP_CLIENTS:
                custom_api = client.CustomObjectsApi(api_client)
                if resource_type.namespaced:
                    result = custom_api.patch_namespaced_custom_object(
                        group=resource_type.api_group, version=resource_type.api_version,
                        namespace=namespace, plural=resource_type.plural,
                        name=resource_name, body=patch_data
                    )
                else:
                    result = custom_api.patch_cluster_custom_object(
                        group=resource_type.api_group, version=resource_type.api_version,
                        plural=resource_type.plural, name=resource_name, body=patch_data
                    )
            else:
                result = self._patch_typed_resource(api_client, resource_type, namespace, resource_name, patch_data)

            return {"success": True, "message": "Resource patched successfully", "resource": result}
        except ApiException as e:
            logger.error(f"Failed to patch {resource_type.kind}/{resource_name}: {e}")
            raise ValueError(f"Kubernetes API error: {e.reason}")
        except Exception as e:
            logger.error(f"Unexpected error patching {resource_type.kind}/{resource_name}: {e}")
            raise

    def _patch_typed_resource(self, api_client, resource_type, namespace, name, patch_data):
        """Patch a resource via the typed client using dynamic method dispatch."""
        api_class = API_GROUP_CLIENTS[resource_type.api_group]
        api_instance = api_class(api_client)
        kind_snake = _kind_to_snake(resource_type.kind)
        kwargs = dict(name=name, body=patch_data)

        if resource_type.namespaced:
            method_name = f"patch_namespaced_{kind_snake}"
            kwargs["namespace"] = namespace
        else:
            method_name = f"patch_{kind_snake}"

        method = getattr(api_instance, method_name, None)
        if method is None:
            raise ValueError(f"Method {method_name} not found on {api_class.__name__}")

        result = method(**kwargs)
        if result is not None and hasattr(result, "to_dict"):
            return result.to_dict()
        return result

    def label_resource(
        self, cluster_id: int, resource_type_key: str, resource_name: str,
        labels: dict[str, str], namespace: str | None = None, overwrite: bool = False
    ) -> dict[str, Any]:
        """Add or update labels on a resource (kubectl label)."""
        if not labels:
            raise ValueError("No labels provided")
        patch_data = {"metadata": {"labels": labels}}
        return self.patch_resource(
            cluster_id=cluster_id, resource_type_key=resource_type_key,
            resource_name=resource_name, patch_data=patch_data,
            namespace=namespace, patch_type="merge"
        )

    def annotate_resource(
        self, cluster_id: int, resource_type_key: str, resource_name: str,
        annotations: dict[str, str], namespace: str | None = None, overwrite: bool = False
    ) -> dict[str, Any]:
        """Add or update annotations on a resource (kubectl annotate)."""
        if not annotations:
            raise ValueError("No annotations provided")
        patch_data = {"metadata": {"annotations": annotations}}
        return self.patch_resource(
            cluster_id=cluster_id, resource_type_key=resource_type_key,
            resource_name=resource_name, patch_data=patch_data,
            namespace=namespace, patch_type="merge"
        )

    # ==================== Node Operations ====================

    def cordon_node(self, cluster_id: int, node_name: str) -> dict[str, Any]:
        """Mark node as unschedulable (kubectl cordon)."""
        try:
            cluster = self.get_cluster(cluster_id)
            api_client = self.load_kubeconfig(cluster)
            v1 = client.CoreV1Api(api_client)
            node = v1.read_node(node_name)
            node.spec.unschedulable = True
            v1.patch_node(node_name, node)
            return {"success": True, "message": f"Node {node_name} cordoned (marked unschedulable)", "node_name": node_name, "unschedulable": True}
        except ApiException as e:
            logger.error(f"Kubernetes API error cordoning node: {e}")
            raise ValueError(f"Failed to cordon node: {e.reason}")
        except Exception as e:
            logger.error(f"Error cordoning node: {e}")
            raise

    def uncordon_node(self, cluster_id: int, node_name: str) -> dict[str, Any]:
        """Mark node as schedulable (kubectl uncordon)."""
        try:
            cluster = self.get_cluster(cluster_id)
            api_client = self.load_kubeconfig(cluster)
            v1 = client.CoreV1Api(api_client)
            node = v1.read_node(node_name)
            node.spec.unschedulable = False
            v1.patch_node(node_name, node)
            return {"success": True, "message": f"Node {node_name} uncordoned (marked schedulable)", "node_name": node_name, "unschedulable": False}
        except ApiException as e:
            logger.error(f"Kubernetes API error uncordoning node: {e}")
            raise ValueError(f"Failed to uncordon node: {e.reason}")
        except Exception as e:
            logger.error(f"Error uncordoning node: {e}")
            raise

    def drain_node(
        self, cluster_id: int, node_name: str,
        ignore_daemonsets: bool = True, delete_emptydir_data: bool = False,
        force: bool = False, grace_period: int = -1
    ) -> dict[str, Any]:
        """Drain node by evicting all pods (kubectl drain)."""
        try:
            cluster = self.get_cluster(cluster_id)
            api_client = self.load_kubeconfig(cluster)
            v1 = client.CoreV1Api(api_client)

            # First, cordon the node
            self.cordon_node(cluster_id, node_name)

            pods = v1.list_pod_for_all_namespaces(field_selector=f"spec.nodeName={node_name}")
            evicted_pods = []
            failed_pods = []

            for pod in pods.items:
                pod_name = pod.metadata.name
                namespace = pod.metadata.namespace

                if ignore_daemonsets and pod.metadata.owner_references:
                    if any(ref.kind == "DaemonSet" for ref in pod.metadata.owner_references):
                        logger.info(f"Skipping DaemonSet pod: {namespace}/{pod_name}")
                        continue

                has_emptydir = False
                if pod.spec.volumes:
                    has_emptydir = any(vol.empty_dir is not None for vol in pod.spec.volumes)
                if has_emptydir and not delete_emptydir_data and not force:
                    failed_pods.append({"name": pod_name, "namespace": namespace, "reason": "Pod has emptyDir volumes"})
                    continue

                try:
                    eviction = client.V1Eviction(
                        metadata=client.V1ObjectMeta(name=pod_name, namespace=namespace),
                        delete_options=client.V1DeleteOptions(grace_period_seconds=grace_period if grace_period >= 0 else None)
                    )
                    v1.create_namespaced_pod_eviction(name=pod_name, namespace=namespace, body=eviction)
                    evicted_pods.append({"name": pod_name, "namespace": namespace, "status": "evicted"})
                except ApiException as e:
                    if e.status == 429:
                        failed_pods.append({"name": pod_name, "namespace": namespace, "reason": "Blocked by PodDisruptionBudget"})
                    else:
                        failed_pods.append({"name": pod_name, "namespace": namespace, "reason": str(e.reason)})

            return {
                "success": len(failed_pods) == 0,
                "message": f"Node {node_name} drained ({len(evicted_pods)} pods evicted, {len(failed_pods)} failed)",
                "node_name": node_name,
                "evicted_pods": evicted_pods,
                "failed_pods": failed_pods,
                "total_evicted": len(evicted_pods),
                "total_failed": len(failed_pods)
            }

        except ApiException as e:
            logger.error(f"Kubernetes API error draining node: {e}")
            raise ValueError(f"Failed to drain node: {e.reason}")
        except Exception as e:
            logger.error(f"Error draining node: {e}")
            raise
