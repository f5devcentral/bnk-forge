"""
Generic Kubernetes resource CRUD operations (get, create, update, delete).
"""

import logging
import re
from typing import Any

from kubernetes import client
from kubernetes.client.rest import ApiException

from core.k8s_resource_registry import get_resource_type
from core.k8s_types import K8sResourceType
from services.reachability import with_breaker

logger = logging.getLogger(__name__)

# Single source-of-truth: api_group → typed client class.
# Anything NOT in this map falls through to CustomObjectsApi.
API_GROUP_CLIENTS: dict[str, type] = {
    "": client.CoreV1Api,
    "apps": client.AppsV1Api,
    "networking.k8s.io": client.NetworkingV1Api,
    "batch": client.BatchV1Api,
    "autoscaling": client.AutoscalingV2Api,
    "rbac.authorization.k8s.io": client.RbacAuthorizationV1Api,
    "policy": client.PolicyV1Api,
    "storage.k8s.io": client.StorageV1Api,
}


def _kind_to_snake(kind: str) -> str:
    """Convert CamelCase kind to snake_case, keeping consecutive caps together.

    Examples:
        ConfigMap       → config_map
        TLSRoute        → tls_route
        BNKSecPolicy    → bnk_sec_policy
        HTTPRoute       → http_route
        Pod             → pod
        GRPCRoute       → grpc_route
    """
    # Insert underscore between: (uppercase-run)(Uppercase+lowercase)
    # e.g. "TLSRoute" → "TLS_Route", then lower everything
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', kind)
    # Insert underscore between: (lowercase/digit)(uppercase)
    s = re.sub(r'([a-z\d])([A-Z])', r'\1_\2', s)
    return s.lower()


def _resolve_via_discovery(db, cluster_id: int, resource_type_key: str, group: str | None):
    """Lazy import + CRD discovery fallback. Separated for testability.

    This function is patched in tests to avoid the circular-import hazard
    (crd_discovery_service → kubernetes._base → kubernetes.__init__ →
    _resources back to crd_discovery_service when imported at module level).

    Returns a K8sResourceType synthesized from the discovered CRDInfo.
    Raises NotFoundError (404) or BadRequestError (400) on failure.
    """
    from services.crd_discovery_service import CrdDiscoveryService  # noqa: PLC0415
    svc = CrdDiscoveryService(db)
    crd_info = svc.resolve_crd(cluster_id, resource_type_key, group=group)
    return K8sResourceType(
        api_group=crd_info.group,
        api_version=crd_info.version or "v1",
        kind=crd_info.kind,
        plural=crd_info.plural,
        namespaced=crd_info.namespaced,
        display_name=crd_info.display_name or crd_info.kind,
        description=None,
    )


def resolve_resource_type(db, cluster_id: int, resource_type_key: str, group: str | None = None) -> K8sResourceType:
    """Registry-first → CRD-discovery resolver. Single shared seam for all call sites.

    Resolution order:
      1. Static registry (registry-first — curated versions always win).
      2. CRD discovery fallback (unregistered CRDs installed in the cluster).

    Raises NotFoundError (404) when not in registry and not installed.
    Raises BadRequestError (400) when bare plural is ambiguous across groups.
    """
    try:
        return get_resource_type(resource_type_key)
    except ValueError:
        return _resolve_via_discovery(db, cluster_id, resource_type_key, group)


def resolve_plural_by_kind(db, cluster_id: int, kind: str, group: str | None) -> str | None:
    """Resolve a manifest's plural resource name from its raw `kind` + apiVersion group.

    Used by config/snapshot import-restore, which only have a raw manifest's `kind`
    (e.g. "TenantControlPlane") and `apiVersion` group on hand — never the plural
    the K8s API needs. Registry lookups work today because registry keys are
    literally `kind.lower()` for every curated type, but unregistered CRDs have no
    registry entry and `resolve_crd`'s bare-plural match never matches a singular
    kind, so this adds a kind-scoped-to-group CRD-discovery match as the fallback.

    Returns None if unresolved; callers should fall back to a naive pluralization.
    """
    try:
        return resolve_resource_type(db, cluster_id, kind.lower(), group).plural
    except Exception:
        pass

    if not group:
        return None

    from services.crd_discovery_service import CrdDiscoveryService  # noqa: PLC0415
    try:
        envelope = CrdDiscoveryService(db).list_crds(cluster_id, group_filter=[group])
    except Exception:
        return None

    for crd in envelope.crds:
        if crd.kind == kind:
            return crd.plural
    return None


