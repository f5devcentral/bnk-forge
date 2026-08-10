"""SSH-based probes for bare-metal host system information."""

import logging
import re
from dataclasses import dataclass, field

from services.bare_metal.ssh_session import SSHSession

logger = logging.getLogger(__name__)


@dataclass
class HostProbeResult:
    """Structured results from probing a bare-metal host via SSH."""

    ssh_reachable: bool = False
    os_type: str | None = None  # "ubuntu", "rhel", "debian", etc.
    os_version: str | None = None
    os_pretty_name: str | None = None
    kernel_version: str | None = None
    architecture: str | None = None  # "x86_64", "aarch64"
    nic_mode: str | None = None  # Normalized: "0" (NIC/SuperNIC) or "1" (DPU mode)
    mst_device: str | None = None  # e.g. "/dev/mst/mt41692_pciconf0"
    pf_interfaces: list[dict] = field(default_factory=list)  # [{name, link_state, speed, driver}]
    vf_count: int = 0
    hugepages_gb: int = 0
    dpu_pci_detected: bool = False  # True if BlueField device found via lspci
    rshim_present: bool = False
    k8s_installed: bool = False
    k8s_running: bool = False
    k8s_version: str | None = None
    default_route: str | None = None       # e.g. "1.1.1.1 via 10.176.11.1 dev ens4f1"
    default_route_iface: str | None = None # Interface name from default route
    phase_c_deposited: bool = False
    phase_c_completed: bool = False
    doca_status: dict | None = None  # DOCA host package readiness snapshot
    missing_prerequisites: list[str] = field(default_factory=list)
    error: str | None = None


def probe_host(ssh: SSHSession) -> HostProbeResult:
    """Run SSH-based probes on a bare-metal host.

    Probes (in order):
    1. SSH reachability
    2. OS info (from /etc/os-release)
    3. Kernel and architecture
    4. DPU PCIe detection (lspci for BlueField devices)
    5. Host prerequisites (mst-tools, rshim)
    6. NIC mode via mlxconfig (INTERNAL_CPU_MODEL) → normalized to "0"/"1"
    7. PF/VF interfaces
    8. Hugepages
    9. Rshim device presence
    10. K8s state
    11. Default route
    12. Phase C deployment state
    """
    result = HostProbeResult()

    # 1. Check SSH reachability
    if not ssh.is_reachable():
        result.error = f"SSH unreachable: {ssh.host}:{ssh.port}"
        return result
    result.ssh_reachable = True

    # 2. OS info
    _probe_os_info(ssh, result)

    # 3. Kernel and arch
    _probe_kernel(ssh, result)

    # 4. DPU PCIe detection (lspci)
    _probe_dpu_pci(ssh, result)

    # 5. Host prerequisites (must run before NIC mode — mst-tools required for mlxconfig)
    _probe_prerequisites(ssh, result)

    # 6. NIC mode
    _probe_nic_mode(ssh, result)

    # 7. PF/VF interfaces
    _probe_interfaces(ssh, result)

    # 8. Hugepages
    _probe_hugepages(ssh, result)

    # 9. Rshim
    _probe_rshim(ssh, result)

    # 10. K8s
    _probe_k8s(ssh, result)

    # 11. Default route
    _probe_default_route(ssh, result)

    # 12. Phase C deployment state
    _probe_phase_c(ssh, result)

    # 12. DOCA host package status
    result.doca_status = _probe_doca_status(ssh)

    return result


def _probe_os_info(ssh: SSHSession, result: HostProbeResult) -> None:
    """Probe OS type and version from /etc/os-release."""
    r = ssh.execute("cat /etc/os-release", timeout=10)
    if r.exit_code != 0:
        return

    for line in r.stdout.splitlines():
        if line.startswith("ID="):
            result.os_type = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("VERSION_ID="):
            result.os_version = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("PRETTY_NAME="):
            result.os_pretty_name = line.split("=", 1)[1].strip().strip('"')


def _probe_kernel(ssh: SSHSession, result: HostProbeResult) -> None:
    """Probe kernel version and architecture."""
    r = ssh.execute("uname -r", timeout=10)
    if r.exit_code == 0:
        result.kernel_version = r.stdout.strip()

    r = ssh.execute("uname -m", timeout=10)
    if r.exit_code == 0:
        result.architecture = r.stdout.strip()


