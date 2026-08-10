"""
Resource describe and events — kubectl describe equivalent.
"""

import logging
from typing import Any

from kubernetes import client
from kubernetes.client.rest import ApiException

from services.kubernetes._resources import API_GROUP_CLIENTS, _kind_to_snake, resolve_resource_type

logger = logging.getLogger(__name__)


class DescribeMixin:
    """Mixin for describe_resource and get_events."""

    def describe_resource(
        self,
        cluster_id: int,
        resource_type_key: str,
        resource_name: str,
        namespace: str | None = None
    ) -> dict[str, Any]:
        """Describe a resource — kubectl describe equivalent."""
        cluster = self.get_cluster(cluster_id)
        api_client = self.load_kubeconfig(cluster)

        if not namespace:
            namespace = cluster.default_namespace or "default"

        try:
            resource_type = resolve_resource_type(self.db, cluster_id, resource_type_key)
            resource = self._describe_fetch_resource(api_client, resource_type, resource_name, namespace)
            events = self._describe_fetch_events(api_client, resource_type, resource_name, namespace, resource)

            is_dict = isinstance(resource, dict)

            if is_dict:
                metadata = resource.get('metadata', {})
                spec = resource.get('spec', {})
                status = resource.get('status', {})
                description = {
                    "name": metadata.get('name'),
                    "namespace": namespace if resource_type.namespaced else None,
                    "kind": resource_type.kind,
                    "api_version": resource_type.api_version,
                    "metadata": self._describe_parse_metadata_dict(metadata),
                    "spec": spec,
                    "status": status,
                    "conditions": self._describe_parse_conditions_dict(status),
                    "events": events,
                    "relationships": {}
                }
            else:
                description = {
                    "name": resource.metadata.name,
                    "namespace": namespace if resource_type.namespaced else None,
                    "kind": resource_type.kind,
                    "api_version": resource_type.api_version,
                    "metadata": self._describe_parse_metadata(resource.metadata),
                    "spec": resource.spec.to_dict() if hasattr(resource, 'spec') and resource.spec else {},
                    "status": self._describe_parse_status(resource),
                    "conditions": self._describe_parse_conditions(resource),
                    "events": events,
                    "relationships": self._describe_find_relationships(api_client, resource_type, resource, namespace)
                }

            return description

        except ApiException as e:
            logger.error(f"Failed to describe {resource_type_key}/{resource_name}: {e}")
            raise ValueError(f"Kubernetes API error: {e.reason}")
        except Exception as e:
            logger.error(f"Unexpected error describing {resource_type_key}/{resource_name}: {e}")
            raise

    def _describe_fetch_resource(self, api_client, resource_type, resource_name, namespace) -> Any:
        """Fetch a single resource for describe using dynamic method dispatch."""
        if resource_type.api_group not in API_GROUP_CLIENTS:
            api = client.CustomObjectsApi(api_client)
            if resource_type.namespaced:
                return api.get_namespaced_custom_object(
                    group=resource_type.api_group, version=resource_type.api_version,
                    namespace=namespace, plural=resource_type.plural, name=resource_name
                )
            else:
                return api.get_cluster_custom_object(
                    group=resource_type.api_group, version=resource_type.api_version,
                    plural=resource_type.plural, name=resource_name
                )

        api_class = API_GROUP_CLIENTS[resource_type.api_group]
        api = api_class(api_client)
        kind_snake = _kind_to_snake(resource_type.kind)

        if resource_type.namespaced:
            method_name = f"read_namespaced_{kind_snake}"
            method = getattr(api, method_name, None)
            if method is None:
                raise ValueError(f"Method {method_name} not found on {api_class.__name__}")
            return method(name=resource_name, namespace=namespace)
        else:
            method_name = f"read_{kind_snake}"
            method = getattr(api, method_name, None)
            if method is None:
                raise ValueError(f"Method {method_name} not found on {api_class.__name__}")
            return method(name=resource_name)

    def _describe_fetch_events(self, api_client, resource_type, resource_name, namespace, resource) -> list[dict[str, Any]]:
        """Fetch events related to a resource."""
        try:
            v1 = client.CoreV1Api(api_client)
            field_selector = f"involvedObject.name={resource_name},involvedObject.kind={resource_type.kind}"
            if resource_type.namespaced:
                field_selector += f",involvedObject.namespace={namespace}"

            if resource_type.namespaced:
                events_list = v1.list_namespaced_event(namespace=namespace, field_selector=field_selector)
            else:
                events_list = v1.list_event_for_all_namespaces(field_selector=field_selector)

            events = []
            for event in events_list.items:
                events.append({
                    "type": event.type,
                    "reason": event.reason,
                    "message": event.message,
                    "count": event.count or 1,
                    "first_timestamp": event.first_timestamp.isoformat() if event.first_timestamp else None,
                    "last_timestamp": event.last_timestamp.isoformat() if event.last_timestamp else None,
                    "source": f"{event.source.component}" if event.source else "unknown"
                })

            events.sort(key=lambda e: e.get("last_timestamp") or "", reverse=True)
            return events[:50]

        except Exception as e:
            logger.warning(f"Failed to fetch events for {resource_name}: {e}")
            return []

    def _describe_parse_metadata(self, metadata) -> dict[str, Any]:
        """Parse metadata from K8s API objects."""
        return {
            "name": metadata.name,
            "namespace": metadata.namespace,
            "uid": metadata.uid,
            "resource_version": metadata.resource_version,
            "creation_timestamp": metadata.creation_timestamp.isoformat() if metadata.creation_timestamp else None,
            "labels": metadata.labels or {},
            "annotations": metadata.annotations or {},
            "owner_references": [
                {"kind": ref.kind, "name": ref.name, "uid": ref.uid, "controller": ref.controller}
                for ref in (metadata.owner_references or [])
            ]
        }

    def _describe_parse_metadata_dict(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """Parse metadata from custom resources (dicts)."""
        return {
            "name": metadata.get('name'),
            "namespace": metadata.get('namespace'),
            "uid": metadata.get('uid'),
            "resource_version": metadata.get('resourceVersion'),
            "creation_timestamp": metadata.get('creationTimestamp'),
            "labels": metadata.get('labels') or {},
            "annotations": metadata.get('annotations') or {},
            "owner_references": [
                {"kind": ref.get('kind'), "name": ref.get('name'), "uid": ref.get('uid'), "controller": ref.get('controller')}
                for ref in (metadata.get('ownerReferences') or [])
            ]
        }

    def _describe_parse_conditions_dict(self, status: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract conditions from custom resource status dicts."""
        if not status:
            return []
        conditions = []
        for condition in (status.get('conditions') or []):
            conditions.append({
                "type": condition.get('type'),
                "status": condition.get('status'),
                "reason": condition.get('reason'),
                "message": condition.get('message'),
                "last_transition_time": condition.get('lastTransitionTime'),
                "last_update_time": condition.get('lastUpdateTime')
            })
        return conditions

    def _describe_parse_status(self, resource) -> dict[str, Any]:
        """Parse status field into structured format."""
        if not hasattr(resource, 'status') or not resource.status:
            return {}
        try:
            return resource.status.to_dict()
        except Exception:
            return {}

    def _describe_parse_conditions(self, resource) -> list[dict[str, Any]]:
        """Extract conditions from resource status."""
        if not hasattr(resource, 'status') or not resource.status:
            return []
        conditions = []
        if hasattr(resource.status, 'conditions') and resource.status.conditions:
            for condition in resource.status.conditions:
                conditions.append({
                    "type": condition.type,
                    "status": condition.status,
                    "reason": getattr(condition, 'reason', None),
                    "message": getattr(condition, 'message', None),
                    "last_transition_time": condition.last_transition_time.isoformat() if hasattr(condition, 'last_transition_time') and condition.last_transition_time else None,
                    "last_update_time": condition.last_update_time.isoformat() if hasattr(condition, 'last_update_time') and condition.last_update_time else None
                })
        return conditions

    def _describe_find_relationships(self, api_client, resource_type, resource, namespace) -> dict[str, list[dict[str, str]]]:
        """Find related resources (owned, owners, services for pods, etc.)."""
        relationships = {"owned": [], "owner": [], "related": []}
        try:
            if hasattr(resource.metadata, 'uid'):
                v1 = client.CoreV1Api(api_client)
                apps_v1 = client.AppsV1Api(api_client)

                if resource_type.kind == "Deployment":
                    try:
                        replica_sets = apps_v1.list_namespaced_replica_set(namespace=namespace)
                        for rs in replica_sets.items:
                            if rs.metadata.owner_references:
                                for ref in rs.metadata.owner_references:
                                    if ref.uid == resource.metadata.uid:
                                        relationships["owned"].append({"kind": "ReplicaSet", "name": rs.metadata.name, "namespace": namespace})
                                        pods = v1.list_namespaced_pod(namespace=namespace)
                                        for pod in pods.items:
                                            if pod.metadata.owner_references:
                                                for pod_ref in pod.metadata.owner_references:
                                                    if pod_ref.uid == rs.metadata.uid:
                                                        relationships["owned"].append({"kind": "Pod", "name": pod.metadata.name, "namespace": namespace})
                    except Exception as e:
                        logger.warning(f"Failed to find ReplicaSets for Deployment: {e}")

                if resource_type.kind == "Pod" and hasattr(resource.metadata, 'labels') and resource.metadata.labels:
                    try:
                        services = v1.list_namespaced_service(namespace=namespace)
                        for svc in services.items:
                            if svc.spec.selector:
                                match = all(resource.metadata.labels.get(k) == v for k, v in svc.spec.selector.items())
                                if match:
                                    relationships["related"].append({"kind": "Service", "name": svc.metadata.name, "namespace": namespace})
                    except Exception as e:
                        logger.warning(f"Failed to find Services for Pod: {e}")

            if hasattr(resource.metadata, 'owner_references') and resource.metadata.owner_references:
                for ref in resource.metadata.owner_references:
                    relationships["owner"].append({"kind": ref.kind, "name": ref.name, "namespace": namespace})

        except Exception as e:
            logger.warning(f"Failed to find relationships: {e}")

        return relationships

    def get_events(
        self,
        cluster_id: int,
        namespace: str | None = None,
        resource_type: str | None = None,
        resource_name: str | None = None,
        event_type: str | None = None
    ) -> list[dict[str, Any]]:
        """Get events from cluster, optionally filtered."""
        cluster = self.get_cluster(cluster_id)
        api_client = self.load_kubeconfig(cluster)

        try:
            v1 = client.CoreV1Api(api_client)
            field_selectors = []
            if resource_name and resource_type:
                field_selectors.append(f"involvedObject.name={resource_name}")
                field_selectors.append(f"involvedObject.kind={resource_type}")
            if event_type:
                field_selectors.append(f"type={event_type}")

            field_selector = ",".join(field_selectors) if field_selectors else None

            if namespace:
                events_list = v1.list_namespaced_event(namespace=namespace, field_selector=field_selector)
            else:
                events_list = v1.list_event_for_all_namespaces(field_selector=field_selector)

            events = []
            for event in events_list.items:
                events.append({
                    "type": event.type,
                    "reason": event.reason,
                    "message": event.message,
                    "count": event.count or 1,
                    "first_timestamp": event.first_timestamp.isoformat() if event.first_timestamp else None,
                    "last_timestamp": event.last_timestamp.isoformat() if event.last_timestamp else None,
                    "source": {
                        "component": event.source.component if event.source else "unknown",
                        "host": event.source.host if event.source else None
                    },
                    "involved_object": {
                        "kind": event.involved_object.kind if event.involved_object else None,
                        "name": event.involved_object.name if event.involved_object else None,
                        "namespace": event.involved_object.namespace if event.involved_object else None
                    },
                    "metadata": {
                        "name": event.metadata.name,
                        "namespace": event.metadata.namespace,
                        "creation_timestamp": event.metadata.creation_timestamp.isoformat() if event.metadata.creation_timestamp else None
                    }
                })

            events.sort(key=lambda e: e.get("last_timestamp") or e.get("first_timestamp") or "", reverse=True)
            return events

        except ApiException as e:
            logger.error(f"Failed to get events: {e}")
            raise ValueError(f"Kubernetes API error: {e.reason}")
        except Exception as e:
            logger.error(f"Unexpected error getting events: {e}")
            raise
