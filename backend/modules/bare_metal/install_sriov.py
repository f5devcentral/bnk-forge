"""
SSH module: bare-metal/install-sriov

Installs the SR-IOV Device Plugin (ConfigMap + DaemonSet) on the K8s host.
This is the lightweight device-plugin approach (matches poc-deployer), NOT
the full SR-IOV Network Operator which has K8s version compatibility issues.
"""

import time
from typing import Any

from modules.base import InputSpec, OutputSpec, SSHModule


class InstallSRIOVModule(SSHModule):
    name = "Install SR-IOV Device Plugin"
    path = "bare-metal/install-sriov"
    category = "bare-metal"
    phase = "Cluster Addons"
    description = "Install SR-IOV Device Plugin for DPU VF/SF passthrough"
    version = "2.0.0"

    # SSH config
    target = "host"
    estimated_duration = 60
    timeout = 180

    dependencies = ["bare-metal/install-gateway-api"]

    inputs = {
        "bare_metal_host_id": InputSpec(
            name="bare_metal_host_id",
            source="host",
            required=True,
            description="ID of the BareMetalHost",
        ),
        "sriov_dp_version": InputSpec(
            name="sriov_dp_version",
            source="profile",
            default="v3.10.0",
            description="SR-IOV Device Plugin version",
        ),
    }

    outputs = {
        "sriov_installed": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=True,
        ),
    }

    def plan_commands(self, variables: dict[str, Any]) -> list[str]:
        return [
            "sudo kubectl get configmap sriovdp-config -n kube-system 2>/dev/null "
            "&& echo 'SRIOV_INSTALLED' || echo 'SRIOV_NEEDED'",
        ]

    def parse_plan_output(self, output: str, variables: dict[str, Any]) -> bool:
        return "SRIOV_INSTALLED" not in output

    # ------------------------------------------------------------------
    # Python execute() path
    # ------------------------------------------------------------------

    def execute(self, session: Any, variables: dict[str, Any], on_output: Any) -> dict[str, Any]:
        """Install SR-IOV Device Plugin via ConfigMap + DaemonSet."""
        t0 = time.monotonic()
        version = variables.get("sriov_dp_version", "v3.10.0")
        if not version.startswith("v"):
            version = f"v{version}"

        # 1. Create SR-IOV Device Plugin ConfigMap
        #    This configures which devices the plugin should discover.
        #    BF3 default config: one Scalable Function (sfnum=1) on each of
        #    p0 and p1. Selector keys on pfName + auxType so it works on any
        #    host regardless of the BF3's PCI slot — do NOT add pciAddresses
        #    here (it'd hardcode mgx-19's specific 0000:03:00.[01] layout).
        on_output("[install-sriov] Applying SR-IOV Device Plugin ConfigMap...")
        sriov_config = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: sriovdp-config
  namespace: kube-system
data:
  config.json: |
    {
        "resourceList": [
            {
                 "resourceName": "bf3_p0_sf",
                  "resourcePrefix": "nvidia.com",
                  "deviceType": "auxNetDevice",
                  "selectors": [{
                      "vendors": ["15b3"],
                      "devices": ["a2dc"],
                      "pfNames": ["p0#1"],
                      "auxTypes": ["sf"]
                  }]
              },
              {
                 "resourceName": "bf3_p1_sf",
                  "resourcePrefix": "nvidia.com",
                  "deviceType": "auxNetDevice",
                  "selectors": [{
                      "vendors": ["15b3"],
                      "devices": ["a2dc"],
                      "pfNames": ["p1#1"],
                      "auxTypes": ["sf"]
                  }]
              }
        ]
    }
"""
        r = session.execute(
            f"cat > /tmp/sriovdp-config.yaml << 'SRIOV_EOF'\n{sriov_config}SRIOV_EOF",
            timeout=15,
        )
        r = session.execute("sudo kubectl apply -f /tmp/sriovdp-config.yaml", timeout=30)
        if r.exit_code != 0:
            raise RuntimeError(f"SR-IOV ConfigMap apply failed (exit {r.exit_code}): {r.stderr[:300]}")

        # 2. Apply SR-IOV Device Plugin DaemonSet
        on_output(f"[install-sriov] Installing SR-IOV Device Plugin DaemonSet {version}...")
        r = session.execute(
            f"sudo kubectl apply -f "
            f"https://raw.githubusercontent.com/k8snetworkplumbingwg/sriov-network-device-plugin/"
            f"{version}/deployments/sriovdp-daemonset.yaml",
            timeout=120,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"SR-IOV DaemonSet install failed (exit {r.exit_code}): {r.stderr[:300]}")

        # 3. Patch DaemonSet to tolerate all taints (so it runs on control-plane nodes)
        on_output("[install-sriov] Patching SR-IOV DaemonSet tolerations...")
        r = session.execute(
            "sudo kubectl patch daemonset kube-sriov-device-plugin -n kube-system "
            """--type='json' -p='[{"op": "add", "path": "/spec/template/spec/tolerations", """
            """"value": [{"effect": "NoSchedule", "operator": "Exists"}]}]'""",
            timeout=30,
        )
        if r.exit_code != 0:
            on_output(f"[install-sriov] WARNING: toleration patch failed (exit {r.exit_code}): {r.stderr[:200]}")

        # 4. Wait for DaemonSet to be ready
        on_output("[install-sriov] Waiting for SR-IOV Device Plugin pods...")
        r = session.execute(
            "sudo kubectl rollout status daemonset/kube-sriov-device-plugin "
            "-n kube-system --timeout=120s",
            timeout=150,
        )
        if r.exit_code != 0:
            on_output(f"[install-sriov] WARNING: rollout status not ready: {r.stderr[:200]}")

        # Cleanup
        session.execute("rm -f /tmp/sriovdp-config.yaml", timeout=10)

        total = time.monotonic() - t0
        on_output(f"[install-sriov] Complete ({total:.1f}s total)")
        return {"sriov_installed": True, "execution_duration_seconds": round(total, 1)}

    # ------------------------------------------------------------------
    # Shell-path stubs
    # ------------------------------------------------------------------

    def apply_commands(self, variables: dict[str, Any]) -> list[str]:
        return []

    def validate_commands(self, variables: dict[str, Any]) -> list[str]:
        return [
            "sudo kubectl get configmap sriovdp-config -n kube-system",
            "sudo kubectl get daemonset kube-sriov-device-plugin -n kube-system",
        ]

    def parse_apply_output(self, output: str, variables: dict[str, Any]) -> dict[str, Any]:
        return {}
