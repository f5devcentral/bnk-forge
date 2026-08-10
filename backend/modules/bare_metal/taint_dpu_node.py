"""
SSH module: bare-metal/taint-dpu-node

Taints the DPU node to ensure only F5 TMM pods are scheduled on it.
The taint key is f5-tmm=true:NoSchedule per F5 docs.

Runs ON the HOST (target="host") since we need kubectl access.

Implementation note: uses the Python execute() path (overrides execute()) to
run each step as a discrete SSH call, bypassing the shell-streaming path.
See CURRENT_WORK.md "Key Architectural Finding".
"""

import time
from typing import Any

from modules.base import InputSpec, OutputSpec, SSHModule


class TaintDPUNodeModule(SSHModule):
    name = "Taint DPU Node"
    path = "bare-metal/taint-dpu-node"
    category = "bare-metal"
    phase = "DPU Join"
    description = "Taint DPU node to reserve for F5 TMM workloads"
    version = "1.0.0"

    # SSH config
    target = "host"
    estimated_duration = 10
    timeout = 60

    dependencies = ["bare-metal/label-dpu-node"]

    inputs = {
        "bare_metal_host_id": InputSpec(
            name="bare_metal_host_id",
            source="host",
            required=True,
            description="ID of the BareMetalHost",
        ),
        "dpu_node_name": InputSpec(
            name="dpu_node_name",
            source="module",
            from_module="bare-metal/label-dpu-node",
            from_output="dpu_node_name",
            required=False,
            description="DPU node name (auto-detected if not provided)",
        ),
        "taint_key": InputSpec(
            name="taint_key",
            default="f5-tmm",
            required=False,
            description="Taint key (f5-tmm per F5 docs, user-configurable)",
        ),
        "taint_value": InputSpec(
            name="taint_value",
            default="true",
            required=False,
            description="Taint value",
        ),
        "taint_effect": InputSpec(
            name="taint_effect",
            default="NoSchedule",
            required=False,
            description="Taint effect (NoSchedule, PreferNoSchedule, NoExecute)",
        ),
    }

    outputs = {
        "node_tainted": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=True,
        ),
    }

    def plan_commands(self, variables: dict[str, Any]) -> list[str]:
        taint_key = variables.get("taint_key", "f5-tmm")
        # Check if DPU node already has the taint
        return [
            f"sudo kubectl get nodes --selector='app=f5-tmm' -o jsonpath='{{.items[*].spec.taints}}' 2>/dev/null | "
            f"grep -q '{taint_key}' && echo 'ALREADY_TAINTED' || echo 'TAINT_NEEDED'"
        ]

    def parse_plan_output(self, output: str, variables: dict[str, Any]) -> bool:
        return "ALREADY_TAINTED" not in output

    # ------------------------------------------------------------------
    # Python execute() path — bypasses apply_commands() / streaming.
    # ------------------------------------------------------------------

    def execute(self, session: Any, variables: dict[str, Any], on_output: Any) -> dict[str, Any]:
        """Taint DPU node via discrete SSH calls."""
        t0 = time.monotonic()
        node_name = variables.get("dpu_node_name", "")
        taint_key = variables.get("taint_key", "f5-tmm")
        taint_value = variables.get("taint_value", "true")
        taint_effect = variables.get("taint_effect", "NoSchedule")
        taint_spec = f"{taint_key}={taint_value}:{taint_effect}"

        # 1. Resolve node name
        if node_name:
            dpu_node = node_name
            on_output(f"[taint-dpu-node] Using provided node name: {dpu_node}")
        else:
            on_output("[taint-dpu-node] Auto-detecting DPU node from label app=f5-tmm...")
            r = session.execute(
                "sudo kubectl get nodes --selector='app=f5-tmm' -o name | head -1",
                timeout=30,
            )
            if r.exit_code != 0 or not r.stdout.strip():
                raise RuntimeError(f"Failed to auto-detect DPU node: {r.stderr[:300]}")
            dpu_node = r.stdout.strip().removeprefix("node/")
            on_output(f"[taint-dpu-node] Detected DPU node: {dpu_node}")

        # 2. Apply taint
        on_output(f"[taint-dpu-node] Tainting node {dpu_node} with {taint_spec}...")
        r = session.execute(
            f"sudo kubectl taint nodes {dpu_node} {taint_spec} --overwrite",
            timeout=30,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"kubectl taint failed (exit {r.exit_code}): {r.stderr[:300]}")
        on_output(f"[taint-dpu-node] {r.stdout.strip()[:200]}")

        elapsed = time.monotonic() - t0
        on_output(f"[taint-dpu-node] Complete ({elapsed:.1f}s)")
        return {
            "node_tainted": True,
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
        taint_key = variables.get("taint_key", "f5-tmm")
        return [
            # Verify taint was applied
            f"sudo kubectl get nodes --selector='app=f5-tmm' -o jsonpath='{{.items[*].spec.taints}}' | grep -q '{taint_key}'",
        ]

    def parse_apply_output(self, output: str, variables: dict[str, Any]) -> dict[str, Any]:
        # Unused — execute() returns outputs directly.
        return {}
