"""
Kubernetes resource type definitions for BNK-Forge.
Defines metadata for different types of Kubernetes resources.
"""

from dataclasses import dataclass

# =============================================================================
# API GROUP CONSTANTS - DRY principle for commonly used API groups
# =============================================================================

class ApiGroups:
    """Common Kubernetes API groups."""
    CORE = ""  # Core API (v1)
    APPS = "apps"
    BATCH = "batch"
    NETWORKING = "networking.k8s.io"
    STORAGE = "storage.k8s.io"
    RBAC = "rbac.authorization.k8s.io"
    AUTOSCALING = "autoscaling"
    POLICY = "policy"
    APIEXTENSIONS = "apiextensions.k8s.io"
    # Gateway API
    GATEWAY = "gateway.networking.k8s.io"
    # F5 BNK
    F5_NET = "k8s.f5net.com"        # Data-plane CRDs: fw-policies, vlans, irules, etc.
    F5_K8S = "k8s.f5.com"           # FLO-managed CRDs: CNEInstance, Analyzer, etc.
    F5_GATEWAY_NET = "gateway.k8s.f5net.com"  # Gateway extensions: BNKSecPolicy, BNKNetPolicy, L4Route
    F5_IPAM = "fic.f5.com"          # F5 IPAM Controller CRDs: IPAMRange, IPAM
    # NVIDIA DPF (DOCA Platform Framework)
    DPF_PROVISIONING = "provisioning.dpu.nvidia.com"  # DPUDevice, DPUSet, DPUCluster, BFB, etc.
    DPF_SERVICE = "svc.dpu.nvidia.com"                # DPUService, DPUServiceChain, etc.
    DPF_OPERATOR = "operator.dpu.nvidia.com"          # DPFOperatorConfig
    # Third-party
    CERT_MANAGER = "cert-manager.io"
    MULTUS = "k8s.cni.cncf.io"
    # F5 CIS (Container Ingress Services) — classic BIG-IP integration (D-023)
    F5_CIS = "cis.f5.com"
    # WAF PLM CRDs: APPolicy, APLogConf, APSignatures, APUserSig
    APPPROTECT = "appprotect.f5.com"


class ResourceCategory:
    """Categories for UI grouping."""
    CORE = "core"
    WORKLOADS = "workloads"
    NETWORKING = "networking"
    STORAGE = "storage"
    CONFIG = "config"
    RBAC = "rbac"
    BATCH = "batch"
    AUTOSCALING = "autoscaling"
    GATEWAY_API = "gateway-api"
    F5_BNK = "f5-bnk"
    CERT_MANAGER = "cert-manager"
    DPF = "dpf"
    CLUSTER = "cluster"
    F5_CIS = "f5-cis"  # F5 Container Ingress Services (D-023)
    WAF = "waf"


@dataclass
class K8sResourceType:
    """Definition of a Kubernetes resource type."""
    api_group: str
    api_version: str
    kind: str
    plural: str
    namespaced: bool
    display_name: str
    description: str | None = None
    category: str = ResourceCategory.CORE

    @property
    def full_api_version(self) -> str:
        """Get the full API version (e.g., 'gateway.networking.k8s.io/v1')."""
        if self.api_group:
            return f"{self.api_group}/{self.api_version}"
        return self.api_version

    @property
    def resource_id(self) -> str:
        """Get a unique identifier for this resource type."""
        if self.api_group:
            return f"{self.api_group}_{self.plural}"
        return self.plural
