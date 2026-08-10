"""
SSH module: bare-metal/kubeadm-init

Initializes a Kubernetes control plane on the bare-metal host using kubeadm.
This creates a single-node K8s cluster that the DPU will later join as a worker.

CRITICAL OUTPUT: This module produces the cluster auto-registration contract
outputs (cluster_name, remote_kubeconfig_path, remote_host) which trigger
automatic KubernetesCluster record creation in Forge.

Implementation note: uses the Python execute() path (overrides execute()) to
guarantee that join_command is reliably captured from a single discrete SSH
call, bypassing the shell-streaming path whose line-reassembly can lose the
JOIN_COMMAND markers when estimated_duration triggers streaming mode.
"""

import time
from typing import Any

from modules.base import InputSpec, OutputSpec, SSHModule


class KubeadmInitModule(SSHModule):
    name = "Initialize K8s Control Plane"
    path = "bare-metal/kubeadm-init"
    category = "bare-metal"
    phase = "Cluster Init"
    description = "Run kubeadm init to create K8s cluster on bare-metal host"
    version = "1.0.0"

    # SSH config
    target = "host"
    estimated_duration = 300
    timeout = 900

    dependencies = ["bare-metal/install-k8s-prereqs"]

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
            description="Host IP address for API server (default if no cluster_api_ip)",
        ),
        "cluster_api_ip": InputSpec(
            name="cluster_api_ip",
            source="module",
            from_module="bare-metal/setup-dpu-networking",
            from_output="cluster_api_ip",
            required=False,
            default="",
            description=(
                "Override for --apiserver-advertise-address. Set by "
                "setup-dpu-networking on the dual_dpu_obmc path to the "
                "host's first data-plane VLAN IP, so DPU's kubeadm-join "
                "reaches the API server directly over the shared VLAN "
                "instead of through the unreachable OOB-routed host mgmt IP."
            ),
        ),
        "cluster_name": InputSpec(
            name="cluster_name",
            source="user",
            required=True,
            description="Name for the K8s cluster",
        ),
        "k8s_version": InputSpec(
            name="k8s_version",
            source="profile",
            default="1.30.2",
            description="Kubernetes version",
        ),
        "pod_cidr": InputSpec(
            name="pod_cidr",
            default="10.244.0.0/16",
            required=False,
            description="Pod network CIDR",
        ),
        "service_cidr": InputSpec(
            name="service_cidr",
            default="10.96.0.0/12",
            required=False,
            description="Service network CIDR",
        ),
        "enable_ipv6": InputSpec(
            name="enable_ipv6",
            type="bool",
            default=False,
            required=False,
            description="Enable dual-stack IPv6 networking",
        ),
    }

    # CRITICAL: These outputs match the cluster auto-registration contract
    outputs = {
        # Auto-registration contract
        "cluster_name": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        "remote_kubeconfig_path": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value="/etc/kubernetes/admin.conf",
        ),
        "remote_host": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        # Additional outputs for downstream modules
        "pod_cidr": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        "join_command": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        "control_plane_endpoint": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
    }

    # ------------------------------------------------------------------
    # Plan — always proceed (join token must be refreshed even when
    # the cluster already exists so kubeadm-join has a valid token).
    # ------------------------------------------------------------------

    def plan_commands(self, variables: dict[str, Any]) -> list[str]:
        return ["sudo kubectl get nodes 2>/dev/null && echo 'CLUSTER_EXISTS' || echo 'CLUSTER_NEEDED'"]

    def parse_plan_output(self, output: str, variables: dict[str, Any]) -> bool:
        # Always return True — even when cluster exists we need execute() to
        # generate a fresh join token for kubeadm-join.
        return True

    # ------------------------------------------------------------------
    # Python execute() path — bypasses apply_commands() / streaming.
    # ------------------------------------------------------------------

    def execute(self, session: Any, variables: dict[str, Any], on_output: Any) -> dict[str, Any]:
        """Initialize K8s control plane and reliably capture the join command."""
        t0 = time.monotonic()
        host_ip = variables["host_ip"]
        # Override host_ip with cluster_api_ip when set (dual_dpu_obmc path:
        # the host's first data-plane VLAN IP, reachable from the DPU at L2).
        # When unset (regular topology), API server stays on host_ip.
        cluster_api_ip = str(variables.get("cluster_api_ip") or "").strip()
        api_addr = cluster_api_ip or host_ip
        if cluster_api_ip and cluster_api_ip != host_ip:
            on_output(
                f"[kubeadm-init] Advertising API server on cluster_api_ip "
                f"{cluster_api_ip} (overrides host_ip {host_ip}); set by "
                "setup-dpu-networking for dual_dpu_obmc"
            )
        k8s_ver = (variables.get("k8s_version") or "1.30.2").lstrip("v")
        # kubeadm requires full semver (e.g., 1.29.0), not just major.minor (1.29).
        # The apt glob 1.29.* works for package install, but kubeadm --kubernetes-version
        # needs the patch component.  Default to .0 when only major.minor is provided.
        if k8s_ver.count(".") < 2:
            k8s_ver = f"{k8s_ver}.0"
        pod_cidr = variables.get("pod_cidr", "10.244.0.0/16")
        service_cidr = variables.get("service_cidr", "10.96.0.0/12")
        cluster_name = variables.get("cluster_name", "bare-metal-cluster")

        # GAP-015/018: Dual-stack IPv6 — override CIDRs when enabled
        enable_ipv6 = variables.get("enable_ipv6", False)
        if enable_ipv6:
            pod_cidr = "10.244.0.0/16,fd00:10:244::/48"
            service_cidr = "10.96.0.0/12,fd00:10:96::/108"

        # 1. Check whether a cluster is already running.
        on_output("[kubeadm-init] Checking if Kubernetes cluster already exists...")
        r = session.execute("sudo kubectl get nodes 2>/dev/null && echo 'CLUSTER_EXISTS' || echo 'CLUSTER_NEEDED'", timeout=30)
        cluster_exists = "CLUSTER_EXISTS" in r.stdout

        if cluster_exists:
            on_output("[kubeadm-init] Cluster already initialised — skipping kubeadm init")
        else:
            # 1b. Clean up stale cluster state if present (e.g., kubelet was killed
            #     but manifests/etcd data remain). Without this, kubeadm init fails
            #     with "Port 10259 is in use" / "kube-apiserver.yaml already exists".
            r = session.execute(
                "test -f /etc/kubernetes/manifests/kube-apiserver.yaml && echo 'STALE' || echo 'CLEAN'",
                timeout=10,
            )
            if "STALE" in r.stdout:
                on_output("[kubeadm-init] Stale cluster state detected — running kubeadm reset...")
                r = session.execute(
                    "sudo kubeadm reset -f --cri-socket=unix:///run/containerd-k8s/containerd.sock 2>&1",
                    timeout=120,
                )
                if r.exit_code != 0:
                    on_output(f"[kubeadm-init] WARNING: kubeadm reset returned exit {r.exit_code}: {r.stderr[:200]}")
                else:
                    on_output("[kubeadm-init] kubeadm reset complete — cluster state cleaned")

            # 1c. When advertising on a non-mgmt IP (dual_dpu_obmc), pin the
            # kubelet's --node-ip to the same address so the K8s Node
            # InternalIP lands on the data-plane VLAN. Without this, kubelet
            # picks the host's default-route interface (the mgmt IP) and
            # Calico/Felix sets up inter-node tunnels over the mgmt subnet,
            # which on dual_dpu_obmc isn't routable to the DPU side and
            # leaves calico-node 0/1 Ready. /etc/default/kubelet is read by
            # the kubelet systemd unit (EnvironmentFile) and added to the
            # ExecStart as $KUBELET_EXTRA_ARGS — survives reboots, no
            # additional drop-in needed.
            if cluster_api_ip and cluster_api_ip != host_ip:
                on_output(
                    f"[kubeadm-init] Pinning kubelet --node-ip={cluster_api_ip} "
                    "via /etc/default/kubelet (so Node InternalIP matches "
                    "the API server's advertise-address)"
                )
                r = session.execute(
                    f"echo 'KUBELET_EXTRA_ARGS=--node-ip={cluster_api_ip}' "
                    "| sudo tee /etc/default/kubelet > /dev/null && echo OK",
                    timeout=10,
                )
                if "OK" not in r.stdout:
                    raise RuntimeError(
                        f"Failed to write /etc/default/kubelet: "
                        f"exit={r.exit_code} stderr={r.stderr[:200]!r}"
                    )

            # 2a. Pre-pull images so init is faster and failures are obvious early.
            on_output(f"[kubeadm-init] Pre-pulling Kubernetes {k8s_ver} images (this may take a few minutes)...")
            r = session.execute(
                f"sudo kubeadm config images pull "
                f"--kubernetes-version=v{k8s_ver} "
                f"--cri-socket=unix:///run/containerd-k8s/containerd.sock",
                timeout=600,
            )
            if r.exit_code != 0:
                on_output(f"[kubeadm-init] WARNING: image pre-pull failed (exit {r.exit_code}): {r.stderr.strip()}")
                # Non-fatal: kubeadm init will pull inline.

            # 2b. Run kubeadm init.
            on_output("[kubeadm-init] Running kubeadm init...")
            init_cmd = (
                f"sudo kubeadm init "
                f"--kubernetes-version=v{k8s_ver} "
                f"--apiserver-advertise-address={api_addr} "
                f"--pod-network-cidr={pod_cidr} "
                f"--service-cidr={service_cidr} "
                f"--cri-socket=unix:///run/containerd-k8s/containerd.sock "
                f"--upload-certs"
            )
            r = session.execute(init_cmd, timeout=self.timeout)
            if r.exit_code != 0:
                raise RuntimeError(
                    f"kubeadm init failed (exit {r.exit_code}):\n"
                    f"stdout: {r.stdout[-2000:]}\n"
                    f"stderr: {r.stderr[-2000:]}"
                )
            on_output(f"[kubeadm-init] kubeadm init complete ({time.monotonic() - t0:.1f}s elapsed)")

            # 2c. Set up kubectl for root so subsequent commands work.
            on_output("[kubeadm-init] Setting up kubectl for root...")
            r = session.execute(
                "sudo mkdir -p /root/.kube && "
                "sudo cp /etc/kubernetes/admin.conf /root/.kube/config && "
                "sudo chown root:root /root/.kube/config",
                timeout=30,
            )
            if r.exit_code != 0:
                on_output(f"[kubeadm-init] WARNING: kubectl setup failed: {r.stderr.strip()}")

        # 3. ALWAYS generate a fresh join command — a single, isolated SSH call
        #    whose entire stdout is exactly the kubeadm join line (plus possible
        #    minor noise).  Using a dedicated call avoids any streaming/buffering
        #    issues that plagued the combined shell-command path.
        on_output("[kubeadm-init] Generating worker join command...")
        r = session.execute("sudo kubeadm token create --print-join-command", timeout=60)
        if r.exit_code != 0:
            raise RuntimeError(
                f"kubeadm token create failed (exit {r.exit_code}): {r.stderr.strip()}"
            )

        join_command: str | None = None
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("kubeadm join"):
                join_command = line
                break

        if not join_command:
            raise RuntimeError(
                f"Could not find 'kubeadm join ...' line in token create output.\n"
                f"stdout: {r.stdout!r}\n"
                f"stderr: {r.stderr!r}"
            )

        on_output(f"[kubeadm-init] Join command captured ({len(join_command)} chars)")

        # 4. Derive control plane endpoint from the join command or fall back
        # to the advertise address we just used (api_addr — host VLAN IP for
        # dual_dpu_obmc, host_ip otherwise).
        control_plane_endpoint = f"{api_addr}:6443"
        # The join command looks like: kubeadm join <host>:<port> --token ...
        parts = join_command.split()
        if len(parts) >= 3 and ":" in parts[2]:
            control_plane_endpoint = parts[2]

        on_output(f"[kubeadm-init] Control plane endpoint: {control_plane_endpoint} ({time.monotonic() - t0:.1f}s elapsed)")

        # 5. Untaint control-plane node for single-node clusters.
        #    Without this, no workloads (coredns, CNI, storage) can schedule.
        on_output("[kubeadm-init] Removing control-plane NoSchedule taint (single-node cluster)...")
        r = session.execute(
            "sudo kubectl taint nodes --all node-role.kubernetes.io/control-plane- 2>&1 || true",
            timeout=30,
        )
        if "untainted" in r.stdout:
            on_output("[kubeadm-init] Control-plane taint removed")
        else:
            on_output(f"[kubeadm-init] Taint removal: {r.stdout.strip()[:100]}")

        # GAP-019: Remove not-ready taint to allow workloads to schedule immediately
        on_output("[kubeadm-init] Removing not-ready taint...")
        r = session.execute(
            "sudo kubectl taint nodes --all node.kubernetes.io/not-ready:NoSchedule- 2>&1 || true",
            timeout=30,
        )
        if "untainted" in r.stdout:
            on_output("[kubeadm-init] Not-ready taint removed")
        else:
            on_output(f"[kubeadm-init] Not-ready taint: {r.stdout.strip()[:100]}")

        # 6. Inline validation — confirm kubectl sees the node before we return.
        #    This avoids the "Socket is closed" error from validate_commands()
        #    which runs on a new SSH connection that may fail if the session aged out.
        on_output("[kubeadm-init] Verifying cluster node is visible...")
        r = session.execute("sudo kubectl get nodes 2>&1", timeout=20)
        if r.exit_code == 0:
            on_output(f"[kubeadm-init] {r.stdout.strip()}")
        else:
            on_output(f"[kubeadm-init] WARNING: kubectl get nodes returned exit {r.exit_code}: {r.stdout.strip()[:200]}")

        total = time.monotonic() - t0
        on_output(f"[kubeadm-init] Complete ({total:.1f}s total)")
        return {
            "cluster_name": cluster_name,
            "remote_kubeconfig_path": "/etc/kubernetes/admin.conf",
            "remote_host": host_ip,
            "pod_cidr": pod_cidr,
            "join_command": join_command,
            "control_plane_endpoint": control_plane_endpoint,
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
        # Validation is done inline in execute() to avoid "Socket is closed"
        # errors from stale SSH connections after long-running kubeadm operations.
        return []

    def parse_apply_output(self, output: str, variables: dict[str, Any]) -> dict[str, Any]:
        # Unused — execute() returns outputs directly.
        return {}
