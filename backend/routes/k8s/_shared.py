"""
Shared helpers, serializers, and Pydantic models used across the k8s route modules.

Extracted from the monolithic kubernetes.py to avoid duplication.
"""

import logging
import subprocess

import yaml
from pydantic import BaseModel, model_validator

from models import KubernetesCluster
from services.platform_context_service import PlatformContextService
from utils.provider_config import normalize_cloud_provider
from utils.validators import validate_aws_region

logger = logging.getLogger(__name__)


# ============================================================================
# DRY Serialization Helpers
# ============================================================================

def serialize_cluster(cluster: KubernetesCluster, include_project_id: bool = True) -> dict:
    """
    Serialize a KubernetesCluster to dict.
    DRY helper to avoid repeated serialization code.
    """
    platform_context = PlatformContextService.serialize_cluster_context(cluster)

    result = {
        "id": cluster.id,
        "name": cluster.name,
        "context": cluster.context,
        "api_server": cluster.api_server,
        "cloud_provider": cluster.cloud_provider,
        "detected_platform_profile": platform_context.detected_platform_profile,
        "detected_platform_provider": platform_context.detected_platform_provider,
        "platform_capabilities": platform_context.platform_capabilities,
        "platform_constraints": platform_context.platform_constraints,
        "region": cluster.region,
        "default_namespace": cluster.default_namespace,
        "status": cluster.status,
        "version": cluster.version,
        "last_synced_at": cluster.last_synced_at.isoformat() if cluster.last_synced_at else None,
        "created_at": cluster.created_at.isoformat() if cluster.created_at else None,
        # SSH tunnel per-cluster config
        "ssh_tunnel_enabled": cluster.ssh_tunnel_enabled,
        "ssh_remote_k8s_host": cluster.ssh_remote_k8s_host,
        "ssh_remote_k8s_port": cluster.ssh_remote_k8s_port,
        # First-class SSH credential
        "ssh_credential_id": cluster.ssh_credential_id,
        "ssh_host_override": cluster.ssh_host_override,
        # Per-cluster prereq selection (NULL → defaults; locked entries are
        # always included in the effective set).
        "enabled_prerequisites": cluster.enabled_prerequisites,
    }
    if include_project_id:
        result["project_id"] = cluster.project_id
    return result


def run_kubectl(cmd, timeout: int = 30):
    """Execute kubectl command and return parsed YAML output.

    Args:
        cmd: Command list to execute
        timeout: Maximum seconds to wait (default 30s, see ADR-019)
    """
    try:
        output = subprocess.check_output(cmd, text=True, timeout=timeout)
        return yaml.safe_load(output)
    except subprocess.TimeoutExpired:
        logger.warning(f"kubectl command timed out after {timeout}s: {' '.join(cmd[:3])}...")
        return None
    except (subprocess.CalledProcessError, FileNotFoundError, Exception) as e:
        logger.warning(f"kubectl command failed: {e}")
        return None


# ============================================================================
# Pydantic Request Models — Cluster CRUD
# ============================================================================

class ClusterCreateRequest(BaseModel):
    """Request model for creating a Kubernetes cluster configuration."""
    name: str
    kubeconfig: str  # Base64 encoded kubeconfig YAML
    cloud_provider: str | None = None  # aws, azure, gcp, ibm, on-prem, other
    region: str | None = None
    context: str | None = None  # kubectl context name (optional, will auto-detect)
    default_namespace: str | None = "default"
    # SSH tunnel: opt-in per-cluster toggle + remote K8s endpoint
    ssh_tunnel_enabled: bool | None = False
    ssh_remote_k8s_host: str | None = "localhost"
    ssh_remote_k8s_port: int | None = 6443
    ssh_credential_id: int | None = None  # First-class SSH credential
    # Optional: override the SSH endpoint host — use a different host than the credential's host
    # (e.g. kind cluster on a directly-reachable node that shares the same SSH key)
    ssh_host_override: str | None = None

    @model_validator(mode="after")
    def _validate_region(self):
        # Canonicalize provider case so "IBM" == "ibm" everywhere downstream.
        self.cloud_provider = normalize_cloud_provider(self.cloud_provider)
        if self.cloud_provider in {"aws", "eks"}:
            validate_aws_region(self.region, field_name="region")
        return self


class ClusterUpdateRequest(BaseModel):
    """Request model for updating a Kubernetes cluster configuration."""
    name: str | None = None
    kubeconfig: str | None = None  # Base64 encoded kubeconfig YAML
    cloud_provider: str | None = None
    region: str | None = None
    context: str | None = None
    default_namespace: str | None = None
    # SSH tunnel
    ssh_tunnel_enabled: bool | None = None
    ssh_remote_k8s_host: str | None = None
    ssh_remote_k8s_port: int | None = None
    ssh_credential_id: int | None = None  # First-class SSH credential
    ssh_host_override: str | None = None
    # Per-cluster prereq selection — list of prereq IDs (e.g.
    # ["cert-manager", "multus", "storage"]). Pass an empty list to disable
    # all optional prereqs; pass None to leave the cluster's setting alone.
    enabled_prerequisites: list[str] | None = None

    @model_validator(mode="after")
    def _validate_region(self):
        # Canonicalize provider case so "IBM" == "ibm" everywhere downstream.
        self.cloud_provider = normalize_cloud_provider(self.cloud_provider)
        if self.cloud_provider in {"aws", "eks"}:
            validate_aws_region(self.region, field_name="region")
        return self


# ============================================================================
# Pydantic Request Models — Resource Operations
# ============================================================================

class ResourceCreateRequest(BaseModel):
    """Request model for creating a Kubernetes resource."""
    resource_yaml: str  # YAML definition of the resource
    namespace: str | None = None
    dry_run: bool = False


class ResourceUpdateRequest(BaseModel):
    """Request model for updating a Kubernetes resource."""
    resource_yaml: str  # YAML definition of the resource
    namespace: str | None = None
    dry_run: bool = False


class ResourceDeleteRequest(BaseModel):
    """Request model for deleting a Kubernetes resource."""
    namespace: str | None = None
    dry_run: bool = False


class ScaleDeploymentRequest(BaseModel):
    """Request model for scaling a deployment."""
    replicas: int
    namespace: str


class ResourcePatchRequest(BaseModel):
    """Request model for patching a Kubernetes resource."""
    patch_data: dict
    namespace: str | None = None
    patch_type: str = "strategic"  # strategic, merge, or json


class LabelResourceRequest(BaseModel):
    """Request model for labeling a resource."""
    labels: dict
    namespace: str | None = None
    overwrite: bool = False


class AnnotateResourceRequest(BaseModel):
    """Request model for annotating a resource."""
    annotations: dict
    namespace: str | None = None
    overwrite: bool = False


# ============================================================================
# Pydantic Request Models — Cluster Scanner
# ============================================================================

class AdaptiveModuleRequest(BaseModel):
    """Request model for adaptive module selection."""
    template_slug: str | None = None         # Stack template to plan for (e.g., "f5-bnk-2.2")
    module_paths: list[str] | None = None    # Or an explicit list of module paths
    sizing_profile: str | None = None        # "lab" applies #387 part C NON-PRODUCTION f5-tmm sizing overrides