# BlueField PCIe device ID patterns (vendor 15b3 = Mellanox/NVIDIA)
_BF_PCI_PATTERNS = [
    "15b3:a2d",   # BlueField-3 family
    "15b3:a2dc",  # BlueField-3 ConnectX-7
    "15b3:a2d6",  # BlueField-2
]


def _probe_dpu_pci(ssh: SSHSession, result: HostProbeResult) -> None:
    """Detect BlueField DPU presence via lspci PCIe device scan.

    This runs before mlxconfig/rshim checks and provides an early
    hardware-level confirmation that a DPU is physically installed.
    """
    r = ssh.execute("lspci -nn 2>/dev/null", timeout=15)
    if r.exit_code != 0 or not r.stdout.strip():
        return

    for line in r.stdout.splitlines():
        line_lower = line.lower()
        for pattern in _BF_PCI_PATTERNS:
            if pattern in line_lower:
                result.dpu_pci_detected = True
                return


def _probe_prerequisites(ssh: SSHSession, result: HostProbeResult) -> None:
    """Check for critical host packages required for DPU management.

    Populates result.missing_prerequisites with names of absent packages.
    This runs before _probe_nic_mode so callers have context when that
    probe silently returns nothing (mst-tools not installed → mlxconfig absent).
    """
    # mst-tools: provides mlxconfig/mst, required for NIC mode detection/change
    r = ssh.execute("which mst 2>/dev/null", timeout=5)
    if r.exit_code != 0 or not r.stdout.strip():
        result.missing_prerequisites.append("mst-tools")

    # rshim: provides bfb-install, required for BFB flashing
    r = ssh.execute("which bfb-install 2>/dev/null", timeout=5)
    if r.exit_code != 0 or not r.stdout.strip():
        result.missing_prerequisites.append("rshim")


def _probe_nic_mode(ssh: SSHSession, result: HostProbeResult) -> None:
    """Probe NIC mode via mlxconfig INTERNAL_CPU_MODEL attribute.

    INTERNAL_CPU_MODEL values:
    - 0 = NIC/SuperNIC mode (host PF → switch directly, ARM inactive)
    - 1 = DPU mode (eSwitch, VFs, full ARM OS)

    mlxconfig output format:
        INTERNAL_CPU_MODEL       EMBEDDED_CPU(1)
    or: INTERNAL_CPU_MODEL       SEPARATED_HOST(0)

    We normalize to just "0" or "1" as the canonical representation.
    The human-readable labels (EMBEDDED_CPU, SEPARATED_HOST) are firmware-
    version-dependent and should not be relied upon for logic.

    Also extracts the MST device path for use in mode change commands.
    """
    r = ssh.execute(
        "sudo mlxconfig -d /dev/mst/mt41692_pciconf0 q INTERNAL_CPU_MODEL 2>/dev/null || "
        "sudo mlxconfig -d $(ls /dev/mst/mt*_pciconf0 2>/dev/null | head -1) q INTERNAL_CPU_MODEL 2>/dev/null",
        timeout=15,
    )
    if r.exit_code == 0 and "INTERNAL_CPU_MODEL" in r.stdout:
        # Extract device path from "Device: /dev/mst/..." line
        for line in r.stdout.splitlines():
            if line.strip().startswith("Device:"):
                parts = line.strip().split()
                if len(parts) >= 2:
                    result.mst_device = parts[1]
            if "INTERNAL_CPU_MODEL" in line:
                # Parse: "INTERNAL_CPU_MODEL       EMBEDDED_CPU(1)"
                # Normalize to just "0" or "1"
                parts = line.strip().split()
                if len(parts) >= 2:
                    raw_value = parts[-1]
                    result.nic_mode = _normalize_nic_mode(raw_value)


def _normalize_nic_mode(raw_value: str) -> str:
    """Normalize mlxconfig INTERNAL_CPU_MODEL output to canonical "0" or "1".

    Handles various firmware output formats:
        "EMBEDDED_CPU(1)"    → "1"  (DPU mode)
        "SEPARATED_HOST(0)"  → "0"  (NIC/SuperNIC mode)
        "1"                  → "1"
        "0"                  → "0"
        "(1)"                → "1"
        "(0)"                → "0"
    """
    raw = raw_value.strip()
    # Extract numeric value from parentheses: "EMBEDDED_CPU(1)" → "1"
    if "(" in raw and ")" in raw:
        start = raw.rindex("(") + 1
        end = raw.rindex(")")
        return raw[start:end]
    # Already numeric
    if raw in ("0", "1"):
        return raw
    # Fallback: return raw for unknown formats
    return raw


