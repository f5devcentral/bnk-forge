"""
SSH module: bare-metal/probe-dpu

Probes current DPU state: NIC mode, DOCA version, rshim presence, VF count,
host OS/kernel/arch, K8s installation status, hugepages, internet/DNS
reachability, and DPU-side software (containerd, kubelet, OVS).

This is the first step in any bare-metal deployment — establishes the
baseline state and discovers hardware configuration. All commands emit
PROBE_<KEY>=<VALUE> markers so output is unambiguous regardless of
surrounding noise. Each command is fully self-contained (no reliance on
shell state from prior SSH calls).

Outputs feed into downstream steps (set-nic-mode, flash-dpu) to determine
what operations are needed.
"""

import time
from typing import Any

from modules.base import InputSpec, OutputSpec, SSHModule


class ProbeDPUModule(SSHModule):
    name = "Probe DPU State"
    path = "bare-metal/probe-dpu"
    category = "bare-metal"
    phase = "Discovery"
    description = (
        "Probe current DPU + host state: NIC mode, DOCA version, rshim presence, "
        "VF count, host OS/kernel, K8s status, hugepages, DPU software stack, "
        "internet/DNS reachability"
    )
    version = "2.0.0"

    # SSH config
    target = "host"
    estimated_duration = 45
    timeout = 90

    dependencies: list[str] = []

    inputs = {
        "bare_metal_host_id": InputSpec(
            name="bare_metal_host_id",
            source="host",
            required=True,
            description="ID of the BareMetalHost to probe",
        ),
        "host_ip": InputSpec(
            name="host_ip",
            source="host",
            required=True,
            description="Host IP address",
        ),
        "doca_release_id": InputSpec(
            name="doca_release_id",
            source="user",
            required=False,
            description="DOCA Release from catalog (selects both BFB image + host package). If empty, prerequisites must already be installed.",
        ),
    }

    outputs = {
        # --- BFB URL (resolved from catalog) ---
        "bfb_url": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        # --- NIC / MST ---
        "nic_mode": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        "nic_mode_raw": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        "mst_device": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        # --- rshim ---
        "rshim_present": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        # --- DOCA / DPU side ---
        "doca_version": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        "dpu_ssh_reachable": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        "dpu_hostname": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        "dpu_arch": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        "dpu_containerd_version": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        "dpu_kubelet_version": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        "dpu_ovs_installed": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        # --- VF / networking ---
        "vf_count": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        "default_route_iface": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        # --- Host OS ---
        "host_os": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        "host_kernel": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        "host_arch": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        # --- K8s on host ---
        "k8s_installed": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        "k8s_version": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        "k8s_running": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        # --- Memory ---
        "hugepages_gb": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        # --- Connectivity ---
        "internet_reachable": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        "dns_ok": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        # --- Prerequisites ---
        "missing_prerequisites": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
    }

    # Host packages required for DPU management
    HOST_PREREQS = [
        ("mst-tools", "which mst"),
        ("rshim", "which bfb-install"),
    ]

    def plan_commands(self, variables: dict[str, Any]) -> list[str]:
        # Always probe — no skip condition
        return []

    def parse_plan_output(self, output: str, variables: dict[str, Any]) -> bool:
        # Always run probe
        return True

    def apply_commands(self, variables: dict[str, Any]) -> list[str]:
        return [
            # 1. MST device + NIC mode (self-contained; starts mst within the same shell)
            # Note: both mst and mlxconfig typically need root/sudo on bare-metal hosts
            "sudo mst start 2>/dev/null; "
            "MST_DEV=$(ls /dev/mst/*_pciconf* 2>/dev/null | head -1); "
            'echo "PROBE_MST_DEVICE=${MST_DEV:-NOT_FOUND}"; '
            'if [ -n "$MST_DEV" ]; then '
            "  RAW=$(sudo mlxconfig -d $MST_DEV q INTERNAL_CPU_MODEL 2>/dev/null "
            "    | grep INTERNAL_CPU_MODEL); "
            '  echo "PROBE_NIC_MODE_RAW=$RAW"; '
            # Extract numeric value: grep for (0) or (1) in the line
            "  if echo \"$RAW\" | grep -q '(1)'; then "
            "    echo 'PROBE_NIC_MODE=DPU'; "
            "  elif echo \"$RAW\" | grep -q '(0)'; then "
            "    echo 'PROBE_NIC_MODE=NIC'; "
            "  else "
            '    echo "PROBE_NIC_MODE=UNKNOWN"; '
            "  fi; "
            "else "
            "  echo 'PROBE_NIC_MODE=UNKNOWN'; "
            "fi",
            # 2. rshim check (use misc node which is always present when rshim is loaded)
            "if [ -e /dev/rshim0/misc ]; then "
            "  echo 'PROBE_RSHIM=true'; "
            "else "
            "  echo 'PROBE_RSHIM=false'; "
            "fi",
            # 3. DPU SSH reachability + DOCA version + DPU OS info
            "if ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o BatchMode=yes "
            "ubuntu@192.168.100.2 "
            "'echo PROBE_DPU_SSH=true; "
            "echo PROBE_DPU_HOSTNAME=$(hostname); "
            "echo PROBE_DPU_ARCH=$(uname -m); "
            'dpkg -l 2>/dev/null | grep doca-runtime | awk "{print \\"PROBE_DOCA_VERSION=\\" \\$3}" '
            "|| echo PROBE_DOCA_VERSION=NOT_FOUND; "
            'which containerd >/dev/null 2>&1 && echo PROBE_DPU_CONTAINERD=$(containerd --version 2>/dev/null | awk "{print \\$3}") '
            "|| echo PROBE_DPU_CONTAINERD=NOT_FOUND; "
            'which kubelet >/dev/null 2>&1 && echo PROBE_DPU_KUBELET=$(kubelet --version 2>/dev/null | awk "{print \\$2}") '
            "|| echo PROBE_DPU_KUBELET=NOT_FOUND; "
            "ovs-vsctl show >/dev/null 2>&1 && echo PROBE_DPU_OVS=true || echo PROBE_DPU_OVS=false' "
            "2>/dev/null; then true; else echo 'PROBE_DPU_SSH=false'; fi",
            # 4. VF count — sum across ALL interfaces so multiple 0-VF interfaces don't mask
            "VF_TOTAL=0; "
            "for f in /sys/class/net/*/device/sriov_numvfs; do "
            '  [ -f "$f" ] && VF_TOTAL=$((VF_TOTAL + $(cat $f 2>/dev/null || echo 0))); '
            "done; "
            'echo "PROBE_VF_COUNT=$VF_TOTAL"',
            # 5. Default route + host OS info
            "echo \"PROBE_DEFAULT_ROUTE=$(ip route show default | awk '{print $5}' | head -1)\"; "
            "echo \"PROBE_HOST_OS=$(cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"')\"; "
            'echo "PROBE_HOST_KERNEL=$(uname -r)"; '
            'echo "PROBE_HOST_ARCH=$(uname -m)"',
            # 6. K8s status on host
            "if which kubeadm >/dev/null 2>&1; then "
            "  echo 'PROBE_K8S_INSTALLED=true'; "
            '  echo "PROBE_K8S_VERSION=$(kubeadm version -o short 2>/dev/null || echo unknown)"; '
            "  kubectl get nodes >/dev/null 2>&1 && echo 'PROBE_K8S_RUNNING=true' || echo 'PROBE_K8S_RUNNING=false'; "
            "else "
            "  echo 'PROBE_K8S_INSTALLED=false'; "
            "fi",
            # 7. Hugepages (value from /proc/meminfo is already in pages; field name
            #    HugePages_Total counts 1 GiB pages when 1G hugepages are used, or
            #    2M pages otherwise — store raw count, caller interprets)
            "HP_TOTAL=$(grep HugePages_Total /proc/meminfo 2>/dev/null | awk '{print $2}'); "
            'echo "PROBE_HUGEPAGES_GB=${HP_TOTAL:-0}"',
            # 8. Internet + DNS
            "curl -sI --max-time 10 https://content.mellanox.com >/dev/null 2>&1 "
            "&& echo 'PROBE_INTERNET=true' || echo 'PROBE_INTERNET=false'; "
            "host content.mellanox.com >/dev/null 2>&1 "
            "&& echo 'PROBE_DNS=true' || echo 'PROBE_DNS=false'",
        ]

    def execute(self, session: Any, variables: dict[str, Any], on_output: Any) -> dict[str, Any]:
        """Probe DPU state using Python SSH session."""
        t0 = time.monotonic()
        outputs: dict[str, Any] = {}

        # 0. Detect host OS and architecture (needed for doca-host URL construction)
        arch_r = session.execute("uname -m", timeout=10)
        host_arch = arch_r.stdout.strip() if arch_r.exit_code == 0 else "x86_64"

        os_r = session.execute("cat /etc/os-release 2>/dev/null", timeout=10)
        os_id = "ubuntu"
        os_ver = "22.04"
        if os_r.exit_code == 0:
            for line in os_r.stdout.splitlines():
                if line.startswith("ID="):
                    os_id = line.split("=", 1)[1].strip().strip('"')
                elif line.startswith("VERSION_ID="):
                    os_ver = line.split("=", 1)[1].strip().strip('"')

        # 0b. Resolve DOCA release from catalog (if selected)
        doca_release_id_str = variables.get("doca_release_id") or ""
        doca_host_url = ""
        bfb_url = ""

        if doca_release_id_str:
            from database import get_db_context
            from models.dpu import BluefieldSoftwareImage

            with get_db_context() as catalog_db:
                release = catalog_db.query(BluefieldSoftwareImage).filter(
                    BluefieldSoftwareImage.id == int(doca_release_id_str)
                ).first()

            if release:
                doca_host_url = release.doca_host_url or ""
                if release.base_url and release.image_filename:
                    base = release.base_url.rstrip("/")
                    bfb_url = f"{base}/{release.image_filename}"
                on_output(f"[probe] DOCA release: {release.doca_version} ({release.host_os} {release.host_os_version or ''} {release.host_arch})")
                if bfb_url:
                    on_output(f"[probe] BFB URL: {bfb_url}")
                if doca_host_url:
                    on_output(f"[probe] Host package: {doca_host_url}")
            else:
                on_output(f"[probe] Warning: DOCA release ID {doca_release_id_str} not found in catalog")

        missing = []
        for pkg_name, check_cmd in self.HOST_PREREQS:
            r = session.execute(check_cmd, timeout=10)
            if r.exit_code != 0:
                missing.append(pkg_name)

        # Determine package manager from OS (needed for any install path)
        is_debian = os_id in ("ubuntu", "debian")
        is_rpm = os_id in ("rhel", "rocky", "centos", "almalinux")

        if missing:
            on_output(f"[probe] Missing host packages: {', '.join(missing)}")

            if not doca_host_url:
                raise RuntimeError(
                    "DOCA prerequisites not installed: " + ", ".join(missing) + ". "
                    "Select a DOCA release in the blueprint variables (Probe DPU State → doca_release_id) "
                    "to enable automatic installation."
                )

            if not is_debian and not is_rpm:
                raise RuntimeError(f"Unsupported OS: {os_id}. DOCA supports Ubuntu, RHEL, and Rocky Linux.")

            # Verify OS/arch matches the selected release URL
            if is_debian and doca_host_url.endswith(".rpm"):
                raise RuntimeError(
                    f"OS mismatch: host is {os_id} (Debian-based) but selected DOCA release has an RPM package URL. "
                    "Select the correct DOCA release for this host's OS."
                )
            if is_rpm and doca_host_url.endswith(".deb"):
                raise RuntimeError(
                    f"OS mismatch: host is {os_id} (RPM-based) but selected DOCA release has a Debian package URL. "
                    "Select the correct DOCA release for this host's OS."
                )

            # --- DKMS build prerequisites (NVIDIA DOCA-Host DKMS Management Guide) ---
            # Must be installed BEFORE doca-all so DKMS can compile kernel modules
            # (including rshim, mlnx-ofed-kernel, etc.) during package installation.
            on_output("[probe] Installing DKMS build prerequisites...")
            if is_debian:
                r = session.execute(
                    "sudo apt-get -y install dkms gcc make perl "
                    "linux-headers-$(uname -r) 2>&1",
                    timeout=120,
                )
            else:
                # RHEL/Rocky: EPEL provides dkms
                session.execute("sudo dnf install -y epel-release 2>&1 || true", timeout=30)
                r = session.execute(
                    "sudo dnf install -y dkms gcc make perl mokutil "
                    "kernel-devel-$(uname -r) kernel-headers-$(uname -r) 2>&1",
                    timeout=120,
                )
            if r.exit_code != 0:
                on_output(f"[probe] WARNING: DKMS prereqs install returned exit {r.exit_code}: {r.stdout.strip()[-200:]}")
                on_output("[probe] Continuing — DKMS may not build kernel modules correctly")
            else:
                on_output("[probe] DKMS build prerequisites installed")

            pkg_filename = doca_host_url.rsplit("/", 1)[-1]
            on_output(f"[probe] Downloading doca-host package: {doca_host_url}")

            # Download
            r = session.execute(
                f"wget -q -O /tmp/{pkg_filename} '{doca_host_url}' 2>&1 || "
                f"curl -sL -o /tmp/{pkg_filename} '{doca_host_url}' 2>&1",
                timeout=120,
            )
            if r.exit_code != 0:
                raise RuntimeError(
                    f"Failed to download doca-host package from: {doca_host_url}\n"
                    f"Output: {r.stdout.strip()[-300:]}\n"
                    f"Check the URL in the DOCA Releases catalog entry."
                )

            # Install doca-host (configures the DOCA apt/yum repo)
            on_output(f"[probe] Installing doca-host package: {pkg_filename}")
            if is_debian:
                r = session.execute(f"sudo dpkg -i /tmp/{pkg_filename} 2>&1", timeout=60)
            else:
                r = session.execute(f"sudo rpm -i /tmp/{pkg_filename} 2>&1", timeout=60)

            if r.exit_code != 0:
                raise RuntimeError(
                    f"Failed to install doca-host package: {pkg_filename}\n"
                    f"Output: {r.stdout.strip()[-300:]}\n"
                    f"The package may not be compatible with {os_id} {os_ver} ({host_arch})."
                )

            # Update package index
            if is_debian:
                on_output("[probe] Updating apt package index...")
                r = session.execute("sudo apt-get update -qq 2>&1 || true", timeout=60)
            else:
                on_output("[probe] Cleaning dnf cache...")
                r = session.execute("sudo dnf clean all && sudo dnf makecache 2>&1", timeout=60)

            # Install doca-all (DKMS will build kernel modules during this step)
            on_output("[probe] Installing doca-all (DKMS will build kernel modules)...")
            if is_debian:
                r = session.execute("sudo apt-get -y install doca-all 2>&1", timeout=600)
            else:
                r = session.execute("sudo dnf -y install doca-all 2>&1", timeout=600)

            if r.exit_code != 0:
                install_output = r.stdout.strip()[-400:]
                raise RuntimeError(
                    f"doca-all installation failed.\n"
                    f"Output: {install_output}\n"
                    f"The doca-host package may have configured an incorrect repo for {os_id} {os_ver} ({host_arch})."
                )

        if missing:
            # Verify all tools after install
            still_missing = []
            for pkg_name, check_cmd in self.HOST_PREREQS:
                if pkg_name in missing:
                    r2 = session.execute(check_cmd, timeout=10)
                    if r2.exit_code != 0:
                        still_missing.append(pkg_name)

            if still_missing:
                raise RuntimeError(
                    f"DOCA installed but prerequisites still not satisfied: {', '.join(still_missing)}.\n"
                    f"For rshim-kernel: check 'modinfo rshim' and 'dkms status' on the host."
                )

            on_output("[probe] All DOCA prerequisites installed and verified")

        outputs["missing_prerequisites"] = []

        # Topology-aware probing
        deploy_dpu_pci_address = variables.get("deploy_dpu_pci_address")
        rshim_source = variables.get("rshim_source")

        # 1. Check sudo access — warn if non-interactive sudo isn't available
        on_output("[probe] Verifying sudo access...")
        r = session.execute("sudo -n true 2>&1", timeout=10)
        if r.exit_code != 0:
            on_output(
                "[probe] WARNING: passwordless sudo not available — "
                "will use SSH password for sudo (sudo -S). "
                "Consider configuring NOPASSWD for better performance."
            )
        else:
            on_output("[probe] sudo access: OK (NOPASSWD)")

        # 1. MST device + NIC mode
        on_output("[probe] Checking MST devices...")
        if "mst-tools" in (outputs.get("missing_prerequisites") or []):
            on_output("[probe] MST: mst-tools package not installed — install it to enable NIC mode detection")
            outputs["mst_device"] = None
            outputs["nic_mode"] = "unknown"
            outputs["nic_mode_raw"] = None
        else:
            # mst start prints status to stdout; run it first, then query the device separately
            r = session.execute("sudo mst start 2>&1", timeout=30)
            if r.exit_code != 0:
                on_output(f"[probe] WARNING: mst start failed (exit {r.exit_code}): {r.stdout.strip()[:100]}")
            # For dual_dpu_obmc with a selected DPU, find the MST device matching the PCI address
            if deploy_dpu_pci_address:
                r = session.execute(
                    "ls -la /dev/mst/ 2>/dev/null | grep pciconf",
                    timeout=10,
                )
                on_output(f"[probe] MST devices found: {r.stdout.strip()}")
                # List all pciconf devices and pick the correct one
                r = session.execute("ls /dev/mst/*_pciconf* 2>/dev/null", timeout=10)
                all_mst_devs = [d.strip() for d in r.stdout.strip().splitlines() if d.strip()]
                if len(all_mst_devs) > 1:
                    on_output(f"[probe] Multiple MST devices detected ({len(all_mst_devs)}), selecting for PCI {deploy_dpu_pci_address}")
                    # For deploy_dpu_index, pciconf0 = first DPU, pciconf1 = second
                    dpu_idx = variables.get("deploy_dpu_index", 0) or 0
                    target_suffix = f"pciconf{dpu_idx}"
                    mst_dev = next((d for d in all_mst_devs if target_suffix in d), all_mst_devs[0])
                    r.stdout = mst_dev
                else:
                    r = session.execute("ls /dev/mst/*_pciconf* 2>/dev/null | head -1", timeout=10)
            else:
                r = session.execute("ls /dev/mst/*_pciconf* 2>/dev/null | head -1", timeout=10)
            mst_dev = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
            if mst_dev and mst_dev.startswith("/dev/mst/") and r.exit_code == 0:
                outputs["mst_device"] = mst_dev
                on_output(f"[probe] MST device: {mst_dev}")

                # Query NIC mode
                r2 = session.execute(f"sudo mlxconfig -d {mst_dev} q INTERNAL_CPU_MODEL 2>/dev/null", timeout=15)
                raw_mode = r2.stdout.strip()
                outputs["nic_mode_raw"] = raw_mode
                if "(1)" in raw_mode:
                    outputs["nic_mode"] = "dpu"
                elif "(0)" in raw_mode:
                    outputs["nic_mode"] = "nic"
                else:
                    outputs["nic_mode"] = "unknown"
                on_output(f"[probe] NIC mode: {outputs['nic_mode']} (raw: {raw_mode})")
            else:
                outputs["mst_device"] = None
                outputs["nic_mode"] = "unknown"
                outputs["nic_mode_raw"] = None
                on_output("[probe] MST device not found")
        on_output(f"[probe] MST/NIC mode check ({time.monotonic() - t0:.1f}s elapsed)")

        # 2. rshim check — auto-detect device, attempt recovery if missing
        on_output("[probe] Checking rshim...")
        r = session.execute("ls -d /dev/rshim*/misc 2>/dev/null | head -1", timeout=10)
        rshim_misc = r.stdout.strip()
        if not rshim_misc:
            on_output("[probe] No rshim device found — attempting recovery...")

            # Try kernel module (works on x86_64 hosts)
            session.execute("sudo modprobe rshim 2>&1 || true", timeout=15)

            # Restart the userspace daemon (on arm64, rshim is userspace-only via PCI)
            session.execute("sudo systemctl restart rshim 2>&1 || true", timeout=15)
            time.sleep(3)  # give daemon + udev time to create device nodes

            r = session.execute("ls -d /dev/rshim*/misc 2>/dev/null | head -1", timeout=10)
            rshim_misc = r.stdout.strip()

            if not rshim_misc:
                # Check what the daemon reports
                r_diag = session.execute("sudo /usr/sbin/rshim -l 2>&1 || true", timeout=10)
                diag_out = r_diag.stdout.strip()
                if diag_out:
                    on_output(f"[probe] rshim daemon device list: {diag_out[:200]}")

        outputs["rshim_present"] = bool(rshim_misc)
        if rshim_misc:
            on_output(f"[probe] rshim present: {rshim_misc}")
        elif rshim_source == "bmc":
            on_output(
                "[probe] No host rshim device found (expected for dual_dpu_obmc) — "
                "rshim will be accessed via DPU OpenBMC"
            )
            outputs["rshim_present"] = False  # Host rshim not present, but BMC rshim expected
        else:
            on_output(
                "[probe] WARNING: rshim device not found — DPU flash will not be possible. "
                "On arm64 hosts, check: systemctl status rshim, /usr/sbin/rshim -l, "
                "and verify BF3 PCI device is accessible."
            )

        # 3. DPU SSH reachability + DPU-side info
        if rshim_source == "bmc":
            on_output("[probe] Skipping DPU SSH probe via tmfifo (dual_dpu_obmc: no host tmfifo)")
            on_output("[probe] DPU will be accessed via high-speed network after flash (Q8)")
            outputs["dpu_ssh_reachable"] = False
            outputs["doca_version"] = None
            outputs["dpu_hostname"] = None
            outputs["dpu_arch"] = None
            outputs["dpu_containerd_version"] = None
            outputs["dpu_kubelet_version"] = None
            outputs["dpu_ovs_installed"] = False
        else:
            on_output("[probe] Probing DPU via SSH (192.168.100.2)...")
            dpu_cmd = (
                "echo HOSTNAME=$(hostname); "
                "echo ARCH=$(uname -m); "
                "dpkg -l 2>/dev/null | grep doca-runtime | awk '{print \"DOCA=\" $3}' || echo 'DOCA=NOT_FOUND'; "
                "which containerd >/dev/null 2>&1 && echo CONTAINERD=$(containerd --version 2>/dev/null "
                "| awk '{print $3}') || echo 'CONTAINERD=NOT_FOUND'; "
                "which kubelet >/dev/null 2>&1 && echo KUBELET=$(kubelet --version 2>/dev/null "
                "| awk '{print $2}') || echo 'KUBELET=NOT_FOUND'; "
                "ovs-vsctl show >/dev/null 2>&1 && echo 'OVS=true' || echo 'OVS=false'"
            )
            r = session.execute(
                f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o BatchMode=yes "
                f"ubuntu@192.168.100.2 '{dpu_cmd}'",
                timeout=15,
            )
            if r.exit_code == 0:
                outputs["dpu_ssh_reachable"] = True
                for line in r.stdout.splitlines():
                    line = line.strip()
                    if line.startswith("HOSTNAME="):
                        outputs["dpu_hostname"] = line.split("=", 1)[1]
                    elif line.startswith("ARCH="):
                        outputs["dpu_arch"] = line.split("=", 1)[1]
                    elif line.startswith("DOCA="):
                        v = line.split("=", 1)[1]
                        outputs["doca_version"] = v if v != "NOT_FOUND" else None
                    elif line.startswith("CONTAINERD="):
                        v = line.split("=", 1)[1]
                        outputs["dpu_containerd_version"] = v if v != "NOT_FOUND" else None
                    elif line.startswith("KUBELET="):
                        v = line.split("=", 1)[1]
                        outputs["dpu_kubelet_version"] = v if v != "NOT_FOUND" else None
                    elif line.startswith("OVS="):
                        outputs["dpu_ovs_installed"] = line.split("=", 1)[1] == "true"
                on_output(
                    f"[probe] DPU reachable: hostname={outputs.get('dpu_hostname')}, "
                    f"arch={outputs.get('dpu_arch')}"
                )
            else:
                outputs["dpu_ssh_reachable"] = False
                outputs["doca_version"] = None
                outputs["dpu_hostname"] = None
                outputs["dpu_arch"] = None
                outputs["dpu_containerd_version"] = None
                outputs["dpu_kubelet_version"] = None
                outputs["dpu_ovs_installed"] = False
                on_output("[probe] DPU not reachable via SSH")
        on_output(f"[probe] DPU SSH probe ({time.monotonic() - t0:.1f}s elapsed)")

        # 4. VF count
        on_output("[probe] Checking SR-IOV VFs...")
        r = session.execute(
            "VF_TOTAL=0; for f in /sys/class/net/*/device/sriov_numvfs; do "
            '[ -f "$f" ] && VF_TOTAL=$((VF_TOTAL + $(cat $f 2>/dev/null || echo 0))); '
            "done; echo $VF_TOTAL",
            timeout=10,
        )
        try:
            outputs["vf_count"] = int(r.stdout.strip())
        except ValueError:
            outputs["vf_count"] = 0
        on_output(f"[probe] VF count: {outputs['vf_count']}")

        # 5. Default route + host OS info
        on_output("[probe] Gathering host information...")
        r = session.execute("ip route show default | awk '{print $5}' | head -1", timeout=10)
        outputs["default_route_iface"] = r.stdout.strip() or None

        r = session.execute(
            "cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'",
            timeout=10,
        )
        outputs["host_os"] = r.stdout.strip() or None

        r = session.execute("uname -r", timeout=10)
        outputs["host_kernel"] = r.stdout.strip() or None

        r = session.execute("uname -m", timeout=10)
        outputs["host_arch"] = r.stdout.strip() or None

        on_output(
            f"[probe] Host: {outputs.get('host_os')} ({outputs.get('host_arch')}), "
            f"kernel {outputs.get('host_kernel')}"
        )

        # 6. K8s status on host
        on_output("[probe] Checking Kubernetes...")
        r = session.execute("which kubeadm >/dev/null 2>&1 && echo 'true' || echo 'false'", timeout=10)
        k8s_installed = r.stdout.strip() == "true"
        outputs["k8s_installed"] = k8s_installed
        if k8s_installed:
            r = session.execute("kubeadm version -o short 2>/dev/null || echo unknown", timeout=10)
            v = r.stdout.strip()
            outputs["k8s_version"] = v if v != "unknown" else None
            r = session.execute(
                "kubectl get nodes >/dev/null 2>&1 && echo 'true' || echo 'false'",
                timeout=10,
            )
            outputs["k8s_running"] = r.stdout.strip() == "true"
            on_output(
                f"[probe] K8s installed: {outputs.get('k8s_version')}, "
                f"running: {outputs.get('k8s_running')}"
            )
        else:
            outputs["k8s_version"] = None
            outputs["k8s_running"] = False
            on_output("[probe] K8s not installed")

        # 7. Hugepages
        r = session.execute(
            "grep HugePages_Total /proc/meminfo 2>/dev/null | awk '{print $2}'",
            timeout=10,
        )
        try:
            outputs["hugepages_gb"] = int(r.stdout.strip() or "0")
        except ValueError:
            outputs["hugepages_gb"] = 0

        # 8. Internet + DNS
        on_output("[probe] Checking connectivity...")
        r = session.execute(
            "curl -sI --max-time 10 https://content.mellanox.com >/dev/null 2>&1 "
            "&& echo 'true' || echo 'false'",
            timeout=15,
        )
        outputs["internet_reachable"] = r.stdout.strip() == "true"

        r = session.execute(
            "host content.mellanox.com >/dev/null 2>&1 && echo 'true' || echo 'false'",
            timeout=10,
        )
        outputs["dns_ok"] = r.stdout.strip() == "true"

        on_output(f"[probe] Internet: {outputs['internet_reachable']}, DNS: {outputs['dns_ok']}")

        total = time.monotonic() - t0
        on_output(f"[probe] Complete ({total:.1f}s total, {len(outputs)} fields collected)")
        outputs["execution_duration_seconds"] = round(total, 1)
        outputs["bfb_url"] = bfb_url
        return outputs

    def validate_commands(self, variables: dict[str, Any]) -> list[str]:
        # Probe validation: just ensure we could connect
        return ["echo 'Probe complete'"]

    def parse_apply_output(self, output: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Parse probe output into structured data using PROBE_ markers.

        Each apply command emits lines of the form ``PROBE_<KEY>=<VALUE>``.
        Lines that don't match that prefix are silently ignored, so log noise,
        SSH banners, and mst startup messages never affect parsing.
        """
        outputs: dict[str, Any] = {}

        for line in output.splitlines():
            line = line.strip()
            if not line.startswith("PROBE_"):
                continue

            parts = line.split("=", 1)
            if len(parts) != 2:
                continue

            key, val = parts[0], parts[1].strip()

            if key == "PROBE_MST_DEVICE":
                outputs["mst_device"] = val if val != "NOT_FOUND" else None
            elif key == "PROBE_NIC_MODE":
                if val == "DPU" or "(1)" in val or "EMBEDDED_CPU" in val:
                    outputs["nic_mode"] = "dpu"
                elif val == "NIC" or "(0)" in val or "SEPARATED_HOST" in val:
                    outputs["nic_mode"] = "nic"
                elif val == "UNKNOWN" or not val:
                    outputs["nic_mode"] = "unknown"
                else:
                    outputs["nic_mode"] = val
            elif key == "PROBE_NIC_MODE_RAW":
                # Store raw mlxconfig line for debugging
                outputs["nic_mode_raw"] = val if val else None
            elif key == "PROBE_RSHIM":
                outputs["rshim_present"] = val == "true"
            elif key == "PROBE_DPU_SSH":
                outputs["dpu_ssh_reachable"] = val == "true"
            elif key == "PROBE_DPU_HOSTNAME":
                outputs["dpu_hostname"] = val if val else None
            elif key == "PROBE_DPU_ARCH":
                outputs["dpu_arch"] = val if val else None
            elif key == "PROBE_DOCA_VERSION":
                outputs["doca_version"] = val if val != "NOT_FOUND" else None
            elif key == "PROBE_DPU_CONTAINERD":
                outputs["dpu_containerd_version"] = val if val != "NOT_FOUND" else None
            elif key == "PROBE_DPU_KUBELET":
                outputs["dpu_kubelet_version"] = val if val != "NOT_FOUND" else None
            elif key == "PROBE_DPU_OVS":
                outputs["dpu_ovs_installed"] = val == "true"
            elif key == "PROBE_VF_COUNT":
                try:
                    outputs["vf_count"] = int(val)
                except ValueError:
                    outputs["vf_count"] = 0
            elif key == "PROBE_DEFAULT_ROUTE":
                outputs["default_route_iface"] = val if val else None
            elif key == "PROBE_HOST_OS":
                outputs["host_os"] = val if val else None
            elif key == "PROBE_HOST_KERNEL":
                outputs["host_kernel"] = val if val else None
            elif key == "PROBE_HOST_ARCH":
                outputs["host_arch"] = val if val else None
            elif key == "PROBE_K8S_INSTALLED":
                outputs["k8s_installed"] = val == "true"
            elif key == "PROBE_K8S_VERSION":
                outputs["k8s_version"] = val if val not in ("unknown", "") else None
            elif key == "PROBE_K8S_RUNNING":
                outputs["k8s_running"] = val == "true"
            elif key == "PROBE_HUGEPAGES_GB":
                try:
                    outputs["hugepages_gb"] = int(val)
                except ValueError:
                    outputs["hugepages_gb"] = 0
            elif key == "PROBE_INTERNET":
                outputs["internet_reachable"] = val == "true"
            elif key == "PROBE_DNS":
                outputs["dns_ok"] = val == "true"

        return outputs
