"""
Scanner prerequisite analyzers — detect cert-manager, Multus, SR-IOV,
HugePages, storage classes, and Gateway API CRDs.

All functions are pure (dict in → dict out) with no I/O.
"""

import re
from typing import Any

from services.scanner.constants import (
    CERT_MANAGER_EXPECTED_CRDS,
    DPF_CORE_CRDS,
    DPF_CRD_GROUPS,
    DPF_SERVICE_CRDS,
    GATEWAY_API_CRD_GROUPS,
    GATEWAY_API_STANDARD_CRDS,
    KAMAJI_CORE_CRDS,
    KAMAJI_CRD_GROUPS,
    PrerequisiteStatus,
)
from services.scanner.nodes import is_kind_cluster, is_local_cluster

# ---------------------------------------------------------------------------
# Cluster Info
# ---------------------------------------------------------------------------


def analyze_cluster_info(
    cluster, version_info: dict | None, nodes: list[dict], namespaces: list[str]
) -> dict[str, Any]:
    """Analyze basic cluster information and distribution."""
    distribution = _detect_distribution(cluster, nodes)
    ready_nodes = [n for n in nodes if n.get("ready")]
    hp_nodes = [n for n in nodes if n.get("is_hp_node")]

    return {
        "version": version_info.get("git_version") if version_info else None,
        "major": version_info.get("major") if version_info else None,
        "minor": version_info.get("minor") if version_info else None,
        "platform": version_info.get("platform") if version_info else None,
        "distribution": distribution,
        "cloud_provider": cluster.cloud_provider,
        "region": cluster.region,
        "node_count": len(nodes),
        "nodes_ready": len(ready_nodes),
        "hp_nodes": len(hp_nodes),
        "hp_node_details": [
            {
                "name": n["name"],
                "instance_type": n.get("instance_type"),
                "zone": n.get("zone"),
                "ready": n.get("ready"),
            }
            for n in hp_nodes
        ],
        "namespaces": len(namespaces),
        # Cheap local/lab-cluster detection (kind, minikube, Docker Desktop) —
        # Node-API-only, no privileged probe. See node_readiness_service for
        # the privileged CNI-plugin / core_pattern probe (issue #387).
        "is_kind": is_kind_cluster(nodes),
        "is_local": is_local_cluster(nodes),
    }


# Cloud provider → distribution mapping (data-driven)
_CLOUD_DISTRIBUTION: dict[str, str] = {
    "eks": "EKS",
    "aws": "EKS",
    "aks": "AKS",
    "azure": "AKS",
    "gke": "GKE",
    "gcp": "GKE",
    "on-prem": "on-prem",
}

# Node label substring → distribution detection
_LABEL_DISTRIBUTION: list[tuple[str, str]] = [
    ("eks.amazonaws.com", "EKS"),
    ("cloud.google.com", "GKE"),
    ("kubernetes.azure.com", "AKS"),
    ("openshift.io", "OpenShift"),
]


def _detect_distribution(cluster, nodes: list[dict]) -> str:
    """Detect cluster distribution from cloud_provider or node labels."""
    distribution = _CLOUD_DISTRIBUTION.get(cluster.cloud_provider, "generic")

    context = (getattr(cluster, "context", None) or "").strip().lower()
    api_server = (getattr(cluster, "api_server", None) or "").strip().lower()
    if "openshift" in context or "okd" in context or "openshift" in api_server:
        return "OpenShift"

    if distribution == "generic" and nodes:
        for n in nodes:
            labels = n.get("labels", {})
            for substring, dist in _LABEL_DISTRIBUTION:
                if any(substring in label for label in labels):
                    return dist
    return distribution


# ---------------------------------------------------------------------------
# cert-manager
# ---------------------------------------------------------------------------