def _probe_interfaces(ssh: SSHSession, result: HostProbeResult) -> None:
    """Probe physical function interfaces and VF count."""
    # Find Mellanox PFs (mlx5 driver)
    r = ssh.execute("ls -la /sys/class/net/*/device/driver 2>/dev/null | grep mlx5", timeout=10)
    if r.exit_code == 0:
        for line in r.stdout.splitlines():
            match = re.search(r"/sys/class/net/(\w+)/", line)
            if match:
                iface_name = match.group(1)
                # Skip VF representors (contains 'Rep' or matches enp*f*v*)
                if "Rep" in iface_name or re.match(r".*f\d+v\d+", iface_name):
                    continue
                pf_info = _get_interface_info(ssh, iface_name)
                if pf_info:
                    result.pf_interfaces.append(pf_info)

    # Count VFs
    r = ssh.execute("cat /sys/class/net/*/device/sriov_numvfs 2>/dev/null", timeout=10)
    if r.exit_code == 0:
        for line in r.stdout.strip().splitlines():
            try:
                count = int(line.strip())
                result.vf_count += count
            except ValueError:
                continue


def _get_interface_info(ssh: SSHSession, iface_name: str) -> dict | None:
    """Get info for a single network interface."""
    info: dict = {"name": iface_name}

    # Link state
    r = ssh.execute(f"cat /sys/class/net/{iface_name}/operstate 2>/dev/null", timeout=5)
    if r.exit_code == 0:
        info["link_state"] = r.stdout.strip()

    # Speed
    r = ssh.execute(f"cat /sys/class/net/{iface_name}/speed 2>/dev/null", timeout=5)
    if r.exit_code == 0:
        try:
            info["speed_mbps"] = int(r.stdout.strip())
        except ValueError:
            pass

    # Driver
    r = ssh.execute(
        f"basename $(readlink /sys/class/net/{iface_name}/device/driver) 2>/dev/null", timeout=5
    )
    if r.exit_code == 0:
        info["driver"] = r.stdout.strip()

    return info


def _probe_hugepages(ssh: SSHSession, result: HostProbeResult) -> None:
    """Probe hugepages allocation."""
    r = ssh.execute("cat /proc/meminfo | grep HugePages_Total", timeout=10)
    if r.exit_code == 0 and r.stdout.strip():
        match = re.search(r"HugePages_Total:\s+(\d+)", r.stdout)
        if match:
            pages = int(match.group(1))
            # Default hugepage size is 2MB; for 1G pages, check Hugepagesize
            r2 = ssh.execute("cat /proc/meminfo | grep Hugepagesize", timeout=5)
            if r2.exit_code == 0:
                size_match = re.search(r"Hugepagesize:\s+(\d+)\s+kB", r2.stdout)
                if size_match:
                    size_kb = int(size_match.group(1))
                    result.hugepages_gb = (pages * size_kb) // (1024 * 1024)


def _probe_rshim(ssh: SSHSession, result: HostProbeResult) -> None:
    """Check if rshim device is present (indicates BF3 DPU)."""
    r = ssh.execute("ls /dev/rshim*/misc 2>/dev/null", timeout=5)
    result.rshim_present = r.exit_code == 0 and bool(r.stdout.strip())


def _probe_k8s(ssh: SSHSession, result: HostProbeResult) -> None:
    """Probe K8s installation state.

    'Installed' means kubeadm or kubelet is present — the actual K8s
    infrastructure, not just kubectl (which is a standalone CLI tool
    often installed independently).
    """
    # Check if kubeadm or kubelet is installed (real K8s infrastructure)
    r = ssh.execute("which kubeadm 2>/dev/null || which kubelet 2>/dev/null", timeout=5)
    if r.exit_code == 0 and r.stdout.strip():
        result.k8s_installed = True

    # Check version (kubectl may exist without kubeadm)
    r = ssh.execute("kubeadm version -o short 2>/dev/null || kubectl version --client -o json 2>/dev/null | grep gitVersion", timeout=10)
    if r.exit_code == 0 and r.stdout.strip():
        match = re.search(r'v?(\d+\.\d+\.\d+)', r.stdout)
        if match:
            result.k8s_version = f"v{match.group(1)}"

    # Check if cluster is actually running
    r = ssh.execute("kubectl get nodes --no-headers 2>/dev/null", timeout=15)
    result.k8s_running = r.exit_code == 0 and bool(r.stdout.strip())


