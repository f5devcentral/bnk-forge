"""
SSH module: bare-metal/install-multus

Installs Multus CNI (thick plugin) on the K8s host. Multus provides the
NetworkAttachmentDefinition CRD required by BNK modules to attach secondary
network interfaces (SR-IOV VFs/SFs) to TMM pods.
"""

import time
from typing import Any

from modules.base import InputSpec, OutputSpec, SSHModule


class InstallMultusModule(SSHModule):
    name = "Install Multus CNI"
    path = "bare-metal/install-multus"
    category = "bare-metal"
    phase = "Cluster Addons"
    description = "Install Multus CNI thick plugin for multi-network pod support"
    version = "1.0.0"

    # SSH config
    target = "host"
    estimated_duration = 90
    timeout = 180

    dependencies = ["bare-metal/install-cni"]

    inputs = {
        "bare_metal_host_id": InputSpec(
            name="bare_metal_host_id",
            source="host",
            required=True,
            description="ID of the BareMetalHost",
        ),
        "multus_version": InputSpec(
            name="multus_version",
            source="profile",
            default="v4.2.0",
            description="Multus CNI version",
        ),
        "host_ip": InputSpec(
            name="host_ip",
            source="host",
            required=False,
            default="",
            description="Host IP address for node annotations",
        ),
    }

    outputs = {
        "multus_installed": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=True,
        ),
    }

    def plan_commands(self, variables: dict[str, Any]) -> list[str]:
        return [
            "sudo kubectl get daemonset kube-multus-ds -n kube-system 2>/dev/null "
            "&& echo 'MULTUS_INSTALLED' || echo 'MULTUS_NEEDED'",
        ]

    def parse_plan_output(self, output: str, variables: dict[str, Any]) -> bool:
        return "MULTUS_INSTALLED" not in output

    # ------------------------------------------------------------------
    # Python execute() path
    # ------------------------------------------------------------------

    def execute(self, session: Any, variables: dict[str, Any], on_output: Any) -> dict[str, Any]:
        """Install Multus CNI thick plugin."""
        t0 = time.monotonic()
        version = variables.get("multus_version", "v4.2.0")
        if not version.startswith("v"):
            version = f"v{version}"

        # 1. Check if already installed (idempotent)
        r = session.execute(
            "sudo kubectl get daemonset kube-multus-ds -n kube-system 2>/dev/null",
            timeout=15,
        )
        if r.exit_code == 0:
            on_output("[install-multus] Multus is already installed, skipping")
            total = time.monotonic() - t0
            return {"multus_installed": True, "execution_duration_seconds": round(total, 1)}

        # 2. Annotate nodes with primary interface address
        # GAP-020: Use host_ip variable when available instead of dynamic hostname -I
        on_output("[install-multus] Adding node primary-ifaddr annotations...")
        host_ip = variables.get("host_ip", "")
        if host_ip:
            # host_ip is known — use Python string interpolation (no shell expansion needed)
            annotation_value = f'{{"ipv4": "{host_ip}"}}'
            r = session.execute(
                "for node in $(sudo kubectl get node -o name); do "
                "  sudo kubectl annotate --overwrite \"$node\" "
                f"  'k8s.ovn.org/node-primary-ifaddr={annotation_value}'; "
                "done",
                timeout=30,
            )
        else:
            # Fallback: use shell command substitution (requires double quotes for expansion)
            r = session.execute(
                "for node in $(sudo kubectl get node -o name); do "
                "  NODE_IP=$(hostname -I | awk '{print $1}'); "
                "  sudo kubectl annotate --overwrite \"$node\" "
                "  \"k8s.ovn.org/node-primary-ifaddr={\\\"ipv4\\\": \\\"$NODE_IP\\\"}\"; "
                "done",
                timeout=30,
            )
        if r.exit_code != 0:
            on_output(f"[install-multus] WARNING: node annotation failed (exit {r.exit_code}): {r.stderr[:200]}")

        # 3. Deploy Multus thick plugin
        on_output(f"[install-multus] Deploying Multus CNI {version} (thick plugin)...")
        r = session.execute(
            f"sudo kubectl apply -f "
            f"https://raw.githubusercontent.com/k8snetworkplumbingwg/multus-cni/"
            f"{version}/deployments/multus-daemonset-thick.yml",
            timeout=120,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"Multus deploy failed (exit {r.exit_code}): {r.stderr[:300]}")

        # 4. Wait for initial rollout before patching
        on_output("[install-multus] Waiting for initial deployment...")
        session.execute("sleep 10", timeout=15)

        # 4b. Pin clusterNetwork to calico conflist (eliminates auto-detect race).
        # When multus uses "auto" mode, it scans /etc/cni/net.d/ at startup and
        # picks the first file it finds. If calico hasn't written 10-calico.conflist
        # yet (e.g. DPU node just joined), multus falls back to 99-loopback.conf
        # and all pod networking breaks on that node. Pinning the clusterNetwork
        # makes multus wait for the specific file instead of guessing.
        on_output("[install-multus] Pinning multus clusterNetwork to calico conflist...")
        r = session.execute(
            "sudo kubectl get configmap multus-daemon-config -n kube-system -o json 2>/dev/null | "
            "python3 -c \""
            "import sys, json; "
            "cm = json.load(sys.stdin); "
            "data = json.loads(cm['data'].get('daemon-config.json', '{}')); "
            "data['clusterNetwork'] = '/host/etc/cni/net.d/10-calico.conflist'; "
            "cm['data']['daemon-config.json'] = json.dumps(data); "
            "json.dump(cm, sys.stdout)"
            "\" | sudo kubectl apply -f - 2>&1",
            timeout=30,
        )
        if r.exit_code != 0:
            on_output(f"[install-multus] WARNING: clusterNetwork pin failed: {r.stderr[:200]}")
        else:
            on_output("[install-multus] clusterNetwork pinned to 10-calico.conflist")

        # 5. Patch memory limits (fixes OOM on large clusters)
        on_output("[install-multus] Patching memory limits to 300Mi (OOM fix)...")
        r = session.execute(
            "sudo kubectl patch daemonset kube-multus-ds -n kube-system "
            """--type='json' -p='["""
            """{"op": "replace", "path": "/spec/template/spec/containers/0/resources/limits/cpu", "value": "100m"},"""
            """{"op": "replace", "path": "/spec/template/spec/containers/0/resources/limits/memory", "value": "300Mi"},"""
            """{"op": "replace", "path": "/spec/template/spec/containers/0/resources/requests/cpu", "value": "100m"},"""
            """{"op": "replace", "path": "/spec/template/spec/containers/0/resources/requests/memory", "value": "300Mi"}"""
            """]'""",
            timeout=30,
        )
        if r.exit_code != 0:
            on_output(f"[install-multus] WARNING: memory patch failed (exit {r.exit_code}): {r.stderr[:200]}")

        # 6. Patch tolerations so it runs on control-plane/tainted nodes
        on_output("[install-multus] Patching tolerations for all taints...")
        r = session.execute(
            "sudo kubectl patch daemonset kube-multus-ds -n kube-system "
            """--type='json' -p='[{"op": "add", "path": "/spec/template/spec/tolerations", """
            """"value": [{"effect": "NoSchedule", "operator": "Exists"}]}]'""",
            timeout=30,
        )
        if r.exit_code != 0:
            on_output(f"[install-multus] WARNING: toleration patch failed (exit {r.exit_code}): {r.stderr[:200]}")

        # 7. Wait for rollout
        on_output("[install-multus] Waiting for Multus daemonset rollout...")
        r = session.execute(
            "sudo kubectl rollout status daemonset/kube-multus-ds "
            "-n kube-system --timeout=120s",
            timeout=150,
        )
        if r.exit_code != 0:
            on_output(f"[install-multus] WARNING: rollout not ready: {r.stderr[:200]}")

        # 8. Wait for NetworkAttachmentDefinition CRD to be registered
        # Multus registers the CRD asynchronously after pods start — downstream
        # modules (k8s/network-setup) need this API to exist before they can apply.
        on_output("[install-multus] Waiting for NetworkAttachmentDefinition CRD...")
        for attempt in range(30):
            r = session.execute(
                "sudo kubectl get crd network-attachment-definitions.k8s.cni.cncf.io 2>/dev/null",
                timeout=10,
            )
            if r.exit_code == 0:
                on_output("[install-multus] NAD CRD registered")
                break
            time.sleep(2)
        else:
            raise RuntimeError(
                "NetworkAttachmentDefinition CRD not registered after 60s — "
                "Multus may not have started correctly"
            )

        total = time.monotonic() - t0
        on_output(f"[install-multus] Complete ({total:.1f}s total)")
        return {"multus_installed": True, "execution_duration_seconds": round(total, 1)}

    # ------------------------------------------------------------------
    # Shell-path stubs
    # ------------------------------------------------------------------

    def apply_commands(self, variables: dict[str, Any]) -> list[str]:
        return []

    def validate_commands(self, variables: dict[str, Any]) -> list[str]:
        return [
            "sudo kubectl get daemonset kube-multus-ds -n kube-system",
            "sudo kubectl get crd network-attachment-definitions.k8s.cni.cncf.io",
        ]

    def parse_apply_output(self, output: str, variables: dict[str, Any]) -> dict[str, Any]:
        return {}
