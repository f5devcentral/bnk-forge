"""
SSH port of catalog module 21 — k8s/bnk-cert-issuer (bare-metal/bnk-cert-issuer).

Creates the cert-manager issuer chain: a bootstrap self-signed ClusterIssuer, a
CA Certificate, the CA-backed ClusterIssuer, and the two OTEL server certificates
(in the instance namespace). Maps poc-deployer 01-clusterissuer.yaml + 41/42-*.yaml.

Parity source: catalog_snapshot/k8s/bnk-cert-issuer/manifests/*.yaml.
"""

from __future__ import annotations

from typing import Any

from modules.bare_metal.bnk_ssh_base import BnkSSHModule
from modules.base import InputSpec, OutputSpec

_OTEL_SUBJECT = {
    "countries": ["US"],
    "provinces": ["Washington"],
    "localities": ["Seattle"],
    "organizations": ["F5 Networks"],
    "organizationalUnits": ["PD"],
}


class BnkCertIssuerSSHModule(BnkSSHModule):
    name = "BNK Cert Issuer [SSH]"
    path = "bare-metal/bnk-cert-issuer"
    description = "Create cert-manager issuer chain + OTEL certs over SSH"
    version = "1.0.0"
    estimated_duration = 30
    timeout = 180

    dependencies = ["bare-metal/cert-manager", "bare-metal/bnk-prerequisites"]

    namespace_var = "namespace"
    default_namespace = "cert-manager"

    inputs = {
        "bare_metal_host_id": InputSpec(name="bare_metal_host_id", source="host", required=True),
        "namespace": InputSpec(name="namespace", source="profile", default="cert-manager"),
        "instance_namespace": InputSpec(name="instance_namespace", source="profile", default="f5-operator"),
        "self_signed_cluster_issuer_name": InputSpec(
            name="self_signed_cluster_issuer_name", source="profile", default="bnk-selfsigned-cluster-issuer"
        ),
        "ca_certificate_name": InputSpec(name="ca_certificate_name", source="profile", default="bnk-ca"),
        "ca_secret_name": InputSpec(name="ca_secret_name", source="profile", default="bnk-ca-secret"),
        "cluster_issuer_name": InputSpec(name="cluster_issuer_name", source="profile", default="bnk-ca-cluster-issuer"),
        "otel_server_certificate_name": InputSpec(
            name="otel_server_certificate_name", source="profile", default="external-otelsvr"
        ),
        "otel_server_secret_name": InputSpec(
            name="otel_server_secret_name", source="profile", default="external-otelsvr-secret"
        ),
        "otel_f5ing_server_certificate_name": InputSpec(
            name="otel_f5ing_server_certificate_name", source="profile", default="external-f5ingotelsvr"
        ),
        "otel_f5ing_server_secret_name": InputSpec(
            name="otel_f5ing_server_secret_name", source="profile", default="external-f5ingotelsvr-secret"
        ),
        # rfc822Name SAN on the OTEL client certs. Cosmetic identity only — the
        # certs are issued by the self-signed CA this module creates, so the
        # address is never resolved or contacted. Overridable per profile.
        "otel_cert_email": InputSpec(name="otel_cert_email", source="profile", default="clientcert@f5net.com"),
        "ca_duration": InputSpec(name="ca_duration", source="profile", default="87600h"),
        "ca_renew_before": InputSpec(name="ca_renew_before", source="profile", default="720h"),
        "otel_duration": InputSpec(name="otel_duration", source="profile", default="8640h"),
        "otel_renew_before": InputSpec(name="otel_renew_before", source="profile", default="720h"),
    }

    outputs = {
        "cluster_issuer_name": OutputSpec(resource_kind="", resource_name="", field_path=""),
        "ca_secret_name": OutputSpec(resource_kind="", resource_name="", field_path=""),
        "cert_issuer_ready": OutputSpec(resource_kind="", resource_name="", static_value=True),
    }

    def _otel_cert(self, cert_name: str, secret_name: str, instance_ns: str, v: dict[str, Any]) -> dict[str, Any]:
        fqdn = f"{cert_name}.{instance_ns}.svc"
        return {
            "apiVersion": "cert-manager.io/v1",
            "kind": "Certificate",
            "metadata": {
                "name": cert_name,
                "namespace": instance_ns,
                "labels": {
                    "app.kubernetes.io/managed-by": "bnk-forge",
                    "app.kubernetes.io/part-of": "bnk",
                },
            },
            "spec": {
                "secretName": secret_name,
                "commonName": fqdn,
                "dnsNames": [
                    cert_name,
                    f"{cert_name}.{instance_ns}",
                    f"{cert_name}.{instance_ns}.svc",
                    f"{cert_name}.{instance_ns}.svc.cluster.local",
                ],
                "subject": dict(_OTEL_SUBJECT),
                "emailAddresses": [str(v.get("otel_cert_email", "clientcert@f5net.com"))],
                "duration": str(v.get("otel_duration", "8640h")),
                "renewBefore": str(v.get("otel_renew_before", "720h")),
                "issuerRef": {
                    "name": str(v.get("cluster_issuer_name", "bnk-ca-cluster-issuer")),
                    "kind": "ClusterIssuer",
                    "group": "cert-manager.io",
                },
                "privateKey": {
                    "rotationPolicy": "Always",
                    "encoding": "PKCS1",
                    "algorithm": "RSA",
                    "size": 4096,
                },
                "revisionHistoryLimit": 10,
            },
        }

    def render_manifests(self, v: dict[str, Any]) -> list[dict[str, Any]]:
        ns = self.resolve_namespace(v)
        instance_ns = str(v.get("instance_namespace", "f5-operator"))
        ss_issuer = str(v.get("self_signed_cluster_issuer_name", "bnk-selfsigned-cluster-issuer"))
        ca_cert = str(v.get("ca_certificate_name", "bnk-ca"))
        ca_secret = str(v.get("ca_secret_name", "bnk-ca-secret"))
        ca_issuer = str(v.get("cluster_issuer_name", "bnk-ca-cluster-issuer"))
        labels = {"app.kubernetes.io/managed-by": "bnk-forge", "app.kubernetes.io/part-of": "bnk"}
        return [
            {
                "apiVersion": "cert-manager.io/v1",
                "kind": "ClusterIssuer",
                "metadata": {"name": ss_issuer, "labels": dict(labels)},
                "spec": {"selfSigned": {}},
            },
            {
                "apiVersion": "cert-manager.io/v1",
                "kind": "Certificate",
                "metadata": {"name": ca_cert, "namespace": ns, "labels": dict(labels)},
                "spec": {
                    "isCA": True,
                    "commonName": "bnk-ca",
                    "secretName": ca_secret,
                    "duration": str(v.get("ca_duration", "87600h")),
                    "renewBefore": str(v.get("ca_renew_before", "720h")),
                    "privateKey": {"algorithm": "RSA", "size": 2048},
                    "issuerRef": {"name": ss_issuer, "kind": "ClusterIssuer", "group": "cert-manager.io"},
                },
            },
            {
                "apiVersion": "cert-manager.io/v1",
                "kind": "ClusterIssuer",
                "metadata": {"name": ca_issuer, "labels": dict(labels)},
                "spec": {"ca": {"secretName": ca_secret}},
            },
            self._otel_cert(
                str(v.get("otel_server_certificate_name", "external-otelsvr")),
                str(v.get("otel_server_secret_name", "external-otelsvr-secret")),
                instance_ns,
                v,
            ),
            self._otel_cert(
                str(v.get("otel_f5ing_server_certificate_name", "external-f5ingotelsvr")),
                str(v.get("otel_f5ing_server_secret_name", "external-f5ingotelsvr-secret")),
                instance_ns,
                v,
            ),
        ]

    def get_readiness_waits(self, v: dict[str, Any]) -> list[dict[str, Any]]:
        # CA-backed ClusterIssuer must become Ready before FLO consumes it.
        return [
            {
                "kind": "clusterissuer",
                "name": str(v.get("cluster_issuer_name", "bnk-ca-cluster-issuer")),
                "condition": "condition=Ready",
                "timeout": 120,
            },
        ]

    def collect_outputs(self, session: Any, v: dict[str, Any]) -> dict[str, Any]:
        return {
            "cluster_issuer_name": v.get("cluster_issuer_name", "bnk-ca-cluster-issuer"),
            "ca_secret_name": v.get("ca_secret_name", "bnk-ca-secret"),
            "cert_issuer_ready": True,
        }