def _probe_default_route(ssh: SSHSession, result: HostProbeResult) -> None:
    """Probe default route to identify independent NIC."""
    r = ssh.execute("ip route show default 2>/dev/null | head -1", timeout=10)
    if r.exit_code == 0 and r.stdout.strip():
        result.default_route = r.stdout.strip()
        # Extract interface name: "default via X.X.X.X dev ethN ..."
        match = re.search(r"dev\s+(\S+)", r.stdout)
        if match:
            result.default_route_iface = match.group(1)


def _probe_phase_c(ssh: SSHSession, result: HostProbeResult) -> None:
    """Probe Phase C BNK deployment state.

    Phase C is the final deployment phase that installs BNK platform components.
    - phase_c_deposited: True if Phase C files are present (deployment prepared)
    - phase_c_completed: True if Phase C ran successfully to completion
    """
    # Check for Phase C directory or service file
    r = ssh.execute("test -d /opt/bnk/phase-c && echo 'deposited'", timeout=5)
    if r.exit_code == 0 and "deposited" in r.stdout:
        result.phase_c_deposited = True

    # Check for completion marker or successful service exit
    r = ssh.execute(
        "test -f /opt/bnk/phase-c/.completed && echo 'done' || "
        "systemctl show bnk-phase-c --property=Result 2>/dev/null | grep -q 'success' && echo 'done'",
        timeout=10,
    )
    if r.exit_code == 0 and "done" in r.stdout:
        result.phase_c_completed = True


def _probe_doca_status(ssh: SSHSession) -> dict:
    """Probe DOCA host package status on the host.

    Checks three layers:
    1. Package level — doca-host repo installed, doca-all profile present
    2. Binary level — key tools BNK needs (mst, mlxconfig, rshim, bfb-install)
    3. Service level — openibd driver loaded, MST devices present
    """
    checks = {
        "repo_configured": "dpkg -s doca-host > /dev/null 2>&1 && echo ok || echo missing",
        "profile_installed": "dpkg -s doca-all > /dev/null 2>&1 && echo ok || echo missing",
        "doca_version": "dpkg-query -W -f='${Version}' doca-all 2>/dev/null || echo ''",
        "mst_present": "command -v mst > /dev/null 2>&1 && echo ok || echo missing",
        "mlxconfig_present": "command -v mlxconfig > /dev/null 2>&1 && echo ok || echo missing",
        "rshim_present": "command -v rshim > /dev/null 2>&1 && echo ok || echo missing",
        "bfb_install_present": "command -v bfb-install > /dev/null 2>&1 && echo ok || echo missing",
        "openibd_loaded": "systemctl is-active openibd > /dev/null 2>&1 && echo active || echo inactive",
        "mst_devices": "ls /dev/mst/mt* 2>/dev/null | head -3 || echo ''",
    }
    results = {}
    for key, cmd in checks.items():
        r = ssh.execute(cmd, timeout=10)
        results[key] = r.stdout.strip() if r.exit_code == 0 else ""

    return {
        "repo_configured": results["repo_configured"] == "ok",
        "profile_installed": results["profile_installed"] == "ok",
        "doca_version": results["doca_version"] or None,
        "mst_present": results["mst_present"] == "ok",
        "mlxconfig_present": results["mlxconfig_present"] == "ok",
        "rshim_present": results["rshim_present"] == "ok",
        "bfb_install_present": results["bfb_install_present"] == "ok",
        "openibd_loaded": results["openibd_loaded"] == "active",
        "mst_devices": [d for d in results["mst_devices"].splitlines() if d],
        "ready": all([
            results["mst_present"] == "ok",
            results["mlxconfig_present"] == "ok",
            results["rshim_present"] == "ok",
            results["bfb_install_present"] == "ok",
        ]),
    }