def analyze_cert_manager(
    crds: list[dict], cert_manager_pods: list[dict], helm_releases: list[dict]
) -> dict[str, Any]:
    """Detect cert-manager installation status."""
    cert_crds = [c for c in crds if c.get("group") == "cert-manager.io"]
    has_crds = len(cert_crds) > 0
    found_kinds = {c["kind"] for c in cert_crds}

    running_pods = [p for p in cert_manager_pods if p.get("phase") == "Running"]
    controller_pods = [
        p for p in running_pods
        if "cert-manager" in p.get("name", "")
        and "webhook" not in p.get("name", "")
        and "cainjector" not in p.get("name", "")
    ]
    webhook_pods = [p for p in running_pods if "webhook" in p.get("name", "")]
    cainjector_pods = [p for p in running_pods if "cainjector" in p.get("name", "")]

    version = _extract_cert_manager_version(cert_manager_pods)

    helm_release = next(
        (r for r in helm_releases if r.get("name") == "cert-manager"),
        None,
    )

    if has_crds and running_pods:
        status = PrerequisiteStatus.DETECTED
    elif has_crds or cert_manager_pods:
        status = PrerequisiteStatus.PARTIAL
    else:
        status = PrerequisiteStatus.MISSING

    return {
        "status": status,
        "version": version,
        "crds_installed": has_crds,
        "crd_count": len(cert_crds),
        "crd_kinds": sorted(found_kinds),
        "missing_crds": sorted(CERT_MANAGER_EXPECTED_CRDS - found_kinds) if has_crds else [],
        "pods": {
            "controller": len(controller_pods),
            "webhook": len(webhook_pods),
            "cainjector": len(cainjector_pods),
            "total_running": len(running_pods),
        },
        "helm_release": helm_release,
    }


def _extract_cert_manager_version(pods: list[dict]) -> str | None:
    """Extract cert-manager version from pod container images."""
    for pod in pods:
        for container in pod.get("containers", []):
            match = re.search(r"cert-manager.*?:v?([\d.]+)", container.get("image", ""))
            if match:
                return match.group(1)
    return None


# ---------------------------------------------------------------------------
# Multus CNI
# ---------------------------------------------------------------------------


def analyze_multus(
    crds: list[dict],
    crd_names: set[str],
    multus_pods: list[dict],
    daemonsets: list[dict],
) -> dict[str, Any]:
    """Detect Multus CNI installation.

    ``multus_pods`` are the pods fetched from the Multus DaemonSet's own
    namespace (kube-system on vanilla k8s, openshift-multus on ROKS/OpenShift —
    Issue #202), so the running-pod count reflects reality regardless of layout.
    """
    has_nad_crd = "network-attachment-definitions.k8s.cni.cncf.io" in crd_names

    multus_ds = [
        ds for ds in daemonsets if "multus" in ds.get("name", "").lower()
    ]
    running_multus_pods = [
        p
        for p in multus_pods
        if "multus" in p.get("name", "").lower() and p.get("phase") == "Running"
    ]

    multus_daemonset_info = None
    if multus_ds:
        ds = multus_ds[0]
        multus_daemonset_info = {
            "name": ds["name"],
            "namespace": ds["namespace"],
            "desired": ds["desired"],
            "ready": ds["ready"],
        }

    if has_nad_crd and (running_multus_pods or multus_ds):
        status = PrerequisiteStatus.DETECTED
    elif has_nad_crd:
        status = PrerequisiteStatus.PARTIAL
    else:
        status = PrerequisiteStatus.MISSING

    return {
        "status": status,
        "nad_crd_installed": has_nad_crd,
        "daemonset": multus_daemonset_info,
        "running_pods": len(running_multus_pods),
    }


# ---------------------------------------------------------------------------
# SR-IOV
# ---------------------------------------------------------------------------


def analyze_sriov(
    nodes: list[dict], daemonsets: list[dict], kube_system_pods: list[dict]
) -> dict[str, Any]:
    """Detect SR-IOV device plugin and VF availability."""
    sriov_ds = [
        ds
        for ds in daemonsets
        if "sriov" in ds.get("name", "").lower()
        or "device-plugin" in ds.get("name", "").lower()
    ]

    sriov_daemonset_info = None
    if sriov_ds:
        ds = sriov_ds[0]
        sriov_daemonset_info = {
            "name": ds["name"],
            "namespace": ds["namespace"],
            "desired": ds["desired"],
            "ready": ds["ready"],
            "images": ds.get("images", []),
        }

    nodes_with_sriov = []
    total_vfs = 0
    for n in nodes:
        sriov_alloc = n.get("allocatable", {}).get("sriov_resources", {})
        sriov_cap = n.get("capacity", {}).get("sriov_resources", {})
        if sriov_alloc or sriov_cap:
            vf_count = sum(int(v) for v in (sriov_cap or sriov_alloc).values() if v)
            total_vfs += vf_count
            nodes_with_sriov.append(
                {
                    "name": n["name"],
                    "resources": sriov_cap or sriov_alloc,
                    "vf_count": vf_count,
                    "instance_type": n.get("instance_type"),
                }
            )

    if sriov_ds and nodes_with_sriov:
        status = PrerequisiteStatus.DETECTED
    elif sriov_ds or nodes_with_sriov:
        status = PrerequisiteStatus.PARTIAL
    else:
        status = PrerequisiteStatus.MISSING

    return {
        "status": status,
        "device_plugin": sriov_daemonset_info,
        "nodes_with_vfs": len(nodes_with_sriov),
        "total_vfs": total_vfs,
        "node_details": nodes_with_sriov,
    }


