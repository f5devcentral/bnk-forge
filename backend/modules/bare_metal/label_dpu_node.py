"""
SSH module: bare-metal/label-dpu-node

Labels the DPU node with app=f5-tmm and other BNK-required labels.
These labels are used by the F5 TMM pod node selector.

Runs ON the HOST (target="host") since we need kubectl access.

Implementation note: uses the Python execute() path (overrides execute()) to
run each step as a discrete SSH call, bypassing the shell-streaming path.
See CURRENT_WORK.md "Key Architectural Finding".
"""

import time
from typing import Any

from modules.base import InputSpec, OutputSpec, SSHModule


class LabelDPUNodeModule(SSHModule):
    name = "Label DPU Node"
    path = "bare-metal/label-dpu-node"
    category = "bare-metal"
    phase = "DPU Join"
    description = "Label DPU node with app=f5-tmm for TMM scheduling"
    version = "1.0.0"

    # SSH config
    target = "host"
    estimated_duration = 10
    timeout = 60

    dependencies = ["bare-metal/kubeadm-join"]

    inputs = {
        "bare_metal_host_id": InputSpec(
            name="bare_metal_host_id",
            source="host",
            required=True,
            description="ID of the BareMetalHost",
        ),
        "node_name": InputSpec(
            name="node_name",
            source="module",
            from_module="bare-metal/kubeadm-join",
            from_output="node_name",
            required=False,
            description="DPU node name (auto-detected if not provided)",
        ),
        "tmm_label": InputSpec(
            name="tmm_label",
            default="app=f5-tmm",
            required=False,
            description="Label for TMM scheduling",
        ),
    }

    outputs = {
        "node_labeled": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=True,
        ),
        "dpu_node_name": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
    }

    def plan_commands(self, variables: dict[str, Any]) -> list[str]:
        # Check if DPU node already has the label
        return [
            "sudo kubectl get nodes --selector='app=f5-tmm' -o name 2>/dev/null | grep -q . && "
            "echo 'ALREADY_LABELED' || echo 'LABEL_NEEDED'"
        ]

    def parse_plan_output(self, output: str, variables: dict[str, Any]) -> bool:
        return "ALREADY_LABELED" not in output

    # ------------------------------------------------------------------
    # Python execute() path — bypasses apply_commands() / streaming.
    # ------------------------------------------------------------------

    def execute(self, session: Any, variables: dict[str, Any], on_output: Any) -> dict[str, Any]:
        """Label DPU node via discrete SSH calls."""
        t0 = time.monotonic()
        node_name = variables.get("node_name", "")
        tmm_label = variables.get("tmm_label", "app=f5-tmm")

        # 1. Resolve node name
        if node_name:
            dpu_node = node_name
            on_output(f"[label-dpu-node] Using provided node name: {dpu_node}")
        else:
            on_output("[label-dpu-node] Auto-detecting DPU node (non-control-plane)...")
            r = session.execute(
                "sudo kubectl get nodes --selector='!node-role.kubernetes.io/control-plane' -o name | head -1",
                timeout=30,
            )
            if r.exit_code != 0 or not r.stdout.strip():
                raise RuntimeError(f"Failed to auto-detect DPU node: {r.stderr[:300]}")
            # kubectl returns "node/hostname" — strip the prefix
            dpu_node = r.stdout.strip().removeprefix("node/")
            on_output(f"[label-dpu-node] Detected DPU node: {dpu_node}")

        # 2. Apply TMM label
        on_output(f"[label-dpu-node] Labeling node {dpu_node} with {tmm_label}...")
        r = session.execute(
            f"sudo kubectl label node {dpu_node} {tmm_label} --overwrite",
            timeout=30,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"kubectl label failed (exit {r.exit_code}): {r.stderr[:300]}")
        on_output(f"[label-dpu-node] {r.stdout.strip()[:200]}")

        # 3. Apply architecture label
        on_output(f"[label-dpu-node] Labeling node {dpu_node} with kubernetes.io/arch=arm64...")
        r = session.execute(
            f"sudo kubectl label node {dpu_node} kubernetes.io/arch=arm64 --overwrite",
            timeout=30,
        )
        if r.exit_code != 0:
            on_output(f"[label-dpu-node] WARNING: arch label failed: {r.stderr[:200]}")
        else:
            on_output(f"[label-dpu-node] {r.stdout.strip()[:200]}")

        elapsed = time.monotonic() - t0
        on_output(f"[label-dpu-node] Complete ({elapsed:.1f}s)")
        return {
            "node_labeled": True,
            "dpu_node_name": dpu_node,
            "execution_duration_seconds": round(elapsed, 1),
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
            # Verify label was applied
            "sudo kubectl get nodes --selector='app=f5-tmm' -o name",
        ]

    def parse_apply_output(self, output: str, variables: dict[str, Any]) -> dict[str, Any]:
        # Unused — execute() returns outputs directly.
        return {}
