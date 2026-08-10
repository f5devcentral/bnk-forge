"""SSH-based probes for DPU state (via rshim SSH or management IP)."""

import logging
import re
from dataclasses import dataclass, field

from services.bare_metal.ssh_session import RemoteSSHSession, SSHSession

logger = logging.getLogger(__name__)


@dataclass
class DpuProbeResult:
    """Structured results from probing a DPU via SSH."""

    ssh_reachable: bool = False
    doca_version: str | None = None
    containerd_version: str | None = None
    runc_version: str | None = None
    k8s_version: str | None = None
    ovs_installed: bool = False
    ovs_bridge_ports: list[str] = field(default_factory=list)
    vf_representors: list[str] = field(default_factory=list)
    hugepages_gb: int = 0
    error: str | None = None


def probe_dpu(ssh: SSHSession | RemoteSSHSession) -> DpuProbeResult:
    """Probe DPU state via SSH (rshim or management IP).

    The SSHSession should already be configured to reach the DPU
    (either via rshim network 192.168.100.2 or management IP).
    """
    result = DpuProbeResult()

    if not ssh.is_reachable():
        result.error = f"DPU SSH unreachable: {ssh.host}:{ssh.port}"
        return result
    result.ssh_reachable = True

    _probe_doca_version(ssh, result)
    _probe_containerd(ssh, result)
    _probe_runc(ssh, result)
    _probe_k8s_version(ssh, result)
    _probe_ovs(ssh, result)
    _probe_hugepages(ssh, result)

    return result


def _probe_doca_version(ssh: SSHSession | RemoteSSHSession, result: DpuProbeResult) -> None:
    """Detect DOCA version on DPU."""
    r = ssh.execute("dpkg -l 2>/dev/null | grep doca-runtime | head -1", timeout=10)
    if r.exit_code == 0 and r.stdout.strip():
        parts = r.stdout.strip().split()
        if len(parts) >= 3:
            result.doca_version = parts[2]  # Version column
            return

    # Fallback: check /etc/mlnx-release
    r = ssh.execute("cat /etc/mlnx-release 2>/dev/null", timeout=5)
    if r.exit_code == 0 and r.stdout.strip():
        result.doca_version = r.stdout.strip()


def _probe_containerd(ssh: SSHSession | RemoteSSHSession, result: DpuProbeResult) -> None:
    """Detect containerd version."""
    r = ssh.execute("containerd --version 2>/dev/null", timeout=10)
    if r.exit_code == 0:
        match = re.search(r"containerd\s+v?([\d.]+)", r.stdout)
        if match:
            result.containerd_version = match.group(1)


def _probe_runc(ssh: SSHSession | RemoteSSHSession, result: DpuProbeResult) -> None:
    """Detect runc version."""
    r = ssh.execute("runc --version 2>/dev/null", timeout=10)
    if r.exit_code == 0:
        match = re.search(r"runc version\s+([\d.]+)", r.stdout)
        if match:
            result.runc_version = match.group(1)


def _probe_k8s_version(ssh: SSHSession | RemoteSSHSession, result: DpuProbeResult) -> None:
    """Detect kubeadm/kubelet version on DPU."""
    r = ssh.execute("kubeadm version -o short 2>/dev/null", timeout=10)
    if r.exit_code == 0 and r.stdout.strip():
        result.k8s_version = r.stdout.strip()
        return

    r = ssh.execute("kubelet --version 2>/dev/null", timeout=10)
    if r.exit_code == 0:
        match = re.search(r"v([\d.]+)", r.stdout)
        if match:
            result.k8s_version = f"v{match.group(1)}"


def _probe_ovs(ssh: SSHSession | RemoteSSHSession, result: DpuProbeResult) -> None:
    """Probe OVS state on DPU."""
    # Check if OVS is installed
    r = ssh.execute("which ovs-vsctl 2>/dev/null", timeout=5)
    if r.exit_code != 0:
        return
    result.ovs_installed = True

    # Get bridge ports
    r = ssh.execute(
        "ovs-vsctl list-ports br-bnk 2>/dev/null || ovs-vsctl list-ports ovs-br0 2>/dev/null", timeout=10
    )
    if r.exit_code == 0 and r.stdout.strip():
        result.ovs_bridge_ports = [p.strip() for p in r.stdout.strip().splitlines() if p.strip()]

    # Find VF representors
    r = ssh.execute("ls /sys/class/net/ 2>/dev/null | grep -E '(pf|vf).*rep|en.*f.*v'", timeout=5)
    if r.exit_code == 0 and r.stdout.strip():
        result.vf_representors = [rep.strip() for rep in r.stdout.strip().splitlines() if rep.strip()]


def _probe_hugepages(ssh: SSHSession | RemoteSSHSession, result: DpuProbeResult) -> None:
    """Probe hugepages on DPU."""
    r = ssh.execute("cat /proc/meminfo | grep HugePages_Total", timeout=5)
    if r.exit_code == 0 and r.stdout.strip():
        match = re.search(r"HugePages_Total:\s+(\d+)", r.stdout)
        if match:
            pages = int(match.group(1))
            r2 = ssh.execute("cat /proc/meminfo | grep Hugepagesize", timeout=5)
            if r2.exit_code == 0:
                size_match = re.search(r"Hugepagesize:\s+(\d+)\s+kB", r2.stdout)
                if size_match:
                    size_kb = int(size_match.group(1))
                    result.hugepages_gb = (pages * size_kb) // (1024 * 1024)
