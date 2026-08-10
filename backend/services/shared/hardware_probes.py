"""Shared hardware probe functions for SSH-based host discovery.

Extracted from discovery_service.py to be reusable by both:
- Discovery tab (services/discovery_service.py)
- Bare Metal tab (services/bare_metal/discovery/)

All functions take a paramiko.SSHClient and return structured dicts.
No database access, no model dependencies.
"""

import json
import logging
import re
from typing import Any

import paramiko

logger = logging.getLogger(__name__)

SSH_COMMAND_TIMEOUT = 30  # seconds


def ssh_exec(client: paramiko.SSHClient, command: str, *, timeout: int = SSH_COMMAND_TIMEOUT) -> tuple[int, str]:
    """Execute a command via paramiko and return (exit_code, stdout)."""
    _stdin, stdout, _stderr = client.exec_command(command, timeout=timeout)
    exit_code = stdout.channel.recv_exit_status()
    output = stdout.read().decode("utf-8", errors="replace").strip()
    return exit_code, output


# BlueField-3 PCI device IDs (Mellanox/NVIDIA)
BF3_PATTERNS = [
    r"Mellanox.*BlueField-3",
    r"NVIDIA.*BlueField-3",
    r"15b3:a2d[0-9a-f]",  # BF3 device IDs
    r"15b3:a2dc",          # BF3 ConnectX-7
]

SOC_MGMT_PATTERN = re.compile(r"SoC Management Interface", re.IGNORECASE)
# PCI bridge entries must be excluded from DPU counting: a single BF3 DPU
# exposes many bridges in its PCIe hierarchy, which would inflate the count.
# lspci -nn inserts the class code between class name and colon, e.g.:
#   "0000:01:00.0 PCI bridge [0604]: Mellanox..."
# so match "PCI bridge" as a word boundary, not "PCI bridge:".
PCI_BRIDGE_RE = re.compile(r"^\S+\s+PCI bridge\b", re.IGNORECASE)


