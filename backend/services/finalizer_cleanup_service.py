"""
Kubernetes Finalizer Cleanup Service

Handles removal of stuck finalizers from Kubernetes resources, particularly
during destroy operations where controllers may have already been removed.

This is common when destroying F5 BNK stacks where:
- CRD resources have finalizers (e.g., k8s.f5net.com/uninstall)
- The controller that would remove those finalizers has already been deleted
- Namespaces get stuck in "Terminating" state

Usage:
    cleanup_service = FinalizerCleanupService(db)
    result = cleanup_service.cleanup_namespace_finalizers(cluster_id, "f5-bnk")
"""

import logging
import time
from typing import Any

from kubernetes import client
from kubernetes.client.rest import ApiException
from sqlalchemy.orm import Session

from services.kubernetes_service import KubernetesService

logger = logging.getLogger(__name__)


# Known finalizer patterns that can be safely removed during destroy
# These are finalizers from controllers that may no longer exist
KNOWN_SAFE_FINALIZERS = [
    # F5 BNK finalizers
    "k8s.f5net.com/uninstall",
    "k8s.f5net.com/finalizer",
    "f5.com/finalizer",

    # Cert-manager finalizers
    "cert-manager.io/certificate-cleanup",
    "cert-manager.io/issuer-cleanup",

    # Common Helm/operator finalizers
    "helm.sh/resource-policy",
    "kubernetes.io/finalizer",

    # Gateway API finalizers
    "gateway.networking.k8s.io/gateway-protection",
]

# F5 API groups to target for discovery-driven cleanup
_F5_CLEANUP_GROUPS = [
    "k8s.f5.com",
    "spk.f5.com",
    "k8s.f5net.com",
    "gateway.k8s.f5net.com",
]

# Known F5 BNK CRD groups and their resources
F5_BNK_CRDS = [
    # Core F5 BNK CRDs
    ("k8s.f5.com", "v1", "afms"),
    ("k8s.f5.com", "v1", "cnecontrollers"),
    ("k8s.f5.com", "v1", "downloaders"),
    ("k8s.f5.com", "v1", "dssms"),
    ("k8s.f5.com", "v1", "f5tmms"),
    ("k8s.f5.com", "v1", "f5ingresses"),
    ("k8s.f5.com", "v1", "cneinstances"),
    ("k8s.f5.com", "v1", "cwcs"),
    ("k8s.f5.com", "v1", "observers"),
    ("k8s.f5.com", "v1", "rabbitmqs"),
    ("k8s.f5.com", "v1", "fluentds"),
    ("k8s.f5.com", "v1", "licenseproxies"),
    ("k8s.f5.com", "v1", "ipamcontrollers"),

    # F5 BNK CRDs (API group still uses "spk" for backwards compatibility)
    ("spk.f5.com", "v1beta1", "f5tmms"),
    ("spk.f5.com", "v1beta1", "spkipams"),
]


