"""
SSH module: bare-metal/set-nic-mode

Sets BlueField NIC mode to DPU (EMBEDDED_CPU) via mlxconfig.
This is typically needed for regular/bf3-ipmi topologies where
the DPU is initially in SuperNIC mode.

Note: mlxconfig alone doesn't change mode — a reboot or power cycle
is required. This module just sets the config; the flash-dpu or
power-cycle-bmc step triggers the actual mode change.

Implementation note: uses the Python execute() path (overrides execute()) to
run each step as a discrete SSH call, bypassing the shell-streaming path.
See CURRENT_WORK.md "Key Architectural Finding".
"""

import time
from typing import Any

from modules.base import InputSpec, OutputSpec, SSHModule


class SetNicModeModule(SSHModule):
    name = "Set NIC Mode (mlxconfig)"
    path = "bare-metal/set-nic-mode"
    category = "bare-metal"
    phase = "NIC Configuration"
    description = "Set BlueField NIC mode to DPU (EMBEDDED_CPU) via mlxconfig"
    version = "1.0.0"

    # SSH config
    target = "host"
    connectivity_risk = False  # mlxconfig alone doesn't change mode — needs reboot
    estimated_duration = 30
    timeout = 60

    dependencies = ["bare-metal/probe-dpu"]

    inputs = {
        "bare_metal_host_id": InputSpec(
            name="bare_metal_host_id",
            source="host",
            required=True,
            description="ID of the BareMetalHost",
        ),
        "mst_device": InputSpec(
            name="mst_device",
            source="module",
            from_module="bare-metal/probe-dpu",
            from_output="mst_device",
            required=False,
            description="MST device path from probe (None if mst-tools not installed — module will skip)",
        ),
        "target_nic_mode": InputSpec(
            name="target_nic_mode",
            default="1",
            required=False,
            description="Target INTERNAL_CPU_MODEL value (1 = DPU mode)",
        ),
    }

    outputs = {
        "nic_mode_configured": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=True,
        ),
        "reboot_required": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=True,
        ),
    }

    def plan_commands(self, variables: dict[str, Any]) -> list[str]:
        mst = variables.get("mst_device")
        if not mst:
            return ["echo 'SKIPPED — mst_device not available'"]
        return [f"sudo mlxconfig -d {mst} q INTERNAL_CPU_MODEL 2>/dev/null || echo 'CONFIG_QUERY_FAILED'"]

    def parse_plan_output(self, output: str, variables: dict[str, Any]) -> bool:
        """Return True if mode change is needed."""
        target = variables.get("target_nic_mode", "1")
        # Check if target mode value is already set (look for the numeric value in parens)
        return f"({target})" not in output

    # ------------------------------------------------------------------
    # Python execute() path — bypasses apply_commands() / streaming.
    # ------------------------------------------------------------------

    def execute(self, session: Any, variables: dict[str, Any], on_output: Any) -> dict[str, Any]:
        """Set NIC mode via mlxconfig using discrete SSH calls."""
        t0 = time.monotonic()
        mst = variables.get("mst_device")
        target = variables.get("target_nic_mode", "1")

        # Skip if mst_device not available (mst-tools not installed)
        if not mst:
            on_output("[set-nic-mode] SKIPPED — mst_device not available (mst-tools not installed on host)")
            on_output("[set-nic-mode] Install DOCA/mst-tools first, then redeploy")
            elapsed = time.monotonic() - t0
            return {
                "nic_mode_configured": False,
                "reboot_required": False,
                "skipped": True,
                "skip_reason": "mst_device not available — mst-tools not installed",
                "_elapsed_seconds": round(elapsed, 1),
            }

        # 1. Set INTERNAL_CPU_MODEL
        on_output(f"[set-nic-mode] Setting INTERNAL_CPU_MODEL={target} on {mst}...")
        r = session.execute(
            f"sudo mlxconfig -d {mst} -y set INTERNAL_CPU_MODEL={target}",
            timeout=30,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"mlxconfig set failed (exit {r.exit_code}): {r.stderr[:300]}")
        on_output(f"[set-nic-mode] mlxconfig output: {r.stdout.strip()[:200]}")

        # 2. Verify the next-boot value was written
        on_output("[set-nic-mode] Verifying config was written...")
        r = session.execute(
            f"sudo mlxconfig -d {mst} q INTERNAL_CPU_MODEL",
            timeout=30,
        )
        if r.exit_code == 0 and f"({target})" in r.stdout:
            on_output("[set-nic-mode] Config verified — next-boot value matches target")
        else:
            on_output("[set-nic-mode] WARNING: could not verify next-boot config value")

        elapsed = time.monotonic() - t0
        on_output(f"[set-nic-mode] Complete ({elapsed:.1f}s). Power cycle required for change to take effect.")
        return {
            "nic_mode_configured": True,
            "reboot_required": True,
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
        mst = variables.get("mst_device", "/dev/mst/mt41692_pciconf0")
        target = variables.get("target_nic_mode", "1")
        # Query the next-boot value to confirm config was written
        return [
            f"sudo mlxconfig -d {mst} q INTERNAL_CPU_MODEL | grep -q '({target})' && echo 'CONFIG_VERIFIED' || echo 'CONFIG_NOT_SET'",
        ]

    def parse_apply_output(self, output: str, variables: dict[str, Any]) -> dict[str, Any]:
        # Unused — execute() returns outputs directly.
        return {}
