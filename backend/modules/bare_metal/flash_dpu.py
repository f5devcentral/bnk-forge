"""
SSH module: bare-metal/flash-dpu

Flashes the DPU with a BFB image via bfb-install.

Uses the Python execute() path (discrete session.execute() calls) rather than
apply_commands() shell strings. This avoids the streaming-mode bug where
connectivity_risk=True causes the engine to reconnect mid-sequence and lose
commands 2-4 — only the first command (the bfb-install check) would run before
the engine re-opened the SSH connection.

Additionally handles rshim network setup: tmfifo_net0 may be UP but have no
IPv4 address, making the DPU unreachable at 192.168.100.2. Step 2 ensures
192.168.100.1/24 is configured on the host side before the flash.

Estimated time: 5-10 minutes for BFB download + flash + DPU boot.
"""

import base64
import re
import time
from typing import Any

from modules.base import InputSpec, OutputSpec, SSHModule

DPU_IP = "192.168.100.2"
HOST_RSHIM_IP = "192.168.100.1"


class FlashDPUModule(SSHModule):
    name = "Flash DPU (BFB)"
    path = "bare-metal/flash-dpu"
    category = "bare-metal"
    phase = "DPU Flash"
    description = "Flash DPU with BFB image via bfb-install."
    version = "1.0.0"

    # SSH config — targets the host (bfb-install runs on the host, not the DPU).
    # connectivity_risk is now False: bfb-install is a host-local command and the
    # SSH connection to the host stays up throughout. The old True setting was
    # triggering streaming mode which silently dropped commands 2-4.
    target = "host"
    connectivity_risk = False
    reconnect_timeout = 600  # 10 minutes (kept for interface compat)
    estimated_duration = 600
    timeout = 900

    # NIC must be in DPU mode before flash. reboot-host ensures the host
    # has rebooted after any NIC mode change so rshim is available.
    dependencies = ["bare-metal/reboot-host"]

    inputs = {
        "bare_metal_host_id": InputSpec(
            name="bare_metal_host_id",
            source="host",
            required=True,
            description="ID of the BareMetalHost",
        ),
        "bfb_url": InputSpec(
            name="bfb_url",
            source="module",
            from_module="bare-metal/probe-dpu",
            from_output="bfb_url",
            required=True,
            description="BFB image URL (resolved from DOCA Release catalog via probe-dpu)",
        ),
        "bfb_config_url": InputSpec(
            name="bfb_config_url",
            source="user",
            required=False,
            default="",
            description="URL of bf.cfg post-install script (optional)",
        ),
        "default_route_iface": InputSpec(
            name="default_route_iface",
            source="module",
            from_module="bare-metal/probe-dpu",
            from_output="default_route_iface",
            required=False,
            default="ens4f1",
            description="Default route interface for connectivity preservation",
        ),
        "rshim_source": InputSpec(
            name="rshim_source",
            source="host",
            required=False,
            default="host",
            description="Where rshim is accessible: 'host' (default) or 'bmc' (dual_dpu_obmc)",
        ),
        "bmc_ip": InputSpec(
            name="bmc_ip",
            source="host",
            required=False,
            default="",
            description="BMC IP address (required when rshim_source=bmc)",
        ),
        "deploy_dpu_index": InputSpec(
            name="deploy_dpu_index",
            source="host",
            required=False,
            default=0,
            description="0-based DPU index for multi-DPU hosts",
        ),
    }

    outputs = {
        "flash_completed": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=True,
        ),
        "dpu_ip": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=DPU_IP,
        ),
        "rshim_source": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
    }

    # ------------------------------------------------------------------ #
    # bf.cfg builder — static, testable, shellcheck-validated              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_bf_cfg_content(
        pw_hash: str,
        dpu_hostname: str,
        dpu_password: str,
        auth_key_block: str = "",
        net_rshim_mac: str = "",                        # BM2-005
        ovs_ext_sf: str = "en3f0pf0sf1",                # BM2-007
        ovs_int_sf: str = "en3f1pf1sf1",                # BM2-007
        ovs_int_vf: str = "pf1vf0",                     # BM2-007
        dpu_versions_env: str = "",                      # BM2-011 (param only, impl by other builder)
        dns_nameservers: str = "nameserver 1.1.1.1\noptions edns0",  # BM2-012
        ssh_pub_key: str = "",                           # BM2-013
    ) -> str:
        """Build the comprehensive bf.cfg shell script content.

        .. deprecated::
            This method generates bf.cfg via Python string concatenation.
            Prefer the Jinja rendering path via bf_conf_renderer.render_bf_conf()
            which uses templates from ProjectDpuSettings. The Jinja path is
            automatically used when a Dpu row exists (DD-12 auto-creates one
            for dual_dpu_obmc topology). This method remains as fallback for
            topologies where DPU tab is not configured.

            Target: delete once Jinja path is validated on hardware for all topologies.
            See POSTMORTEM_BARE_METAL_REUSE.md for context.

        Extracted for testability — shellcheck can validate the generated script
        without requiring a live SSH session or a 5-minute DPU flash cycle.
        """
        versions_b64 = base64.b64encode(dpu_versions_env.encode()).decode() if dpu_versions_env else ""
        dns_b64 = base64.b64encode(dns_nameservers.encode()).decode() if dns_nameservers else ""
        ssh_key_b64 = base64.b64encode(ssh_pub_key.encode()).decode() if ssh_pub_key else ""
        return (
            "#!/bin/bash\n"
            "# Comprehensive bf.cfg — generated by bnk-forge flash-dpu module.\n"
            "# Matches poc-deployer dpu_config.sh generate_bluefield_config().\n"
            'UPDATE_DPU_OS="yes"\n'
            f"ubuntu_PASSWORD='{pw_hash}'\n"
            + (f"NET_RSHIM_MAC='{net_rshim_mac}'\n" if net_rshim_mac else "")
            + "\n"
            "bfb_modify_os()\n"
            "{\n"
            "    # ── Hostname\n"
            f'    local hname="{dpu_hostname}"\n'
            '    echo ${hname} > /mnt/etc/hostname\n'
            '    echo "127.0.0.1 ${hname}" >> /mnt/etc/hosts\n'
            "\n"
            "    # ── SSH config\n"
            "    sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication yes/' "
            "/mnt/etc/ssh/sshd_config\n"
            "    sed -i 's/^#*ChallengeResponseAuthentication.*/ChallengeResponseAuthentication no/' "
            "/mnt/etc/ssh/sshd_config\n"
            + auth_key_block
            + "\n"
            "    # -- Mask dnsmasq (prevent DNS interference)\n"
            "    ln -sf /dev/null /mnt/etc/systemd/system/dnsmasq.service\n"
            "\n"
            "    # ── Cloud-init network disable\n"
            "    mkdir -p /mnt/etc/cloud/cloud.cfg.d\n"
            "    cat << EOFCIDISABLE > /mnt/etc/cloud/cloud.cfg.d/99-disable-network-config.cfg\n"
            "network: {config: disabled}\n"
            "EOFCIDISABLE\n"
            "\n"
            "    # ── Netplan management interfaces\n"
            "    mkdir -p /mnt/etc/netplan\n"
            "    cat << EOFMGMT > /mnt/etc/netplan/50-mgmt.yaml\n"
            "network:\n"
            "  version: 2\n"
            "  renderer: networkd\n"
            "  ethernets:\n"
            "    tmfifo_net0:\n"
            "      dhcp4: false\n"
            "      addresses:\n"
            "        - 192.168.100.2/30\n"
            "    oob_net0:\n"
            "      dhcp4: true\n"
            "      dhcp6: true\n"
            "EOFMGMT\n"
            "    chmod 600 /mnt/etc/netplan/50-mgmt.yaml\n"
            "\n"
            "    # ── Kernel modules\n"
            "    cat << EOFMODULES >> /mnt/etc/modules-load.d/custom.conf\n"
            "overlay\n"
            "br_netfilter\n"
            "vfio_pci\n"
            "EOFMODULES\n"
            "\n"
            "    # ── sysctl\n"
            "    cat << EOFSYSCTL >> /mnt/etc/sysctl.d/kubernetes.conf\n"
            "net.bridge.bridge-nf-call-ip6tables = 1\n"
            "net.bridge.bridge-nf-call-iptables = 1\n"
            "net.ipv4.ip_forward = 1\n"
            "EOFSYSCTL\n"
            "\n"
            "    # ── Hugepages via grub\n"
            '    local grub_cmd="console=hvc0 console=ttyAMA0 earlycon=pl011,0x13010000 '
            "fixrttc net.ifnames=0 biosdevname=0 iommu.passthrough=1 "
            'cgroup_no_v1=net_prio,net_cls default_hugepagesz=2MB hugepagesz=2M hugepages=8192"\n'
            '    sed -i -E "s|^(GRUB_CMDLINE_LINUX=\\").*|\\1${grub_cmd}\\"|" /mnt/etc/default/grub\n'
            "    chroot /mnt env PATH=$PATH /usr/sbin/grub-mkconfig -o /boot/grub/grub.cfg || true\n"
            "\n"
            "    # ── SF provisioning\n"
            "    rm -f /mnt/etc/mellanox/mlnx-sf.conf\n"
            "    for pciid in $(lspci -nD 2>/dev/null | grep '15b3:a2d[26c]' | awk '{print $1}')\n"
            "    do\n"
            "        cat << EOFSF >> /mnt/etc/mellanox/mlnx-sf.conf\n"
            "/sbin/mlnx-sf --action create --enable-trust --device $pciid --sfnum 0 "
            "--hwaddr $(uuidgen | sed -e 's/-//;s/^\\(..\\)\\(..\\)\\(..\\)\\(..\\)\\(..\\).*$/02:\\1:\\2:\\3:\\4:\\5/')\n"
            "/sbin/mlnx-sf --action create --enable-trust --device $pciid --sfnum 1 "
            "--hwaddr $(uuidgen | sed -e 's/-//;s/^\\(..\\)\\(..\\)\\(..\\)\\(..\\)\\(..\\).*$/02:\\1:\\2:\\3:\\4:\\5/')\n"
            "/sbin/mlnx-sf --action create --enable-trust --device $pciid --sfnum 2 "
            "--hwaddr $(uuidgen | sed -e 's/-//;s/^\\(..\\)\\(..\\)\\(..\\)\\(..\\)\\(..\\).*$/02:\\1:\\2:\\3:\\4:\\5/')\n"
            "EOFSF\n"
            "    done\n"
            "\n"
            "    # ── OVS bridges\n"
            "    cat << EOFOVS > /mnt/etc/mellanox/mlnx-ovs.conf\n"
            'CREATE_OVS_BRIDGES="yes"\n'
            'OVS_BRIDGE1="sf_external"\n'
            f'OVS_BRIDGE1_PORTS="p0 {ovs_ext_sf}"\n'
            'OVS_BRIDGE2="sf_internal"\n'
            f'OVS_BRIDGE2_PORTS="p1 {ovs_int_sf} {ovs_int_vf}"\n'
            'OVS_HW_OFFLOAD="yes"\n'
            "OVS_BR_PORTS_TIMEOUT=300\n"
            "EOFOVS\n"
            "\n"
            "    # ── Cloud-init: containerd config + OVS bridge-retry service\n"
            "    cat << 'EOFCLOUDINIT' > /mnt/var/lib/cloud/seed/nocloud-net/user-data\n"
            "#cloud-config\n"
            "write_files:\n"
            "  - path: /etc/containerd/config.toml\n"
            "    content: |\n"
            '      version = 2\n'
            '      root = "/var/lib/containerd"\n'
            '      state = "/run/containerd"\n'
            '      oom_score = 0\n'
            '      [grpc]\n'
            '        max_recv_message_size = 16777216\n'
            '        max_send_message_size = 16777216\n'
            '      [debug]\n'
            '        address = ""\n'
            '        level = "info"\n'
            '        format = ""\n'
            '        uid = 0\n'
            '        gid = 0\n'
            '      [plugins]\n'
            '        [plugins."io.containerd.grpc.v1.cri"]\n'
            '          sandbox_image = "registry.k8s.io/pause:3.10"\n'
            '          max_container_log_line_size = 16384\n'
            '          enable_unprivileged_ports = false\n'
            '          enable_unprivileged_icmp = false\n'
            '          enable_selinux = false\n'
            '          disable_apparmor = false\n'
            '          tolerate_missing_hugetlb_controller = true\n'
            '          disable_hugetlb_controller = true\n'
            '          image_pull_progress_timeout = "5m"\n'
            '          [plugins."io.containerd.grpc.v1.cri".containerd]\n'
            '            default_runtime_name = "runc"\n'
            '            snapshotter = "overlayfs"\n'
            '            discard_unpacked_layers = true\n'
            '            [plugins."io.containerd.grpc.v1.cri".containerd.runtimes]\n'
            '              [plugins."io.containerd.grpc.v1.cri".containerd.runtimes.runc]\n'
            '                runtime_type = "io.containerd.runc.v2"\n'
            '                runtime_engine = ""\n'
            '                runtime_root = ""\n'
            '                base_runtime_spec = "/etc/containerd/cri-base.json"\n'
            '                [plugins."io.containerd.grpc.v1.cri".containerd.runtimes.runc.options]\n'
            '                  systemdCgroup = true\n'
            '                  binaryName = "/usr/sbin/runc"\n'
            "\n"
            "  - path: /etc/systemd/system/bnk-ovs-bridge-retry.service\n"
            "    permissions: '0644'\n"
            "    content: |\n"
            "      [Unit]\n"
            "      Description=BNK OVS bridge retry\n"
            "      After=network.target ovsdb-server.service openvswitch.service\n"
            "      Wants=ovsdb-server.service openvswitch.service\n"
            "\n"
            "      [Service]\n"
            "      Type=oneshot\n"
            "      RemainAfterExit=yes\n"
            "      TimeoutStartSec=1500\n"
            "      ExecStart=/usr/local/bin/bnk-ovs-bridge-retry.sh\n"
            "\n"
            "      [Install]\n"
            "      WantedBy=multi-user.target\n"
            "\n"
            "  - path: /usr/local/bin/bnk-ovs-bridge-retry.sh\n"
            "    permissions: '0755'\n"
            "    content: |\n"
            "      #!/usr/bin/env bash\n"
            "      set -euo pipefail\n"
            '      CONF="/etc/mellanox/mlnx-ovs.conf"\n'
            "      MAX_WAIT=600\n"
            '      LOG_TAG="bnk-ovs-bridge-retry"\n'
            "      log()  { echo \"$(date '+%Y-%m-%dT%H:%M:%S') [$LOG_TAG] $*\" | tee /dev/kmsg 2>/dev/null || true; }\n"
            '      info() { log "INFO  $*"; }\n'
            '      warn() { log "WARN  $*"; }\n'
            '      err()  { log "ERROR $*"; }\n'
            '      if [[ ! -f "$CONF" ]]; then warn "$CONF not found"; exit 0; fi\n'
            '      source "$CONF"\n'
            '      if [[ "${CREATE_OVS_BRIDGES:-no}" != "yes" ]]; then info "CREATE_OVS_BRIDGES != yes"; exit 0; fi\n'
            "      wait_for_iface() {\n"
            '          local iface="$1" max="${2:-$MAX_WAIT}" elapsed=0\n'
            '          info "Waiting for interface $iface (timeout ${max}s)..."\n'
            "          while [[ $elapsed -lt $max ]]; do\n"
            '              [[ -d "/sys/class/net/$iface" ]] && info "$iface appeared after ${elapsed}s" && return 0\n'
            "              sleep 5; elapsed=$((elapsed + 5))\n"
            "          done\n"
            '          err "$iface did NOT appear after ${max}s"; return 1\n'
            "      }\n"
            "      ensure_bridge() {\n"
            '          local bridge="$1"; shift; local ports=("$@")\n'
            '          info "Ensuring bridge $bridge with ports: ${ports[*]}"\n'
            "          local missing=()\n"
            '          for port in "${ports[@]}"; do\n'
            '              ovs-vsctl list-ports "$bridge" 2>/dev/null | grep -qx "$port" || missing+=("$port")\n'
            "          done\n"
            '          [[ ${#missing[@]} -eq 0 ]] && info "Bridge $bridge complete" && return 0\n'
            '          info "Bridge $bridge missing: ${missing[*]}"\n'
            '          ovs-vsctl --may-exist add-br "$bridge"\n'
            '          [[ "${OVS_HW_OFFLOAD:-no}" == "yes" ]] && ovs-vsctl set Open_vSwitch . other_config:hw-offload=true 2>/dev/null || true\n'
            '          for port in "${ports[@]}"; do\n'
            '              [[ -d "/sys/class/net/$port" ]] && ovs-vsctl --may-exist add-port "$bridge" "$port" && info "Added $port" || warn "Skipping $port"\n'
            "          done\n"
            "      }\n"
            "      for n in $(seq 1 8); do\n"
            '          br_name_var="OVS_BRIDGE${n}"\n'
            '          br_ports_var="OVS_BRIDGE${n}_PORTS"\n'
            '          br_name="${!br_name_var:-}"\n'
            '          br_ports="${!br_ports_var:-}"\n'
            '          [[ -z "$br_name" ]] && continue\n'
            "          for port in $br_ports; do\n"
            '              [[ "$port" =~ ^pf[0-9]+vf[0-9]+$ ]] && wait_for_iface "$port" "$MAX_WAIT" || warn "Continuing without $port"\n'
            "          done\n"
            "          ensure_bridge \"$br_name\" $br_ports\n"
            "      done\n"
            '      info "OVS bridge retry complete"\n'
            + (
                "\n"
                "  - path: /var/tmp/bnk/dpu-versions.env\n"
                "    permissions: '0644'\n"
                "    encoding: base64\n"
                "    owner: root:root\n"
                f"    content: {versions_b64}\n"
                if versions_b64 else ""
            )
            + (
                "\n"
                "  - path: /var/tmp/bnk/etc/resolv.conf\n"
                "    permissions: '0644'\n"
                "    encoding: base64\n"
                "    owner: root:root\n"
                f"    content: {dns_b64}\n"
                if dns_b64 else ""
            )
            + (
                "\n"
                "  - path: /var/tmp/bnk/home/ubuntu/.ssh/authorized_keys\n"
                "    permissions: '0600'\n"
                "    encoding: base64\n"
                "    owner: root:root\n"
                f"    content: {ssh_key_b64}\n"
                if ssh_key_b64 else ""
            )
            + "\n"
            "runcmd:\n"
            "  - systemctl enable bnk-ovs-bridge-retry.service\n"
            "  - systemctl start bnk-ovs-bridge-retry.service\n"
            + (
                "  - cp /var/tmp/bnk/etc/resolv.conf /etc/resolv.conf 2>/dev/null || true\n"
                if dns_b64 else ""
            )
            + (
                "  - mkdir -p /home/ubuntu/.ssh\n"
                "  - cp /var/tmp/bnk/home/ubuntu/.ssh/authorized_keys /home/ubuntu/.ssh/authorized_keys 2>/dev/null || true\n"
                "  - chown -R ubuntu:ubuntu /home/ubuntu/.ssh\n"
                "  - chmod 700 /home/ubuntu/.ssh\n"
                "  - chmod 600 /home/ubuntu/.ssh/authorized_keys 2>/dev/null || true\n"
                if ssh_key_b64 else ""
            )
            + "\n"
            "chpasswd:\n"
            "  expire: false\n"
            "  list: |\n"
            f"    ubuntu:{dpu_password}\n"
            "\n"
            "EOFCLOUDINIT\n"
            "}\n"
            "\n"
            "bfb_post_install()\n"
            "{\n"
            "    mst start\n"
            "    for mst_device in /dev/mst/mt*pciconf*\n"
            "    do\n"
            "        mlxconfig -y -d $mst_device s \\\n"
            "            PF_BAR2_ENABLE=0 \\\n"
            "            PER_PF_NUM_SF=1 \\\n"
            "            PF_TOTAL_SF=20 \\\n"
            "            PF_SF_BAR_SIZE=10 \\\n"
            "            NUM_OF_VFS=46 \\\n"
            "            INTERNAL_CPU_OFFLOAD_ENGINE=0 \\\n"
            "            INTERNAL_CPU_MODEL=1\n"
            "        mlxprivhost -d $mst_device p\n"
            "    done\n"
            "}\n"
        )

    # ------------------------------------------------------------------ #
    # plan / validate / pre_stage — engine still calls these               #
    # ------------------------------------------------------------------ #

    def plan_commands(self, variables: dict[str, Any]) -> list[str]:
        bfb_url = variables.get("bfb_url", "")
        if not bfb_url:
            return []
        return [
            # Check if BFB is already cached locally
            f"BFB_FILE=/tmp/$(basename {bfb_url}); "
            f"if [ -f \"$BFB_FILE\" ]; then "
            f"  echo \"BFB_CACHED=true\"; ls -lh \"$BFB_FILE\"; "
            f"else "
            f"  echo \"BFB_CACHED=false\"; "
            f"fi",
            # Verify BFB URL is reachable (HEAD request, don't download)
            f"curl -sI --max-time 15 '{bfb_url}' | head -5 || echo 'BFB_URL_UNREACHABLE'",
        ]

    def parse_plan_output(self, output: str, variables: dict[str, Any]) -> bool:
        # Always apply (flash), but log reachability status
        return True

    def pre_stage_commands(self, variables: dict[str, Any]) -> list[str]:
        """No pre-stage needed — connectivity_risk=False (host SSH stays up throughout)."""
        return []

    @staticmethod
    def _parse_flash_log(log_lines: list[str], rshim_source: str) -> tuple[str, int]:
        """Parse the execute() output log and decide post-flash rshim status.

        Returns (marker, exit_code):
          - ("RSHIM_OK", 0) when the log shows rshim is active after flash
          - ("RSHIM_MISSING", 1) when the log shows the post-flash check failed
            (or no decisive marker is present)
        """
        if rshim_source == "bmc":
            ok_marker = "BMC rshim post-flash: OK"
            fail_marker = "WARNING: BMC rshim device not ready after flash"
        else:
            ok_marker = "[flash-dpu] rshim: OK"
            fail_marker = "WARNING: rshim device not found after flash"
        for line in log_lines:
            if ok_marker in line:
                return ("RSHIM_OK", 0)
            if fail_marker in line:
                return ("RSHIM_MISSING", 1)
        return ("RSHIM_MISSING", 1)

    def validate_commands(self, variables: dict[str, Any]) -> list[str]:
        """Verify rshim is active after flash by parsing the execute() log.

        For dual_dpu_obmc (rshim_source=bmc) the host has no /dev/rshim*,
        so checking it on the host always fails. Both paths instead read
        the captured execute() log and emit a corresponding RSHIM_OK /
        RSHIM_MISSING marker — the marker carries the actual exit code so
        the engine fails the validate phase only when the flash log shows
        a real post-flash rshim failure.

        Note: We do NOT validate DPU SSH here — that's wait-dpu-ready's job.
        """
        rshim_source = variables.get("rshim_source", "host")
        log_lines = variables.get("_flash_log_lines", [])
        marker, exit_code = self._parse_flash_log(log_lines, rshim_source)
        if exit_code == 0:
            return [f"echo '{marker}'"]
        return [f"echo '{marker}' && exit {exit_code}"]

    # ------------------------------------------------------------------ #
    # apply_commands / parse_apply_output — stubbed out                    #
    # The engine uses execute() instead when it is implemented.            #
    # ------------------------------------------------------------------ #

    def apply_commands(self, variables: dict[str, Any]) -> list[str]:
        """Stubbed — execute() takes over the apply phase."""
        return []

    def parse_apply_output(self, output: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Stubbed — execute() returns outputs directly."""
        return {}

    # ------------------------------------------------------------------ #
    # Python execute() path                                                #
    # ------------------------------------------------------------------ #

    def execute(self, session: Any, variables: dict[str, Any], on_output: Any) -> dict[str, Any]:
        """Flash the DPU with a BFB image via bfb-install.

        Each step is a discrete session.execute() call with explicit exit-code
        checking. This avoids the streaming-mode bug that previously caused the
        engine to lose commands 2-4 when connectivity_risk was True.

        Steps:
          1. Verify bfb-install is present.
          2. Configure rshim network (tmfifo_net0 / 192.168.100.1).
          3. Download BFB image if not already cached.
          4. Flash the DPU (bfb-install — may take several minutes).
          5. Verify rshim device is still present after flash.

        Output lines emitted via on_output are captured in
        variables["_flash_log_lines"] so validate_commands can parse the
        post-flash rshim status without re-checking the host.
        """
        t0 = time.monotonic()

        # Tee on_output into a list so validate_commands can grep the log
        # for post-flash status markers ("BMC rshim post-flash: OK", etc.).
        log_lines: list[str] = []
        variables["_flash_log_lines"] = log_lines
        _delegate_on_output = on_output

        def on_output(line: str) -> None:  # type: ignore[no-redef]
            log_lines.append(line)
            _delegate_on_output(line)
        bfb_url: str = variables["bfb_url"]
        bfb_config: str = variables.get("bfb_config_url", "")

        # ---- Step 0: rshim guard -----------------------------------------
        rshim_source = variables.get("rshim_source", "host")
        rshim_device = "rshim0"  # default, overridden below if host rshim found

        if rshim_source == "bmc":
            on_output("[flash-dpu] rshim_source=bmc — skipping host rshim check (rshim managed by DPU OpenBMC)")
        else:
            # rshim must be available on the host for bfb-install. Try to start it if missing.
            on_output("[flash-dpu] Checking rshim availability...")
            rshim_check = session.execute(
                "ls /dev/rshim*/boot 2>/dev/null | head -1 || echo 'NO_RSHIM'",
                timeout=10,
            )
            rshim_boot = rshim_check.stdout.strip()
            if not rshim_boot or rshim_boot == "NO_RSHIM":
                on_output("[flash-dpu] rshim not found — attempting to start rshim service...")
                session.execute("sudo modprobe rshim_pcie 2>/dev/null; sudo modprobe rshim 2>/dev/null", timeout=15)
                session.execute("sudo systemctl enable rshim 2>/dev/null", timeout=10)
                session.execute("sudo systemctl start rshim 2>&1", timeout=30)
                time.sleep(3)
                # Re-check
                rshim_check = session.execute(
                    "ls /dev/rshim*/boot 2>/dev/null | head -1 || echo 'NO_RSHIM'",
                    timeout=10,
                )
                rshim_boot = rshim_check.stdout.strip()
                if not rshim_boot or rshim_boot == "NO_RSHIM":
                    raise RuntimeError(
                        "rshim driver not loaded — /dev/rshim*/boot not found even after "
                        "attempting modprobe + systemctl start rshim. "
                        "Check 'systemctl status rshim' and 'dmesg | grep rshim' on the host."
                    )
                on_output(f"[flash-dpu] rshim started successfully: {rshim_boot}")
            # Extract the rshim device name (e.g., /dev/rshim0/boot -> rshim0)
            rshim_match = re.search(r"/dev/(rshim\d+)/boot", rshim_boot)
            rshim_device = rshim_match.group(1) if rshim_match else "rshim0"
            on_output(f"[flash-dpu] rshim device: {rshim_device}")

        # Early BMC IP validation — fail fast before downloading BFB
        if rshim_source == "bmc":
            bmc_ip = variables.get("bmc_ip")
            if not bmc_ip:
                raise RuntimeError(
                    "BMC IP required for dual_dpu_obmc flash (rshim_source=bmc). "
                    "Set bmc_ip in host settings before deploying."
                )
            on_output(f"[flash-dpu] BMC IP validated: {bmc_ip}")

        # ---- Step 1: verify bfb-install ----------------------------------
        on_output("[flash-dpu] Checking bfb-install...")
        r = session.execute("which bfb-install", timeout=10)
        if r.exit_code != 0:
            raise RuntimeError(
                "bfb-install not found. Install rshim tools: sudo apt-get install -y rshim"
            )
        on_output(f"[flash-dpu] bfb-install: OK ({time.monotonic() - t0:.1f}s elapsed)")

        # ---- Step 2: configure rshim network on the host side ------------
        # tmfifo_net0 may be UP but have no IPv4 — DPU won't be reachable at
        # 192.168.100.2 without 192.168.100.1/24 on the host side.
        on_output("[flash-dpu] Configuring rshim network interface...")
        r = session.execute(
            f"ip addr show tmfifo_net0 2>/dev/null | grep -q 'inet {HOST_RSHIM_IP}' || "
            f"sudo ip addr add {HOST_RSHIM_IP}/24 dev tmfifo_net0 2>/dev/null; "
            f"sudo ip link set tmfifo_net0 up 2>/dev/null; "
            f"echo 'RSHIM_NET_OK'",
            timeout=15,
        )
        on_output(f"[flash-dpu] rshim network: {r.stdout.strip()}")

        # ---- Step 3: download BFB if not cached --------------------------
        on_output("[flash-dpu] Checking BFB cache...")
        bfb_filename = bfb_url.rsplit("/", 1)[-1] if "/" in bfb_url else "firmware.bfb"
        bfb_path = f"/tmp/{bfb_filename}"

        r = session.execute(
            f"test -f '{bfb_path}' && echo 'CACHED' || echo 'DOWNLOAD_NEEDED'",
            timeout=10,
        )
        if "DOWNLOAD_NEEDED" in r.stdout:
            on_output(f"[flash-dpu] Downloading BFB image ({bfb_filename})...")
            r = session.execute(
                f"wget -q --show-progress -O '{bfb_path}' '{bfb_url}' 2>&1 || "
                f"curl --fail -sL -o '{bfb_path}' '{bfb_url}' 2>&1",
                timeout=600,  # BFB files can be 1-2 GB
            )
            if r.exit_code != 0:
                raise RuntimeError(
                    f"BFB download failed: {r.stderr.strip() or r.stdout.strip()}"
                )
            # Validate download — BFB images are typically 1-3 GB
            size_r = session.execute(
                f"stat -c %s '{bfb_path}' 2>/dev/null || stat -f %z '{bfb_path}' 2>/dev/null",
                timeout=10,
            )
            file_size = int(size_r.stdout.strip()) if size_r.exit_code == 0 and size_r.stdout.strip().isdigit() else 0
            if file_size < 1_000_000:  # less than 1 MB is definitely not a real BFB
                head_r = session.execute(f"head -c 200 '{bfb_path}'", timeout=10)
                raise RuntimeError(
                    f"BFB download failed — file is only {file_size} bytes (expected ~1-3 GB). "
                    f"The URL may be wrong or return a redirect/error page. "
                    f"URL: {bfb_url}\n"
                    f"File content preview: {head_r.stdout.strip()[:200]}"
                )
            on_output(f"[flash-dpu] BFB file validated: {file_size:,} bytes")
        else:
            on_output(f"[flash-dpu] BFB already cached: {bfb_path}")

        r = session.execute(f"ls -lh '{bfb_path}'", timeout=10)
        on_output(f"[flash-dpu] BFB file: {r.stdout.strip()}")
        on_output(f"[flash-dpu] BFB download/cache ({time.monotonic() - t0:.1f}s elapsed)")

        # ---- Step 3.5: generate bf.cfg ----------------------------------------
        # Priority:
        #   1. User-provided bfb_config_url (already set above — skip this block)
        #   2. Full rendered bf.conf from bf_conf_renderer (via variable_assembler)
        #      — includes hostname, VLANs, DNS, NTP, kernel modules, sysctl,
        #      hugepages, OVS, mlxconfig — everything the template defines
        #   3. Minimal bf.cfg with just password + SSH config (fallback)
        if not bfb_config:
            rendered_bf_conf = variables.get("rendered_bf_conf")
            if rendered_bf_conf:
                on_output("[flash-dpu] Writing full bf.conf from rendered template...")
                r = session.execute(
                    "cat > /tmp/bf.cfg << 'BF_CFG_EOF'\n"
                    + rendered_bf_conf
                    + "\nBF_CFG_EOF\n"
                    "chmod +x /tmp/bf.cfg && echo 'BF_CFG_OK'",
                    timeout=15,
                )
                if "BF_CFG_OK" in r.stdout:
                    on_output(f"[flash-dpu] Full bf.conf written ({len(rendered_bf_conf)} chars)")
                    bfb_config = "/tmp/bf.cfg"
                else:
                    on_output(
                        f"[flash-dpu] WARNING: bf.conf write failed, falling back to minimal: "
                        f"{r.stderr[:100]}"
                    )

        # Comprehensive bf.cfg fallback — password, SSH, mlxconfig, SFs, OVS, hugepages, containerd.
        # Key requirements for DOCA BFB compatibility:
        #   - cloud-init user-data must be OVERWRITTEN (>), not appended (>>)
        #   - cloud-init must start with #cloud-config header
        #   - chpasswd block in cloud-init is belt-and-suspenders for password
        if not bfb_config:
            dpu_password = variables.get("dpu_password")
            dpu_private_key_content = variables.get("dpu_private_key_content")

            if dpu_password:
                on_output("[flash-dpu] Generating comprehensive bf.cfg (mlxconfig, SFs, OVS, hugepages, containerd)...")
                # Generate SHA-512 password hash on the remote host.
                r = session.execute(
                    f"openssl passwd -6 '{dpu_password}'",
                    timeout=10,
                )
                if r.exit_code != 0 or not r.stdout.strip():
                    on_output("[flash-dpu] WARNING: password hash generation failed, skipping bf.cfg")
                else:
                    pw_hash = r.stdout.strip()

                    auth_key_block = ""
                    if dpu_private_key_content:
                        auth_key_block = (
                            "    # Inject SSH authorized key\n"
                            "    mkdir -p /mnt/home/ubuntu/.ssh\n"
                            "    chmod 700 /mnt/home/ubuntu/.ssh\n"
                            "    chroot /mnt ssh-keygen -y -f /dev/stdin "
                            "<<< \"$(cat /tmp/_dpu_privkey)\" "
                            "> /mnt/home/ubuntu/.ssh/authorized_keys\n"
                            "    chmod 600 /mnt/home/ubuntu/.ssh/authorized_keys\n"
                            "    chown -R 1000:1000 /mnt/home/ubuntu/.ssh\n"
                        )

                    dpu_hostname = variables.get("cluster_name", "dpu") + "-dpu"

                    # Build dpu-versions.env content for cloud-init staging
                    dpu_versions_env = (
                        f"RUNC_VERSION={variables.get('runc_version', '1.2.1')}\n"
                        f"CONTAINERD_VERSION={variables.get('containerd_version', '1.7.23')}\n"
                        f"K8S_VERSION={variables.get('k8s_version', '1.29')}\n"
                        f"PAUSE_IMAGE_TAG={variables.get('pause_image_tag', '3.10')}\n"
                    )

                    bf_cfg_content = self._build_bf_cfg_content(
                        pw_hash=pw_hash,
                        dpu_hostname=dpu_hostname,
                        dpu_password=dpu_password,
                        auth_key_block=auth_key_block,
                        net_rshim_mac=variables.get("net_rshim_mac", ""),
                        ovs_ext_sf=variables.get("ovs_ext_sf", "en3f0pf0sf1"),
                        ovs_int_sf=variables.get("ovs_int_sf", "en3f1pf1sf1"),
                        ovs_int_vf=variables.get("ovs_int_vf", "pf1vf0"),
                        dpu_versions_env=dpu_versions_env,
                        dns_nameservers=variables.get("dns_nameservers", "nameserver 1.1.1.1\noptions edns0"),
                        ssh_pub_key=variables.get("ssh_pub_key", ""),
                    )

                    # Write bf.cfg using heredoc to preserve all quotes and special chars
                    r = session.execute(
                        "cat > /tmp/bf.cfg << 'BF_CFG_EOF'\n"
                        + bf_cfg_content
                        + "BF_CFG_EOF\n"
                        "chmod +x /tmp/bf.cfg && echo 'BF_CFG_OK'",
                        timeout=10,
                    )
                    if "BF_CFG_OK" in r.stdout:
                        on_output("[flash-dpu] Comprehensive bf.cfg written (mlxconfig, SFs, OVS, hugepages, containerd, bridge-retry)")
                        bfb_config = "/tmp/bf.cfg"

                        # If we have a private key, stage it for authorized_keys extraction
                        if dpu_private_key_content:
                            r = session.execute(
                                "cat > /tmp/_dpu_privkey << 'PRIVKEY_EOF'\n"
                                f"{dpu_private_key_content}\n"
                                "PRIVKEY_EOF\n"
                                "chmod 600 /tmp/_dpu_privkey && echo 'KEY_STAGED'",
                                timeout=10,
                            )
                            if "KEY_STAGED" in r.stdout:
                                on_output("[flash-dpu] SSH private key staged for authorized_keys injection")
                            else:
                                on_output("[flash-dpu] WARNING: private key staging failed, skipping key injection")
                    else:
                        on_output(f"[flash-dpu] WARNING: bf.cfg write failed: {r.stderr[:200]}")

        # ---- Step 4: flash the DPU ---------------------------------------
        if rshim_source == "bmc":
            # BMC rshim flash path (dual_dpu_obmc)
            # Uses DPU tab service-layer code for proper credential resolution,
            # rshim activation, and SFTP streaming.
            bmc_ip = variables.get("bmc_ip")
            if not bmc_ip:
                raise RuntimeError(
                    "BMC IP required for dual_dpu_obmc flash (rshim_source=bmc). "
                    "Set bmc_ip in host settings."
                )

            bmc_user = variables.get("bmc_user", "root")
            bmc_password = variables.get("bmc_password", "0penBmc")

            on_output(f"[flash-dpu] Flashing via BMC rshim at {bmc_ip} (user={bmc_user})...")

            # 1. Connect to BMC via paramiko, tunneling through the jumphost.
            # The BMC is on the management network reachable from the jumphost,
            # NOT from the Forge worker or necessarily from the bare-metal host.
            from services.dpu_tunnel import ssh_connect_to_bmc
            from services.rshim_service import RshimNotReadyError, ensure_rshim_active_on_bmc

            # Build jumphost_cred from the session's jumphost chain.
            # The jumphost can reach the BMC on the management network.
            jumphost_cred: dict[str, Any] | None = None
            if session.jumphost_chain:
                jh = session.jumphost_chain[0]
                jumphost_cred = {
                    "host": jh["host"],
                    "port": jh.get("port", 22),
                    "username": jh.get("username", "root"),
                    "auth_type": "key" if jh.get("private_key_content") else "password",
                    "private_key": jh.get("private_key_content"),
                    "key_passphrase": jh.get("passphrase"),
                    "password": jh.get("password"),
                }
                on_output(f"[flash-dpu] BMC tunnel: worker → {jh['host']} → {bmc_ip}:22")
            else:
                on_output(f"[flash-dpu] BMC direct connect: worker → {bmc_ip}:22")

            try:
                bmc_client = ssh_connect_to_bmc(
                    bmc_ip, username=bmc_user, password=bmc_password,
                    jumphost_cred=jumphost_cred, timeout=20,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to SSH to BMC at {bmc_ip}: {exc}\n"
                    "Check BMC IP, credentials, and network reachability from the jumphost."
                ) from exc
            on_output("[flash-dpu] BMC SSH: connected")

            try:
                # 2. Ensure rshim is active on BMC
                try:
                    ensure_rshim_active_on_bmc(bmc_client, device="boot")
                    on_output("[flash-dpu] BMC rshim: active")
                except RshimNotReadyError as exc:
                    raise RuntimeError(f"BMC rshim not ready: {exc}") from exc

                # 3. SW_RESET: put DPU into BFB-receive mode before streaming.
                # Without this, a running DPU OS only consumes firmware portions
                # of the BFB — the OS partition and bf.cfg are ignored.
                # BOOT_MODE 1 = boot from rshim on next reset.
                # SW_RESET 1 = trigger SoC reset now.
                on_output("[flash-dpu] Resetting DPU into BFB-receive mode (BOOT_MODE 1 + SW_RESET 1)...")
                def _bmc_exec(cmd: str, timeout: int = 20) -> tuple[int, str, str]:
                    _in, _out, _err = bmc_client.exec_command(cmd, timeout=timeout)
                    out = _out.read().decode("utf-8", errors="replace").strip()
                    err = _err.read().decode("utf-8", errors="replace").strip()
                    rc = _out.channel.recv_exit_status()
                    return rc, out, err

                rc, _, err = _bmc_exec(
                    'echo "BOOT_MODE 1" > /dev/rshim0/misc && '
                    'echo "SW_RESET 1" > /dev/rshim0/misc'
                )
                if rc != 0:
                    raise RuntimeError(f"SW_RESET on BMC failed (exit {rc}): {err}")
                on_output("[flash-dpu] DPU reset issued — waiting 5s for rshim to settle...")
                time.sleep(5)

                # Re-verify rshim is ready after reset (device nodes may bounce)
                try:
                    ensure_rshim_active_on_bmc(bmc_client, device="boot")
                    on_output("[flash-dpu] BMC rshim post-reset: active")
                except RshimNotReadyError as exc:
                    raise RuntimeError(f"BMC rshim not ready after SW_RESET: {exc}") from exc

                # 4. Stream BFB (+ bf.cfg) to /dev/rshim0/boot via the host
                # The BFB file (1-3 GB) lives on the HOST filesystem. We pipe it
                # directly from host → BMC via SSH. To avoid requiring sshpass on
                # the host, we plant a temporary SSH key on the BMC via the already-
                # open bmc_client, then use key-based SSH from the host.
                on_output("[flash-dpu] Streaming BFB to BMC /dev/rshim0/boot...")
                on_output("[flash-dpu] Planting temporary SSH key on BMC for host→BMC pipe...")

                # Generate a temporary key pair on the host
                keygen_cmd = (
                    "rm -f /tmp/.forge_bmc_key /tmp/.forge_bmc_key.pub && "
                    "ssh-keygen -t ed25519 -f /tmp/.forge_bmc_key -N '' -q && "
                    "cat /tmp/.forge_bmc_key.pub"
                )
                r = session.execute(keygen_cmd, timeout=15)
                if r.exit_code != 0 or not r.stdout.strip():
                    raise RuntimeError(
                        f"Failed to generate temporary SSH key on host: {r.stderr[:300]}"
                    )
                pub_key = r.stdout.strip()

                # Plant the public key on the BMC via the paramiko session
                def bmc_run(cmd: str, timeout: int = 10) -> tuple[int, str]:
                    stdin, stdout, stderr = bmc_client.exec_command(cmd, timeout=timeout)
                    out = stdout.read().decode("utf-8", errors="replace")
                    rc = stdout.channel.recv_exit_status()
                    return rc, out.strip()

                bmc_run("mkdir -p ~/.ssh && chmod 700 ~/.ssh")
                # Append key (idempotent — grep first to avoid duplicates)
                escaped_key = pub_key.replace("'", "'\\''")
                bmc_run(
                    f"grep -qF '{escaped_key}' ~/.ssh/authorized_keys 2>/dev/null || "
                    f"echo '{escaped_key}' >> ~/.ssh/authorized_keys && "
                    f"chmod 600 ~/.ssh/authorized_keys"
                )
                on_output("[flash-dpu] Temporary SSH key planted on BMC")

                # Stream BFB + bf.cfg concatenated to /dev/rshim0/boot.
                # The rshim boot protocol expects bf.cfg appended AFTER the BFB
                # bytes in the same stream — a separate file upload does nothing.
                # This matches bfb-install behavior and the DPU tab's
                # bfb_with_config_stream() approach.
                if bfb_config:
                    # cat BFB then bf.cfg in a single pipe
                    flash_cmd = (
                        f"cat '{bfb_path}' '{bfb_config}' | ssh -i /tmp/.forge_bmc_key "
                        f"-o StrictHostKeyChecking=no -o ConnectTimeout=10 "
                        f"{bmc_user}@{bmc_ip} 'cat > /dev/rshim0/boot'"
                    )
                    on_output("[flash-dpu] Streaming BFB + bf.cfg to BMC rshim (this may take several minutes)...")
                else:
                    flash_cmd = (
                        f"cat '{bfb_path}' | ssh -i /tmp/.forge_bmc_key "
                        f"-o StrictHostKeyChecking=no -o ConnectTimeout=10 "
                        f"{bmc_user}@{bmc_ip} 'cat > /dev/rshim0/boot'"
                    )
                    on_output("[flash-dpu] Streaming BFB to BMC rshim (no bf.cfg — this may take several minutes)...")
                r = session.execute(flash_cmd, timeout=self.timeout)

                # Clean up temporary key from both host and BMC
                session.execute("rm -f /tmp/.forge_bmc_key /tmp/.forge_bmc_key.pub", timeout=5)
                bmc_run(f"sed -i '/{pub_key.split()[1][:20]}/d' ~/.ssh/authorized_keys 2>/dev/null")
                on_output("[flash-dpu] Temporary SSH key cleaned up")

                if r.exit_code != 0:
                    raise RuntimeError(
                        f"BMC rshim flash failed (exit {r.exit_code}):\n"
                        f"stdout: {r.stdout[-1000:]}\n"
                        f"stderr: {r.stderr[-1000:]}"
                    )
                on_output(f"[flash-dpu] BMC rshim flash completed ({time.monotonic() - t0:.1f}s)")

                # 6. Verify rshim still present after flash
                try:
                    ensure_rshim_active_on_bmc(bmc_client, device="boot")
                    on_output("[flash-dpu] BMC rshim post-flash: OK")
                except RshimNotReadyError:
                    on_output("[flash-dpu] WARNING: BMC rshim device not ready after flash")

            finally:
                bmc_client.close()

            total = time.monotonic() - t0
            on_output(f"[flash-dpu] Complete ({total:.1f}s total)")
            return {
                "flash_completed": True,
                "dpu_ip": "pending",  # Discovered by wait_dpu_ready via IPv6 link-local
                "rshim_source": "bmc",
                "execution_duration_seconds": round(total, 1),
            }

        # Host rshim flash path (existing behavior for regular/bf3/bf3_ipmi/bmc topologies)
        if bfb_config:
            flash_cmd = (
                f"sudo bfb-install --bfb '{bfb_path}' --config '{bfb_config}' --rshim {rshim_device} 2>&1"
            )
        else:
            flash_cmd = f"sudo bfb-install --bfb '{bfb_path}' --rshim {rshim_device} 2>&1"

        on_output(f"[flash-dpu] Running: bfb-install --bfb ... {'--config ' + bfb_config if bfb_config else '(no config)'} --rshim {rshim_device}")
        on_output("[flash-dpu] Flashing DPU (this may take several minutes)...")
        r = session.execute(flash_cmd, timeout=self.timeout)
        if r.exit_code != 0:
            raise RuntimeError(
                f"bfb-install failed (exit {r.exit_code}):\n"
                f"stdout: {r.stdout[-2000:]}\n"
                f"stderr: {r.stderr[-2000:]}"
            )
        on_output(f"[flash-dpu] bfb-install completed ({time.monotonic() - t0:.1f}s elapsed)")

        # ---- Step 5: verify rshim still present after flash --------------
        on_output("[flash-dpu] Verifying rshim post-flash...")
        r = session.execute(
            f"test -e /dev/{rshim_device}/misc && echo 'RSHIM_OK' || echo 'RSHIM_MISSING'",
            timeout=10,
        )
        if "RSHIM_MISSING" in r.stdout:
            on_output("[flash-dpu] WARNING: rshim device not found after flash")
        else:
            on_output("[flash-dpu] rshim: OK")

        total = time.monotonic() - t0
        on_output(f"[flash-dpu] Complete ({total:.1f}s total)")
        return {
            "flash_completed": True,
            "dpu_ip": DPU_IP,
            "execution_duration_seconds": round(total, 1),
        }
