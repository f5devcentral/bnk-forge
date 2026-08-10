"""
SSH port of catalog module 19 — k8s/network-setup (bare-metal/network-setup).

Creates the Multus NetworkAttachmentDefinitions for TMM external/internal
data-plane networks. DPU case (via the d019 _transform_network_setup): SF NADs
``sf-external`` / ``sf-internal`` in ``f5-bnk`` with ``cni_type=sf`` and the
BlueField SR-IOV resource names.

Parity source: catalog_snapshot/k8s/network-setup/manifests/{01,02}-*.yaml.
"""

from __future__ import annotations

import json
from typing import Any

from modules.bare_metal.bnk_ssh_base import BnkSSHModule
from modules.base import InputSpec, OutputSpec


class NetworkSetupSSHModule(BnkSSHModule):
    name = "Network Setup (NADs) [SSH]"
    path = "bare-metal/network-setup"
    description = "Create Multus NADs for TMM data-plane interfaces over SSH (DPU SF mode)"
    version = "1.0.0"
    estimated_duration = 20
    timeout = 120

    dependencies = ["bare-metal/bnk-prerequisites"]

    namespace_var = "namespace"
    default_namespace = "f5-operator"

    inputs = {
        "bare_metal_host_id": InputSpec(name="bare_metal_host_id", source="host", required=True),
        "namespace": InputSpec(name="namespace", source="profile", default="f5-operator"),
        "cni_type": InputSpec(name="cni_type", source="profile", default="host-device"),
        "external_nad_name": InputSpec(name="external_nad_name", source="profile", default="external-netdevice"),
        "internal_nad_name": InputSpec(name="internal_nad_name", source="profile", default="internal-netdevice"),
        "external_resource_name": InputSpec(
            name="external_resource_name", source="profile", default="intel.com/external_netdevice"
        ),
        "internal_resource_name": InputSpec(
            name="internal_resource_name", source="profile", default="intel.com/internal_netdevice"
        ),
    }

    outputs = {
        "external_nad_name": OutputSpec(resource_kind="", resource_name="", field_path=""),
        "internal_nad_name": OutputSpec(resource_kind="", resource_name="", field_path=""),
        "nads_ready": OutputSpec(resource_kind="", resource_name="", static_value=True),
    }

    def _nad(self, name: str, resource: str, net_name: str, ns: str, cni_type: str) -> dict[str, Any]:
        return {
            "apiVersion": "k8s.cni.cncf.io/v1",
            "kind": "NetworkAttachmentDefinition",
            "metadata": {
                "name": name,
                "namespace": ns,
                "annotations": {"k8s.v1.cni.cncf.io/resourceName": resource},
                "labels": {"app.kubernetes.io/managed-by": "bnk-forge"},
            },
            "spec": {
                # JSON string, no whitespace — byte-identical to the catalog template:
                # '{"type":"${cni_type}","cniVersion":"0.3.1","name":"external-network"}'
                "config": json.dumps(
                    {"type": cni_type, "cniVersion": "0.3.1", "name": net_name},
                    separators=(",", ":"),
                ),
            },
        }

    def render_manifests(self, variables: dict[str, Any]) -> list[dict[str, Any]]:
        ns = self.resolve_namespace(variables)
        cni_type = str(variables.get("cni_type", "host-device"))
        ext = str(variables.get("external_nad_name", "external-netdevice"))
        intl = str(variables.get("internal_nad_name", "internal-netdevice"))
        ext_res = str(variables.get("external_resource_name", "intel.com/external_netdevice"))
        int_res = str(variables.get("internal_resource_name", "intel.com/internal_netdevice"))
        return [
            self._nad(ext, ext_res, "external-network", ns, cni_type),
            self._nad(intl, int_res, "internal-network", ns, cni_type),
        ]

    def collect_outputs(self, session: Any, variables: dict[str, Any]) -> dict[str, Any]:
        return {
            "external_nad_name": variables.get("external_nad_name", "external-netdevice"),
            "internal_nad_name": variables.get("internal_nad_name", "internal-netdevice"),
            "nads_ready": True,
        }
