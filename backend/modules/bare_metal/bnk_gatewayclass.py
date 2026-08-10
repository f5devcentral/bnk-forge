"""
SSH port of catalog module 25 — bnk/bnk-gatewayclass (bare-metal/bnk-gatewayclass).

Creates the BNK GatewayClass. DPU case (via d019 _transform_bnk_gatewayclass):
controllerName = ``f5.com/f5-bnk-f5-cne-controller`` (the f5-operator default
stays Pending forever on DPU). Maps poc-deployer 61-install-gatewayclass.sh.

Parity source: catalog_snapshot/bnk/bnk-gatewayclass/manifests/01-gatewayclass.yaml.
"""

from __future__ import annotations

from typing import Any

from modules.bare_metal.bnk_ssh_base import BnkSSHModule
from modules.base import InputSpec, OutputSpec


class BnkGatewayClassSSHModule(BnkSSHModule):
    name = "BNK GatewayClass [SSH]"
    path = "bare-metal/bnk-gatewayclass"
    description = "Create BNK GatewayClass over SSH (DPU controllerName)"
    version = "1.0.0"
    estimated_duration = 20
    timeout = 360

    dependencies = ["bare-metal/install-gateway-api", "bare-metal/bnk-cneinstance"]

    inputs = {
        "bare_metal_host_id": InputSpec(name="bare_metal_host_id", source="host", required=True),
        "gatewayclass_name": InputSpec(name="gatewayclass_name", source="profile", default="bnk-gatewayclass"),
        "controller_name": InputSpec(
            name="controller_name", source="profile", default="f5.com/f5-operator-f5-cne-controller"
        ),
        "description": InputSpec(
            name="description", source="profile", default="F5 BIG-IP Kubernetes Gateway"
        ),
    }

    outputs = {
        "gatewayclass_name": OutputSpec(resource_kind="", resource_name="", field_path=""),
        "gatewayclass_ready": OutputSpec(resource_kind="", resource_name="", static_value=True),
    }

    def render_manifests(self, v: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "apiVersion": "gateway.networking.k8s.io/v1",
                "kind": "GatewayClass",
                "metadata": {
                    "name": str(v.get("gatewayclass_name", "bnk-gatewayclass")),
                    "labels": {
                        "app.kubernetes.io/name": "bnk-gatewayclass",
                        "app.kubernetes.io/component": "gateway-api",
                        "app.kubernetes.io/managed-by": "bnk-forge",
                    },
                },
                "spec": {
                    "controllerName": str(v.get("controller_name", "f5.com/f5-operator-f5-cne-controller")),
                    "description": str(v.get("description", "F5 BIG-IP Kubernetes Gateway")),
                },
            },
        ]

    def get_readiness_waits(self, v: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "kind": "gatewayclass",
                "name": str(v.get("gatewayclass_name", "bnk-gatewayclass")),
                "condition": "condition=Accepted",
                # 300s (not 120): on a cold deploy the cne-controller that accepts the
                # GatewayClass may still be settling right after bnk-vlans programs.
                "timeout": 300,
            },
        ]

    def collect_outputs(self, session: Any, v: dict[str, Any]) -> dict[str, Any]:
        return {
            "gatewayclass_name": v.get("gatewayclass_name", "bnk-gatewayclass"),
            "gatewayclass_ready": True,
        }