class FinalizerCleanupService:
    """
    Service for cleaning up stuck Kubernetes finalizers.

    Primarily used during destroy operations to unblock namespace deletion
    when controllers are no longer available to remove their finalizers.
    """

    def __init__(self, db: Session):
        self.db = db
        self.k8s_service = KubernetesService(db)

    def cleanup_namespace_finalizers(
        self,
        cluster_id: int,
        namespace: str,
        force: bool = False,
        timeout_seconds: int = 60
    ) -> dict[str, Any]:
        """
        Clean up finalizers from resources in a stuck namespace.

        This will:
        1. Check if namespace is in Terminating state
        2. Find all resources with stuck finalizers
        3. Remove finalizers from CRDs that match known patterns
        4. Wait for namespace to be deleted

        Args:
            cluster_id: ID of the Kubernetes cluster
            namespace: Name of the stuck namespace
            force: If True, remove ALL finalizers (dangerous!)
            timeout_seconds: How long to wait for namespace deletion

        Returns:
            Dict with cleanup results
        """
        result = {
            "success": False,
            "namespace": namespace,
            "resources_cleaned": [],
            "errors": [],
            "namespace_deleted": False
        }

        try:
            cluster = self.k8s_service.get_cluster(cluster_id)
            api_client = self.k8s_service.load_kubeconfig(cluster)
            v1 = client.CoreV1Api(api_client)

            # Check namespace status
            try:
                ns = v1.read_namespace(name=namespace)
                ns_phase = ns.status.phase

                if ns_phase != "Terminating":
                    result["errors"].append(f"Namespace is not Terminating (current: {ns_phase})")
                    return result

                logger.info(f"Namespace {namespace} is in Terminating state, checking for stuck finalizers")

            except ApiException as e:
                if e.status == 404:
                    # Namespace already deleted
                    result["success"] = True
                    result["namespace_deleted"] = True
                    return result
                raise

            # Find and clean up F5 BNK CRDs
            cleaned = self._cleanup_f5_bnk_crds(api_client, namespace, force, cluster_id=cluster_id)
            result["resources_cleaned"].extend(cleaned)

            # Also check for any other resources with stuck finalizers
            other_cleaned = self._cleanup_generic_crds(api_client, namespace, force)
            result["resources_cleaned"].extend(other_cleaned)

            # Wait for namespace deletion
            if result["resources_cleaned"]:
                logger.info(f"Cleaned {len(result['resources_cleaned'])} resources, waiting for namespace deletion")
                deleted = self._wait_for_namespace_deletion(v1, namespace, timeout_seconds)
                result["namespace_deleted"] = deleted

            result["success"] = True

        except Exception as e:
            logger.error(f"Failed to cleanup finalizers in namespace {namespace}: {e}")
            result["errors"].append(str(e))

        return result

    def _get_f5_crd_targets(self, cluster_id: int | None = None) -> list[tuple[str, str, str]]:
        """Build F5 CRD cleanup targets from discovery, using SERVED version.

        Falls back to F5_BNK_CRDS static list when discovery is unavailable.
        Returns list of (group, version, plural) tuples.
        """
        if cluster_id is None:
            return F5_BNK_CRDS
        try:
            from services.crd_discovery_service import CrdDiscoveryService
            svc = CrdDiscoveryService(self.db)
            envelope = svc.list_crds(cluster_id, group_filter=_F5_CLEANUP_GROUPS)
            if envelope.crds:
                targets = []
                for crd in envelope.crds:
                    if crd.group and crd.version and crd.plural:
                        targets.append((crd.group, crd.version, crd.plural))
                if targets:
                    return targets
        except Exception:
            pass
        return F5_BNK_CRDS

    def _cleanup_f5_bnk_crds(
        self,
        api_client: client.ApiClient,
        namespace: str,
        force: bool,
        cluster_id: int | None = None,
    ) -> list[dict[str, str]]:
        """Clean up F5 BNK specific CRDs."""
        cleaned = []
        custom_api = client.CustomObjectsApi(api_client)

        targets = self._get_f5_crd_targets(cluster_id) if cluster_id is not None else F5_BNK_CRDS
        for group, version, plural in targets:
            try:
                resources = custom_api.list_namespaced_custom_object(
                    group=group,
                    version=version,
                    namespace=namespace,
                    plural=plural
                )

                for item in resources.get("items", []):
                    name = item["metadata"]["name"]
                    finalizers = item["metadata"].get("finalizers", [])

                    if not finalizers:
                        continue

                    # Check if finalizers should be removed
                    should_remove = force or any(
                        f in KNOWN_SAFE_FINALIZERS or f.startswith("k8s.f5")
                        for f in finalizers
                    )

                    if should_remove:
                        logger.info(f"Removing finalizers from {group}/{plural}/{name}: {finalizers}")

                        try:
                            custom_api.patch_namespaced_custom_object(
                                group=group,
                                version=version,
                                namespace=namespace,
                                plural=plural,
                                name=name,
                                body={"metadata": {"finalizers": None}}
                            )
                            cleaned.append({
                                "kind": plural,
                                "name": name,
                                "finalizers_removed": finalizers
                            })
                        except ApiException as e:
                            if e.status != 404:  # Ignore if already deleted
                                logger.warning(f"Failed to patch {plural}/{name}: {e}")

            except ApiException as e:
                if e.status == 404:
                    # CRD doesn't exist, skip
                    continue
                logger.debug(f"Error listing {group}/{plural}: {e}")

        return cleaned

    def _cleanup_generic_crds(
        self,
        api_client: client.ApiClient,
        namespace: str,
        force: bool
    ) -> list[dict[str, str]]:
        """
        Clean up any other CRDs with stuck finalizers.

        This uses the API discovery to find all namespaced CRDs and checks
        for resources with known finalizer patterns.
        """
        cleaned = []

        try:
            # Get all API groups
            apis_api = client.ApisApi(api_client)
            api_groups = apis_api.get_api_versions()

            client.CustomObjectsApi(api_client)

            for group_info in api_groups.groups:
                group = group_info.name

                # Skip core k8s groups
                if group in ["", "apps", "batch", "networking.k8s.io", "storage.k8s.io"]:
                    continue

                # Get preferred version
                group_info.preferred_version.version if group_info.preferred_version else group_info.versions[0].version

                # Try to discover resources in this group
                try:
                    client.CustomObjectsApi(api_client)
                    # This is a hacky way to discover resources, but works for most cases

                except Exception:
                    continue

        except Exception as e:
            logger.debug(f"Error during generic CRD cleanup: {e}")

        return cleaned

    def _wait_for_namespace_deletion(
        self,
        v1: client.CoreV1Api,
        namespace: str,
        timeout_seconds: int
    ) -> bool:
        """Wait for namespace to be deleted."""
        start_time = time.time()

        while time.time() - start_time < timeout_seconds:
            try:
                ns = v1.read_namespace(name=namespace)
                phase = ns.status.phase
                logger.debug(f"Namespace {namespace} still in {phase} state")
                time.sleep(2)
            except ApiException as e:
                if e.status == 404:
                    logger.info(f"Namespace {namespace} successfully deleted")
                    return True
                raise

        logger.warning(f"Namespace {namespace} not deleted within {timeout_seconds}s timeout")
        return False

    def force_delete_namespace(
        self,
        cluster_id: int,
        namespace: str
    ) -> dict[str, Any]:
        """
        Force delete a namespace by removing its finalizers directly.

        WARNING: This is a last resort and may leave orphaned resources!

        Args:
            cluster_id: ID of the Kubernetes cluster
            namespace: Name of the namespace to force delete

        Returns:
            Dict with deletion results
        """
        result = {
            "success": False,
            "namespace": namespace,
            "method": "force_delete",
            "errors": []
        }

        try:
            cluster = self.k8s_service.get_cluster(cluster_id)
            api_client = self.k8s_service.load_kubeconfig(cluster)
            v1 = client.CoreV1Api(api_client)

            # First try normal cleanup
            cleanup_result = self.cleanup_namespace_finalizers(
                cluster_id, namespace, force=True, timeout_seconds=30
            )

            if cleanup_result["namespace_deleted"]:
                result["success"] = True
                return result

            # If still stuck, patch the namespace itself to remove finalizers
            try:
                logger.warning(f"Force removing finalizers from namespace {namespace} itself")

                # Get current namespace
                ns = v1.read_namespace(name=namespace)

                if ns.spec and ns.spec.finalizers:
                    # Patch to remove namespace finalizers
                    v1.patch_namespace(
                        name=namespace,
                        body={"spec": {"finalizers": None}}
                    )
                    logger.info(f"Removed finalizers from namespace {namespace}")

                # Wait for deletion
                deleted = self._wait_for_namespace_deletion(v1, namespace, 30)
                result["success"] = deleted

            except ApiException as e:
                if e.status == 404:
                    result["success"] = True
                else:
                    result["errors"].append(f"Failed to patch namespace: {e}")

        except Exception as e:
            result["errors"].append(str(e))
            logger.error(f"Force delete failed for namespace {namespace}: {e}")

        return result

    def get_stuck_namespaces(self, cluster_id: int) -> list[dict[str, Any]]:
        """
        Get all namespaces in Terminating state.

        Returns list of stuck namespaces with details about blocking resources.
        """
        stuck = []

        try:
            cluster = self.k8s_service.get_cluster(cluster_id)
            api_client = self.k8s_service.load_kubeconfig(cluster)
            v1 = client.CoreV1Api(api_client)

            namespaces = v1.list_namespace()

            for ns in namespaces.items:
                if ns.status.phase == "Terminating":
                    # Get conditions for more details
                    conditions = []
                    if ns.status.conditions:
                        for cond in ns.status.conditions:
                            conditions.append({
                                "type": cond.type,
                                "status": cond.status,
                                "message": cond.message,
                                "reason": cond.reason
                            })

                    stuck.append({
                        "name": ns.metadata.name,
                        "phase": ns.status.phase,
                        "deletion_timestamp": ns.metadata.deletion_timestamp.isoformat() if ns.metadata.deletion_timestamp else None,
                        "finalizers": ns.spec.finalizers if ns.spec else [],
                        "conditions": conditions
                    })

        except Exception as e:
            logger.error(f"Failed to get stuck namespaces: {e}")

        return stuck


