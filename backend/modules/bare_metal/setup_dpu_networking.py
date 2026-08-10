"""
SSH module: bare-metal/setup-dpu-networking

Configures NAT on the host and routing/DNS on the DPU so the DPU
has internet access through the host's network connection.
This bridges the gap between wait-dpu-ready (DPU is SSH-reachable
over tmfifo) and install-dpu-prereqs (which needs apt/internet).

Topology-aware:
  - rshim_source="host" (default): tmfifo NAT on 192.168.100.0/24.
  - rshim_source="bmc": IPv6 link-local NAT through the host PF
    (used by dual_dpu_obmc where there is no tmfifo).
"""

import time
from typing import Any

from modules.base import InputSpec, OutputSpec, SSHModule


class SetupDPUNetworkingModule(SSHModule):
    name = "Setup DPU Networking"
    path = "bare-metal/setup-dpu-networking"
    category = "bare-metal"
    description = "Configure NAT and routing so DPU has internet access through the host"
    version = "1.0.0"
    phase = "DPU Setup"

    target = "host"  # Runs on the host (configures iptables + SSHes to DPU)
    estimated_duration = 30
    timeout = 120

    dependencies = ["bare-metal/wait-dpu-ready"]

    inputs = {
        "bare_metal_host_id": InputSpec(
            name="bare_metal_host_id",
            source="host",
            required=True,
            description="ID of the BareMetalHost",
        ),
        "host_ip": InputSpec(
            name="host_ip",
            source="host",
            required=True,
            description="Host management IP (for DPU→host API server route)",
        ),
        "dpu_ip": InputSpec(
            name="dpu_ip",
            source="module",
            from_module="bare-metal/wait-dpu-ready",
            from_output="dpu_ip",
            default="192.168.100.2",
            description=(
                "DPU management IP resolved by wait-dpu-ready — either the "
                "tmfifo IP (192.168.100.2) for the host-rshim path, or the "
                "DPU's oob_net0 DHCP IP for dual_dpu_obmc"
            ),
        ),
        "dns_servers": InputSpec(
            name="dns_servers",
            source="profile",
            default="8.8.8.8,8.8.4.4",
            description="DNS servers for the DPU (comma-separated)",
        ),
        "rshim_source": InputSpec(
            name="rshim_source",
            source="host",
            required=False,
            default="host",
            description="Where rshim is: 'host' (tmfifo NAT) or 'bmc' (IPv6 link-local NAT)",
        ),
        "deploy_dpu_pci_address": InputSpec(
            name="deploy_dpu_pci_address",
            source="host",
            required=False,
            default="",
            description=(
                "PCI address of the deploy DPU; used on the BMC path to "
                "find the host's LAG PF for VLAN sub-interface configuration"
            ),
        ),
    }

    outputs = {
        "dpu_networking_configured": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=True,
        ),
        # The host VLAN IP chosen as the K8s API server endpoint for
        # dual_dpu_obmc deployments. Empty for regular topology (the
        # cluster keeps using host_ip). When set, kubeadm-init advertises
        # on this address so DPU's kubeadm-join can reach the API server
        # over the matching VLAN at L2 — no OOB, no asymmetric routing.
        "cluster_api_ip": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
    }

    # ── Plan (no-op — always configure) ───────────────────────────────

    def plan_commands(self, variables: dict[str, Any]) -> list[str]:
        return []

    def parse_plan_output(self, output: str, variables: dict[str, Any]) -> bool:
        return True

    # ── Apply / Validate stubs (Python path bypasses these) ──────────

    def apply_commands(self, variables: dict[str, Any]) -> list[str]:
        return []

    def parse_apply_output(self, output: str, variables: dict[str, Any]) -> dict[str, Any]:
        return {}

    def validate_commands(self, variables: dict[str, Any]) -> list[str]:
        # Validation already performed in execute() via paramiko (ping verified).
        # Shell ssh would hit stale host key issues after DPU reflash.
        return []

    # ── Python execute path ───────────────────────────────────────────

    def execute(self, session: Any, variables: dict[str, Any], on_output: Any) -> dict[str, Any]:
        """Configure NAT on host and routing/DNS on DPU.

        Dispatches to the appropriate path based on rshim_source:
          - "host" (default): tmfifo NAT on 192.168.100.0/24
          - "bmc" (dual_dpu_obmc): DPU already has internet via oob_net0
            DHCP; verify reachability and optionally tighten DNS, no NAT.
        """
        rshim_source = variables.get("rshim_source", "host")

        if rshim_source == "bmc":
            return self._execute_via_oob(session, variables, on_output)

        return self._execute_tmfifo(session, variables, on_output)

    def _execute_tmfifo(self, session: Any, variables: dict[str, Any], on_output: Any) -> dict[str, Any]:
        """Configure NAT on host and routing/DNS on DPU via tmfifo (192.168.100.x).

        Args:
            session:    Live SSHSession connected to the bare-metal host
                        (target="host" means the engine built a host session).
            variables:  Assembled variable dict — includes dpu_ip, dpu_username,
                        dpu_password injected by build_variables_for_ssh().
            on_output:  Streaming log callback (line: str) -> None.

        Returns:
            dict with dpu_networking_configured and execution_duration_seconds.

        Raises:
            RuntimeError: If any critical step fails.
        """
        t0 = time.monotonic()

        from services.bare_metal.ssh_session import SSHSession

        dpu_ip: str = str(variables.get("dpu_ip") or "192.168.100.2")
        host_ip: str = str(variables.get("host_ip") or "")
        dns_servers_raw: str = str(variables.get("dns_servers") or "8.8.8.8,8.8.4.4")
        dns_servers = [s.strip() for s in dns_servers_raw.split(",") if s.strip()]

        # DPU credentials (same pattern as wait-dpu-ready)
        dpu_username: str = str(variables.get("dpu_username") or "ubuntu")
        dpu_password: str | None = variables.get("dpu_password") or None
        dpu_private_key_content: str | None = variables.get("dpu_private_key_content") or None

        on_output(
            f"[setup-dpu-net] Configuring NAT/routing for DPU at {dpu_ip} "
            f"(dns={','.join(dns_servers)})"
        )

        # ── Step 1: Detect default interface on host ──────────────────
        on_output("[setup-dpu-net] Detecting host default interface...")
        r = session.execute("ip route | grep default | awk '{print $5}' | head -1", timeout=10)
        if r.exit_code != 0 or not r.stdout.strip():
            raise RuntimeError(
                f"Failed to detect host default interface: exit={r.exit_code} "
                f"stdout={r.stdout[:200]!r} stderr={r.stderr[:200]!r}"
            )
        default_iface = r.stdout.strip().splitlines()[0].strip()
        on_output(f"[setup-dpu-net] Host default interface: {default_iface}")

        # ── Step 2: Enable IP forwarding on host ──────────────────────
        on_output("[setup-dpu-net] Enabling IP forwarding on host...")
        r = session.execute("sudo sysctl -w net.ipv4.ip_forward=1", timeout=10)
        if r.exit_code != 0:
            raise RuntimeError(f"Failed to enable IP forwarding: {r.stderr[:200]}")
        on_output(f"[setup-dpu-net] IP forwarding enabled: {r.stdout.strip()[:80]}")

        # ── Step 3: Configure MASQUERADE on host (idempotent) ─────────
        on_output(f"[setup-dpu-net] Configuring iptables MASQUERADE via {default_iface}...")
        masq_cmd = (
            f"sudo iptables -t nat -C POSTROUTING -s 192.168.100.0/24 -o {default_iface} -j MASQUERADE 2>/dev/null "
            f"|| sudo iptables -t nat -A POSTROUTING -s 192.168.100.0/24 -o {default_iface} -j MASQUERADE"
        )
        r = session.execute(masq_cmd, timeout=10)
        if r.exit_code != 0:
            raise RuntimeError(f"Failed to configure iptables MASQUERADE: {r.stderr[:200]}")
        on_output("[setup-dpu-net] iptables MASQUERADE rule active")

        elapsed_host = time.monotonic() - t0
        on_output(f"[setup-dpu-net] Host-side config complete ({elapsed_host:.1f}s)")

        # ── Step 4: SSH to DPU and configure routing/DNS ──────────────
        on_output(f"[setup-dpu-net] Connecting to DPU {dpu_username}@{dpu_ip}...")

        # Build the jumphost chain: [existing jumphosts of host session] + [host itself]
        host_hop: dict[str, Any] = {
            "host": session.host,
            "port": session.port,
            "username": session.username,
            "password": session.password,
            "private_key_content": session.private_key_content,
        }
        jumphost_chain: list[dict] = list(session.jumphost_chain or []) + [host_hop]

        try:
            dpu_session = SSHSession(
                host=dpu_ip,
                username=dpu_username,
                port=22,
                password=dpu_password,
                private_key_content=dpu_private_key_content,
                jumphost_chain=jumphost_chain,
                connect_timeout=10,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to SSH to DPU at {dpu_ip}: {str(exc)[:200]}"
            ) from exc

        on_output("[setup-dpu-net] Connected to DPU — configuring default route...")

        # Add default route via host tmfifo IP
        r = dpu_session.execute("sudo ip route replace default via 192.168.100.1", timeout=10)
        if r.exit_code != 0:
            raise RuntimeError(f"Failed to set DPU default route: {r.stderr[:200]}")
        on_output("[setup-dpu-net] DPU default route set via 192.168.100.1")

        # Add explicit host route so DPU can reach host's management IP (for K8s API server).
        # Without this, kubeadm-join fails: the DPU tries to reach host_ip:6443 but
        # the packet goes out the default route and doesn't reach the host's local address.
        if host_ip:
            on_output(f"[setup-dpu-net] Adding host route: {host_ip} via 192.168.100.1...")
            r = dpu_session.execute(f"sudo ip route replace {host_ip}/32 via 192.168.100.1", timeout=10)
            if r.exit_code != 0:
                on_output(f"[setup-dpu-net] WARNING: Failed to add host route: {r.stderr[:200]}")
            else:
                on_output(f"[setup-dpu-net] Host route added: {host_ip} via 192.168.100.1")

        # Configure DNS
        on_output(f"[setup-dpu-net] Configuring DNS on DPU ({', '.join(dns_servers)})...")
        nameserver_lines = "\\n".join(f"nameserver {s}" for s in dns_servers)
        r = dpu_session.execute(
            f"echo -e '{nameserver_lines}' | sudo tee /etc/resolv.conf",
            timeout=10,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"Failed to configure DPU DNS: {r.stderr[:200]}")
        on_output("[setup-dpu-net] DPU DNS configured")

        # ── Step 5: Verify DPU internet access ────────────────────────
        on_output("[setup-dpu-net] Verifying DPU internet access (ping 8.8.8.8)...")
        r = dpu_session.execute("ping -c1 -W5 8.8.8.8", timeout=15)
        if r.exit_code != 0:
            on_output(
                f"[setup-dpu-net] WARNING: DPU ping failed (exit={r.exit_code}): "
                f"{r.stderr[:200]}"
            )
            raise RuntimeError(
                "DPU cannot reach 8.8.8.8 after NAT/routing setup. "
                "Check host iptables, IP forwarding, and DPU route table."
            )
        on_output(f"[setup-dpu-net] DPU internet access verified: {r.stdout.strip()[:120]}")

        total = time.monotonic() - t0
        on_output(f"[setup-dpu-net] Complete ({total:.1f}s total)")
        return {
            "dpu_networking_configured": True,
            "execution_duration_seconds": round(total, 1),
        }

    # ── OOB path (dual_dpu_obmc) ──────────────────────────────────────

    def _execute_via_oob(
        self, session: Any, variables: dict[str, Any], on_output: Any
    ) -> dict[str, Any]:
        """No-op-leaning networking config for dual_dpu_obmc.

        For dual_dpu_obmc the DPU OS's `oob_net0` gets a DHCP lease on the
        OOB management network (the bf.conf netplan ships `oob_net0:
        dhcp4: true`). That gives the DPU:
          - a default gateway → internet access via the OOB network
          - DNS — already set by the bf.conf template's resolved.conf

        The host's data-plane PFs do NOT share an L2 segment with the
        DPU OS's sshd-bound interfaces (they terminate on the BF3 ASIC),
        so there is no NAT for us to set up — and no usable host PF
        to attach a private subnet to. We just verify the DPU has
        internet and, if it doesn't, surface a clear error.

        Note: the DPU→host_ip route (host's K8s API server) is NOT set
        up here. The host's management subnet is usually not reachable
        from the DPU's OOB subnet, and there's no host-side NAT we can
        bolt on. That problem is tracked separately as BM-012 (DPU-to-host
        API server connectivity for kubeadm-join).
        """
        t0 = time.monotonic()

        from services.bare_metal.ssh_session import SSHSession

        dpu_ip: str = str(variables.get("dpu_ip") or "")
        if not dpu_ip or "%" in dpu_ip or dpu_ip == "pending":
            raise RuntimeError(
                "setup-dpu-networking (BMC path) needs a routable IPv4 "
                f"dpu_ip from wait-dpu-ready, got: {dpu_ip!r}. "
                "Confirm wait-dpu-ready discovered the DPU's oob_net0 "
                "DHCP lease and persisted it to BareMetalHost.dpu_mgmt_ip."
            )

        dpu_username: str = str(variables.get("dpu_username") or "ubuntu")
        dpu_password: str | None = variables.get("dpu_password") or None
        dpu_private_key_content: str | None = variables.get("dpu_private_key_content") or None

        on_output(
            f"[setup-dpu-net] dual_dpu_obmc — DPU has its own OOB DHCP "
            f"({dpu_ip}); verifying internet access (no NAT to set up)"
        )

        # Build the same host-relay jumphost chain as the tmfifo path.
        host_hop: dict[str, Any] = {
            "host": session.host,
            "port": session.port,
            "username": session.username,
            "password": session.password,
            "private_key_content": session.private_key_content,
        }
        jumphost_chain = list(session.jumphost_chain or []) + [host_hop]

        try:
            dpu_session = SSHSession(
                host=dpu_ip,
                username=dpu_username,
                port=22,
                password=dpu_password,
                private_key_content=dpu_private_key_content,
                jumphost_chain=jumphost_chain,
                connect_timeout=10,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to SSH to DPU at {dpu_ip} from host: {exc}. "
                "Check host→DPU OOB-subnet routing and DPU SSH credentials."
            ) from exc

        on_output("[setup-dpu-net] Connected to DPU — verifying internet...")
        r = dpu_session.execute("ping -c2 -W2 8.8.8.8", timeout=15)
        if r.exit_code == 0:
            on_output("[setup-dpu-net] DPU internet access verified (ping 8.8.8.8 OK)")
        else:
            on_output(
                "[setup-dpu-net] WARNING: DPU ping to 8.8.8.8 failed "
                f"(exit={r.exit_code}); trying DNS resolution as fallback..."
            )
            r = dpu_session.execute(
                "getent hosts pkgs.k8s.io > /dev/null && echo DNS_OK || echo DNS_FAILED",
                timeout=15,
            )
            if "DNS_OK" not in r.stdout:
                raise RuntimeError(
                    "DPU has no internet via oob_net0 (both ICMP and DNS "
                    "checks failed). Confirm the OOB management network "
                    "provides internet and that the DPU's bf.conf netplan "
                    "set oob_net0 to dhcp4: true."
                )
            on_output("[setup-dpu-net] DPU DNS resolution works (oob_net0 OK)")

        # ── Host-side VLAN sub-interfaces ─────────────────────────────
        # For each VLAN configured on the DPU's br-lag, lay down a matching
        # VLAN sub-interface on the host's LAG PF so host and DPU share an
        # L2 segment on that VLAN. Without this, the host has no path into
        # the DPU's data-plane bridge other than the OOB management network
        # — which is exactly the asymmetric-routing problem that blocks
        # kubeadm-join.
        host_ip_str = str(variables.get("host_ip") or "")
        dpu_vlans = variables.get("dpu_vlans") or []
        cluster_api_ip: str | None = None
        if host_ip_str and dpu_vlans:
            cluster_api_ip = self._configure_host_vlans(
                session, variables, on_output, host_ip_str, dpu_vlans,
            )
        else:
            on_output(
                "[setup-dpu-net] No host VLAN config (host_ip or dpu_vlans missing) — skipping"
            )

        if cluster_api_ip:
            on_output(
                f"[setup-dpu-net] Cluster API IP for kubeadm-init: {cluster_api_ip} "
                "(host's address on the first data-plane VLAN; reachable from "
                "the DPU at L2 over the matching VLAN — no OOB or external "
                "gateway involved)"
            )

        total = time.monotonic() - t0
        on_output(f"[setup-dpu-net] Complete ({total:.1f}s total)")
        return {
            "dpu_networking_configured": True,
            "cluster_api_ip": cluster_api_ip,
            "execution_duration_seconds": round(total, 1),
        }

    # ── Helper: configure host-side VLAN sub-interfaces ───────────────

    @staticmethod
    def _host_vlan_ip(dpu_cidr: str, host_last_octet: int) -> str | None:
        """Derive the host-side VLAN IP from the DPU's CIDR + host mgmt last octet.

        Example: dpu_cidr='192.168.40.66/24', host_last_octet=19
                 → '192.168.40.19/24'

        Returns None if the input doesn't parse as `a.b.c.d/N` or if the
        derived IP would collide with the DPU's (last octet already matches).
        """
        if "/" not in dpu_cidr:
            return None
        addr, prefix = dpu_cidr.split("/", 1)
        parts = addr.split(".")
        if len(parts) != 4 or not parts[3].isdigit():
            return None
        if int(parts[3]) == host_last_octet:
            return None
        parts[3] = str(host_last_octet)
        return f"{'.'.join(parts)}/{prefix}"

    def _configure_host_vlans(
        self,
        session: Any,
        variables: dict[str, Any],
        on_output: Any,
        host_ip_str: str,
        dpu_vlans: list[dict[str, Any]],
    ) -> str | None:
        """Lay down /etc/netplan/70-bnk-dataplane-vlans.yaml and apply.

        Writes an `ethernets:` stanza for the parent LAG PF (MTU set) plus a
        `vlans:` stanza per DPU VLAN, with:
          - id  = DPU's vlan tag
          - link = host LAG PF
          - mtu = same as VLAN
          - addresses = [<dpu-subnet-prefix>.<host-last-octet>/<prefix>]
          - routes = default via <subnet>.1 with high metric (won't displace
            the existing mgmt default route)
        """
        # Host's mgmt-IP last octet (parsed defensively)
        host_octets = host_ip_str.split(".")
        if len(host_octets) != 4 or not host_octets[3].isdigit():
            on_output(
                f"[setup-dpu-net] host_ip={host_ip_str!r} is not an IPv4 "
                "address — skipping host VLAN config"
            )
            return None
        host_last_octet = int(host_octets[3])

        mtu = int(variables.get("dpu_mtu") or 9000)

        # Resolve the host PF for the deploy DPU — lowest-numbered mlx5_core
        # netdev whose sysfs PCI slot starts with the deploy DPU's bus prefix.
        host_pf = self._resolve_lag_host_pf(session, variables, on_output)
        if not host_pf:
            on_output(
                "[setup-dpu-net] Could not resolve host LAG PF — skipping "
                "host VLAN config (kubeadm-join may need manual host VLAN "
                "setup)"
            )
            return None
        on_output(f"[setup-dpu-net] Host LAG PF for VLAN sub-interfaces: {host_pf}")

        # Build the netplan YAML body
        lines: list[str] = [
            "# Managed by bnk-forge — DO NOT EDIT.",
            "# Host-side VLAN sub-interfaces matching the DPU's br-lag VLANs",
            "# (dual_dpu_obmc topology). Used so host and DPU share an L2",
            "# segment on each data-plane VLAN — see setup-dpu-networking.",
            "network:",
            "  version: 2",
            "  renderer: networkd",
            "  ethernets:",
            f"    {host_pf}:",
            f"      mtu: {mtu}",
            "      dhcp4: no",
            "      dhcp6: no",
            "  vlans:",
        ]
        configured: list[tuple[str, int, str]] = []  # (name, tag, host_ip)
        for idx, vlan in enumerate(dpu_vlans):
            tag = vlan.get("tag")
            name = vlan.get("name")
            dpu_cidr = vlan.get("ip") or ""
            if tag in (None, 0):
                on_output(
                    f"[setup-dpu-net] Skipping VLAN {name!r} tag={tag!r} "
                    "(VLAN 0 / untagged is not a usable data VLAN on Linux)"
                )
                continue
            host_cidr = self._host_vlan_ip(dpu_cidr, host_last_octet)
            if not host_cidr:
                on_output(
                    f"[setup-dpu-net] Skipping VLAN {name!r}: "
                    f"could not derive host IP from {dpu_cidr!r} + .{host_last_octet}"
                )
                continue
            # Gateway = .1 of the same /24 (or whatever prefix). High metric
            # (1000 + idx) so we don't displace the host's existing mgmt
            # default route.
            gw_octets = host_cidr.split("/", 1)[0].split(".")
            gw_octets[3] = "1"
            gateway = ".".join(gw_octets)
            metric = 1000 + idx

            lines.append(f"    {name}:")
            lines.append(f"      id: {tag}")
            lines.append(f"      link: {host_pf}")
            lines.append(f"      mtu: {mtu}")
            lines.append("      addresses:")
            lines.append(f"        - {host_cidr}")
            lines.append("      routes:")
            lines.append("        - to: default")
            lines.append(f"          via: {gateway}")
            lines.append(f"          metric: {metric}")
            configured.append((name, int(tag), host_cidr))

        if not configured:
            on_output("[setup-dpu-net] No usable VLANs to configure — skipping netplan write")
            return None

        netplan_body = "\n".join(lines) + "\n"
        netplan_path = "/etc/netplan/70-bnk-dataplane-vlans.yaml"
        on_output(
            f"[setup-dpu-net] Writing {netplan_path} "
            f"({len(configured)} VLAN(s): {[c[0] for c in configured]})"
        )

        # Write atomically via a /tmp staging file + sudo mv. Heredoc keeps
        # all the YAML special characters intact.
        write_cmd = (
            "cat > /tmp/.bnk_dataplane_vlans.yaml << 'BNK_NETPLAN_EOF'\n"
            f"{netplan_body}"
            "BNK_NETPLAN_EOF\n"
            f"sudo install -m 0600 /tmp/.bnk_dataplane_vlans.yaml {netplan_path} && "
            "rm -f /tmp/.bnk_dataplane_vlans.yaml && "
            "echo NETPLAN_WRITE_OK"
        )
        r = session.execute(write_cmd, timeout=20)
        if "NETPLAN_WRITE_OK" not in r.stdout:
            raise RuntimeError(
                f"Failed to write {netplan_path} on host: "
                f"exit={r.exit_code} stderr={r.stderr[:300]!r}"
            )

        # Apply. netplan apply on a non-mgmt PF is safe — the host's mgmt
        # is on enP2p3s0f0np0 (DPU#2 side); we're touching the DPU#1 side.
        on_output("[setup-dpu-net] Running sudo netplan apply...")
        r = session.execute("sudo netplan apply 2>&1", timeout=30)
        if r.exit_code != 0:
            raise RuntimeError(
                f"netplan apply failed (exit {r.exit_code}): "
                f"stdout={r.stdout[:400]!r} stderr={r.stderr[:400]!r}"
            )

        # Verify each VLAN sub-interface came up with the right address
        first_verified_ip: str | None = None
        for name, tag, expected_cidr in configured:
            r = session.execute(
                f"ip -4 -br a show {name} 2>&1", timeout=10,
            )
            line = r.stdout.strip().splitlines()[0] if r.stdout.strip() else ""
            expected_addr = expected_cidr  # e.g. "192.168.40.19/24"
            if r.exit_code == 0 and expected_addr in line:
                on_output(f"[setup-dpu-net]   {name} (vlan {tag}): {line}")
                if first_verified_ip is None:
                    # Strip the /CIDR — kubeadm wants a bare IP for
                    # --apiserver-advertise-address.
                    first_verified_ip = expected_cidr.split("/", 1)[0]
            else:
                on_output(
                    f"[setup-dpu-net]   WARNING: {name} (vlan {tag}) not "
                    f"in expected state — got: exit={r.exit_code} {line!r}"
                )

        # Return the first successfully-configured host VLAN IP. kubeadm-init
        # will use this as --apiserver-advertise-address so the DPU can
        # reach the API server directly over the shared VLAN.
        return first_verified_ip

    def _resolve_lag_host_pf(
        self, session: Any, variables: dict[str, Any], on_output: Any,
    ) -> str | None:
        """Find the host PF for the deploy DPU. Picks the lowest-named
        mlx5_core netdev whose sysfs PCI slot starts with the deploy DPU's
        bus prefix.

        Works for both LAG (HIDE_PORT2_PF=True → single PF) and the
        production setup where both PFs are visible (first PF wins).
        """
        deploy_pci = str(variables.get("deploy_dpu_pci_address") or "").strip()
        if not deploy_pci or "." not in deploy_pci:
            on_output(
                "[setup-dpu-net] deploy_dpu_pci_address not set "
                f"({deploy_pci!r}) — can't resolve host LAG PF"
            )
            return None
        bus_prefix = deploy_pci.rsplit(".", 1)[0]

        r = session.execute(
            "for iface in $(ls /sys/class/net/); do "
            "  slot=$(basename $(readlink /sys/class/net/$iface/device 2>/dev/null) 2>/dev/null); "
            "  driver=$(basename $(readlink /sys/class/net/$iface/device/driver 2>/dev/null) 2>/dev/null); "
            '  if [ "$driver" = "mlx5_core" ]; then '
            "    echo \"$iface $slot\"; "
            "  fi; "
            "done",
            timeout=15,
        )
        matches: list[str] = []
        for line in r.stdout.strip().splitlines():
            parts = line.strip().split()
            if len(parts) == 2 and parts[1].startswith(bus_prefix):
                matches.append(parts[0])
        if not matches:
            on_output(
                f"[setup-dpu-net] No mlx5 PF found at PCI bus {bus_prefix} — "
                "DPU PCI may not have re-enumerated. Skipping host VLAN config."
            )
            return None
        # Lowest-named = the f0 PF in either LAG-single-PF or two-PF setups.
        return sorted(matches)[0]
