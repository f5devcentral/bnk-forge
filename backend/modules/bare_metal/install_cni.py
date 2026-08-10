"""
SSH module: bare-metal/install-cni

Installs Calico CNI on the K8s cluster. Required for pod networking
before any workloads can run.

Implementation note: uses the Python execute() path (overrides execute()) to
run each install step as a discrete SSH call, bypassing the shell-streaming
path whose line-reassembly loses commands when estimated_duration triggers
streaming mode. See CURRENT_WORK.md "Key Architectural Finding".

BM-016 fix: Generates the Installation CR inline with the correct pod_cidr
from variables instead of downloading the default custom-resources.yaml from
GitHub (which hardcodes 192.168.0.0/16, not matching kubeadm's 10.244.0.0/16).
"""

import time
from typing import Any

from modules.base import InputSpec, OutputSpec, SSHModule


class InstallCNIModule(SSHModule):
    name = "Install Calico CNI"
    path = "bare-metal/install-cni"
    category = "bare-metal"
    phase = "Cluster Addons"
    description = "Install Calico CNI for pod networking"
    version = "1.0.0"

    # SSH config
    target = "host"
    estimated_duration = 120
    timeout = 300

    dependencies = ["bare-metal/kubeadm-init"]

    inputs = {
        "bare_metal_host_id": InputSpec(
            name="bare_metal_host_id",
            source="host",
            required=True,
            description="ID of the BareMetalHost",
        ),
        "calico_version": InputSpec(
            name="calico_version",
            source="profile",
            default="3.29.1",
            description="Calico version to install",
        ),
        "pod_cidr": InputSpec(
            name="pod_cidr",
            source="module",
            from_module="bare-metal/kubeadm-init",
            from_output="pod_cidr",
            default="10.244.0.0/16",
            description="Pod network CIDR (must match kubeadm init)",
        ),
        "host_ip": InputSpec(
            name="host_ip",
            source="host",
            required=False,
            default="",
            description="Host IP for Calico nodeAddressAutodetection",
        ),
        "enable_ipv6": InputSpec(
            name="enable_ipv6",
            type="bool",
            default=False,
            required=False,
            description="Enable dual-stack IPv6 networking",
        ),
        "dpu_oob_subnet": InputSpec(
            name="dpu_oob_subnet",
            default="",
            required=False,
            description="DPU OOB subnet CIDR (e.g. 192.168.100.0/24). "
                        "Added to calico nodeAddressAutodetection so DPU node gets an IP.",
        ),
        "rshim_source": InputSpec(
            name="rshim_source",
            source="host",
            required=False,
            default="host",
            description=(
                "'host' or 'bmc'. When 'bmc' (dual_dpu_obmc), Calico uses "
                "`kubernetes: NodeInternalIP` autodetection instead of CIDR "
                "matching — kubelet's --node-ip is already pinned by "
                "kubeadm-init/join to the data-plane VLAN IP."
            ),
        ),
    }

    outputs = {
        "cni_installed": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=True,
        ),
        "calico_version": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
    }

    def plan_commands(self, variables: dict[str, Any]) -> list[str]:
        return ["sudo kubectl get pods -n calico-system 2>/dev/null | grep -q Running && echo 'CNI_INSTALLED' || echo 'CNI_NEEDED'"]

    def parse_plan_output(self, output: str, variables: dict[str, Any]) -> bool:
        return "CNI_INSTALLED" not in output

    # ------------------------------------------------------------------
    # Python execute() path — bypasses apply_commands() / streaming.
    # BM-016: Generates Installation CR inline with correct pod_cidr
    # instead of downloading default custom-resources.yaml (wrong CIDR).
    # ------------------------------------------------------------------

    def execute(self, session: Any, variables: dict[str, Any], on_output: Any) -> dict[str, Any]:
        """Install Calico CNI via discrete SSH calls with correct pod_cidr."""
        t0 = time.monotonic()
        version = variables.get("calico_version", "3.29.1")
        pod_cidr = variables.get("pod_cidr", "10.244.0.0/16")
        host_ip = variables.get("host_ip", "")
        enable_ipv6 = variables.get("enable_ipv6", False)
        dpu_oob_subnet = variables.get("dpu_oob_subnet", "")

        # GAP-018: Build optional IPv6 ipPool block for dual-stack clusters
        ipv6_pool = ""
        if enable_ipv6:
            ipv6_pool = (
                "    - name: default-ipv6-ippool\n"
                "      blockSize: 122\n"
                "      cidr: fd00:10:244::/48\n"
                "      encapsulation: VXLANCrossSubnet\n"
                "      natOutgoing: Enabled\n"
                "      nodeSelector: all()\n"
            )

        # 0. Wait for API server to be reachable (kubeadm-init may have just finished)
        on_output("[install-cni] Waiting for API server to be ready...")
        api_ready = False
        for attempt in range(12):  # up to 60s
            r = session.execute("sudo kubectl get nodes 2>&1", timeout=15)
            if r.exit_code == 0:
                api_ready = True
                on_output(f"[install-cni] API server ready ({(attempt + 1) * 5}s)")
                break
            time.sleep(5)
        if not api_ready:
            raise RuntimeError(
                f"API server not reachable after 60s. Last output: {r.stdout[:200]} {r.stderr[:200]}"
            )

        # 1. Install Tigera operator
        #    Use --server-side to avoid "missing kubectl.kubernetes.io/last-applied-configuration"
        #    errors when re-running on a cluster with leftover resources from a prior install.
        on_output(f"[install-cni] Installing Calico {version} Tigera operator...")
        r = session.execute(
            f"sudo kubectl apply --server-side --force-conflicts -f "
            f"https://raw.githubusercontent.com/projectcalico/calico/v{version}/manifests/tigera-operator.yaml",
            timeout=120,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"Tigera operator install failed (exit {r.exit_code}): {r.stderr[:300]}")
        on_output(f"[install-cni] Tigera operator applied ({time.monotonic() - t0:.1f}s elapsed)")

        # 2. Wait for tigera-operator deployment to be available before applying CRs
        on_output("[install-cni] Waiting for Tigera operator to be ready...")
        r = session.execute(
            "sudo kubectl wait --for=condition=Available deployment/tigera-operator "
            "-n tigera-operator --timeout=120s",
            timeout=150,
        )
        if r.exit_code != 0:
            on_output(f"[install-cni] WARNING: Tigera operator wait timed out: {r.stderr[:200]}")

        # 3. Generate and apply Installation CR with correct pod_cidr (BM-016)
        #    Do NOT use the default custom-resources.yaml from GitHub — it hardcodes
        #    192.168.0.0/16 which doesn't match kubeadm's pod CIDR.
        on_output(f"[install-cni] Applying Calico Installation CR with pod_cidr={pod_cidr}...")

        # GAP-011: Derive management network CIDR from host_ip for nodeAddressAutodetection.
        # Calico needs to know which interface to use for BGP peering / node IP selection.
        # For DPU deployments, also include the DPU OOB subnet so calico-node can
        # autodetect the DPU node's IP (which is on a different subnet than the host).
        #
        # For dual_dpu_obmc (rshim_source=bmc), use `kubernetes: NodeInternalIP`
        # instead of CIDR matching. PR-D pins kubelet's --node-ip to the
        # data-plane VLAN address on both nodes, so the K8s Node InternalIP
        # is already the correct IP for inter-node BGP/VXLAN. Letting Calico
        # delegate to that value avoids CIDR mismatches (the host's mgmt
        # subnet and the DPU's OOB subnet are NOT the BGP-peering surface
        # for this topology — the shared data-plane VLAN is, and only
        # kubelet's InternalIP reliably points there).
        rshim_source = (variables.get("rshim_source") or "host").lower()

        autodetect_block = ""
        if rshim_source == "bmc":
            autodetect_block = (
                "    nodeAddressAutodetectionV4:\n"
                "      kubernetes: NodeInternalIP\n"
            )
        else:
            autodetect_cidrs: list[str] = []
            if host_ip:
                parts = host_ip.split(".")
                if len(parts) == 4:
                    autodetect_cidrs.append(f"{parts[0]}.{parts[1]}.{parts[2]}.0/24")
            if dpu_oob_subnet:
                autodetect_cidrs.append(dpu_oob_subnet)
            if autodetect_cidrs:
                cidrs_yaml = "".join(f"      - \"{c}\"\n" for c in autodetect_cidrs)
                autodetect_block = (
                    "    nodeAddressAutodetectionV4:\n"
                    "      cidrs:\n"
                    + cidrs_yaml
                )

        installation_cr = (
            "apiVersion: operator.tigera.io/v1\n"
            "kind: Installation\n"
            "metadata:\n"
            "  name: default\n"
            "spec:\n"
            "  calicoNetwork:\n"
            + autodetect_block
            + "    ipPools:\n"
            f"    - cidr: {pod_cidr}\n"
            "      encapsulation: VXLANCrossSubnet\n"
            "      natOutgoing: Enabled\n"
            "      nodeSelector: all()\n"
            f"{ipv6_pool}"
            "---\n"
            "apiVersion: operator.tigera.io/v1\n"
            "kind: APIServer\n"
            "metadata:\n"
            "  name: default\n"
            "spec: {}"
        )

        # Write the CR to a temp file on the host, then apply it
        r = session.execute(
            f"cat > /tmp/calico-installation.yaml << 'CALICO_EOF'\n{installation_cr}\nCALICO_EOF",
            timeout=30,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"Failed to write Calico Installation CR: {r.stderr[:200]}")

        r = session.execute(
            "sudo kubectl apply --server-side --force-conflicts -f /tmp/calico-installation.yaml",
            timeout=60,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"Calico Installation CR apply failed (exit {r.exit_code}): {r.stderr[:300]}")

        # GAP-012: Restart containerd after Calico install — ensures Calico
        # networking is available to the container runtime before pods start.
        on_output("[install-cni] Restarting containerd-k8s after Calico install...")
        session.execute("sudo systemctl restart containerd-k8s", timeout=30)

        # 4. Wait for calico-node pods to be Ready (check both namespaces)
        on_output("[install-cni] Waiting for Calico pods to be ready...")
        ready = False
        deadline = time.time() + 180  # 3 minute timeout
        while time.time() < deadline:
            r = session.execute(
                "sudo kubectl get pods -n calico-system -l k8s-app=calico-node "
                "--no-headers 2>/dev/null | grep -c Running || "
                "sudo kubectl get pods -n kube-system -l k8s-app=calico-node "
                "--no-headers 2>/dev/null | grep -c Running",
                timeout=30,
            )
            count = r.stdout.strip()
            if count and count != "0":
                ready = True
                on_output(f"[install-cni] Calico node pods running: {count}")
                break
            time.sleep(15)
            on_output("[install-cni] Still waiting for Calico pods...")

        if not ready:
            on_output("[install-cni] WARNING: Calico pods not confirmed Running within timeout")

        # Cleanup temp file
        session.execute("rm -f /tmp/calico-installation.yaml", timeout=10)

        total = time.monotonic() - t0
        on_output(f"[install-cni] Complete ({total:.1f}s total)")
        return {
            "cni_installed": True,
            "calico_version": version,
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
            # Verify Calico is running
            "sudo kubectl get pods -n calico-system -l k8s-app=calico-node 2>/dev/null || "
            "sudo kubectl get pods -n kube-system -l k8s-app=calico-node",
        ]

    def parse_apply_output(self, output: str, variables: dict[str, Any]) -> dict[str, Any]:
        # Unused — execute() returns outputs directly.
        return {}
