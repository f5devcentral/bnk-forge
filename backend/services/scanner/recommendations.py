"""
Scanner recommendations — data-driven recommendation builder.

Replaces the original 278-line ``_build_recommendations`` with a compact
data-driven approach.  Each prerequisite defines its recommendation variants
keyed by status, eliminating repetitive if/elif/else chains.
"""

from typing import Any

from services.scanner.constants import PrerequisiteStatus

# ---------------------------------------------------------------------------
# Prerequisite IDs and per-cluster default enablement
# ---------------------------------------------------------------------------
# IDs the scanner knows how to check. Used by the cluster-settings UI to
# render checkboxes.
KNOWN_PREREQUISITE_IDS: tuple[str, ...] = (
    "cert-manager",
    "multus",
    "sriov",
    "hugepages",
    "storage",
    "gateway-api",
)

# Defaults applied when cluster.enabled_prerequisites is NULL. SR-IOV and
# HugePages are off by default because they only apply to bare-metal/dedicated
# nodes; users opt in per-cluster via the Settings dialog.
DEFAULT_ENABLED_PREREQUISITES: frozenset[str] = frozenset({
    "cert-manager",
    "multus",
    "storage",
    "gateway-api",
})

# Multus is required for any BNK deployment, so the UI keeps its checkbox
# locked-on. Listed here so the backend rejects requests that try to disable
# it instead of silently re-enabling.
LOCKED_PREREQUISITE_IDS: frozenset[str] = frozenset({"multus"})


def resolve_enabled_prereqs(stored: list[str] | None) -> set[str]:
    """Return the effective enabled-prereq set for a cluster.

    Stored ``None`` means "use defaults". Locked prereqs (multus) are always
    included regardless of what was stored.
    """
    base = set(stored) if stored is not None else set(DEFAULT_ENABLED_PREREQUISITES)
    base.update(LOCKED_PREREQUISITE_IDS)
    return base


# ---------------------------------------------------------------------------
# Prerequisite recommendation definitions
# ---------------------------------------------------------------------------
# Each entry maps (prerequisite_id, status) → a template dict.
# Templates may contain ``{placeholders}`` that are filled from the analysis dict.