# ---------------------------------------------------------------------------
# HugePages
# ---------------------------------------------------------------------------


def analyze_hugepages(nodes: list[dict]) -> dict[str, Any]:
    """Detect HugePages configuration on nodes."""
    nodes_with_hugepages = []
    for n in nodes:
        alloc = n.get("allocatable", {})
        hp_2mi = alloc.get("hugepages_2mi")
        hp_1gi = alloc.get("hugepages_1gi")

        # Explicit parens for clarity (and binds tighter than or)
        if (hp_2mi and hp_2mi != "0") or (hp_1gi and hp_1gi != "0"):
            nodes_with_hugepages.append(
                {
                    "name": n["name"],
                    "hugepages_2mi": hp_2mi,
                    "hugepages_1gi": hp_1gi,
                    "instance_type": n.get("instance_type"),
                    "is_hp_node": n.get("is_hp_node", False),
                }
            )

    status = PrerequisiteStatus.DETECTED if nodes_with_hugepages else PrerequisiteStatus.MISSING

    return {
        "status": status,
        "nodes_with_hugepages": len(nodes_with_hugepages),
        "node_details": nodes_with_hugepages,
    }


# ---------------------------------------------------------------------------
# Storage Classes
# ---------------------------------------------------------------------------


def analyze_storage(storage_classes: list[dict]) -> dict[str, Any]:
    """Analyze available storage classes."""
    default = next((sc for sc in storage_classes if sc.get("is_default")), None)
    has_gp3 = any(
        sc["name"] == "gp3" or "gp3" in sc.get("provisioner", "")
        for sc in storage_classes
    )
    has_gp2 = any(
        sc["name"] == "gp2" or "gp2" in sc.get("provisioner", "")
        for sc in storage_classes
    )

    status = PrerequisiteStatus.DETECTED if storage_classes else PrerequisiteStatus.MISSING

    return {
        "status": status,
        "count": len(storage_classes),
        "default": default.get("name") if default else None,
        "has_gp3": has_gp3,
        "has_gp2": has_gp2,
        "classes": storage_classes,
    }


# ---------------------------------------------------------------------------
# Gateway API
# ---------------------------------------------------------------------------


def analyze_gateway_api(
    crds: list[dict],
    crd_names: set[str],
    gateways: list,
    gatewayclasses: list,
) -> dict[str, Any]:
    """Detect Gateway API CRDs and resources."""
    gateway_crds = [
        c for c in crds if c.get("group") in GATEWAY_API_CRD_GROUPS
    ]

    found_standard = GATEWAY_API_STANDARD_CRDS & crd_names
    missing_standard = GATEWAY_API_STANDARD_CRDS - crd_names

    versions: set[str] = set()
    for c in gateway_crds:
        versions.update(c.get("versions", []))

    if found_standard == GATEWAY_API_STANDARD_CRDS:
        status = PrerequisiteStatus.DETECTED
    elif found_standard:
        status = PrerequisiteStatus.PARTIAL
    else:
        status = PrerequisiteStatus.MISSING

    return {
        "status": status,
        "crds_installed": len(gateway_crds),
        "standard_crds_found": sorted(found_standard),
        "standard_crds_missing": sorted(missing_standard),
        "api_versions": sorted(versions),
        "gatewayclasses": len(gatewayclasses),
        "gateways": len(gateways),
    }


# ---------------------------------------------------------------------------
# NVIDIA DPF (DOCA Platform Framework)
# ---------------------------------------------------------------------------


