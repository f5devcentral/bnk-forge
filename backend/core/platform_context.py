"""Canonical platform-context vocabulary shared across backend layers."""

from enum import StrEnum


class PlatformProfile(StrEnum):
    GENERIC_ONPREM = "generic_onprem"
    EKS = "eks"
    AKS = "aks"
    GKE = "gke"
    ROKS = "roks"
    OCP = "ocp"
    UNKNOWN = "unknown"


class PlatformProvider(StrEnum):
    AWS = "aws"
    AZURE = "azure"
    GCP = "gcp"
    IBM = "ibm"
    BAREMETAL = "baremetal"
    VMWARE = "vmware"
    OTHER = "other"
    UNKNOWN = "unknown"


class ManagementBoundary(StrEnum):
    CUSTOMER_MANAGED_CLUSTER = "customer_managed_cluster"
    FORGE_MANAGED_COMPONENTS_ONLY = "forge_managed_components_only"
    UNKNOWN = "unknown"


PLATFORM_PROFILE_VALUES = tuple(v.value for v in PlatformProfile)
PLATFORM_PROVIDER_VALUES = tuple(v.value for v in PlatformProvider)
MANAGEMENT_BOUNDARY_VALUES = tuple(v.value for v in ManagementBoundary)

PLATFORM_CAPABILITY_KEYS = (
    "secondary_networks",
    "network_attachment_definitions",
    "sriov",
    "hugepages",
    "gateway_api",
    "openshift_routes",
    "scc",
    "machine_config",
    "operator_framework",
    "cloud_load_balancer",
)

PLATFORM_CONSTRAINT_KEYS = (
    "managed_control_plane",
    "cluster_lifecycle_external",
    "operator_required_for_networking",
    "privileged_workloads_require_platform_security_binding",
    "direct_node_package_mutation_not_supported",
)
