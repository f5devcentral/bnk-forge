"""
SSH port of catalog module 20 — k8s/cert-manager (bare-metal/cert-manager).

Installs cert-manager via Helm. Per the CONCRETE forge catalog (not the ADR's
assumption), the chart is **Jetstack** ``oci://quay.io/jetstack/charts/cert-manager``
v1.16.1 — that is the parity target. Maps poc-deployer install-host-k8s.sh
(``helm install cert-manager``).

Parity source: catalog_snapshot/k8s/cert-manager/{bnkforge.pack.json,values.yaml}.
"""

from __future__ import annotations

from typing import Any

from modules.bare_metal.bnk_ssh_base import BnkSSHModule
from modules.base import InputSpec, OutputSpec


class CertManagerSSHModule(BnkSSHModule):
    name = "cert-manager [SSH]"
    path = "bare-metal/cert-manager"
    description = "Install Jetstack cert-manager via Helm over SSH"
    version = "1.0.0"
    estimated_duration = 120
    timeout = 300

    dependencies = ["bare-metal/bnk-prerequisites"]

    # Helm config — matches catalog k8s/cert-manager entrypoints exactly.
    chart_ref = "oci://quay.io/jetstack/charts/cert-manager"
    release_name = "cert-manager"
    chart_version = "v1.16.1"
    chart_version_var = "cert_manager_version"
    create_namespace = True
    namespace_var = "namespace"
    default_namespace = "cert-manager"
    # Public chart — no OCI registry login (oci_registry stays empty).

    inputs = {
        "bare_metal_host_id": InputSpec(name="bare_metal_host_id", source="host", required=True),
        "namespace": InputSpec(name="namespace", source="profile", default="cert-manager"),
        "release_name": InputSpec(name="release_name", source="profile", default="cert-manager"),
        "cert_manager_version": InputSpec(name="cert_manager_version", source="profile", default="v1.16.1"),
        "controller_replicas": InputSpec(name="controller_replicas", type="number", source="profile", default=1),
        "webhook_replicas": InputSpec(name="webhook_replicas", type="number", source="profile", default=1),
        "cainjector_replicas": InputSpec(name="cainjector_replicas", type="number", source="profile", default=1),
        "resources": InputSpec(name="resources", type="map", source="profile", required=False, default=None),
    }

    outputs = {
        "cert_manager_ready": OutputSpec(resource_kind="", resource_name="", static_value=True),
        "release_name": OutputSpec(resource_kind="", resource_name="", field_path=""),
        "namespace": OutputSpec(resource_kind="", resource_name="", field_path=""),
    }

    def render_helm_values(self, v: dict[str, Any]) -> dict[str, Any]:
        ns = self.resolve_namespace(v)
        return {
            "crds": {"enabled": True, "keep": False},
            "global": {"leaderElection": {"namespace": ns}},
            "startupapicheck": {"enabled": False},
            "webhook": {"replicaCount": v.get("webhook_replicas", 1), "timeoutSeconds": 30},
            "cainjector": {"replicaCount": v.get("cainjector_replicas", 1)},
            "replicaCount": v.get("controller_replicas", 1),
            "resources": v.get("resources", None),
        }

    def collect_outputs(self, session: Any, v: dict[str, Any]) -> dict[str, Any]:
        return {
            "cert_manager_ready": True,
            "release_name": self.resolve_release_name(v),
            "namespace": self.resolve_namespace(v),
        }
