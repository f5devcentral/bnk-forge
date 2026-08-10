"""
SSH port of catalog module 23 — bnk/cneinstance (bare-metal/bnk-cneinstance).

Creates the CNEInstance CR that tells FLO to deploy the BNK data plane, then
waits for FLO to accept it. DPU case (via d019 _transform_cneinstance):
dpu.enabled, dataPlane.mode=sriov, SF NADs, deploymentSize=Large, MTU 9000.
Maps poc-deployer 60-install-bnk-using-flo.sh (envsubst CR | kubectl apply).

This is a Python port of bnk/cneinstance/manifests/cneinstance.yaml (jinja2). The
parity test asserts byte-equivalence with the catalog render for both modes.

Parity source: catalog_snapshot/bnk/cneinstance/manifests/cneinstance.yaml.
"""

from __future__ import annotations

import json
from typing import Any

from modules.bare_metal.bnk_ssh_base import BnkSSHModule
from modules.base import InputSpec, OutputSpec


def _b(value: Any, default: bool) -> bool:
    """Coerce a templated boolean-ish value the way `| default(x) | lower` + YAML would."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


class BnkCneInstanceSSHModule(BnkSSHModule):
    name = "BNK CNEInstance [SSH]"
    path = "bare-metal/bnk-cneinstance"
    description = "Create CNEInstance CR over SSH; wait for FLO reconcile"
    version = "1.0.0"
    estimated_duration = 120
    timeout = 600

    dependencies = ["bare-metal/bnk-flo"]

    namespace_var = "instance_namespace"
    default_namespace = "f5-operator"

    inputs = {
        "bare_metal_host_id": InputSpec(name="bare_metal_host_id", source="host", required=True),
        "instance_namespace": InputSpec(name="instance_namespace", source="profile", default="f5-operator"),
        "instance_name": InputSpec(name="instance_name", source="profile", default="bnk-instance"),
        "manifest_version": InputSpec(
            name="manifest_version", source="module", required=True,
            from_module="bare-metal/bnk-prerequisites", from_output="manifest_version",
        ),
        "far_secret_name": InputSpec(name="far_secret_name", source="profile", default="far-secret"),
        "cluster_issuer_name": InputSpec(name="cluster_issuer_name", source="profile", default="bnk-ca-cluster-issuer"),
        "deployment_size": InputSpec(name="deployment_size", source="profile", default="Small"),
        "whole_cluster": InputSpec(name="whole_cluster", type="bool", source="profile", default=True),
        "dpu_enabled": InputSpec(name="dpu_enabled", type="bool", source="profile", default=False),
        "dynamic_routing_enabled": InputSpec(name="dynamic_routing_enabled", type="bool", source="profile", default=True),
        "firewall_acl_enabled": InputSpec(name="firewall_acl_enabled", type="bool", source="profile", default=True),
        "pseudo_cni_enabled": InputSpec(name="pseudo_cni_enabled", type="bool", source="profile", default=True),
        "intelligent_lb_enabled": InputSpec(name="intelligent_lb_enabled", type="bool", source="profile", default=True),
        "storage_class_name": InputSpec(name="storage_class_name", source="profile", default="gp3"),
        "tmm_default_mtu": InputSpec(name="tmm_default_mtu", type="number", source="profile", default=9000),
        "external_nad_name": InputSpec(name="external_nad_name", source="module", default="external-netdevice"),
        "internal_nad_name": InputSpec(name="internal_nad_name", source="module", default="internal-netdevice"),
        "tmm_data_plane_mode": InputSpec(name="tmm_data_plane_mode", source="module", default="kernel"),
        "external_pci_bus_id": InputSpec(name="external_pci_bus_id", source="module", default="0000:00:07.0"),
        "internal_pci_bus_id": InputSpec(name="internal_pci_bus_id", source="module", default="0000:00:08.0"),
        "cloud_provider": InputSpec(name="cloud_provider", source="auto", default=""),
        "bnk_cr_kind": InputSpec(name="bnk_cr_kind", source="profile", required=True),
    }

    outputs = {
        "instance_name": OutputSpec(resource_kind="", resource_name="", field_path=""),
        "instance_namespace": OutputSpec(resource_kind="", resource_name="", field_path=""),
        "instance_ready": OutputSpec(resource_kind="", resource_name="", static_value=True),
    }

    def render_manifests(self, v: dict[str, Any]) -> list[dict[str, Any]]:
        name = str(v.get("instance_name", "bnk-instance"))
        ns = str(v.get("instance_namespace", "f5-operator"))
        manifest_version = str(v.get("manifest_version", ""))
        is_kernel = str(v.get("tmm_data_plane_mode", "kernel")) == "kernel"
        mtu = v.get("tmm_default_mtu", 9000)
        ext_nad = str(v.get("external_nad_name", "external-netdevice"))
        int_nad = str(v.get("internal_nad_name", "internal-netdevice"))
        cloud_provider = str(v.get("cloud_provider", "") or "")

        spec: dict[str, Any] = {
            "product": {"gatewayAPI": True, "type": "BNK"},
            "manifestVersion": manifest_version,
            "wholeCluster": _b(v.get("whole_cluster"), True),
            "dpu": {"enabled": _b(v.get("dpu_enabled"), False)},
        }
        # NB: no top-level spec.dataPlane — the released FLO 2.2 CNEInstance CRD has
        # no such field (verified against the live CRD + clouddocs.f5.com v2.2
        # CNEInstance parameters). DPU/sriov mode is conveyed by spec.dpu.enabled +
        # the SF networkAttachments below; the catalog snapshot's spec.dataPlane was
        # a WIP-branch artifact that the real API rejects (strict decoding error).
        spec["telemetry"] = {
            "loggingSubsystem": {"enabled": True},
            "metricSubsystem": {"enabled": True},
        }
        spec["certificate"] = {"clusterIssuer": str(v.get("cluster_issuer_name", "bnk-ca-cluster-issuer"))}
        spec["deploymentSize"] = str(v.get("deployment_size", "Small"))
        storage_class = v.get("storage_class_name")
        if storage_class:
            spec["storageClassName"] = str(storage_class)
        spec["registry"] = {
            "uri": "repo.f5.com",
            "imagePullSecrets": [{"name": str(v.get("far_secret_name", "far-secret"))}],
            "imagePullPolicy": "IfNotPresent",
        }
        spec["networkAttachments"] = [ext_nad, int_nad]
        spec["dynamicRouting"] = {"enabled": _b(v.get("dynamic_routing_enabled"), True)}
        spec["firewallACL"] = {"enabled": _b(v.get("firewall_acl_enabled"), True)}
        spec["pseudoCNI"] = {"enabled": _b(v.get("pseudo_cni_enabled"), True)}
        spec["intelligentLB"] = {"enabled": _b(v.get("intelligent_lb_enabled"), True)}
        spec["coreCollection"] = {"enabled": False}

        cne_env: list[dict[str, str]] = [{"name": "TMM_DEFAULT_MTU", "value": f"{mtu}"}]
        if cloud_provider:
            cne_env += [
                {"name": "CLOUD_ENV", "value": "true"},
                {"name": "CLOUD_PROVIDER", "value": cloud_provider},
                {"name": "CLOUD_NETWORK_CONFIGMAP", "value": "cloud-network-mapping"},
            ]

        tmm: dict[str, Any] = {}
        tmm_memory = str(v.get("tmm_memory") or ("6Gi" if is_kernel else "4Gi"))
        if is_kernel:
            # Build the CNI-networks annotation via json.dumps (compact separators
            # to match the catalog render byte-for-byte) so a NAD name or namespace
            # containing a quote can't produce a malformed annotation / invalid CR.
            tmm["annotations"] = {
                "k8s.v1.cni.cncf.io/networks": json.dumps(
                    [
                        {"name": ext_nad, "namespace": ns, "interface": "eth1"},
                        {"name": int_nad, "namespace": ns, "interface": "eth2"},
                    ],
                    separators=(",", ":"),
                ),
            }
        tmm["resources"] = {
            "requests": {"memory": tmm_memory},
            "limits": {"memory": tmm_memory},
        }
        tmm_env: list[dict[str, str]] = [
            {"name": "TMM_DEFAULT_MTU", "value": f"{mtu}"},
            {"name": "TMM_IGNORE_GATEWAYS", "value": "TRUE"},
        ]
        if is_kernel:
            tmm_env += [
                {"name": "TMM_GENERIC_SOCKET_DRIVER", "value": "true"},
                {"name": "TMM_CALICO_ROUTER", "value": "default"},
                {"name": "PAL_CPU_SET", "value": "0,2"},
                {"name": "TMM_MAPRES_ADDL_VETHS_ON_DP", "value": "TRUE"},
                {"name": "ROBIN_VFIO_RESOURCE_1", "value": "eth1"},
                {"name": "PCIDEVICE_INTEL_COM_ETH1", "value": str(v.get("external_pci_bus_id", "0000:00:07.0"))},
                {"name": "ROBIN_VFIO_RESOURCE_2", "value": "eth2"},
                {"name": "PCIDEVICE_INTEL_COM_ETH2", "value": str(v.get("internal_pci_bus_id", "0000:00:08.0"))},
            ]
        tmm["env"] = tmm_env

        spec["advanced"] = {
            "envDiscovery": {"enabled": False, "stopOnFail": False},
            "cneController": {"env": cne_env},
            "tmm": tmm,
        }

        cr_kind = str(v.get("bnk_cr_kind") or "CNEInstance")
        return [
            {
                "apiVersion": "k8s.f5.com/v1",
                "kind": cr_kind,
                "metadata": {
                    "name": name,
                    "namespace": ns,
                    "labels": {
                        "app.kubernetes.io/name": name,
                        "app.kubernetes.io/component": "cne-instance",
                        "app.kubernetes.io/managed-by": "bnk-forge",
                        "app.kubernetes.io/version": manifest_version,
                    },
                },
                "spec": spec,
            },
        ]

    def get_readiness_waits(self, v: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "kind": "cneinstance",
                "name": str(v.get("instance_name", "bnk-instance")),
                "namespace": str(v.get("instance_namespace", "f5-operator")),
                "condition": "condition=Accepted",
                "timeout": 300,
            },
        ]

    def collect_outputs(self, session: Any, v: dict[str, Any]) -> dict[str, Any]:
        return {
            "instance_name": v.get("instance_name", "bnk-instance"),
            "instance_namespace": v.get("instance_namespace", "f5-operator"),
            "instance_ready": True,
        }
