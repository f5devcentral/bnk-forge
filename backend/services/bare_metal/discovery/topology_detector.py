"""Auto-detect deployment topology from probe results.

Uses INTERNAL_CPU_MODEL numeric values:
  "0" = NIC/SuperNIC mode (host PF → switch directly, ARM inactive)
  "1" = DPU mode (eSwitch, VFs, full ARM OS running on BlueField)
"""

import logging
import re
from dataclasses import dataclass

from services.bare_metal.discovery.host_probe import HostProbeResult

logger = logging.getLogger(__name__)


@dataclass
class TopologyRecommendation:
    """Recommended topology with reasoning."""

    topology: str  # BareMetalTopology value: "regular", "bf3", "bf3_ipmi", "bmc", "dual_dpu_obmc"
    confidence: str  # "high", "medium", "low"
    reasons: list[str]  # Human-readable reasoning


def _count_dpu_pci_domains(host_result: HostProbeResult) -> int:
    """Count distinct DPU PCI domains from interface names.

    On multi-DPU systems, each DPU has PFs on a different PCI domain.
    Example: enp3s0f0 (domain 0000:) and enP2p3s0f0 (domain 0002:).
    We count unique PCI bus paths extracted from sysfs-style names.
    """
    mlx5_pfs = [
        pf.get("name", "")
        for pf in host_result.pf_interfaces
        if pf.get("driver") == "mlx5_core" and pf.get("link_state") == "up"
    ]
    if len(mlx5_pfs) < 4:  # Need at least 4 PFs (2 per DPU) for dual-DPU
        return 1 if mlx5_pfs else 0
    # Group by PCI bus prefix: "enp3s0f0np0" → "enp3s0", "enP2p3s0f0np0" → "enP2p3s0"
    bus_prefixes = set()
    for name in mlx5_pfs:
        # Extract up to the function part: match everything before f<N>
        m = re.match(r"(.*?)f\d+", name)
        if m:
            bus_prefixes.add(m.group(1))
    return len(bus_prefixes) if bus_prefixes else 1


def detect_topology(
    host_result: HostProbeResult,
    bmc_result: object | None = None,  # BmcProbeResult (from bmc_probe.py)
) -> TopologyRecommendation:
    """Auto-detect the appropriate deployment topology from probe results.

    Decision tree:
    0. If 2+ DPU PCI domains, no rshim on host, no independent NIC → "dual_dpu_obmc"
       - MGX-class servers: rshim access via DPU OpenBMC, not host rshim
    1. If host has independent NIC (non-BF3 connectivity) → "regular"
       - Regardless of NIC mode — host survives DPU reboot
    2. If INTERNAL_CPU_MODEL=0 (NIC/SuperNIC) AND Redfish available → "bmc"
    3. If INTERNAL_CPU_MODEL=0 (NIC/SuperNIC) AND IPMI available → "bf3_ipmi"
    4. If INTERNAL_CPU_MODEL=0 (NIC/SuperNIC) AND no BMC → "bf3"
       - Mode change via BFB flash, connectivity risk during reboot
    5. If host network through BF3 (VFs, no independent NIC) → "bf3"
    6. Default: "regular" (safest assumption)
    """
    reasons: list[str] = []

    # Parse NIC mode (normalized to "0" or "1")
    is_nic_mode = is_supernic_mode(host_result.nic_mode)
    is_dpu = is_dpu_mode(host_result.nic_mode)
    has_independent_nic = _has_independent_nic(host_result)
    has_rshim = host_result.rshim_present
    has_dpu_hw = host_result.dpu_pci_detected
    has_vfs = host_result.vf_count > 0

    # Extract BMC capabilities
    redfish_available = False
    ipmi_available = False
    if bmc_result is not None:
        redfish_available = getattr(bmc_result, "redfish_reachable", False)
        ipmi_available = getattr(bmc_result, "ipmi_reachable", False)

    # 0. Dual DPU + BMC rshim (dual_dpu_obmc) — MGX-class servers
    dpu_domain_count = _count_dpu_pci_domains(host_result)
    if dpu_domain_count >= 2 and not has_rshim and not has_independent_nic:
        reasons.append(f"Multiple DPU PCI domains detected ({dpu_domain_count})")
        reasons.append("No usable rshim on host — rshim access via DPU OpenBMC")
        reasons.append("Default route through DPU PF (no independent NIC)")
        return TopologyRecommendation(
            topology="dual_dpu_obmc",
            confidence="high",
            reasons=reasons,
        )

    # 1. Independent NIC = "regular" topology (host survives DPU reboot)
    if has_independent_nic:
        reasons.append("Host has independent NIC with connectivity (non-BlueField default route)")
        if is_dpu or has_rshim or has_dpu_hw:
            reasons.append("DPU is present")
        if is_nic_mode:
            reasons.append("NIC is in SuperNIC mode (INTERNAL_CPU_MODEL=0) — will need mode change + BFB flash")
        return TopologyRecommendation(
            topology="regular",
            confidence="high",
            reasons=reasons,
        )

    # 2-4. SuperNIC mode without independent NIC — connectivity risk
    if is_nic_mode:
        reasons.append("NIC in SuperNIC mode (INTERNAL_CPU_MODEL=0), no independent NIC")

        if redfish_available:
            reasons.append("Redfish BMC available — can set BIOS attributes for mode change")
            return TopologyRecommendation(topology="bmc", confidence="high", reasons=reasons)

        if ipmi_available:
            reasons.append("IPMI available — can power cycle after mlxconfig mode change")
            return TopologyRecommendation(topology="bf3_ipmi", confidence="high", reasons=reasons)

        # SuperNIC mode but no BMC — must use BFB flash for mode change
        reasons.append("No BMC access — mode change via BFB flash (connectivity risk)")
        return TopologyRecommendation(topology="bf3", confidence="medium", reasons=reasons)

    # 5. DPU mode but host network through BF3 (VFs present, no independent NIC)
    if has_vfs and not has_independent_nic:
        reasons.append("VFs present but no independent NIC — host network likely through BF3")
        return TopologyRecommendation(topology="bf3", confidence="medium", reasons=reasons)

    # 6. Default fallback
    reasons.append("Could not conclusively determine topology — defaulting to regular")
    if not has_dpu_hw and not has_rshim:
        reasons.append("No DPU hardware detected (no BlueField PCIe device, no rshim)")
    return TopologyRecommendation(topology="regular", confidence="low", reasons=reasons)


