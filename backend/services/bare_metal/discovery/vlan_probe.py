"""VLAN path validation probe."""

import logging
from dataclasses import dataclass

from services.bare_metal.ssh_session import SSHSession

logger = logging.getLogger(__name__)


@dataclass
class VlanProbeResult:
    """Result of VLAN path validation."""

    vlan_path_valid: bool = False
    test_interface: str | None = None
    gateway_reachable: bool = False
    error: str | None = None


def probe_vlan(
    ssh: SSHSession,
    pf_name: str,
    vlan_id: int,
    gateway_ip: str,
    test_ip: str,
) -> VlanProbeResult:
    """Test VLAN path by creating temporary sub-interface.

    Steps:
    1. Create VLAN sub-interface on PF
    2. Assign test IP
    3. Bring interface up
    4. Ping gateway
    5. Clean up temporary config

    This is a DESTRUCTIVE probe — it creates a temporary network interface.
    Only run when explicitly requested (probe_vlan=True).
    """
    result = VlanProbeResult()
    vlan_iface = f"{pf_name}.{vlan_id}"
    result.test_interface = vlan_iface

    try:
        # 1. Create VLAN sub-interface
        r = ssh.execute(f"ip link add link {pf_name} name {vlan_iface} type vlan id {vlan_id}", timeout=10)
        if r.exit_code != 0:
            result.error = f"Failed to create VLAN interface: {r.stderr.strip()}"
            return result

        # 2. Assign test IP
        r = ssh.execute(f"ip addr add {test_ip} dev {vlan_iface}", timeout=10)
        if r.exit_code != 0:
            result.error = f"Failed to assign IP: {r.stderr.strip()}"
            return result

        # 3. Bring up
        r = ssh.execute(f"ip link set {vlan_iface} up", timeout=10)
        if r.exit_code != 0:
            result.error = f"Failed to bring up interface: {r.stderr.strip()}"
            return result

        # 4. Ping gateway (3 pings, 5s timeout)
        # Extract gateway IP without CIDR suffix
        gw_ip = gateway_ip.split("/")[0]
        r = ssh.execute(f"ping -c 3 -W 5 -I {vlan_iface} {gw_ip}", timeout=30)
        result.gateway_reachable = r.exit_code == 0
        result.vlan_path_valid = result.gateway_reachable

        if not result.gateway_reachable:
            result.error = f"Gateway {gw_ip} unreachable via {vlan_iface}"

    finally:
        # 5. Always clean up
        _cleanup_vlan_interface(ssh, vlan_iface)

    return result


def _cleanup_vlan_interface(ssh: SSHSession, vlan_iface: str) -> None:
    """Remove temporary VLAN sub-interface."""
    ssh.execute(f"ip link set {vlan_iface} down 2>/dev/null", timeout=5)
    ssh.execute(f"ip link delete {vlan_iface} 2>/dev/null", timeout=5)
