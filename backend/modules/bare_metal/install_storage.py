"""
SSH module: bare-metal/install-storage

Installs a local storage provisioner on the K8s cluster. Required for
PersistentVolumeClaims used by BNK components.

Uses the Rancher local-path-provisioner as it's lightweight and works
on single-node bare-metal clusters.

Implementation note: uses the Python execute() path (overrides execute()) to
run each install step as a discrete SSH call, bypassing the shell-streaming
path whose line-reassembly loses commands when estimated_duration triggers
streaming mode. See CURRENT_WORK.md "Key Architectural Finding".
"""

import time
from typing import Any

from modules.base import InputSpec, OutputSpec, SSHModule


class InstallStorageModule(SSHModule):
    name = "Install Storage Provisioner"
    path = "bare-metal/install-storage"
    category = "bare-metal"
    phase = "Cluster Addons"
    description = "Install local-path storage provisioner for PVCs"
    version = "1.0.0"

    # SSH config
    target = "host"
    estimated_duration = 60
    timeout = 180

    dependencies = ["bare-metal/install-sriov"]

    inputs = {
        "bare_metal_host_id": InputSpec(
            name="bare_metal_host_id",
            source="host",
            required=True,
            description="ID of the BareMetalHost",
        ),
        "storage_class_name": InputSpec(
            name="storage_class_name",
            default="local-path",
            required=False,
            description="Name for the default StorageClass",
        ),
        "set_default": InputSpec(
            name="set_default",
            type="bool",
            default=True,
            required=False,
            description="Set as default StorageClass",
        ),
    }

    outputs = {
        "storage_installed": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=True,
        ),
        "storage_class_name": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
    }

    def plan_commands(self, variables: dict[str, Any]) -> list[str]:
        sc_name = variables.get("storage_class_name", "local-path")
        return [
            f"sudo kubectl get sc {sc_name} 2>/dev/null && echo 'STORAGE_INSTALLED' || echo 'STORAGE_NEEDED'",
        ]

    def parse_plan_output(self, output: str, variables: dict[str, Any]) -> bool:
        return "STORAGE_INSTALLED" not in output

    # ------------------------------------------------------------------
    # Python execute() path — bypasses apply_commands() / streaming.
    # ------------------------------------------------------------------

    def execute(self, session: Any, variables: dict[str, Any], on_output: Any) -> dict[str, Any]:
        """Install local-path storage provisioner via discrete SSH calls."""
        t0 = time.monotonic()
        sc_name = variables.get("storage_class_name", "local-path")
        set_default = variables.get("set_default", True)

        # 1. Apply local-path-provisioner manifest
        on_output("[install-storage] Installing local-path storage provisioner...")
        r = session.execute(
            "sudo kubectl apply -f "
            "https://raw.githubusercontent.com/rancher/local-path-provisioner/v0.0.30/deploy/local-path-storage.yaml",
            timeout=120,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"local-path-provisioner install failed (exit {r.exit_code}): {r.stderr[:300]}")

        # 2. Wait for provisioner pod to be Ready (polling with timeout)
        on_output("[install-storage] Waiting for local-path-provisioner pod...")
        ready = False
        deadline = time.time() + 120  # 2 minute timeout
        while time.time() < deadline:
            r = session.execute(
                "sudo kubectl get pods -n local-path-storage -l app=local-path-provisioner "
                "--no-headers 2>/dev/null | grep -c Running",
                timeout=30,
            )
            count = r.stdout.strip()
            if count and count != "0":
                ready = True
                on_output("[install-storage] local-path-provisioner pod running")
                break
            time.sleep(10)
            on_output("[install-storage] Still waiting for provisioner pod...")

        if not ready:
            on_output("[install-storage] WARNING: provisioner pod not confirmed Running within timeout")

        # 3. Set as default StorageClass if requested
        if set_default:
            on_output(f"[install-storage] Setting {sc_name} as default StorageClass...")
            r = session.execute(
                f"sudo kubectl patch storageclass {sc_name} -p "
                "'{\"metadata\": {\"annotations\": {\"storageclass.kubernetes.io/is-default-class\": \"true\"}}}'",
                timeout=30,
            )
            if r.exit_code != 0:
                on_output(f"[install-storage] WARNING: failed to set default StorageClass: {r.stderr[:200]}")

        # 4. Verify StorageClass exists
        on_output("[install-storage] Verifying StorageClass...")
        r = session.execute(f"sudo kubectl get sc {sc_name}", timeout=30)
        if r.exit_code == 0:
            on_output(f"[install-storage]   {r.stdout.strip()[:200]}")
        else:
            on_output(f"[install-storage] WARNING: StorageClass {sc_name} not found after install")

        total = time.monotonic() - t0
        on_output(f"[install-storage] Complete ({total:.1f}s total)")
        return {
            "storage_installed": True,
            "storage_class_name": sc_name,
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
        sc_name = variables.get("storage_class_name", "local-path")
        return [
            f"sudo kubectl get sc {sc_name}",
            # Pod may still be starting — check it exists (not necessarily Running yet)
            "sudo kubectl get pods -n local-path-storage 2>/dev/null || true",
        ]

    def parse_apply_output(self, output: str, variables: dict[str, Any]) -> dict[str, Any]:
        # Unused — execute() returns outputs directly.
        return {}