def analyze_dpf(
    crds: list[dict],
    crd_names: set[str],
    dpf_operator_configs: list,
    dpudevices: list,
    dpusets: list,
    dpuclusters: list,
    dpuservices: list,
    bfbs: list,
    helm_releases: list[dict],
) -> dict[str, Any]:
    """Detect NVIDIA DPF installation and summarize DPU infrastructure."""
    dpf_crds = [c for c in crds if c.get("group") in DPF_CRD_GROUPS]
    has_crds = len(dpf_crds) > 0

    found_core = DPF_CORE_CRDS & crd_names
    found_service = DPF_SERVICE_CRDS & crd_names

    # Extract version from DPFOperatorConfig status or operator pod image
    version = _extract_dpf_version(dpf_operator_configs, helm_releases)

    # Determine operator health from DPFOperatorConfig conditions
    operator_ready = False
    operator_conditions: list[dict[str, str]] = []
    if dpf_operator_configs:
        cfg = dpf_operator_configs[0]
        conditions_list = (
            cfg.get("status", {}).get("conditions") or []
        )
        operator_conditions = [
            {
                "type": c.get("type", ""),
                "status": c.get("status", ""),
                "reason": c.get("reason", ""),
            }
            for c in conditions_list
        ]
        operator_ready = any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in conditions_list
        )

    # Count DPU devices by condition
    devices_ready = sum(
        1
        for d in dpudevices
        if any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in (d.get("status", {}).get("conditions") or [])
        )
    )

    # Determine status
    if has_crds and dpf_operator_configs and operator_ready:
        status = PrerequisiteStatus.DETECTED
    elif has_crds or dpf_operator_configs:
        status = PrerequisiteStatus.PARTIAL
    else:
        status = PrerequisiteStatus.MISSING

    # Helm release for DPF operator
    dpf_helm = next(
        (r for r in helm_releases if "dpf" in r.get("name", "").lower()),
        None,
    )

    return {
        "status": status,
        "version": version,
        "crds_installed": len(dpf_crds),
        "core_crds_found": sorted(found_core),
        "core_crds_missing": sorted(DPF_CORE_CRDS - found_core),
        "service_crds_found": sorted(found_service),
        "operator": {
            "configured": len(dpf_operator_configs) > 0,
            "ready": operator_ready,
            "conditions": operator_conditions,
        },
        "devices": {
            "total": len(dpudevices),
            "ready": devices_ready,
        },
        "dpusets": len(dpusets),
        "dpuclusters": len(dpuclusters),
        "dpuservices": len(dpuservices),
        "bfbs": len(bfbs),
        "helm_release": dpf_helm,
    }


def _extract_dpf_version(
    dpf_operator_configs: list, helm_releases: list[dict]
) -> str | None:
    """Extract DPF version from operator config status or Helm release."""
    # Prefer version from DPFOperatorConfig status
    for cfg in dpf_operator_configs:
        version = cfg.get("status", {}).get("version")
        if version:
            return version

    # Fallback to Helm release metadata
    for r in helm_releases:
        if "dpf" in r.get("name", "").lower():
            return r.get("version")

    return None


# ---------------------------------------------------------------------------
# Kamaji (multi-tenant K8s control plane)
# ---------------------------------------------------------------------------


def analyze_kamaji(
    crds: list[dict],
    crd_names: set[str],
    kamaji_pods: list[dict],
    kamaji_tcps: list,
    helm_releases: list[dict],
) -> dict[str, Any]:
    """Detect Kamaji multi-tenant control plane manager.

    Kamaji is an optional prerequisite for DPF — it provides managed K8s
    control planes for DPU clusters.  If not installed, DPF can still work
    with static DPU clusters (bring-your-own control plane).
    """
    kamaji_crds = [c for c in crds if c.get("group") in KAMAJI_CRD_GROUPS]
    has_crds = len(kamaji_crds) > 0
    found_core = KAMAJI_CORE_CRDS & crd_names

    # Pod health
    running_pods = [
        p for p in kamaji_pods if p.get("phase") == "Running"
    ]

    # Version from Helm release or pod image
    version = _extract_kamaji_version(kamaji_pods, helm_releases)

    kamaji_helm = next(
        (r for r in helm_releases if "kamaji" in r.get("name", "").lower()),
        None,
    )

    # Determine status
    if has_crds and running_pods:
        status = PrerequisiteStatus.DETECTED
    elif has_crds or kamaji_pods:
        status = PrerequisiteStatus.PARTIAL
    else:
        status = PrerequisiteStatus.MISSING

    return {
        "status": status,
        "version": version,
        "crds_installed": len(kamaji_crds),
        "core_crds_found": sorted(found_core),
        "core_crds_missing": sorted(KAMAJI_CORE_CRDS - found_core),
        "tenant_control_planes": len(kamaji_tcps),
        "pods_running": len(running_pods),
        "helm_release": kamaji_helm,
    }


