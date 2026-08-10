"""
Blueprint Module Service Layer — canonical project context resolution
and module-specific variable transforms.

Bridges the gap between the project data model (ProjectDpuSettings,
BareMetalHost, BnkVersionProfile, Dpu) and external catalog modules
that expect specific variable names and types.

Integration points in variable_assembler.py:
  Layer 2.5: resolve_project_context() — inject canonical vars at low precedence
  Layer 4.5: transform_for_module() — reshape vars for specific module interfaces

Design note: The MODULE_TRANSFORMS registry is a bridge pattern. The long-term
target is catalog-declared adapter metadata (module.json "canonical" + "coerce"
fields) that Forge reads generically. Until the catalog evolves, this registry
provides the same functionality from the Forge side.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Canonical project context
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProjectContext:
    """Canonical variables extracted from project data models.

    Module-agnostic snapshot. Produced once per module execution by
    resolve_project_context(), consumed read-only by transforms.
    """

    # From ProjectDpuSettings
    external_vlan_id: int | None = None
    external_vlan_ipv4_subnet: str | None = None      # CIDR e.g. "10.10.20.0/24"
    internal_vlan_id: int | None = None
    internal_vlan_ipv4_subnet: str | None = None
    high_speed_mtu: int | None = None

    # From Dpu record (per-DPU assigned IPs)
    dpu_external_vlan_ipv4: str | None = None          # e.g. "10.10.20.100"
    dpu_internal_vlan_ipv4: str | None = None

    # From BnkVersionProfile
    bnk_manifest_version: str | None = None
    flo_version: str | None = None
    cert_manager_version: str | None = None
    storage_class_type: str | None = None
    storage_provisioner: str | None = None

    # Derived flags
    is_bare_metal: bool = False
    is_dpu_enabled: bool = False

    def as_low_precedence_vars(self) -> dict[str, Any]:
        """Export non-None fields as a flat dict for low-precedence injection."""
        result: dict[str, Any] = {}
        for k in (
            "external_vlan_id", "external_vlan_ipv4_subnet",
            "internal_vlan_id", "internal_vlan_ipv4_subnet",
            "high_speed_mtu",
            "dpu_external_vlan_ipv4", "dpu_internal_vlan_ipv4",
            "bnk_manifest_version", "flo_version", "cert_manager_version",
            "storage_class_type", "storage_provisioner",
            "is_bare_metal", "is_dpu_enabled",
        ):
            v = getattr(self, k)
            if v is not None and v is not False:
                result[k] = v
        return result


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

def resolve_project_context(
    db: Session,
    project_id: int,
    bare_metal_host_id: int | None = None,
) -> ProjectContext:
    """Resolve canonical variables from project data models.

    DB access is isolated here. All downstream transforms are pure.
    Graceful degradation: missing models → None fields, not errors.
    """
    from models.bare_metal import BareMetalHost
    from models.dpu import Dpu, ProjectDpuSettings

    kwargs: dict[str, Any] = {}

    # 1. ProjectDpuSettings
    settings = (
        db.query(ProjectDpuSettings)
        .filter(ProjectDpuSettings.project_id == project_id)
        .first()
    )
    if settings:
        kwargs["external_vlan_id"] = settings.external_vlan_id
        kwargs["external_vlan_ipv4_subnet"] = settings.external_vlan_ipv4_subnet
        kwargs["internal_vlan_id"] = settings.internal_vlan_id
        kwargs["internal_vlan_ipv4_subnet"] = settings.internal_vlan_ipv4_subnet
        kwargs["high_speed_mtu"] = settings.high_speed_mtu or 9000  # sensible default

    # 2. BareMetalHost → BnkVersionProfile
    host: BareMetalHost | None = None
    if bare_metal_host_id:
        host = db.query(BareMetalHost).filter(BareMetalHost.id == bare_metal_host_id).first()

    if host:
        kwargs["is_bare_metal"] = True
        vp = host.version_profile
        if vp:
            kwargs["bnk_manifest_version"] = vp.bnk_manifest_version
            kwargs["flo_version"] = vp.flo_version
            kwargs["cert_manager_version"] = vp.cert_manager_version
            kwargs["storage_class_type"] = vp.storage_class_type
            kwargs["storage_provisioner"] = vp.storage_provisioner

    # 2b. Fill cert_manager_version with a safe default when not supplied by version profile.
    # v1.16.1 was the pinned version aligned with BNK 2.2 (previously seeded via ExternalHelmChart,
    # which was removed in D-028 P5).  The UX version-picker that previously let admins choose from
    # ExternalHelmChart rows is a deferred follow-up tracked in the D-028 backlog — it will be
    # relocated to BnkVersionProfile.  Until then this constant is the single source of truth.
    DEFAULT_CERT_MANAGER_VERSION = "v1.16.1"
    if not kwargs.get("cert_manager_version"):
        kwargs["cert_manager_version"] = DEFAULT_CERT_MANAGER_VERSION

    # 3. Dpu record (matched by project + host IP, same pattern as _inject_rendered_bf_conf)
    if host:
        dpu = (
            db.query(Dpu)
            .filter(Dpu.project_id == project_id, Dpu.host_node_ip == host.host_ip)
            .first()
        )
        if dpu:
            kwargs["dpu_external_vlan_ipv4"] = dpu.external_vlan_ipv4
            kwargs["dpu_internal_vlan_ipv4"] = dpu.internal_vlan_ipv4
            kwargs["is_dpu_enabled"] = True

    # 4. Derive self-IPs from VLAN definitions when no Dpu record exists.
    # The self-IP is an address FROM the VLAN subnet assigned to this DPU/TMM.
    # DPU settings provide subnet + start_host offset → derive the IP.
    if settings and not kwargs.get("dpu_external_vlan_ipv4"):
        derived = _derive_selfip_from_vlan(
            settings.external_vlan_ipv4_subnet,
            getattr(settings, "external_vlan_start_host", None),
        )
        if derived:
            kwargs["dpu_external_vlan_ipv4"] = derived
            kwargs["is_dpu_enabled"] = True

    if settings and not kwargs.get("dpu_internal_vlan_ipv4"):
        derived = _derive_selfip_from_vlan(
            settings.internal_vlan_ipv4_subnet,
            getattr(settings, "internal_vlan_start_host", None),
        )
        if derived:
            kwargs["dpu_internal_vlan_ipv4"] = derived
            kwargs["is_dpu_enabled"] = True

    logger.info(
        "resolve_project_context(project=%d, host=%s): is_dpu_enabled=%s, is_bare_metal=%s, "
        "dpu_ext_ip=%s, dpu_int_ip=%s, ext_subnet=%s, int_subnet=%s",
        project_id, bare_metal_host_id,
        kwargs.get("is_dpu_enabled", False),
        kwargs.get("is_bare_metal", False),
        kwargs.get("dpu_external_vlan_ipv4"),
        kwargs.get("dpu_internal_vlan_ipv4"),
        kwargs.get("external_vlan_ipv4_subnet"),
        kwargs.get("internal_vlan_ipv4_subnet"),
    )
    return ProjectContext(**kwargs)


# ---------------------------------------------------------------------------
# Self-IP derivation from VLAN definitions
# ---------------------------------------------------------------------------

def _derive_selfip_from_vlan(
    subnet_cidr: str | None,
    start_host: int | None,
) -> str | None:
    """Derive a self-IP address from a VLAN subnet + start_host offset.

    The self-IP is an address FROM the VLAN subnet assigned to the DPU/TMM.
    ProjectDpuSettings stores the subnet CIDR and a start_host offset:
      subnet_cidr="10.10.20.0/24", start_host=100 → "10.10.20.100"

    Returns the bare IP string, or None if inputs are missing/invalid.
    """
    if not subnet_cidr or start_host is None:
        return None
    try:
        from ipaddress import ip_network
        network = ip_network(subnet_cidr, strict=False)
        # start_host is the host offset within the subnet
        ip = network.network_address + start_host
        if ip not in network:
            logger.warning(
                "Derived self-IP %s is outside subnet %s (start_host=%d)",
                ip, subnet_cidr, start_host,
            )
            return None
        return str(ip)
    except (ValueError, TypeError) as exc:
        logger.warning("Failed to derive self-IP from %s + offset %s: %s", subnet_cidr, start_host, exc)
        return None


# ---------------------------------------------------------------------------
# Self-IP parsing
# ---------------------------------------------------------------------------

def _resolve_selfip(
    dialog_value: str | None,
    dpu_ip: str | None,
) -> tuple[str | None, int | None]:
    """Parse self-IP from dialog CIDR, bare IP, or fall back to DPU record.

    Returns (bare_ip, prefix_length). prefix_length is None when not derivable.

    Supports:
      "10.0.10.240/24" → ("10.0.10.240", 24)
      "10.0.10.240"    → ("10.0.10.240", None)
      None             → falls through to dpu_ip
    """
    if dialog_value:
        raw = str(dialog_value).strip()
        if not raw:
            pass  # fall through
        elif "/" in raw:
            ip_part, prefix_part = raw.rsplit("/", 1)
            try:
                prefix = int(prefix_part.strip())
                if 1 <= prefix <= 128:
                    return ip_part.strip(), prefix
                logger.warning("selfIP prefix out of range /%d in '%s'", prefix, dialog_value)
            except ValueError:
                logger.warning("selfIP prefix not numeric in '%s'", dialog_value)
            # Fall through with bare IP portion even if prefix is bad
            return ip_part.strip(), None
        else:
            return raw, None

    if dpu_ip:
        return str(dpu_ip).strip(), None

    return None, None


def _derive_subnet_cidr(ip: str, prefix: int) -> str | None:
    """Derive network CIDR from an IP and prefix length.

    "10.0.10.240" + 24 → "10.0.10.0/24"
    Returns None on failure.
    """
    try:
        from ipaddress import ip_network
        network = ip_network(f"{ip}/{prefix}", strict=False)
        return str(network)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Per-module transforms (pure functions — no DB access)
# ---------------------------------------------------------------------------

def _transform_install_cni(
    variables: dict[str, Any], ctx: ProjectContext,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    # DPU mode: the DPU node joins the cluster on a different subnet
    # (192.168.100.0/24 OOB network) than the host. Calico needs both
    # subnets in nodeAddressAutodetectionV4 or it can't find the DPU
    # node's IP and crashes.
    if ctx.is_dpu_enabled and "dpu_oob_subnet" not in variables:
        result["dpu_oob_subnet"] = "192.168.100.0/24"
    return result


def _transform_taint_dpu_node(
    variables: dict[str, Any], ctx: ProjectContext,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    # DPU mode: FLO generates TMM with toleration key=dpu (not f5-tmm).
    # The taint must match for TMM DaemonSet to schedule on the DPU node.
    # Standard (non-DPU) mode uses f5-tmm per F5 docs.
    if ctx.is_dpu_enabled:
        result["taint_key"] = "dpu"
    return result


def _transform_bnk_prerequisites(
    variables: dict[str, Any], ctx: ProjectContext,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if ctx.bnk_manifest_version and "bnk_manifest_version" not in variables:
        result["bnk_manifest_version"] = ctx.bnk_manifest_version
    # DPU mode: FLO deploys BNK components in f5-bnk namespace and needs
    # far-secret there for image pulls from repo.f5.com.
    # Force-override to ensure DPU mode always uses f5-bnk.
    if ctx.is_dpu_enabled:
        result["instance_namespace"] = "f5-bnk"
    return result


def _transform_cert_manager(
    variables: dict[str, Any], ctx: ProjectContext,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if ctx.cert_manager_version and "cert_manager_version" not in variables:
        result["cert_manager_version"] = ctx.cert_manager_version
    return result


def _transform_network_setup(
    variables: dict[str, Any], ctx: ProjectContext,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if ctx.external_vlan_ipv4_subnet and "external_subnet_cidrs" not in variables:
        result["external_subnet_cidrs"] = [ctx.external_vlan_ipv4_subnet]
    if ctx.internal_vlan_ipv4_subnet and "internal_subnet_cidrs" not in variables:
        result["internal_subnet_cidrs"] = [ctx.internal_vlan_ipv4_subnet]
    # DPU mode: SF (SubFunction) networking on BlueField
    if ctx.is_dpu_enabled:
        result["tmm_data_plane_mode"] = "sriov"
        if "cni_type" not in variables:
            result["cni_type"] = "sf"
        if "external_resource_name" not in variables:
            result["external_resource_name"] = "nvidia.com/bf3_p0_sf"
        if "internal_resource_name" not in variables:
            result["internal_resource_name"] = "nvidia.com/bf3_p1_sf"
        if "external_nad_name" not in variables:
            result["external_nad_name"] = "sf-external"
        if "internal_nad_name" not in variables:
            result["internal_nad_name"] = "sf-internal"
        if "namespace" not in variables:
            result["namespace"] = "f5-bnk"
    return result


def _transform_flo(
    variables: dict[str, Any], ctx: ProjectContext,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if ctx.flo_version and "flo_version" not in variables:
        result["flo_version"] = ctx.flo_version
    return result


def _transform_cneinstance(
    variables: dict[str, Any], ctx: ProjectContext,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if ctx.bnk_manifest_version and "manifest_version" not in variables:
        result["manifest_version"] = ctx.bnk_manifest_version
    if ctx.is_dpu_enabled and "dpu_enabled" not in variables:
        result["dpu_enabled"] = True
    if ctx.high_speed_mtu and "tmm_default_mtu" not in variables:
        result["tmm_default_mtu"] = ctx.high_speed_mtu
    # DPU mode uses SR-IOV data plane (vfio-pci), not kernel mode (host-device CNI).
    # Kernel mode (the default) generates empty resource names for the TMM DaemonSet
    # when SR-IOV device plugin resources aren't in the kernel-mode resource map.
    if ctx.is_dpu_enabled:
        result["tmm_data_plane_mode"] = "sriov"
    # DPU mode: SF NAD names + Large deployment size
    if ctx.is_dpu_enabled and "external_nad_name" not in variables:
        result["external_nad_name"] = "sf-external"
    if ctx.is_dpu_enabled and "internal_nad_name" not in variables:
        result["internal_nad_name"] = "sf-internal"
    if ctx.is_dpu_enabled and "deployment_size" not in variables:
        result["deployment_size"] = "Large"
    return result


def _transform_bnk_vlans(
    variables: dict[str, Any], ctx: ProjectContext,
) -> dict[str, Any]:
    """Transform canonical inputs into bnk/bnk-vlans catalog variables.

    Priority for self-IPs:
      1. Already-set external_self_ips (user override at module level)
      2. Dialog-provided external_selfip (CIDR or bare IP from deploy dialog)
      3. Dpu record IP (dpu_external_vlan_ipv4 from DPU tab)

    Subnet CIDRs:
      1. Already-set external_subnet_cidrs
      2. Derived from dialog CIDR (network portion)
      3. ProjectDpuSettings subnet
    """
    result: dict[str, Any] = {}

    # External self-IP
    if "external_self_ips" not in variables:
        ext_ip, ext_prefix = _resolve_selfip(
            variables.get("external_selfip"),
            ctx.dpu_external_vlan_ipv4,
        )
        if ext_ip:
            result["external_self_ips"] = [ext_ip]

    # Internal self-IP
    if "internal_self_ips" not in variables:
        int_ip, int_prefix = _resolve_selfip(
            variables.get("internal_selfip"),
            ctx.dpu_internal_vlan_ipv4,
        )
        if int_ip:
            result["internal_self_ips"] = [int_ip]

    # External subnet CIDRs
    if "external_subnet_cidrs" not in variables:
        # Try to derive from dialog CIDR first
        ext_ip_val = variables.get("external_selfip", "")
        if ext_ip_val and "/" in str(ext_ip_val):
            ip_part, pfx = _resolve_selfip(ext_ip_val, None)
            if ip_part and pfx:
                derived = _derive_subnet_cidr(ip_part, pfx)
                if derived:
                    result["external_subnet_cidrs"] = [derived]
        # Fall back to DPU settings
        if "external_subnet_cidrs" not in result and ctx.external_vlan_ipv4_subnet:
            result["external_subnet_cidrs"] = [ctx.external_vlan_ipv4_subnet]

    # Internal subnet CIDRs
    if "internal_subnet_cidrs" not in variables:
        int_ip_val = variables.get("internal_selfip", "")
        if int_ip_val and "/" in str(int_ip_val):
            ip_part, pfx = _resolve_selfip(int_ip_val, None)
            if ip_part and pfx:
                derived = _derive_subnet_cidr(ip_part, pfx)
                if derived:
                    result["internal_subnet_cidrs"] = [derived]
        if "internal_subnet_cidrs" not in result and ctx.internal_vlan_ipv4_subnet:
            result["internal_subnet_cidrs"] = [ctx.internal_vlan_ipv4_subnet]

    # MTU
    if "mtu" not in variables and ctx.high_speed_mtu:
        result["mtu"] = ctx.high_speed_mtu

    # DPU SF mode: VLANs must be in the BNK tenant namespace.
    # Force-override — the module default is "f5-operator" which is wrong
    # for DPU deployments where FLO puts components in f5-bnk.
    if ctx.is_dpu_enabled:
        result["namespace"] = "f5-bnk"

    return result


def _transform_bnk_gatewayclass(
    variables: dict[str, Any], ctx: ProjectContext,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    # DPU mode: FLO deploys the BNK controller in the f5-bnk namespace, so the
    # GatewayClass controllerName must be "f5.com/f5-bnk-f5-cne-controller"
    # (verified live: that name is Accepted by the controller; the f5-operator
    # form stays Pending forever).
    #
    # The OpenTofu pack constructs controllerName from flo_namespace in main.tf,
    # but this module runs kubernetes-direct: its manifest renders
    # "controllerName: ${controller_name}" and falls back to the pack's hardcoded
    # default "f5.com/f5-operator-f5-cne-controller" when controller_name is unset.
    # So we must set controller_name directly — setting flo_namespace alone has no
    # effect on the kubernetes-direct path. flo_namespace is kept for the OpenTofu
    # path / outputs.
    if ctx.is_dpu_enabled:
        result["flo_namespace"] = "f5-bnk"
        result["controller_name"] = "f5.com/f5-bnk-f5-cne-controller"
    return result


# ---------------------------------------------------------------------------
# Dialog-field pre-populate helpers
# ---------------------------------------------------------------------------

def resolve_dialog_field_default(
    inp_name: str,
    module_path: str,
    ctx: ProjectContext,
) -> str | None:
    """Return a pre-populated scalar default for a deploy-dialog input field.

    This is the inverse of the module transforms: while transforms map
    canonical context vars → catalog variable names (e.g. external_self_ips),
    dialog fields use the *user-facing* input names (e.g. external_selfip).

    Called by StackService.get_required_inputs for BM-020 pre-population.
    Returns a string scalar or None (callers do not receive lists).
    """
    # bnk/bnk-vlans: dialog asks for bare-IP strings; transform produces lists.
    # Map the user-facing field → the IP extracted from context.
    if module_path == "bnk/bnk-vlans":
        if inp_name == "external_selfip" and ctx.dpu_external_vlan_ipv4:
            return ctx.dpu_external_vlan_ipv4
        if inp_name == "internal_selfip" and ctx.dpu_internal_vlan_ipv4:
            return ctx.dpu_internal_vlan_ipv4

    return None


# ---------------------------------------------------------------------------
# Transform registry
# ---------------------------------------------------------------------------

TransformFn = Callable[[dict[str, Any], ProjectContext], dict[str, Any]]

MODULE_TRANSFORMS: dict[str, TransformFn] = {
    "bare-metal/install-cni": _transform_install_cni,
    "bare-metal/taint-dpu-node": _transform_taint_dpu_node,
    "k8s/bnk-prerequisites": _transform_bnk_prerequisites,
    "k8s/cert-manager": _transform_cert_manager,
    "k8s/network-setup": _transform_network_setup,
    "bnk/flo": _transform_flo,
    "bnk/cneinstance": _transform_cneinstance,
    "bnk/bnk-vlans": _transform_bnk_vlans,
    "bnk/bnk-gatewayclass": _transform_bnk_gatewayclass,
    # ADR-204: SSH ports of the BNK layer (bare-metal/bnk-*) reuse the SAME d019
    # transforms — they are engine-agnostic. transform_for_module() keys on the
    # module path, so the SSH paths need their own aliases pointing at the
    # existing functions (logic unchanged). See modules/bare_metal/bnk_ssh_base.py.
    "bare-metal/bnk-prerequisites": _transform_bnk_prerequisites,
    "bare-metal/network-setup": _transform_network_setup,
    "bare-metal/cert-manager": _transform_cert_manager,
    "bare-metal/bnk-flo": _transform_flo,
    "bare-metal/bnk-cneinstance": _transform_cneinstance,
    "bare-metal/bnk-vlans": _transform_bnk_vlans,
    "bare-metal/bnk-gatewayclass": _transform_bnk_gatewayclass,
}


def transform_for_module(
    module_path: str,
    variables: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    """Apply module-specific transform if registered.

    Returns only NEW/OVERRIDDEN variables. Caller merges into the pool.
    Unknown module paths return {}.
    """
    transform = MODULE_TRANSFORMS.get(module_path)
    if transform is None:
        return {}
    try:
        result = transform(variables, ctx)
        if result:
            logger.debug(
                "Blueprint transform for '%s' produced: %s",
                module_path,
                list(result.keys()),
            )
        return result
    except Exception:
        logger.exception("Blueprint transform failed for module '%s'", module_path)
        return {}
