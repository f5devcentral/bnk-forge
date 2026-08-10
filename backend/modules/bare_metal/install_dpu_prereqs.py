"""
SSH module: bare-metal/install-dpu-prereqs

Installs Kubernetes prerequisites on the DPU: containerd, runc,
kubeadm, kubelet. This prepares the DPU to join a K8s cluster as a
worker node.

Runs ON the DPU (target="dpu") via SSH relay through the host.

Implementation note: uses the Python execute() path (overrides execute()) to
run each install step as a discrete SSH call, bypassing the shell-streaming
path whose line-reassembly loses commands when estimated_duration triggers
streaming mode. See CURRENT_WORK.md "Key Architectural Finding".

Decision D-BM2-001: Uses standard containerd.service (matching poc-deployer
setup-dpu-script.sh) rather than the previously isolated containerd-k8s.service.
"""

import time
from typing import Any

from modules.base import InputSpec, OutputSpec, SSHModule


class InstallDPUPrereqsModule(SSHModule):
    name = "Install DPU Prerequisites"
    path = "bare-metal/install-dpu-prereqs"
    category = "bare-metal"
    phase = "DPU Setup"
    description = "Install containerd, runc, kubeadm, kubelet on DPU"
    version = "1.0.0"

    # SSH config — runs ON the DPU
    target = "dpu"
    estimated_duration = 180
    timeout = 600

    dependencies = ["bare-metal/setup-dpu-networking"]

    inputs = {
        "bare_metal_host_id": InputSpec(
            name="bare_metal_host_id",
            source="host",
            required=True,
            description="ID of the BareMetalHost",
        ),
        "containerd_version": InputSpec(
            name="containerd_version",
            source="profile",
            default="1.7.23",
            description="Containerd version to install",
        ),
        "runc_version": InputSpec(
            name="runc_version",
            source="profile",
            default="1.2.1",
            description="runc version to install",
        ),
        "k8s_version": InputSpec(
            name="k8s_version",
            source="profile",
            default="1.29",
            description="Kubernetes version (kubeadm/kubelet/kubectl)",
        ),
    }

    outputs = {
        "dpu_prereqs_installed": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=True,
        ),
        "containerd_version": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        "k8s_version": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
    }

    def plan_commands(self, variables: dict[str, Any]) -> list[str]:
        k8s_ver = variables.get("k8s_version", "1.29")
        # Idempotency check: prereqs are considered installed only if BOTH
        # the matching kubeadm version is present AND containerd is
        # currently active. The BFB image ships with kubeadm + containerd
        # pre-installed, and the bf.conf cloud-init script `bfb_modify_os`
        # stops + disables containerd on first boot — so a kubeadm-only
        # check would falsely conclude PREREQS_INSTALLED and skip the
        # `systemctl enable --now containerd` step, leaving kubeadm-join
        # to fail later with `no such file or directory` on
        # /run/containerd/containerd.sock.
        return [
            f"sudo kubeadm version 2>/dev/null | grep -q 'v{k8s_ver}' "
            f"&& sudo systemctl is-active --quiet containerd "
            f"&& echo 'PREREQS_INSTALLED' || echo 'PREREQS_NEEDED'",
        ]

    def parse_plan_output(self, output: str, variables: dict[str, Any]) -> bool:
        return "PREREQS_INSTALLED" not in output

    def prereq_commands(self, variables: dict[str, Any]) -> list[str]:
        """Check basic DPU requirements."""
        return [
            "which apt-get || which yum",  # Package manager exists
            "uname -m | grep -iE 'aarch64|arm'",  # ARM architecture (BlueField DPUs are aarch64)
        ]

    # ------------------------------------------------------------------
    # Python execute() path — bypasses apply_commands() / streaming.
    # Each original shell command becomes a discrete session.execute() call
    # to avoid the shell-streaming mode losing commands (see CURRENT_WORK.md).
    # ------------------------------------------------------------------

    def execute(self, session: Any, variables: dict[str, Any], on_output: Any) -> dict[str, Any]:
        """Install DPU prerequisites via discrete SSH calls."""
        t0 = time.monotonic()
        containerd_ver = variables.get("containerd_version", "1.7.23")
        runc_ver = variables.get("runc_version", "1.2.1")
        k8s_ver = (variables.get("k8s_version") or "1.29").lstrip("v")
        k8s_minor = ".".join(k8s_ver.split(".")[:2])  # e.g. "1.29"

        # 1. Disable swap (non-critical — may already be off)
        on_output("[install-dpu-prereqs] Disabling swap...")
        r = session.execute("sudo swapoff -a && sudo sed -i '/swap/d' /etc/fstab", timeout=30)
        if r.exit_code != 0:
            on_output(f"[install-dpu-prereqs] WARNING: swap disable failed: {r.stderr[:200]}")

        # 2. Write kernel module config
        on_output("[install-dpu-prereqs] Configuring kernel modules...")
        r = session.execute(
            "sudo bash -c 'cat > /etc/modules-load.d/k8s.conf << \"EOF\"\n"
            "overlay\n"
            "br_netfilter\n"
            "vfio_pci\n"
            "EOF'",
            timeout=30,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"Failed to write /etc/modules-load.d/k8s.conf: {r.stderr[:200]}")

        # 3. Load kernel modules
        on_output("[install-dpu-prereqs] Loading kernel modules...")
        r = session.execute("sudo modprobe overlay && sudo modprobe br_netfilter && sudo modprobe vfio_pci", timeout=30)
        if r.exit_code != 0:
            raise RuntimeError(f"Failed to load kernel modules: {r.stderr[:200]}")

        # 4. Write sysctl config
        on_output("[install-dpu-prereqs] Configuring sysctl for Kubernetes...")
        r = session.execute(
            "sudo bash -c 'cat > /etc/sysctl.d/k8s.conf << \"EOF\"\n"
            "net.bridge.bridge-nf-call-iptables  = 1\n"
            "net.bridge.bridge-nf-call-ip6tables = 1\n"
            "net.ipv4.ip_forward                 = 1\n"
            "EOF'",
            timeout=30,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"Failed to write /etc/sysctl.d/k8s.conf: {r.stderr[:200]}")

        # 5. Apply sysctl
        on_output("[install-dpu-prereqs] Applying sysctl settings...")
        r = session.execute("sudo sysctl --system", timeout=30)
        if r.exit_code != 0:
            raise RuntimeError(f"sysctl --system failed: {r.stderr[:200]}")

        on_output(f"[install-dpu-prereqs] System prep ({time.monotonic() - t0:.1f}s elapsed)")

        # 6. Install containerd
        on_output(f"[install-dpu-prereqs] Installing containerd {containerd_ver}...")
        r = session.execute(
            f"curl -L -o /tmp/containerd.tar.gz "
            f"https://github.com/containerd/containerd/releases/download/v{containerd_ver}/"
            f"containerd-{containerd_ver}-linux-arm64.tar.gz && "
            f"sudo tar -C /usr/local -xzf /tmp/containerd.tar.gz",
            timeout=180,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"containerd install failed (exit {r.exit_code}): {r.stderr[:300]}")

        # 6b. Generate cri-base.json — bf.cfg containerd config references base_runtime_spec
        on_output("[install-dpu-prereqs] Generating cri-base.json...")
        r = session.execute(
            "sudo mkdir -p /etc/containerd && "
            "/usr/local/bin/ctr oci spec | sudo tee /etc/containerd/cri-base.json > /dev/null",
            timeout=30,
        )
        if r.exit_code != 0:
            on_output(f"[install-dpu-prereqs] WARNING: cri-base.json generation failed: {r.stderr[:200]}")

        # 7. Install containerd systemd service from upstream GitHub
        #    D-BM2-001: Use standard containerd.service (matches poc-deployer)
        on_output("[install-dpu-prereqs] Installing containerd systemd service...")
        r = session.execute(
            "sudo curl -L -o /etc/systemd/system/containerd.service "
            "https://raw.githubusercontent.com/containerd/containerd/main/containerd.service",
            timeout=60,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"containerd.service download failed (exit {r.exit_code}): {r.stderr[:200]}")

        # 8. Install runc
        on_output(f"[install-dpu-prereqs] Installing runc {runc_ver}...")
        r = session.execute(
            f"curl -L -o /tmp/runc.arm64 "
            f"https://github.com/opencontainers/runc/releases/download/v{runc_ver}/runc.arm64 && "
            f"sudo install -m 755 /tmp/runc.arm64 /usr/local/sbin/runc",
            timeout=120,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"runc install failed (exit {r.exit_code}): {r.stderr[:200]}")

        # GAP-013: Symlink runc to /usr/sbin/runc — the DPU's bf.cfg containerd
        # config uses binaryName="/usr/sbin/runc" but we install to /usr/local/sbin/.
        session.execute("sudo ln -sf /usr/local/sbin/runc /usr/sbin/runc", timeout=10)

        # 10. Ensure containerd config directory exists
        #     The full config.toml is written by bf.cfg cloud-init — no generation needed here.
        on_output("[install-dpu-prereqs] Ensuring containerd config directory...")
        r = session.execute("sudo mkdir -p /etc/containerd", timeout=15)

        # 11. Start containerd (standard service — D-BM2-001)
        on_output("[install-dpu-prereqs] Starting containerd...")
        r = session.execute(
            "sudo systemctl daemon-reload && "
            "sudo systemctl enable --now containerd",
            timeout=60,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"containerd start failed (exit {r.exit_code}): {r.stderr[:200]}")

        on_output(f"[install-dpu-prereqs] containerd + runc installed ({time.monotonic() - t0:.1f}s elapsed)")

        # 12. Purge stale K8s packages and apt config (matches poc-deployer approach)
        #     BFB images ship with K8s pre-installed. We must purge first to avoid
        #     version conflicts and stale apt sources.
        on_output("[install-dpu-prereqs] Cleaning stale K8s packages and apt config...")
        session.execute(
            "sudo apt-mark unhold kubelet kubeadm kubectl 2>/dev/null; "
            "sudo systemctl stop kubelet 2>/dev/null; "
            "sudo apt-get purge -y --allow-change-held-packages kubelet kubeadm kubectl 2>/dev/null; "
            "sudo rm -f /usr/lib/systemd/system/kubelet.service.d/90-kubelet-bluefield.conf 2>/dev/null; "
            "sudo rm -f /etc/apt/sources.list.d/kubernetes.list 2>/dev/null; "
            "sudo rm -f /etc/apt/sources.list.d/kubernetes.sources 2>/dev/null; "
            "sudo rm -f /etc/apt/keyrings/kubernetes*.gpg 2>/dev/null; "
            "sudo rm -f /etc/apt/keyrings/kubernetes*.asc 2>/dev/null; "
            "true",
            timeout=60,
        )

        # 13. Install apt transport prerequisites
        on_output("[install-dpu-prereqs] Installing apt prerequisites...")
        # Run apt-get update separately — warnings from unrelated repos (e.g., DOCA
        # GPG key) should not abort the module. Only the install exit code matters.
        session.execute("sudo apt-get update -qq 2>&1 || true", timeout=120)
        r = session.execute(
            "sudo apt-get install -y -qq apt-transport-https ca-certificates curl gpg",
            timeout=180,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"apt prerequisites install failed (exit {r.exit_code}): {r.stderr[:300]}")

        # 14. Add Kubernetes apt repository
        on_output(f"[install-dpu-prereqs] Adding Kubernetes v{k8s_minor} apt repository...")

        # 14. Add Kubernetes apt keyring
        r = session.execute(
            "sudo mkdir -p /etc/apt/keyrings && "
            f"curl -fsSL https://pkgs.k8s.io/core:/stable:/v{k8s_minor}/deb/Release.key | "
            "sudo gpg --dearmor --yes -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg",
            timeout=60,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"Kubernetes apt keyring setup failed (exit {r.exit_code}): {r.stderr[:200]}")

        # 15. Add Kubernetes apt source list
        r = session.execute(
            f"echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] "
            f"https://pkgs.k8s.io/core:/stable:/v{k8s_minor}/deb/ /' | "
            "sudo tee /etc/apt/sources.list.d/kubernetes.list",
            timeout=30,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"Kubernetes apt source setup failed (exit {r.exit_code}): {r.stderr[:200]}")

        # 15. Install kubeadm, kubelet, kubectl
        #     --allow-downgrades: re-runs on a DPU that already has K8s packages
        #     (same or newer revision) would otherwise fail with
        #     "Packages were downgraded and -y was used without --allow-downgrades".
        # Use minor version (e.g. "1.29") for apt pattern — exact patch versions
        # may not be available on all architectures (host=x86 vs DPU=arm64).
        on_output(f"[install-dpu-prereqs] Installing kubeadm/kubelet/kubectl {k8s_minor}.*...")
        r = session.execute(
            f"sudo apt-get update -qq && "
            f"sudo apt-get install -y -qq --allow-downgrades "
            f"kubelet={k8s_minor}.* kubeadm={k8s_minor}.* kubectl={k8s_minor}.*",
            timeout=300,
        )
        if r.exit_code != 0:
            raise RuntimeError(
                f"kubeadm/kubelet/kubectl install failed (exit {r.exit_code}):\n"
                f"stdout: {r.stdout[-1000:]}\n"
                f"stderr: {r.stderr[-1000:]}"
            )

        # 16. Hold K8s packages at installed version
        on_output("[install-dpu-prereqs] Holding Kubernetes packages...")
        r = session.execute("sudo apt-mark hold kubelet kubeadm kubectl", timeout=30)
        if r.exit_code != 0:
            on_output(f"[install-dpu-prereqs] WARNING: apt-mark hold failed: {r.stderr[:200]}")

        # 17. Enable kubelet
        on_output("[install-dpu-prereqs] Enabling kubelet...")
        r = session.execute(
            "sudo systemctl daemon-reload && sudo systemctl enable --now kubelet",
            timeout=30,
        )
        if r.exit_code != 0:
            on_output(f"[install-dpu-prereqs] WARNING: kubelet enable failed: {r.stderr[:200]}")

        # 18. Validate installations
        on_output("[install-dpu-prereqs] Validating installations...")
        for check_cmd, label in [
            ("sudo containerd --version", "containerd"),
            ("sudo kubeadm version", "kubeadm"),
            ("sudo kubelet --version", "kubelet"),
            ("sudo kubectl version --client", "kubectl"),
        ]:
            r = session.execute(check_cmd, timeout=15)
            if r.exit_code == 0:
                on_output(f"[install-dpu-prereqs]   {label}: {r.stdout.strip()[:120]}")
            else:
                on_output(f"[install-dpu-prereqs]   WARNING: {label} check failed: {r.stderr[:100]}")

        total = time.monotonic() - t0
        on_output(f"[install-dpu-prereqs] Complete ({total:.1f}s total)")
        return {
            "dpu_prereqs_installed": True,
            "containerd_version": containerd_ver,
            "k8s_version": k8s_ver,
            "execution_duration_seconds": round(total, 1),
        }

    # ------------------------------------------------------------------
    # Shell-path stubs — engine falls back to these only when execute()
    # raises NotImplementedError, which we never do above.
    # ------------------------------------------------------------------

    def apply_commands(self, variables: dict[str, Any]) -> list[str]:
        # Stubbed out — the Python execute() path is always used.
        return []

    def validate_commands(self, variables: dict[str, Any]) -> list[str]:
        return [
            "sudo containerd --version",
            "sudo kubeadm version",
            "sudo kubelet --version",
            "sudo kubectl version --client",
        ]

    def parse_apply_output(self, output: str, variables: dict[str, Any]) -> dict[str, Any]:
        # Unused — execute() returns outputs directly.
        return {}