def _extract_kamaji_version(
    kamaji_pods: list[dict], helm_releases: list[dict]
) -> str | None:
    """Extract Kamaji version from pod images or Helm release."""
    # Try Helm release first
    for r in helm_releases:
        if "kamaji" in r.get("name", "").lower():
            return r.get("version")

    # Fallback: extract from pod container image tag
    for pod in kamaji_pods:
        for c in pod.get("containers", []):
            image = c.get("image", "")
            if "kamaji" in image.lower():
                match = re.search(r":v?(\d+\.\d+\.\d+)", image)
                if match:
                    return match.group(1)

    return None


# ---------------------------------------------------------------------------
# CIS (Container Ingress Services) — D-023 Phase 1
# ---------------------------------------------------------------------------

_CIS_IMAGE_MARKER = "f5networks/k8s-bigip-ctlr"
_CIS_EOL_NOTE = "k8s-bigip-ctlr is End-of-Life as of April 2026"


def analyze_cis(
    cis_controllers: list[dict],
    cis_virtualservers: list[dict],
    cis_transportservers: list[dict],
    cis_ingresslinks: list[dict],
    cis_as3_configmaps: list[dict] | None = None,
    cis_f5_ingresses: list[dict] | None = None,
    openshift_routes: list[dict] | None = None,
) -> dict[str, Any]:
    """Detect CIS controller, parse external BIG-IP identity, and enumerate CIS CRs.

    Pure function — no I/O. All arguments come from fetch_scan_data.

    The ``cis_as3_configmaps``, ``cis_f5_ingresses``, and ``openshift_routes`` lists
    allow the CIS section to be emitted even when no cis.f5.com CRD group is registered
    (AS3-ConfigMap-only, Ingress-only, or Route-only clusters).
    Status rules:
      - DETECTED  → controller with bigip_url
      - PARTIAL   → controller without bigip_url, OR any AS3 ConfigMaps / Ingresses /
                    OpenShift Routes found
      - MISSING   → no controller and no AS3 ConfigMaps and no Ingresses and no Routes
                    and no CRD-based CRs

    Returns:
        {
          status: "detected" | "partial" | "missing",
          controller: {found, image, namespace, replicas_ready, bigip_url,
                       login_secret_ref: {name, namespace}},
          external_bigip: {host, port},
          inventory: {virtual_servers, transport_servers, ingresslinks,
                      as3_configmaps, f5_ingresses, openshift_routes},
          eol_note: str,
        }
    """
    cis_as3_configmaps = cis_as3_configmaps or []
    cis_f5_ingresses = cis_f5_ingresses or []
    openshift_routes = openshift_routes or []

    # Determine whether ANY CIS surface exists before deciding to return MISSING
    has_any_surface = bool(
        cis_controllers
        or cis_virtualservers
        or cis_transportservers
        or cis_ingresslinks
        or cis_as3_configmaps
        or cis_f5_ingresses
        or openshift_routes
    )

    if not has_any_surface:
        return {
            "status": PrerequisiteStatus.MISSING,
            "controller": {"found": False},
            "external_bigip": {},
            "inventory": {
                "virtual_servers": [],
                "transport_servers": [],
                "ingresslinks": [],
                "as3_configmaps": [],
                "f5_ingresses": [],
                "openshift_routes": [],
            },
            "eol_note": _CIS_EOL_NOTE,
        }

    # No controller but other surfaces found — partial / ConfigMap-only / Ingress-only /
    # Route-only mode
    if not cis_controllers:
        return {
            "status": PrerequisiteStatus.PARTIAL,
            "controller": {"found": False},
            "external_bigip": {},
            "inventory": {
                "virtual_servers": cis_virtualservers,
                "transport_servers": cis_transportservers,
                "ingresslinks": cis_ingresslinks,
                "as3_configmaps": cis_as3_configmaps,
                "f5_ingresses": cis_f5_ingresses,
                "openshift_routes": openshift_routes,
            },
            "eol_note": _CIS_EOL_NOTE,
        }

    controller_raw = cis_controllers[0]  # take first match
    image = next(
        (img for img in (controller_raw.get("images") or []) if _CIS_IMAGE_MARKER in (img or "")),
        None,
    )

    bigip_url, login_secret_ref, bigip_partition = _parse_cis_args(controller_raw.get("args") or [])
    host, port = _parse_bigip_url(bigip_url)

    controller_info: dict[str, Any] = {
        "found": True,
        "image": image,
        "namespace": controller_raw.get("namespace"),
        "replicas_ready": controller_raw.get("replicas_ready", 0),
        "bigip_url": bigip_url,
        "bigip_partition": bigip_partition,
        "login_secret_ref": login_secret_ref,
    }

    status = PrerequisiteStatus.DETECTED if bigip_url else PrerequisiteStatus.PARTIAL

    return {
        "status": status,
        "controller": controller_info,
        "external_bigip": {"host": host, "port": port},
        "inventory": {
            "virtual_servers": cis_virtualservers,
            "transport_servers": cis_transportservers,
            "ingresslinks": cis_ingresslinks,
            "as3_configmaps": cis_as3_configmaps,
            "f5_ingresses": cis_f5_ingresses,
            "openshift_routes": openshift_routes,
        },
        "eol_note": _CIS_EOL_NOTE,
    }


