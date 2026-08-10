"""
SSH module: bare-metal/install-gateway-api

Installs Kubernetes Gateway API CRDs (experimental channel). Required by
bnk/bnk-gatewayclass to create GatewayClass resources.
"""

import time
from typing import Any

from modules.base import InputSpec, OutputSpec, SSHModule


class InstallGatewayAPIModule(SSHModule):
    name = "Install Gateway API"
    path = "bare-metal/install-gateway-api"
    category = "bare-metal"
    phase = "Cluster Addons"
    description = "Install Kubernetes Gateway API CRDs (experimental channel)"
    version = "1.0.0"

    # SSH config
    target = "host"
    estimated_duration = 30
    timeout = 120

    dependencies = ["bare-metal/install-multus"]

    inputs = {
        "bare_metal_host_id": InputSpec(
            name="bare_metal_host_id",
            source="host",
            required=True,
            description="ID of the BareMetalHost",
        ),
        "gateway_api_version": InputSpec(
            name="gateway_api_version",
            source="profile",
            default="v1.2.0",
            description="Gateway API CRD version",
        ),
    }

    outputs = {
        "gateway_api_installed": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=True,
        ),
    }

    def plan_commands(self, variables: dict[str, Any]) -> list[str]:
        return [
            "sudo kubectl get crd gateways.gateway.networking.k8s.io 2>/dev/null "
            "&& echo 'GATEWAY_API_INSTALLED' || echo 'GATEWAY_API_NEEDED'",
        ]

    def parse_plan_output(self, output: str, variables: dict[str, Any]) -> bool:
        return "GATEWAY_API_INSTALLED" not in output

    # ------------------------------------------------------------------
    # Python execute() path
    # ------------------------------------------------------------------

    def execute(self, session: Any, variables: dict[str, Any], on_output: Any) -> dict[str, Any]:
        """Install Gateway API CRDs."""
        t0 = time.monotonic()
        version = variables.get("gateway_api_version", "v1.2.0")
        if not version.startswith("v"):
            version = f"v{version}"

        # 1. Check if already installed (idempotent)
        r = session.execute(
            "sudo kubectl get crd gateways.gateway.networking.k8s.io 2>/dev/null",
            timeout=15,
        )
        if r.exit_code == 0:
            on_output("[install-gateway-api] Gateway API CRDs already installed, skipping")
            total = time.monotonic() - t0
            return {"gateway_api_installed": True, "execution_duration_seconds": round(total, 1)}

        # 2. Install experimental CRDs
        on_output(f"[install-gateway-api] Installing Gateway API {version} (experimental channel)...")
        r = session.execute(
            f"sudo kubectl apply -f "
            f"https://github.com/kubernetes-sigs/gateway-api/releases/download/"
            f"{version}/experimental-install.yaml",
            timeout=120,
        )
        if r.exit_code != 0:
            raise RuntimeError(f"Gateway API install failed (exit {r.exit_code}): {r.stderr[:300]}")

        # 3. Wait for GatewayClass CRD to be registered
        on_output("[install-gateway-api] Waiting for GatewayClass CRD...")
        for attempt in range(15):
            r = session.execute(
                "sudo kubectl get crd gatewayclasses.gateway.networking.k8s.io 2>/dev/null",
                timeout=10,
            )
            if r.exit_code == 0:
                on_output("[install-gateway-api] GatewayClass CRD registered")
                break
            time.sleep(2)
        else:
            raise RuntimeError("GatewayClass CRD not registered after 30s")

        total = time.monotonic() - t0
        on_output(f"[install-gateway-api] Complete ({total:.1f}s total)")
        return {"gateway_api_installed": True, "execution_duration_seconds": round(total, 1)}

    # ------------------------------------------------------------------
    # Shell-path stubs
    # ------------------------------------------------------------------

    def apply_commands(self, variables: dict[str, Any]) -> list[str]:
        return []

    def validate_commands(self, variables: dict[str, Any]) -> list[str]:
        return [
            "sudo kubectl get crd gateways.gateway.networking.k8s.io",
            "sudo kubectl get crd gatewayclasses.gateway.networking.k8s.io",
        ]

    def parse_apply_output(self, output: str, variables: dict[str, Any]) -> dict[str, Any]:
        return {}
