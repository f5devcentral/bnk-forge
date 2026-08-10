"""
SSH port of catalog module 22 — bnk/flo (bare-metal/bnk-flo).

Installs the F5 Lifecycle Operator via Helm from the F5 OCI registry. Requires a
``helm registry login repo.f5.com`` using FAR credentials first. Maps poc-deployer
50-generate-flo-values.sh + 51-install-flo.sh.

Parity source: catalog_snapshot/bnk/flo/{bnkforge.pack.json,values.yaml}. The large
static F5 license/TEEM block lives in _flo_license_static.py (generated from the
same snapshot) so the rendered values match the catalog byte-for-byte.
"""

from __future__ import annotations

from typing import Any

from modules.bare_metal._flo_license_static import FLO_LICENSE_STATIC
from modules.bare_metal.bnk_ssh_base import BnkSSHModule
from modules.base import InputSpec, OutputSpec


class BnkFloSSHModule(BnkSSHModule):
    name = "F5 Lifecycle Operator (FLO) [SSH]"
    path = "bare-metal/bnk-flo"
    description = "Install FLO via Helm from repo.f5.com over SSH (FAR registry login)"
    version = "1.0.0"
    estimated_duration = 180
    timeout = 600

    dependencies = ["bare-metal/bnk-prerequisites", "bare-metal/bnk-cert-issuer"]

    # Helm config — matches catalog bnk/flo entrypoints exactly.
    chart_ref = "oci://repo.f5.com/charts/f5-lifecycle-operator"
    release_name = "flo"
    chart_version_var = "flo_version"
    oci_registry = "repo.f5.com"
    create_namespace = True
    namespace_var = "flo_namespace"
    default_namespace = "f5-operator"

    inputs = {
        "bare_metal_host_id": InputSpec(name="bare_metal_host_id", source="host", required=True),
        "flo_namespace": InputSpec(name="flo_namespace", source="module", default="f5-operator"),
        "flo_version": InputSpec(
            name="flo_version", source="module", required=True,
            from_module="bare-metal/bnk-prerequisites", from_output="flo_version",
        ),
        "far_secret_name": InputSpec(name="far_secret_name", source="module", default="far-secret"),
        "cluster_issuer_name": InputSpec(name="cluster_issuer_name", source="module", default="bnk-ca-cluster-issuer"),
        "jwt_token": InputSpec(name="jwt_token", source="user", required=True, sensitive=True),
        "license_mode": InputSpec(name="license_mode", source="profile", default="connected"),
        "container_platform": InputSpec(name="container_platform", source="profile", default="Generic"),
        "cne_pull_secret": InputSpec(
            name="cne_pull_secret", source="project_secret", required=True, sensitive=True,
            description="FAR creds for helm registry login to repo.f5.com",
        ),
    }

    outputs = {
        "flo_namespace": OutputSpec(resource_kind="", resource_name="", field_path=""),
        "flo_ready": OutputSpec(resource_kind="", resource_name="", static_value=True),
        "crds_installed": OutputSpec(resource_kind="", resource_name="", static_value=True),
    }

    def render_helm_values(self, v: dict[str, Any]) -> dict[str, Any]:
        far = str(v.get("far_secret_name", "far-secret"))
        return {
            "global": {
                "certmgr": {"clusterIssuer": str(v.get("cluster_issuer_name", "bnk-ca-cluster-issuer"))},
                "imagePullSecrets": [{"name": far}],
            },
            "ServiceIPFamily": "ipv4",
            "image": {"repository": "repo.f5.com/images", "pullPolicy": "IfNotPresent"},
            "imagePullSecrets": [{"name": far}],
            "serviceAccount": {"create": True, "name": "flo-controller"},
            "license": {
                "operationMode": str(v.get("license_mode", "connected")),
                "jwt": str(v.get("jwt_token", "")),
                **FLO_LICENSE_STATIC,
            },
            "containerPlatform": str(v.get("container_platform", "Generic")),
            "crds": {"enabled": True, "keep": True},
        }

    def get_readiness_waits(self, v: dict[str, Any]) -> list[dict[str, Any]]:
        # `helm --wait` already blocks; additionally confirm the operator deployment.
        return [
            {
                "kind": "deployment",
                "name": "--all",
                "namespace": self.resolve_namespace(v),
                "condition": "condition=Available",
                "timeout": 300,
            },
        ]

    def collect_outputs(self, session: Any, v: dict[str, Any]) -> dict[str, Any]:
        return {
            "flo_namespace": self.resolve_namespace(v),
            "flo_ready": True,
            "crds_installed": True,
        }