def _parse_cis_args(args: list[str]) -> tuple[str | None, dict[str, str | None], str | None]:
    """Parse --bigip-url, --bigip-ctlr-creds, and --bigip-partition from CIS controller args.

    Handles both forms for each flag:
        --bigip-url=https://10.0.0.1
        --bigip-url https://10.0.0.1
        --bigip-partition=Production
        --bigip-partition Production

    Returns:
        (bigip_url, {name: secret_name, namespace: secret_namespace}, bigip_partition)
    """
    bigip_url: str | None = None
    secret_name: str | None = None
    secret_namespace: str | None = None
    bigip_partition: str | None = None

    i = 0
    while i < len(args):
        arg = args[i]
        # --bigip-url=VALUE form
        if arg.startswith("--bigip-url="):
            bigip_url = arg.split("=", 1)[1].strip()
        # --bigip-url VALUE form
        elif arg == "--bigip-url" and i + 1 < len(args):
            bigip_url = args[i + 1].strip()
            i += 1
        # --bigip-ctlr-creds=namespace/name or --bigip-ctlr-creds=name
        elif arg.startswith("--bigip-ctlr-creds="):
            val = arg.split("=", 1)[1].strip()
            if "/" in val:
                secret_namespace, secret_name = val.split("/", 1)
            else:
                secret_name = val
        elif arg == "--bigip-ctlr-creds" and i + 1 < len(args):
            val = args[i + 1].strip()
            if "/" in val:
                secret_namespace, secret_name = val.split("/", 1)
            else:
                secret_name = val
            i += 1
        # --bigip-partition=VALUE form
        elif arg.startswith("--bigip-partition="):
            bigip_partition = arg.split("=", 1)[1].strip()
        # --bigip-partition VALUE form
        elif arg == "--bigip-partition" and i + 1 < len(args):
            bigip_partition = args[i + 1].strip()
            i += 1
        i += 1

    return bigip_url, {"name": secret_name, "namespace": secret_namespace}, bigip_partition


def _parse_bigip_url(bigip_url: str | None) -> tuple[str | None, int | None]:
    """Extract host and port from a BIG-IP management URL.

    Examples:
        https://10.0.0.1        → ("10.0.0.1", 443)
        https://10.0.0.1:8443   → ("10.0.0.1", 8443)
        10.0.0.1                → ("10.0.0.1", None)
    """
    if not bigip_url:
        return None, None
    url = bigip_url.strip()
    # Strip scheme
    if "://" in url:
        url = url.split("://", 1)[1]
    # Strip path
    url = url.split("/")[0]
    # Split host:port
    if ":" in url:
        host, port_str = url.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            port = None
        return host, port
    return url, None