_PREREQ_RECS: list[dict[str, Any]] = [
    # ---- cert-manager ----
    {
        "id": "cert-manager",
        "module": "k8s/cert-manager",
        "variants": {
            PrerequisiteStatus.MISSING: {
                "severity": "required",
                "title": "Install cert-manager",
                "description": "cert-manager is required for TLS certificate management. BNK uses it for internal OTEL certs and ClusterIssuers.",
                "status": "deploy",
            },
            PrerequisiteStatus.PARTIAL: {
                "severity": "recommended",
                "title": "cert-manager partially installed",
                "description_fn": lambda d: (
                    f"cert-manager CRDs found but some components may be missing. "
                    f"Running pods: {d['pods']['total_running']}."
                ),
                "status": "investigate",
            },
            PrerequisiteStatus.DETECTED: {
                "severity": "info",
                "title_fn": lambda d: f"cert-manager v{d.get('version', 'unknown')} installed",
                "description_fn": lambda d: (
                    f"{d['pods']['total_running']} pods running, "
                    f"{d['crd_count']} CRDs installed."
                ),
                "status": "skip",
            },
        },
    },
    # ---- Multus CNI ----
    {
        "id": "multus",
        "module": None,
        "variants": {
            PrerequisiteStatus.MISSING: {
                "severity": "required",
                "title": "Install Multus CNI",
                "description": "Multus CNI is required for multi-network pod attachments. BNK uses it for SR-IOV data-plane interfaces on TMM.",
                "status": "deploy",
            },
            PrerequisiteStatus.PARTIAL: {
                "severity": "recommended",
                "title": "Multus CNI partially configured",
                "description": "NetworkAttachmentDefinition CRD exists but Multus pods may not be running.",
                "status": "investigate",
            },
            PrerequisiteStatus.DETECTED: {
                "severity": "info",
                "title": "Multus CNI installed",
                "description_fn": lambda d: f"NAD CRD present, {d['running_pods']} Multus pods running.",
                "status": "skip",
            },
        },
    },
    # ---- SR-IOV ----
    {
        "id": "sriov",
        "module": None,
        "variants": {
            PrerequisiteStatus.MISSING: {
                "severity": "required",
                "title": "Configure SR-IOV device plugin",
                "description": "SR-IOV device plugin and virtual functions are required for high-performance BNK data-plane networking.",
                "status": "deploy",
            },
            PrerequisiteStatus.PARTIAL: {
                "severity": "recommended",
                "title": "SR-IOV partially configured",
                "description_fn": lambda d: (
                    f"Device plugin {'found' if d['device_plugin'] else 'missing'}, "
                    f"{d['nodes_with_vfs']} node(s) with VFs, "
                    f"{d['total_vfs']} total VFs."
                ),
                "status": "investigate",
            },
            PrerequisiteStatus.DETECTED: {
                "severity": "info",
                "title": "SR-IOV configured",
                "description_fn": lambda d: (
                    f"{d['nodes_with_vfs']} node(s) with VFs, "
                    f"{d['total_vfs']} total virtual functions available."
                ),
                "status": "skip",
            },
        },
    },
    # ---- HugePages ----
    {
        "id": "hugepages",
        "module": None,
        "variants": {
            PrerequisiteStatus.MISSING: {
                "severity": "recommended",
                "title": "Configure HugePages",
                "description": "HugePages improve TMM memory performance. Configure 2Mi or 1Gi HugePages on high-performance nodes.",
                "status": "deploy",
            },
            PrerequisiteStatus.DETECTED: {
                "severity": "info",
                "title_fn": lambda d: f"HugePages available on {d['nodes_with_hugepages']} node(s)",
                "description": "HugePages configured for TMM performance.",
                "status": "skip",
            },
        },
    },
    # ---- Storage ----
    {
        "id": "storage",
        "module": None,
        "variants": {
            PrerequisiteStatus.MISSING: {
                "severity": "required",
                "title": "No storage classes found",
                "description": "At least one storage class is required for persistent storage. BNK DSSM requires persistent volumes.",
                "status": "deploy",
            },
            # "no_default" is a pseudo-status handled in _storage_status()
            "no_default": {
                "severity": "recommended",
                "title_fn": lambda d: "No default storage class",
                "description_fn": lambda d: f"{d['count']} storage class(es) found but none marked as default.",
                "status": "investigate",
            },
            PrerequisiteStatus.DETECTED: {
                "severity": "info",
                "title_fn": lambda d: f"{d['count']} storage class(es) available",
                "description_fn": lambda d: f"Default: {d['default']}.",
                "status": "skip",
            },
        },
    },
    # ---- Gateway API ----
    {
        "id": "gateway-api",
        "module": None,
        "variants": {
            PrerequisiteStatus.MISSING: {
                "severity": "info",
                "title": "Gateway API CRDs not installed",
                "description": "Gateway API CRDs are installed when BNK is deployed (FLO installs them automatically in the Forge deploy flow; a manual/helm install must install the Gateway API CRDs itself).",
                "status": "skip",
            },
            # PARTIAL and DETECTED get the same treatment
            PrerequisiteStatus.PARTIAL: {
                "severity": "info",
                "title_fn": lambda d: f"Gateway API CRDs installed ({d['crds_installed']} CRDs)",
                "description_fn": lambda d: f"{d['gatewayclasses']} GatewayClass(es), {d['gateways']} Gateway(s).",
                "status": "skip",
            },
            PrerequisiteStatus.DETECTED: {
                "severity": "info",
                "title_fn": lambda d: f"Gateway API CRDs installed ({d['crds_installed']} CRDs)",
                "description_fn": lambda d: f"{d['gatewayclasses']} GatewayClass(es), {d['gateways']} Gateway(s).",
                "status": "skip",
            },
        },
    },
    # ---- NVIDIA DPF ----
    {
        "id": "dpf",
        "module": None,
        "variants": {
            PrerequisiteStatus.MISSING: {
                "severity": "info",
                "title": "NVIDIA DPF not detected",
                "description": "No DOCA Platform Framework CRDs found. DPF is optional — required only for BlueField DPU management.",
                "status": "skip",
            },
            PrerequisiteStatus.PARTIAL: {
                "severity": "recommended",
                "title": "NVIDIA DPF partially installed",
                "description_fn": lambda d: (
                    f"{d['crds_installed']} DPF CRD(s) found but operator is "
                    f"{'not configured' if not d['operator']['configured'] else 'not ready'}. "
                    f"Missing core CRDs: {len(d['core_crds_missing'])}."
                ),
                "status": "investigate",
            },
            PrerequisiteStatus.DETECTED: {
                "severity": "info",
                "title_fn": lambda d: (
                    f"NVIDIA DPF v{d['version']}" if d.get("version")
                    else "NVIDIA DPF installed"
                ),
                "description_fn": lambda d: (
                    f"{d['devices']['total']} DPU device(s) "
                    f"({d['devices']['ready']} ready), "
                    f"{d['dpusets']} DPUSet(s), "
                    f"{d['dpuclusters']} DPU cluster(s), "
                    f"{d['dpuservices']} service(s)."
                ),
                "status": "skip",
            },
        },
    },
    # ---- Kamaji ----
    {
        "id": "kamaji",
        "module": None,
        "variants": {
            PrerequisiteStatus.MISSING: {
                "severity": "info",
                "title": "Kamaji not detected",
                "description": "No Kamaji CRDs found. Kamaji is optional — required only for managed DPU cluster control planes.",
                "status": "skip",
            },
            PrerequisiteStatus.PARTIAL: {
                "severity": "recommended",
                "title": "Kamaji partially installed",
                "description_fn": lambda d: (
                    f"{d['crds_installed']} Kamaji CRD(s) found, "
                    f"{d['pods_running']} pod(s) running."
                ),
                "status": "investigate",
            },
            PrerequisiteStatus.DETECTED: {
                "severity": "info",
                "title_fn": lambda d: (
                    f"Kamaji v{d['version']}" if d.get("version")
                    else "Kamaji installed"
                ),
                "description_fn": lambda d: (
                    f"{d['tenant_control_planes']} tenant control plane(s), "
                    f"{d['pods_running']} pod(s) running."
                ),
                "status": "skip",
            },
        },
    },
]


