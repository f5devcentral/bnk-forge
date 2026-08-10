"""
Scanner constants — prerequisite status values and expected CRD sets.
"""


class PrerequisiteStatus:
    """Status of a single prerequisite."""

    DETECTED = "detected"
    MISSING = "missing"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


# Expected cert-manager CRD kinds
CERT_MANAGER_EXPECTED_CRDS = frozenset(
    {"Certificate", "ClusterIssuer", "Issuer", "CertificateRequest", "Order", "Challenge"}
)

# Standard Gateway API CRD names
GATEWAY_API_STANDARD_CRDS = frozenset(
    {
        "gatewayclasses.gateway.networking.k8s.io",
        "gateways.gateway.networking.k8s.io",
        "httproutes.gateway.networking.k8s.io",
    }
)

# F5 CRD groups
F5_CRD_GROUPS = frozenset(
    {"k8s.f5.com", "k8s.f5net.com", "gateway.k8s.f5net.com", "fic.f5.com"}
)

# Gateway API CRD groups
GATEWAY_API_CRD_GROUPS = frozenset(
    {"gateway.networking.k8s.io", "gateway.k8s.f5net.com"}
)

# ---------------------------------------------------------------------------
# NVIDIA DPF (DOCA Platform Framework) CRD groups
# ---------------------------------------------------------------------------

DPF_CRD_GROUPS = frozenset(
    {
        "provisioning.dpu.nvidia.com",
        "svc.dpu.nvidia.com",
        "operator.dpu.nvidia.com",
    }
)

# Core DPF provisioning CRD names — minimum set for "DPF detected"
DPF_CORE_CRDS = frozenset(
    {
        "dpfoperatorconfigs.operator.dpu.nvidia.com",
        "dpudevices.provisioning.dpu.nvidia.com",
        "dpusets.provisioning.dpu.nvidia.com",
        "dpuclusters.provisioning.dpu.nvidia.com",
        "bfbs.provisioning.dpu.nvidia.com",
    }
)

# Service-layer CRD names — present when DPF services are configured
DPF_SERVICE_CRDS = frozenset(
    {
        "dpuservices.svc.dpu.nvidia.com",
        "dpuservicechains.svc.dpu.nvidia.com",
        "dpuserviceinterfaces.svc.dpu.nvidia.com",
    }
)

# ---------------------------------------------------------------------------
# Kamaji (multi-tenant Kubernetes control plane manager)
# ---------------------------------------------------------------------------

KAMAJI_CRD_GROUPS = frozenset({"kamaji.clastix.io"})

# Core Kamaji CRD names — minimum for "Kamaji detected"
KAMAJI_CORE_CRDS = frozenset(
    {
        "tenantcontrolplanes.kamaji.clastix.io",
        "datastores.kamaji.clastix.io",
    }
)


# ---------------------------------------------------------------------------
# Per-group CRD discovery targets
# ---------------------------------------------------------------------------

# The complete set of API groups the scanner prereq checks actually care
# about. Replaces the full list_custom_resource_definition() fetch (which can
# be 15+ MB on mature clusters) with targeted discovery calls against only
# these groups — one small `/apis/<group>` + `/apis/<group>/<version>` pair
# per group, parallelized. Anything outside this set can't influence the
# scanner's decisions, so there's no reason to download it.
SCANNER_RELEVANT_CRD_GROUPS: frozenset[str] = frozenset(
    {
        # cert-manager — checks for Certificate/Issuer/ClusterIssuer kinds.
        "cert-manager.io",
        # Multus CNI — checks for network-attachment-definitions CRD.
        "k8s.cni.cncf.io",
        # Gateway API core + F5 extensions (httproute, gateway, gatewayclass,
        # bnksecpolicy, bnknetpolicy, l4route, etc.).
        *GATEWAY_API_CRD_GROUPS,
        # F5 BNK data plane + FLO-managed CRDs (cneinstance, f5spkvlan,
        # f5bigfwpolicy, analyzer, fic IPAM, etc.).
        *F5_CRD_GROUPS,
        # NVIDIA DPF operator/provisioning/service CRDs (dpudevice, bfb,
        # dpuservice, etc.).
        *DPF_CRD_GROUPS,
        # Kamaji multi-tenant control plane (tenantcontrolplane, datastore).
        *KAMAJI_CRD_GROUPS,
        # F5 CIS (Container Ingress Services) — VirtualServer/TransportServer/IngressLink.
        # Gated in the scan: CIS CR enumeration only runs when cis.f5.com ∈ api_groups.
        "cis.f5.com",
    }
)