def detect_dpus(
    client: paramiko.SSHClient, arch: str | None, log_lines: list[str]
) -> dict[str, Any]:
    """Detect BlueField-3 DPUs via lspci and architecture."""
    result: dict[str, Any] = {
        "is_dpu_host": False,
        "is_dpu_node": False,
        "dpu_count": 0,
        "dpu_details": [],
    }

    # If aarch64 + BlueField markers -> this IS a DPU node
    if arch == "aarch64":
        rc, out = ssh_exec(client, "cat /etc/mlnx-release 2>/dev/null || cat /etc/bf-release 2>/dev/null")
        if rc == 0 and out:
            result["is_dpu_node"] = True
            log_lines.append(f"DPU node detected (aarch64 + bf-release): {out[:100]}")
            return result

        rc, out = ssh_exec(client, "cat /sys/bus/auxiliary/devices/mlx5_core.sf.*/sfnum 2>/dev/null")
        if rc == 0 and out:
            result["is_dpu_node"] = True
            log_lines.append("DPU node detected (aarch64 + mlx5 sub-functions)")
            return result

    # x86_64 host -- look for BF3 in PCIe.
    # `-D` forces full domain prefix on every line ("0000:01:00.0"). Without
    # it, lspci elides the domain on systems with a single PCI domain but
    # keeps it on multi-domain hosts -- and BlueField hosts often DO have
    # multiple domains, one per DPU, where short BDFs collide
    # (e.g. 0000:00:01.0 vs 0001:00:01.0). Always-domain output keeps
    # downstream dedup keys unique.
    rc, out = ssh_exec(client, "lspci -Dnn 2>/dev/null")
    if rc != 0 or not out:
        log_lines.append("lspci not available or returned empty — cannot detect DPUs")
        return result

    log_lines.append(f"lspci output ({len(out.splitlines())} lines)")

    lspci_lines = out.splitlines()

    # Collect all BF3-related PCI endpoint entries.
    # Skip "PCI bridge" class lines: a single DPU exposes many bridges in its
    # PCIe hierarchy (upstream port, downstream ports, etc.) but the actual
    # endpoints (Ethernet controllers, SoC Management Interface) all share the
    # same bus:device address and collapse to one entry after deduplication.
    # Counting bridges inflated the DPU count to 9x per physical DPU.
    dpu_devices = []
    for line in lspci_lines:
        if PCI_BRIDGE_RE.match(line):
            continue
        for pattern in BF3_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                parts = line.split(" ", 1)
                pci_addr = parts[0] if parts else ""
                description = parts[1] if len(parts) > 1 else line
                dpu_devices.append({
                    "pci_address": pci_addr,
                    "description": description.strip(),
                })
                break

    if not dpu_devices:
        return result

    # Build a set of bus:device addresses that have SoC Management Interface
    soc_mgmt_addrs: set[str] = set()
    for line in lspci_lines:
        if SOC_MGMT_PATTERN.search(line):
            parts = line.split(" ", 1)
            pci_addr = parts[0] if parts else ""
            base_addr = pci_addr.rsplit(".", 1)[0] if "." in pci_addr else pci_addr
            soc_mgmt_addrs.add(base_addr)

    # Deduplicate by bus:device (ignore function number)
    unique_addrs: set[str] = set()
    unique_devices = []
    any_soc_mgmt_missing = False
    for d in dpu_devices:
        base_addr = d["pci_address"].rsplit(".", 1)[0] if "." in d["pci_address"] else d["pci_address"]
        if base_addr not in unique_addrs:
            unique_addrs.add(base_addr)
            has_soc_mgmt = base_addr in soc_mgmt_addrs
            if not has_soc_mgmt:
                any_soc_mgmt_missing = True
            unique_devices.append({
                **d,
                "soc_mgmt_available": has_soc_mgmt,
            })

    result["is_dpu_host"] = True
    result["dpu_count"] = len(unique_devices)
    result["dpu_details"] = unique_devices
    result["dpu_soc_mgmt_missing"] = any_soc_mgmt_missing
    log_lines.append(f"Found {len(unique_devices)} BlueField-3 DPU(s)")
    if any_soc_mgmt_missing:
        missing = [d["pci_address"] for d in unique_devices if not d["soc_mgmt_available"]]
        log_lines.append(
            f"WARNING: SoC Management Interface missing for DPU(s) at {', '.join(missing)} "
            f"— likely Zero-Trust mode, imaging will not be possible"
        )
    else:
        log_lines.append("SoC Management Interface available for all DPUs")

    return result


SYSFS_NET_PROBE = (
    "for iface in $(ls /sys/class/net 2>/dev/null); do "
    "  [ \"$iface\" = lo ] && continue; "
    "  pci=\"\"; is_vf=0; "
    "  if [ -L /sys/class/net/$iface/device ]; then "
    "    target=$(readlink /sys/class/net/$iface/device 2>/dev/null); "
    "    pci=$(basename \"$target\"); "
    "    [ -L /sys/class/net/$iface/device/physfn ] && is_vf=1; "
    "  fi; "
    "  echo \"$iface|$pci|$is_vf\"; "
    "done"
)


def normalize_pci_base(pci: str | None) -> str | None:
    """Return the bus:dev portion of a PCI address (drop function, drop domain).

    Accepts "0000:01:00.0" and "01:00.0" forms; returns "01:00".
    """
    if not pci:
        return None
    parts = pci.split(":")
    if len(parts) < 2:
        return pci
    bus = parts[-2]
    dev = parts[-1].split(".")[0]
    return f"{bus}:{dev}"