# ---------------------------------------------------------------------------
# BNK deployment recommendations (when not_installed)
# ---------------------------------------------------------------------------

_BNK_DEPLOYMENT_STEPS: list[dict[str, str]] = [
    {
        "id": "bnk-prerequisites",
        "severity": "required",
        "title": "Deploy BNK prerequisites",
        "description": "Create F5 namespaces, image pull secrets, and download manifests.",
        "module": "k8s/bnk-prerequisites",
    },
    {
        "id": "network-setup",
        "severity": "required",
        "title": "Configure network attachments",
        "description": "Create Multus NetworkAttachmentDefinitions for TMM data-plane interfaces.",
        "module": "k8s/network-setup",
    },
    {
        "id": "flo",
        "severity": "required",
        "title": "Install F5 Lifecycle Operator (FLO)",
        "description": "FLO manages the BNK TMM pods and lifecycle.",
        "module": "bnk/flo",
    },
    {
        "id": "cneinstance",
        "severity": "required",
        "title": "Create CNEInstance",
        "description": "Deploy the TMM data-plane with desired feature toggles.",
        "module": "bnk/cneinstance",
    },
    {
        "id": "bnk-vlans",
        "severity": "required",
        "title": "Configure VLANs",
        "description": "Create F5SPKVlan CRs for external and internal self-IPs on TMM interfaces.",
        "module": "bnk/bnk-vlans",
    },
    {
        "id": "bnk-gatewayclass",
        "severity": "required",
        "title": "Create BNK GatewayClass",
        "description": "Register the BNK GatewayClass for Gateway API resources.",
        "module": "bnk/bnk-gatewayclass",
    },
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_recommendations(
    cert_manager: dict,
    multus: dict,
    sriov: dict,
    hugepages: dict,
    storage: dict,
    gateway_api: dict,
    bnk_install: dict,
    dpf: dict | None = None,
    kamaji: dict | None = None,
    platform_profile: str | None = None,
    platform_context: dict[str, Any] | None = None,
    enabled_prerequisites: set[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Build actionable recommendations based on scan results.

    Each recommendation has:
      - id: machine-readable identifier
      - category: "prerequisite" | "bnk_component" | "optimization"
      - severity: "required" | "recommended" | "info"
      - title: human-readable title
      - description: what to do
      - module: BNK Forge module path (if any)
      - status: "deploy" | "skip" | "upgrade" | "investigate"
    """
    prereq_data: dict[str, dict] = {
        "cert-manager": cert_manager,
        "multus": multus,
        "sriov": sriov,
        "hugepages": hugepages,
        "storage": storage,
        "gateway-api": gateway_api,
    }
    if dpf is not None:
        prereq_data["dpf"] = dpf
    if kamaji is not None:
        prereq_data["kamaji"] = kamaji

    recs: list[dict[str, Any]] = []
    effective_enabled = (
        enabled_prerequisites
        if enabled_prerequisites is not None
        else resolve_enabled_prereqs(None)
    )

    # Prerequisites
    for spec in _PREREQ_RECS:
        prereq_id = spec["id"]
        if prereq_id in KNOWN_PREREQUISITE_IDS and prereq_id not in effective_enabled:
            continue  # User opted out of this prereq for this cluster
        data = prereq_data.get(prereq_id)
        if data is None:
            continue  # Skip prerequisites not present (e.g. dpf=None)
        effective_status = _effective_status(prereq_id, data)

        variant = spec["variants"].get(effective_status)
        if variant is None:
            continue

        recs.append(_render_rec(spec, variant, data, platform_profile))

    # BNK installation
    recs.extend(_bnk_recommendations(bnk_install))

    # Platform-specific readiness truth (bounded to awareness/reporting).
    recs.extend(_platform_recommendations(platform_profile, prereq_data, platform_context))

    return recs


def build_proxy_recommendations(
    existing_proxies: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Emit one 'Migrate to BNK' recommendation per detected non-BNK proxy.

    Status is intentionally ``"investigate"`` (not ``"deploy"``) so the FE
    renders it as text-only with no action button (DECOMP §2 note).
    """
    if not existing_proxies:
        return []
    proxies = existing_proxies.get("proxies") or []
    recs: list[dict[str, Any]] = []
    for proxy in proxies:
        if proxy.get("is_bnk"):
            continue  # Already BNK — no migration needed
        if not proxy.get("found"):
            continue
        proxy_type = proxy.get("proxy_type", "unknown")
        display = proxy.get("display_name") or proxy_type
        ic_name = (proxy.get("details") or {}).get("ingress_class_name") or ""
        gc_name = (proxy.get("details") or {}).get("gateway_class_name") or ""
        label = ic_name or gc_name or proxy_type
        backends = proxy.get("backends") or []
        backend_summary = (
            f"{len(backends)} backend(s) fronted" if backends
            else "no backends mapped"
        )
        recs.append({
            "id": f"migrate-proxy-{proxy_type}-{label}".replace("/", "-").replace(".", "-"),
            "category": "optimization",
            "severity": "info",
            "title": f"Migrate {display} to F5 BNK",
            "description": (
                f"Detected {display} ({label}) with {backend_summary}. "
                "Consider migrating to F5 BNK Gateway API for advanced traffic management, "
                "security policies, and BNK-native observability."
            ),
            "module": None,
            "status": "investigate",
        })
    return recs


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _effective_status(prereq_id: str, data: dict) -> str:
    """
    Determine the effective status for a prerequisite.

    Storage has a special "no_default" pseudo-status when classes exist
    but none is marked default.
    """
    status = data["status"]
    if prereq_id == "storage" and status == PrerequisiteStatus.DETECTED and not data.get("default"):
        return "no_default"
    return status


def _render_rec(
    spec: dict,
    variant: dict,
    data: dict,
    platform_profile: str | None,
) -> dict[str, Any]:
    """Render a single recommendation from a spec + variant + data."""
    title_fn = variant.get("title_fn")
    desc_fn = variant.get("description_fn")
    description = desc_fn(data) if desc_fn else variant["description"]

    # PLATFORM-CONTEXT-004: make baseline assumptions explicit for the
    # current kubeconfig-first/manual-friendly on-prem profile.
    if platform_profile == "generic_onprem" and spec["id"] in {"multus", "sriov", "hugepages"}:
        description = (
            f"{description} "
            "This guidance reflects the generic_onprem baseline "
            "(kubeconfig-first, manual-friendly clusters)."
        )

    return {
        "id": spec["id"],
        "category": "prerequisite",
        "severity": variant["severity"],
        "title": title_fn(data) if title_fn else variant["title"],
        "description": description,
        "module": spec["module"],
        "status": variant["status"],
    }


def _bnk_recommendations(bnk_install: dict) -> list[dict[str, Any]]:
    """Build BNK component recommendations based on installation status."""
    bnk_status = bnk_install["status"]

    if bnk_status == "not_installed":
        return [
            {
                "id": step["id"],
                "category": "bnk_component",
                "severity": step["severity"],
                "title": step["title"],
                "description": step["description"],
                "module": step["module"],
                "status": "deploy",
            }
            for step in _BNK_DEPLOYMENT_STEPS
        ]

    if bnk_status == "partial":
        return [
            {
                "id": "bnk-install",
                "category": "bnk_component",
                "severity": "recommended",
                "title": "BNK partially installed",
                "description": (
                    f"F5 CRDs: {bnk_install['crds']['total']}, "
                    f"FLO: {bnk_install['flo']['running']}/{bnk_install['flo']['pods']}, "
                    f"TMM: {bnk_install['tmm']['running']}/{bnk_install['tmm']['pods']}. "
                    f"Investigate and complete the installation."
                ),
                "module": None,
                "status": "investigate",
            }
        ]

    # Fully installed
    tmm_info = bnk_install["tmm"]
    flo_info = bnk_install["flo"]
    health = bnk_install.get("health", "unknown")
    containers_str = (
        f" ({tmm_info['containers']['containers']} containers)"
        if tmm_info.get("containers")
        else ""
    )
    return [
        {
            "id": "bnk-install",
            "category": "bnk_component",
            "severity": "info",
            "title": f"BNK installed — {health}",
            "description": (
                f"FLO {flo_info.get('version', 'unknown')}, "
                f"TMM {tmm_info['running']}/{tmm_info['pods']} pods"
                f"{containers_str}"
                f", {bnk_install['crds']['total']} F5 CRDs."
            ),
            "module": None,
            "status": "skip",
        }
    ]


def _platform_recommendations(
    platform_profile: str | None,
    prereq_data: dict[str, dict],
    platform_context: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Build bounded platform-awareness recommendations.

    This layer intentionally stays inside readiness/reporting truthfulness and
    does not introduce generic platform lifecycle automation.
    """
    if platform_profile != "ocp":
        return []

    capabilities = ((platform_context or {}).get("platform_capabilities") or {}) if isinstance(platform_context, dict) else {}
    constraints = ((platform_context or {}).get("platform_constraints") or {}) if isinstance(platform_context, dict) else {}

    scc_capability = capabilities.get("scc") is True
    machine_config_capability = capabilities.get("machine_config") is True
    operator_framework_capability = capabilities.get("operator_framework") is True
    security_binding_constraint = constraints.get("privileged_workloads_require_platform_security_binding") is True
    operator_network_constraint = constraints.get("operator_required_for_networking") is True

    recs: list[dict[str, Any]] = []

    if scc_capability or security_binding_constraint:
        recs.append(
            {
                "id": "ocp-security-binding-awareness",
                "category": "optimization",
                "severity": "info",
                "title": "OpenShift security binding awareness",
                "description": (
                    "This cluster reports SCC/security-binding constraints for "
                    "privileged or specialized networking workloads. Ensure platform "
                    "security binding alignment before declaring BNK deployment ready."
                ),
                "module": None,
                "status": "skip",
            }
        )

    multus_status = _effective_status("multus", prereq_data.get("multus") or {"status": "missing"})
    sriov_status = _effective_status("sriov", prereq_data.get("sriov") or {"status": "missing"})
    hugepages_status = _effective_status("hugepages", prereq_data.get("hugepages") or {"status": "missing"})

    if (multus_status != PrerequisiteStatus.DETECTED or sriov_status != PrerequisiteStatus.DETECTED) and (
        operator_network_constraint or operator_framework_capability
    ):
        recs.append(
            {
                "id": "ocp-network-operator-path",
                "category": "optimization",
                "severity": "recommended",
                "title": "Use OpenShift operator-driven networking path",
                "description": (
                    "For OCP clusters, Multus/SR-IOV readiness usually depends on "
                    "operator-managed workflows and cluster policy. Missing or partial "
                    "network prerequisites should be resolved through platform operators "
                    "before BNK rollout."
                ),
                "module": None,
                "status": "investigate",
            }
        )

    if hugepages_status != PrerequisiteStatus.DETECTED and machine_config_capability:
        recs.append(
            {
                "id": "ocp-node-config-awareness",
                "category": "optimization",
                "severity": "info",
                "title": "OpenShift node configuration path",
                "description": (
                    "This cluster reports MachineConfig-style node configuration "
                    "capabilities. Node-level performance settings such as HugePages "
                    "should be aligned through platform node-configuration workflows "
                    "rather than ad hoc direct mutation."
                ),
                "module": None,
                "status": "investigate",
            }
        )

    return recs
