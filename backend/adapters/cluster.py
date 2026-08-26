import logging
import os
import tempfile
from typing import List

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from backend.adapters.base import DiscoveryAdapter, DiscoveredClusterData

logger = logging.getLogger(__name__)

class ClusterLevelDiscoveryAdapter(DiscoveryAdapter):
    def discover(self, credentials: dict) -> List[DiscoveredClusterData]:
        kubeconfig_path = credentials.get("kubeconfig_path")
        kubeconfig_content = credentials.get("kubeconfig_content")
        context_name = credentials.get("context_name")
        namespace_filter = credentials.get("namespace_filter")
        
        provider = credentials.get("provider", "Unknown")
        region = credentials.get("region", "Unknown")
        account_id = credentials.get("account_id", "Unknown")
        cluster_name = credentials.get("cluster_name", "Unknown")

        temp_kubeconfig_path = None
        
        try:
            if kubeconfig_content:
                fd, temp_kubeconfig_path = tempfile.mkstemp(suffix=".yaml")
                with os.fdopen(fd, 'w') as f:
                    f.write(kubeconfig_content)
                kubeconfig_path = temp_kubeconfig_path

            if kubeconfig_path:
                config.load_kubeconfig(config_file=kubeconfig_path, context=context_name)
            else:
                config.load_incluster_config()
        except Exception as e:
            logger.error(f"Failed to load Kubernetes configuration: {e}")
            if temp_kubeconfig_path and os.path.exists(temp_kubeconfig_path):
                os.remove(temp_kubeconfig_path)
            # Return unenriched cluster data if we can't connect, or empty if we prefer
            return [DiscoveredClusterData(
                provider=provider,
                region=region,
                account_id=account_id,
                name=cluster_name
            )]
            
        core_v1 = client.CoreV1Api()
        
        namespaces = []
        pods = []
        services = []
        nodes = []
        timeout = credentials.get("timeout", 10)
        
        try:
            node_list = core_v1.list_node(_request_timeout=timeout)
            for node in node_list.items:
                labels = node.metadata.labels or {}
                nodes.append({
                    "name": node.metadata.name,
                    "labels": labels,
                    "instance_type": labels.get("node.kubernetes.io/instance-type") or labels.get("beta.kubernetes.io/instance-type", "Unknown")
                })
                
            if namespace_filter:
                namespaces = [namespace_filter]
            else:
                ns_list = core_v1.list_namespace(_request_timeout=timeout)
                namespaces = [ns.metadata.name for ns in ns_list.items]
                
            for ns in namespaces:
                try:
                    pod_list = core_v1.list_namespaced_pod(namespace=ns, _request_timeout=timeout)
                    for pod in pod_list.items:
                        pods.append({
                            "name": pod.metadata.name,
                            "namespace": ns,
                            "status": pod.status.phase
                        })
                except ApiException as e:
                    logger.warning(f"Could not list pods in namespace {ns}: {e}")

                try:
                    svc_list = core_v1.list_namespaced_service(namespace=ns, _request_timeout=timeout)
                    for svc in svc_list.items:
                        services.append({
                            "name": svc.metadata.name,
                            "namespace": ns,
                            "type": svc.spec.type,
                            "cluster_ip": svc.spec.cluster_ip
                        })
                except ApiException as e:
                    logger.warning(f"Could not list services in namespace {ns}: {e}")
                    
        except ApiException as e:
            logger.error(f"Kubernetes API error during cluster discovery: {e}")
        except Exception as e:
            logger.error(f"Unexpected error discovering cluster: {e}")
        finally:
            if temp_kubeconfig_path and os.path.exists(temp_kubeconfig_path):
                os.remove(temp_kubeconfig_path)
                
        return [
            DiscoveredClusterData(
                provider=provider,
                region=region,
                account_id=account_id,
                name=cluster_name,
                namespaces=namespaces,
                pods=pods,
                services=services,
                nodes=nodes
            )
        ]