def classify_interface(
    *,
    name: str,
    pci: str | None,
    is_vf: bool,
    operstate: str,
    link_type: str,
    dpu_pci_bases: set[str],
) -> str | None:
    """Return 'builtin' | 'bluefield' | 'virtual', or None if the iface should be excluded.

    VFs are excluded entirely so the connectivity matrix only reports PFs.
    """
    if is_vf:
        return None

    # Common kernel virtual interfaces we never want in the matrix.
    if name == "lo":
        return None
    virtual_prefixes = (
        "docker", "br-", "veth", "virbr", "vxlan", "cni", "flannel",
        "kube-", "cali", "tun", "tap", "wg", "vnet", "ovs-",
    )
    if any(name.startswith(p) for p in virtual_prefixes):
        return "virtual"

    if not pci:
        # No backing PCI device -> kernel-virtual (bonds, bridges, dummy, etc.)
        return "virtual"

    if link_type and link_type != "ether":
        return "virtual"

    base = normalize_pci_base(pci)
    if base and base in dpu_pci_bases:
        return "bluefield"

    return "builtin"


def detect_network_interfaces(
    client: paramiko.SSHClient, log_lines: list[str], dpu_details: list[dict] | None
) -> list[dict] | None:
    """Detect network interfaces, their addresses, and classify built-in vs BlueField-3."""
    rc, addr_out = ssh_exec(client, "ip -j addr show 2>/dev/null")
    if rc != 0 or not addr_out:
        # Fallback: minimal text-mode (no addresses, no classification)
        rc, out = ssh_exec(client, "ip -br link show 2>/dev/null")
        if rc != 0:
            return None
        interfaces = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0] != "lo":
                interfaces.append({
                    "name": parts[0],
                    "state": parts[1],
                    "mac": parts[2],
                    "kind": "builtin",
                    "addresses": [],
                })
        log_lines.append(f"interfaces: {len(interfaces)} found (text mode)")
        return interfaces if interfaces else None

    try:
        links = json.loads(addr_out)
    except json.JSONDecodeError:
        return None
    if not isinstance(links, list):
        return None

    # Gather sysfs PCI bus-info + VF flag for every interface.
    sysfs_map: dict[str, tuple[str | None, bool]] = {}
    rc, sysfs_out = ssh_exec(client, SYSFS_NET_PROBE)
    if rc == 0 and sysfs_out:
        for line in sysfs_out.splitlines():
            parts = line.split("|")
            if len(parts) >= 3:
                name = parts[0].strip()
                pci = parts[1].strip() or None
                is_vf = parts[2].strip() == "1"
                sysfs_map[name] = (pci, is_vf)

    dpu_pci_bases: set[str] = set()
    for d in dpu_details or []:
        base = normalize_pci_base(d.get("pci_address"))
        if base:
            dpu_pci_bases.add(base)

    interfaces: list[dict] = []
    for link in links:
        if not isinstance(link, dict):
            continue
        name = link.get("ifname", "")
        if not name or name == "lo":
            continue
        pci, is_vf = sysfs_map.get(name, (None, False))
        kind = classify_interface(
            name=name,
            pci=pci,
            is_vf=is_vf,
            operstate=link.get("operstate", ""),
            link_type=link.get("link_type", ""),
            dpu_pci_bases=dpu_pci_bases,
        )
        if kind is None:
            continue  # VF -- excluded entirely

        addresses: list[dict] = []
        for entry in link.get("addr_info") or []:
            if not isinstance(entry, dict):
                continue
            family = entry.get("family")
            local = entry.get("local")
            prefix = entry.get("prefixlen")
            scope = entry.get("scope")
            if not local or family not in ("inet", "inet6"):
                continue
            # Skip link-local IPv6 (fe80::) for matrix targeting; keep them recorded.
            addresses.append({
                "family": family,
                "address": local,
                "prefix": prefix,
                "scope": scope,
            })

        interfaces.append({
            "name": name,
            "state": link.get("operstate", "UNKNOWN"),
            "mac": link.get("address"),
            "mtu": link.get("mtu"),
            "link_type": link.get("link_type", ""),
            "pci": pci,
            "kind": kind,
            "addresses": addresses,
        })

    log_lines.append(f"interfaces: {len(interfaces)} found (json mode, classified)")
    return interfaces if interfaces else None