class ResourcesMixin:
    """Mixin for generic Kubernetes resource CRUD."""

    @with_breaker("cluster", target_id_arg="cluster_id")
    def get_resources(
        self,
        cluster_id: int,
        resource_type_key: str,
        namespace: str | None = None,
        label_selector: str | None = None,
        group: str | None = None,
    ) -> list[dict]:
        """Generic method to fetch ANY Kubernetes resource.

        Resolution order:
          1. Static registry (registry-first — curated versions always win).
          2. CRD discovery fallback (unregistered CRDs installed in the cluster).

        Raises NotFoundError (404) when not in registry and not installed.
        Raises BadRequestError (400) when bare plural is ambiguous across groups.
        Raises BreakerOpenError (503) when cluster is unreachable.
        """
        resource_type = resolve_resource_type(self.db, cluster_id, resource_type_key, group)

        cluster = self.get_cluster(cluster_id)
        api_client = self.load_kubeconfig(cluster)

        return self._fetch_from_k8s(api_client, resource_type, namespace, label_selector)

    def _fetch_from_k8s(
        self,
        api_client: client.ApiClient,
        resource_type: K8sResourceType,
        namespace: str | None,
        label_selector: str | None
    ) -> list[dict]:
        """Internal method to fetch resources from Kubernetes API."""
        try:
            resources = []

            if resource_type.api_group and resource_type.api_group not in API_GROUP_CLIENTS:
                custom_api = client.CustomObjectsApi(api_client)

                if resource_type.namespaced:
                    if namespace:
                        response = custom_api.list_namespaced_custom_object(
                            group=resource_type.api_group,
                            version=resource_type.api_version,
                            namespace=namespace,
                            plural=resource_type.plural,
                            label_selector=label_selector or ""
                        )
                    else:
                        response = custom_api.list_cluster_custom_object(
                            group=resource_type.api_group,
                            version=resource_type.api_version,
                            plural=resource_type.plural,
                            label_selector=label_selector or ""
                        )
                else:
                    response = custom_api.list_cluster_custom_object(
                        group=resource_type.api_group,
                        version=resource_type.api_version,
                        plural=resource_type.plural,
                        label_selector=label_selector or ""
                    )

                resources = response.get('items', [])
            else:
                resources = self._fetch_core_resource(api_client, resource_type, namespace, label_selector)

            result = []
            for item in resources:
                resource_dict = None
                if hasattr(item, 'to_dict'):
                    resource_dict = item.to_dict()
                elif isinstance(item, dict):
                    resource_dict = item
                else:
                    logger.warning(f"Unknown item type: {type(item)}")
                    continue

                if resource_dict:
                    if not resource_dict.get('kind'):
                        resource_dict['kind'] = resource_type.kind
                    if not resource_dict.get('api_version') and not resource_dict.get('apiVersion'):
                        if resource_type.api_group:
                            resource_dict['apiVersion'] = f"{resource_type.api_group}/{resource_type.api_version}"
                        else:
                            resource_dict['apiVersion'] = resource_type.api_version
                    result.append(resource_dict)

            return result

        except ApiException as e:
            logger.error(f"Failed to fetch {resource_type.kind}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error fetching {resource_type.kind}: {e}")
            raise

    def _fetch_core_resource(
        self,
        api_client: client.ApiClient,
        resource_type: K8sResourceType,
        namespace: str | None,
        label_selector: str | None
    ) -> list:
        """Fetch core Kubernetes resources using standard APIs (dynamic method dispatch)."""
        api_class = API_GROUP_CLIENTS.get(resource_type.api_group)
        if not api_class:
            raise ValueError(f"Unsupported API group for core resources: {resource_type.api_group}")

        api_instance = api_class(api_client)

        kind_snake = _kind_to_snake(resource_type.kind)

        if resource_type.namespaced:
            if namespace:
                method_name = f"list_namespaced_{kind_snake}"
            else:
                method_name = f"list_{kind_snake}_for_all_namespaces"
        else:
            method_name = f"list_{kind_snake}"

        if not hasattr(api_instance, method_name):
            raise ValueError(f"Method {method_name} not found on {api_class.__name__}.")

        list_method = getattr(api_instance, method_name)

        if resource_type.namespaced and namespace:
            response = list_method(namespace=namespace, label_selector=label_selector or "")
        else:
            response = list_method(label_selector=label_selector or "")

        return response.items

    # ========================================================================
    # Resource Management (Write Operations)
    # ========================================================================

    def create_resource(
        self,
        cluster_id: int,
        resource_type_key: str,
        resource_yaml: str,
        namespace: str | None = None,
        dry_run: bool = False
    ) -> dict[str, Any]:
        """Create a Kubernetes resource from YAML."""
        import yaml as yaml_lib

        try:
            resource_dict = yaml_lib.safe_load(resource_yaml)
        except Exception as e:
            raise ValueError(f"Invalid YAML: {e}")

        if not resource_dict:
            raise ValueError("Empty resource definition")

        resource_type = resolve_resource_type(self.db, cluster_id, resource_type_key)
        cluster = self.get_cluster(cluster_id)
        api_client = self.load_kubeconfig(cluster)

        if resource_type.namespaced and not namespace:
            namespace = resource_dict.get("metadata", {}).get("namespace") or cluster.default_namespace or "default"

        dry_run_param = "All" if dry_run else None

        try:
            if resource_type.api_group not in API_GROUP_CLIENTS:
                custom_api = client.CustomObjectsApi(api_client)
                if resource_type.namespaced:
                    result = custom_api.create_namespaced_custom_object(
                        group=resource_type.api_group, version=resource_type.api_version,
                        namespace=namespace, plural=resource_type.plural,
                        body=resource_dict, dry_run=dry_run_param
                    )
                else:
                    result = custom_api.create_cluster_custom_object(
                        group=resource_type.api_group, version=resource_type.api_version,
                        plural=resource_type.plural, body=resource_dict, dry_run=dry_run_param
                    )
            else:
                result = self._write_typed_resource(
                    "create", api_client, resource_type, namespace,
                    body=resource_dict, dry_run=dry_run_param
                )

            return {
                "success": True,
                "message": f"{'Dry-run: ' if dry_run else ''}Resource created successfully",
                "resource": result
            }
        except ApiException as e:
            logger.error(f"Failed to create {resource_type.kind}: {e}")
            raise ValueError(f"Kubernetes API error: {e.reason}")
        except Exception as e:
            logger.error(f"Unexpected error creating {resource_type.kind}: {e}")
            raise

    def update_resource(
        self,
        cluster_id: int,
        resource_type_key: str,
        resource_name: str,
        resource_yaml: str,
        namespace: str | None = None,
        dry_run: bool = False
    ) -> dict[str, Any]:
        """Update an existing Kubernetes resource from YAML."""
        import yaml as yaml_lib

        try:
            resource_dict = yaml_lib.safe_load(resource_yaml)
        except Exception as e:
            raise ValueError(f"Invalid YAML: {e}")

        if not resource_dict:
            raise ValueError("Empty resource definition")

        resource_type = resolve_resource_type(self.db, cluster_id, resource_type_key)
        cluster = self.get_cluster(cluster_id)
        api_client = self.load_kubeconfig(cluster)

        if resource_type.namespaced and not namespace:
            namespace = resource_dict.get("metadata", {}).get("namespace") or cluster.default_namespace or "default"

        dry_run_param = "All" if dry_run else None

        try:
            if resource_type.api_group not in API_GROUP_CLIENTS:
                custom_api = client.CustomObjectsApi(api_client)
                if resource_type.namespaced:
                    result = custom_api.replace_namespaced_custom_object(
                        group=resource_type.api_group, version=resource_type.api_version,
                        namespace=namespace, plural=resource_type.plural,
                        name=resource_name, body=resource_dict, dry_run=dry_run_param
                    )
                else:
                    result = custom_api.replace_cluster_custom_object(
                        group=resource_type.api_group, version=resource_type.api_version,
                        plural=resource_type.plural, name=resource_name,
                        body=resource_dict, dry_run=dry_run_param
                    )
            else:
                result = self._write_typed_resource(
                    "replace", api_client, resource_type, namespace,
                    name=resource_name, body=resource_dict, dry_run=dry_run_param
                )

            return {
                "success": True,
                "message": f"{'Dry-run: ' if dry_run else ''}Resource updated successfully",
                "resource": result
            }
        except ApiException as e:
            logger.error(f"Failed to update {resource_type.kind}/{resource_name}: {e}")
            raise ValueError(f"Kubernetes API error: {e.reason}")
        except Exception as e:
            logger.error(f"Unexpected error updating {resource_type.kind}/{resource_name}: {e}")
            raise

    def delete_resource(
        self,
        cluster_id: int,
        resource_type_key: str,
        resource_name: str,
        namespace: str | None = None,
        dry_run: bool = False
    ) -> dict[str, Any]:
        """Delete a Kubernetes resource."""
        resource_type = resolve_resource_type(self.db, cluster_id, resource_type_key)
        cluster = self.get_cluster(cluster_id)
        api_client = self.load_kubeconfig(cluster)

        if resource_type.namespaced and not namespace:
            namespace = cluster.default_namespace or "default"

        dry_run_param = "All" if dry_run else None

        try:
            if resource_type.api_group not in API_GROUP_CLIENTS:
                custom_api = client.CustomObjectsApi(api_client)
                if resource_type.namespaced:
                    custom_api.delete_namespaced_custom_object(
                        group=resource_type.api_group, version=resource_type.api_version,
                        namespace=namespace, plural=resource_type.plural,
                        name=resource_name, dry_run=dry_run_param
                    )
                else:
                    custom_api.delete_cluster_custom_object(
                        group=resource_type.api_group, version=resource_type.api_version,
                        plural=resource_type.plural, name=resource_name, dry_run=dry_run_param
                    )
            else:
                self._write_typed_resource(
                    "delete", api_client, resource_type, namespace,
                    name=resource_name, dry_run=dry_run_param
                )

            return {
                "success": True,
                "message": f"{'Dry-run: ' if dry_run else ''}Resource deleted successfully"
            }
        except ApiException as e:
            logger.error(f"Failed to delete {resource_type.kind}/{resource_name}: {e}")
            raise ValueError(f"Kubernetes API error: {e.reason}")
        except Exception as e:
            logger.error(f"Unexpected error deleting {resource_type.kind}/{resource_name}: {e}")
            raise

    def _write_typed_resource(self, verb: str, api_client, resource_type, namespace, **kwargs):
        """
        Dispatch a write operation (create/replace/delete) to the correct typed K8s client.

        Uses dynamic snake_case method name construction, mirroring how _fetch_core_resource
        handles reads. Falls back to None return for verbs that don't return a resource body
        (e.g. delete).
        """
        api_class = API_GROUP_CLIENTS[resource_type.api_group]
        api_instance = api_class(api_client)
        kind_snake = _kind_to_snake(resource_type.kind)

        if resource_type.namespaced:
            method_name = f"{verb}_namespaced_{kind_snake}"
            if namespace:
                kwargs["namespace"] = namespace
        else:
            method_name = f"{verb}_{kind_snake}"

        method = getattr(api_instance, method_name, None)
        if method is None:
            raise ValueError(f"Method {method_name} not found on {api_class.__name__}")

        result = method(**kwargs)
        if result is not None and hasattr(result, "to_dict"):
            return result.to_dict()
        return result
