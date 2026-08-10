"""
SSH module: bare-metal/install-k8s-prereqs

Installs Kubernetes prerequisites on the bare-metal host: containerd,
runc, crictl, kubeadm, kubelet. This prepares the host to become a
K8s control plane node.

Runs AFTER wait-dpu-ready — the DPU must be flashed and booted before
we install K8s packages on the host.

Implementation note: uses the Python execute() path (overrides execute()) to
run each install step as a discrete SSH call, bypassing the shell-streaming
path whose line-reassembly loses commands when estimated_duration triggers
streaming mode. See CURRENT_WORK.md "Key Architectural Finding".
"""

import shlex
import time
from typing import Any

from modules.base import InputSpec, OutputSpec, SSHModule


class InstallK8sPrereqsModule(SSHModule):
    name = "Install K8s Prerequisites"
    path = "bare-metal/install-k8s-prereqs"
    category = "bare-metal"
    phase = "DPU Setup"
    description = "Install containerd, runc, crictl, kubeadm, kubelet on bare-metal host"
    version = "1.0.0"

    # SSH config
    target = "host"
    estimated_duration = 180
    timeout = 600

    # Must run after DPU is ready and DPU prereqs are installed.
    # Strictly sequential: DPU flash + boot + DPU setup before host K8s setup.
    dependencies = ["bare-metal/install-dpu-prereqs"]

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
        "crictl_version": InputSpec(
            name="crictl_version",
            source="profile",
            default="1.30.0",
            description="crictl version to install",
        ),
        "k8s_version": InputSpec(
            name="k8s_version",
            source="profile",
            default="1.30.2",
            description="Kubernetes version (kubeadm/kubelet/kubectl)",
        ),
        "helm_version": InputSpec(
            name="helm_version",
            source="profile",
            default="3.16.3",
            description="Helm version to install on the host (BNK SSH modules run `sudo helm` against the host cluster)",
        ),
    }

    outputs = {
        "host_prereqs_installed": OutputSpec(
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
        # Strip any leading "v" so the grep target matches execute()'s normalized
        # form (a profile value of "v1.30.2" must not become "vv1.30.2").
        k8s_ver = (variables.get("k8s_version") or "1.30.2").lstrip("v")
        return [
            f"sudo kubeadm version 2>/dev/null | grep -q {shlex.quote('v' + k8s_ver)} "
            "&& echo 'PREREQS_INSTALLED' || echo 'PREREQS_NEEDED'"
        ]

    def parse_plan_output(self, output: str, variables: dict[str, Any]) -> bool:
        return "PREREQS_INSTALLED" not in output

    # ------------------------------------------------------------------
    # Python execute() path — bypasses apply_commands() / streaming.
    # Each original shell command becomes a discrete session.execute() call
    # to avoid the shell-streaming mode losing commands (see CURRENT_WORK.md).
    # ------------------------------------------------------------------

    def execute(self, session: Any, variables: dict[str, Any], on_output: Any) -> dict[str, Any]:
        """Install host K8s prerequisites via discrete SSH calls."""
        t0 = time.monotonic()
        containerd_ver = variables.get("containerd_version", "1.7.23")
        runc_ver = variables.get("runc_version", "1.2.1")
        crictl_ver = variables.get("crictl_version", "1.30.0")
        k8s_ver = (variables.get("k8s_version") or "1.30.2").lstrip("v")
        k8s_minor = ".".join(k8s_ver.split(".")[:2])  # e.g. "1.30"
        helm_ver = (variables.get("helm_version") or "3.16.3").lstrip("v")

        # All version strings are profile-controlled InputSpecs — quote them before
        # they land in shell command strings (wget/curl/apt URLs and version pins).
        # host_arch is derived from `uname -m` and constrained to amd64/arm64, so it
        # is not an injection vector.
        q_containerd = shlex.quote(str(containerd_ver))
        q_runc = shlex.quote(str(runc_ver))
        q_crictl = shlex.quote(str(crictl_ver))
        q_k8s_minor = shlex.quote(k8s_minor)
        q_helm = shlex.quote(helm_ver)

        # Detect host architecture for containerd/runc/crictl binary
        # selection. NVIDIA MGX hosts are aarch64 (Grace ARM); regular
        # topology hosts are typically x86_64.
        arch_r = session.execute("uname -m", timeout=10)
        uname_m = arch_r.stdout.strip()
        if uname_m == "x86_64":
            host_arch = "amd64"
        elif uname_m in ("aarch64", "arm64"):
            host_arch = "arm64"
        else:
            raise RuntimeError(
                f"Unsupported host architecture {uname_m!r} — install-k8s-prereqs "
                "needs amd64 or arm64 containerd/runc/crictl binaries."
            )
        on_output(f"[install-k8s-prereqs] Host arch: {uname_m} → using linux-{host_arch} binaries")

        # 1. Disable swap (non-critical — may already be off)
        on_output("[install-k8s-prereqs] Disabling swap...")
        r = session.execute("sudo swapoff -a && sudo sed -i '/swap/d' /etc/fstab", timeout=30)
        if r.exit_code != 0:
            on_output(f"[install-k8s-prereqs] WARNING: swap disable failed: {r.stderr[:200]}")

        # 2. Write kernel module config
        on_output("[install-k8s-prereqs] Configuring kernel modules...")
        r = session.execute(
            "sudo bash -c 'cat > /etc/modules-load.d/k8s.conf << \"EOF\"\n"
            "overlay\n"
            "br_netfilter\n"
            "EOF'",
            timeout=30,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"Failed to write /etc/modules-load.d/k8s.conf: {r.stderr[:200]}")

        # 3. Load kernel modules
        on_output("[install-k8s-prereqs] Loading kernel modules...")
        r = session.execute("sudo modprobe overlay && sudo modprobe br_netfilter", timeout=30)
        if r.exit_code != 0:
            raise RuntimeError(f"Failed to load kernel modules: {r.stderr[:200]}")

        # 4. Write sysctl config
        on_output("[install-k8s-prereqs] Configuring sysctl for Kubernetes...")
        r = session.execute(
            "sudo bash -c 'cat > /etc/sysctl.d/k8s.conf << \"EOF\"\n"
            "net.bridge.bridge-nf-call-iptables  = 1\n"
            "net.bridge.bridge-nf-call-ip6tables = 1\n"
            "net.ipv4.ip_forward                 = 1\n"
            "net.ipv6.conf.default.forwarding    = 1\n"
            "fs.inotify.max_user_watches         = 2099999999\n"
            "fs.inotify.max_user_instances       = 2099999999\n"
            "fs.inotify.max_queued_events        = 2099999999\n"
            "EOF'",
            timeout=30,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"Failed to write /etc/sysctl.d/k8s.conf: {r.stderr[:200]}")

        # 5. Apply sysctl
        on_output("[install-k8s-prereqs] Applying sysctl settings...")
        r = session.execute("sudo sysctl --system", timeout=30)
        if r.exit_code != 0:
            raise RuntimeError(f"sysctl --system failed: {r.stderr[:200]}")

        on_output(f"[install-k8s-prereqs] System prep ({time.monotonic() - t0:.1f}s elapsed)")

        # 6. Install containerd (arch-matched binary)
        on_output(f"[install-k8s-prereqs] Installing containerd {containerd_ver} ({host_arch})...")
        r = session.execute(
            f"wget -q https://github.com/containerd/containerd/releases/download/v{q_containerd}/"
            f"containerd-{q_containerd}-linux-{host_arch}.tar.gz -O /tmp/containerd.tar.gz && "
            f"sudo tar -C /usr/local -xzf /tmp/containerd.tar.gz",
            timeout=180,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"containerd install failed (exit {r.exit_code}): {r.stderr[:300]}")

        # 7. Install containerd-k8s systemd service (distinct from Docker's containerd)
        on_output("[install-k8s-prereqs] Installing containerd-k8s systemd service...")
        containerd_k8s_service = """\
[Unit]
Description=containerd for Kubernetes (isolated from Docker containerd)
Documentation=https://containerd.io
After=network.target

[Service]
ExecStartPre=-/sbin/modprobe overlay
ExecStart=/usr/local/bin/containerd --config /etc/containerd-k8s/config.toml --address /run/containerd-k8s/containerd.sock --state /run/containerd-k8s --root /var/lib/containerd-k8s
Type=notify
Delegate=yes
KillMode=process
Restart=always
RestartSec=5
LimitNPROC=infinity
LimitCORE=infinity
TasksMax=infinity
OOMScoreAdjust=-999

[Install]
WantedBy=multi-user.target
"""
        r = session.execute(
            f"sudo mkdir -p /usr/local/lib/systemd/system && "
            f"cat > /tmp/containerd-k8s.service << 'CTRD_EOF'\n{containerd_k8s_service}CTRD_EOF\n"
            f"sudo mv /tmp/containerd-k8s.service /usr/local/lib/systemd/system/containerd-k8s.service",
            timeout=30,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"containerd-k8s service install failed: {r.stderr[:200]}")

        # 8. Install runc (arch-matched binary)
        on_output(f"[install-k8s-prereqs] Installing runc {runc_ver} ({host_arch})...")
        r = session.execute(
            f"wget -q https://github.com/opencontainers/runc/releases/download/v{q_runc}/runc.{host_arch} "
            f"-O /tmp/runc && "
            f"sudo mv /tmp/runc /usr/local/sbin/runc && "
            f"sudo chmod +x /usr/local/sbin/runc",
            timeout=120,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"runc install failed (exit {r.exit_code}): {r.stderr[:200]}")

        # GAP-013: Symlink runc to /usr/sbin/runc for containerd config compatibility.
        session.execute("sudo ln -sf /usr/local/sbin/runc /usr/sbin/runc", timeout=10)

        # 9. Install crictl (arch-matched binary)
        on_output(f"[install-k8s-prereqs] Installing crictl {crictl_ver} ({host_arch})...")
        r = session.execute(
            f"wget -q https://github.com/kubernetes-sigs/cri-tools/releases/download/v{q_crictl}/"
            f"crictl-v{q_crictl}-linux-{host_arch}.tar.gz -O /tmp/crictl.tar.gz && "
            f"sudo tar -C /usr/local/bin -xzf /tmp/crictl.tar.gz",
            timeout=120,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"crictl install failed (exit {r.exit_code}): {r.stderr[:200]}")

        # 9b. Configure crictl to use containerd-k8s socket
        r = session.execute(
            "cat > /tmp/crictl.yaml << 'EOF'\n"
            "runtime-endpoint: unix:///run/containerd-k8s/containerd.sock\n"
            "image-endpoint: unix:///run/containerd-k8s/containerd.sock\n"
            "timeout: 10\n"
            "EOF\n"
            "sudo mv /tmp/crictl.yaml /etc/crictl.yaml",
            timeout=15,
        )

        # 10. Configure containerd-k8s (isolated config + SystemdCgroup)
        on_output("[install-k8s-prereqs] Configuring containerd-k8s...")
        r = session.execute(
            "sudo mkdir -p /etc/containerd-k8s /run/containerd-k8s /var/lib/containerd-k8s && "
            "sudo /usr/local/bin/containerd config default | "
            "sudo tee /etc/containerd-k8s/config.toml > /dev/null && "
            "sudo sed -i 's|SystemdCgroup = false|SystemdCgroup = true|' /etc/containerd-k8s/config.toml && "
            "sudo sed -i 's|/run/containerd/containerd.sock|/run/containerd-k8s/containerd.sock|' /etc/containerd-k8s/config.toml",
            timeout=30,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"containerd-k8s config failed (exit {r.exit_code}): {r.stderr[:200]}")

        # 11. Start containerd-k8s
        #     Also remove any stale legacy containerd.service that conflicts with Docker
        on_output("[install-k8s-prereqs] Starting containerd-k8s...")
        session.execute(
            # Remove legacy containerd.service from K8s (prevents Docker conflict)
            "sudo rm -f /usr/local/lib/systemd/system/containerd.service 2>/dev/null; "
            # Unmask containerd-k8s in case it was masked
            "sudo systemctl unmask containerd-k8s.service 2>/dev/null; "
            "for f in /etc/systemd/system/containerd-k8s.service /usr/local/lib/systemd/system/containerd-k8s.service; do "
            "  [ -L \"$f\" ] && [ \"$(readlink \"$f\")\" = '/dev/null' ] && sudo rm -f \"$f\"; "
            "done; true",
            timeout=15,
        )
        r = session.execute("sudo systemctl daemon-reload && sudo systemctl enable --now containerd-k8s", timeout=60)
        if r.exit_code != 0:
            raise RuntimeError(f"containerd-k8s start failed (exit {r.exit_code}): {r.stderr[:200]}")

        on_output(f"[install-k8s-prereqs] containerd-k8s + runc + crictl installed ({time.monotonic() - t0:.1f}s elapsed)")

        # 12. Clean stale K8s apt config and conditionally purge packages.
        #     On first run: purge any pre-existing K8s packages (from prior manual
        #     installs or different versions) to avoid conflicts.
        #     On re-run (cluster already initialized): SKIP the purge — stopping
        #     kubelet kills the running cluster. Only clean apt sources.
        on_output("[install-k8s-prereqs] Cleaning stale K8s apt config...")
        r = session.execute("test -f /etc/kubernetes/admin.conf && echo 'CLUSTER_LIVE' || echo 'NO_CLUSTER'", timeout=10)
        cluster_live = "CLUSTER_LIVE" in r.stdout

        if cluster_live:
            on_output("[install-k8s-prereqs] Cluster detected — skipping package purge (preserving running cluster)")
        else:
            on_output("[install-k8s-prereqs] No cluster detected — purging stale K8s packages...")
            session.execute(
                # Unhold packages so they can be purged
                "sudo apt-mark unhold kubelet kubeadm kubectl 2>/dev/null; "
                # Stop services that hold locks
                "sudo systemctl stop kubelet 2>/dev/null; "
                # Purge existing K8s packages
                "sudo apt-get purge -y --allow-change-held-packages kubelet kubeadm kubectl 2>/dev/null; "
                "true",
                timeout=60,
            )

        # Always clean apt sources (safe even with a running cluster)
        session.execute(
            # Remove any keyrings related to kubernetes
            "sudo rm -f /etc/apt/keyrings/kubernetes* 2>/dev/null; "
            # Remove ALL sources.list.d entries referencing any K8s repo URL
            "sudo grep -rl -E 'pkgs\\.k8s\\.io|apt\\.kubernetes\\.io|packages\\.cloud\\.google\\.com/apt.*kubernetes' "
            "/etc/apt/sources.list.d/ 2>/dev/null | sudo xargs rm -f 2>/dev/null; "
            # Also clean K8s repo lines from sources.list itself
            "sudo sed -i -E '/pkgs\\.k8s\\.io|apt\\.kubernetes\\.io|packages\\.cloud\\.google\\.com\\/apt.*kubernetes/d' "
            "/etc/apt/sources.list 2>/dev/null; "
            "true",
            timeout=15,
        )

        # 12b. Deduplicate Docker apt sources (common on manually-configured hosts).
        #      Duplicate entries cause apt-get update to fail with exit 100.
        session.execute(
            "if [ -f /etc/apt/sources.list.d/docker.list ] && "
            "ls /etc/apt/sources.list.d/archive_uri-*docker*.list 1>/dev/null 2>&1; then "
            "  sudo rm -f /etc/apt/sources.list.d/archive_uri-*docker*.list; "
            "fi; true",
            timeout=15,
        )

        # 13. Install apt transport prerequisites
        on_output("[install-k8s-prereqs] Installing apt prerequisites...")
        # Run apt-get update separately — warnings from unrelated repos (e.g., DOCA
        # GPG key) should not abort the module. Only the install exit code matters.
        session.execute("sudo apt-get update -qq 2>&1 || true", timeout=120)
        r = session.execute(
            "sudo apt-get install -y -qq apt-transport-https ca-certificates curl gpg ipset",
            timeout=180,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"apt prerequisites install failed (exit {r.exit_code}): {r.stderr[:300]}")

        # 14. Add Kubernetes apt repository
        on_output(f"[install-k8s-prereqs] Adding Kubernetes v{k8s_minor} apt repository...")

        # 14. Add Kubernetes apt keyring
        r = session.execute(
            "sudo mkdir -p /etc/apt/keyrings && "
            f"curl -fsSL https://pkgs.k8s.io/core:/stable:/v{q_k8s_minor}/deb/Release.key | "
            "sudo gpg --dearmor --yes -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg",
            timeout=60,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"Kubernetes apt keyring setup failed (exit {r.exit_code}): {r.stderr[:200]}")

        # 15. Add Kubernetes apt source list
        deb_line = (
            "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] "
            f"https://pkgs.k8s.io/core:/stable:/v{k8s_minor}/deb/ /"
        )
        r = session.execute(
            f"echo {shlex.quote(deb_line)} | sudo tee /etc/apt/sources.list.d/kubernetes.list",
            timeout=30,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"Kubernetes apt source setup failed (exit {r.exit_code}): {r.stderr[:200]}")

        # 15. Install kubeadm, kubelet, kubectl
        #     --allow-downgrades: re-runs on a host that already has K8s packages
        #     (same or newer revision) would otherwise fail with
        #     "Packages were downgraded and -y was used without --allow-downgrades".
        # Use minor version (e.g. "1.29") for apt pattern — exact patch versions
        # may not be available on all architectures.
        on_output(f"[install-k8s-prereqs] Installing kubeadm/kubelet/kubectl {k8s_minor}.*...")
        r = session.execute(
            f"sudo apt-get update -qq && "
            f"sudo apt-get install -y -qq --allow-downgrades "
            f"kubelet={q_k8s_minor}.* kubeadm={q_k8s_minor}.* kubectl={q_k8s_minor}.*",
            timeout=300,
        )
        if r.exit_code != 0:
            raise RuntimeError(
                f"kubeadm/kubelet/kubectl install failed (exit {r.exit_code}):\n"
                f"stdout: {r.stdout[-1000:]}\n"
                f"stderr: {r.stderr[-1000:]}"
            )

        # 16. Hold K8s packages at installed version
        on_output("[install-k8s-prereqs] Holding Kubernetes packages...")
        r = session.execute("sudo apt-mark hold kubelet kubeadm kubectl", timeout=30)
        if r.exit_code != 0:
            on_output(f"[install-k8s-prereqs] WARNING: apt-mark hold failed: {r.stderr[:200]}")

        # 17. Enable kubelet
        on_output("[install-k8s-prereqs] Enabling kubelet...")
        r = session.execute("sudo systemctl enable kubelet", timeout=30)
        if r.exit_code != 0:
            on_output(f"[install-k8s-prereqs] WARNING: kubelet enable failed: {r.stderr[:200]}")

        # 17b. Configure kubelet to use containerd-k8s socket
        on_output("[install-k8s-prereqs] Configuring kubelet container runtime endpoint...")
        r = session.execute(
            "sudo mkdir -p /etc/default && "
            "echo 'KUBELET_EXTRA_ARGS=--container-runtime-endpoint=unix:///run/containerd-k8s/containerd.sock' "
            "| sudo tee /etc/default/kubelet",
            timeout=15,
        )
        if r.exit_code != 0:
            on_output(f"[install-k8s-prereqs] WARNING: kubelet config failed: {r.stderr[:200]}")

        # 17c. Install helm on the host. The BNK SSH layer (bnk-prerequisites,
        # cert-manager, flo) runs `sudo helm` against the host's cluster, so helm
        # must be on the host PATH. kubeadm packages don't include it; the
        # poc-deployer reference (install-host-k8s.sh) installs it here. Idempotent:
        # skip when the pinned version is already present.
        on_output(f"[install-k8s-prereqs] Installing helm {helm_ver} (linux-{host_arch})...")
        r = session.execute(
            f"if command -v helm >/dev/null 2>&1 && "
            f"helm version --short 2>/dev/null | grep -q {shlex.quote('v' + helm_ver)}; then "
            f"echo {shlex.quote(f'helm {helm_ver} already present')}; else "
            f"curl -fsSL https://get.helm.sh/helm-v{q_helm}-linux-{host_arch}.tar.gz -o /tmp/helm.tgz && "
            f"sudo tar -C /tmp -xzf /tmp/helm.tgz && "
            f"sudo install -m 0755 /tmp/linux-{host_arch}/helm /usr/local/bin/helm && "
            f"rm -rf /tmp/helm.tgz /tmp/linux-{host_arch}; fi",
            timeout=120,
        )
        if r.exit_code != 0:
            raise RuntimeError(
                f"helm install failed (exit {r.exit_code}):\n"
                f"stdout: {r.stdout[-500:]}\nstderr: {r.stderr[-500:]}"
            )

        # 18. Validate installations
        on_output("[install-k8s-prereqs] Validating installations...")
        for check_cmd, label in [
            ("sudo containerd --version", "containerd"),
            ("sudo kubeadm version", "kubeadm"),
            ("sudo kubelet --version", "kubelet"),
            ("sudo crictl --version", "crictl"),
            ("sudo helm version --short", "helm"),
        ]:
            r = session.execute(check_cmd, timeout=15)
            if r.exit_code == 0:
                on_output(f"[install-k8s-prereqs]   {label}: {r.stdout.strip()[:120]}")
            else:
                on_output(f"[install-k8s-prereqs]   WARNING: {label} check failed: {r.stderr[:100]}")

        total = time.monotonic() - t0
        on_output(f"[install-k8s-prereqs] Complete ({total:.1f}s total)")
        return {
            "host_prereqs_installed": True,
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
            "sudo crictl --version",
            "sudo helm version --short",
        ]

    def parse_apply_output(self, output: str, variables: dict[str, Any]) -> dict[str, Any]:
        # Unused — execute() returns outputs directly.
        return {}