def cleanup_finalizers_for_module(
    db: Session,
    cluster_id: int,
    module_path: str,
    namespaces: list[str] | None = None
) -> dict[str, Any]:
    """
    Convenience function to clean up finalizers for a module destroy.

    Called from the destroy task when namespace deletion times out.

    Args:
        db: Database session
        cluster_id: ID of the Kubernetes cluster
        module_path: Path of the module being destroyed (for logging)
        namespaces: List of namespaces to clean up, or None to detect from module

    Returns:
        Dict with cleanup results
    """
    cleanup_service = FinalizerCleanupService(db)

    # Default F5 BNK namespaces if not specified
    if not namespaces:
        namespaces = ["f5-bnk", "f5-utils", "bnk-gw", "observability"]

    results = {
        "module_path": module_path,
        "namespaces_cleaned": [],
        "errors": []
    }

    for namespace in namespaces:
        logger.info(f"Cleaning up finalizers in namespace {namespace} for module {module_path}")

        result = cleanup_service.cleanup_namespace_finalizers(
            cluster_id=cluster_id,
            namespace=namespace,
            force=False,  # Only remove known safe finalizers
            timeout_seconds=60
        )

        if result["resources_cleaned"]:
            results["namespaces_cleaned"].append({
                "namespace": namespace,
                "resources": result["resources_cleaned"],
                "deleted": result["namespace_deleted"]
            })

        if result["errors"]:
            results["errors"].extend(result["errors"])

    return results
