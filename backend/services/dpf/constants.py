"""
DPF service constants — resource types and CRD group identifiers.

All DPF CRD types that can be fetched from a cluster.
Keys match the RESOURCE_REGISTRY keys in core/k8s_resource_registry.py.
"""

# DPF resource type keys for parallel fetching (all registered in RESOURCE_REGISTRY)
DPF_RESOURCE_TYPES: list[str] = [
    # Provisioning (provisioning.dpu.nvidia.com)
    "dpfoperatorconfig",
    "dpudevice",
    "dpuset",
    "dpucluster",
    "dpu",
    "bfb",
    "dpuflavor",
    "dpunode",
    "dpudiscovery",
    "dpunodemaintenance",
    # Services (svc.dpu.nvidia.com)
    "dpuservice",
    "dpudeployment",
    "dpuservicechain",
    "dpuserviceinterface",
    "dpuserviceipam",
    "dpuservicecredreq",
    "servicechain",
    "serviceinterface",
    # Operator (operator.dpu.nvidia.com) — already in provisioning list above
]

# Subset for lightweight detection (just check if DPF is installed)
DPF_DETECT_TYPES: list[str] = [
    "dpfoperatorconfig",
    "dpudevice",
    "dpucluster",
]