def is_supernic_mode(nic_mode: str | None) -> bool:
    """Check if INTERNAL_CPU_MODEL indicates NIC/SuperNIC mode (value 0)."""
    if not nic_mode:
        return False
    normalized = nic_mode.strip()
    # Canonical: "0"
    if normalized == "0":
        return True
    # Legacy: "SEPARATED_HOST(0)" — extract from parentheses
    if "(0)" in normalized:
        return True
    return False


def is_dpu_mode(nic_mode: str | None) -> bool:
    """Check if INTERNAL_CPU_MODEL indicates DPU mode (value 1)."""
    if not nic_mode:
        return False
    normalized = nic_mode.strip()
    # Canonical: "1"
    if normalized == "1":
        return True
    # Legacy: "EMBEDDED_CPU(1)" — extract from parentheses
    if "(1)" in normalized:
        return True
    return False


NIC_MODE_DISPLAY: dict[str, str] = {
    "0": "NIC/SuperNIC (INTERNAL_CPU_MODEL=0)",
    "1": "DPU (INTERNAL_CPU_MODEL=1)",
}


def nic_mode_display(nic_mode: str | None) -> str:
    """Human-readable NIC mode string for UI display."""
    if not nic_mode:
        return "Unknown"
    return NIC_MODE_DISPLAY.get(nic_mode.strip(), f"Unknown ({nic_mode})")


def _has_independent_nic(host_result: HostProbeResult) -> bool:
    """Check if host has an independent NIC (not through the BF3).

    Detection methods (in priority order):
    1. Default route goes through a non-Mellanox interface
    2. Multiple PFs with link up AND different drivers (mixed NIC vendors)

    Note: Two Mellanox PFs with link up does NOT indicate independent NIC —
    a dual-port BF3 has two PFs (e.g., enp3s0f0 + enp3s0f1) both with
    mlx5_core driver.
    """
    # Method 1: Default route interface is NOT a Mellanox PF
    if host_result.default_route_iface:
        mellanox_iface_names = {
            pf.get("name")
            for pf in host_result.pf_interfaces
            if pf.get("driver") == "mlx5_core"
        }
        if host_result.default_route_iface not in mellanox_iface_names:
            return True

    # Method 2: PFs with link up from different drivers (e.g., mlx5 + i40e/ice)
    up_pf_drivers = {
        pf.get("driver")
        for pf in host_result.pf_interfaces
        if pf.get("link_state") == "up" and pf.get("driver")
    }
    if len(up_pf_drivers) > 1:
        return True

    return False
