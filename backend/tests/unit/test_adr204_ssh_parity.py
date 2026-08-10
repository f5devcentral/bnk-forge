"""ADR-204 PARITY GATE — bare-metal/bnk-* SSH ports vs catalog render (DPU case).

Each SSH port must apply resources equivalent to the catalog path
(``build_manifest_payload`` / ``build_helm_payload`` + d019 DPU transforms). The
catalog side renders from the pinned snapshot (tests/fixtures/catalog_snapshot,
see CATALOG_SHA). The DPU variable set is produced by running the SAME d019
transform the catalog path uses — exercised here via its bare-metal/* alias, so
these tests also prove the transform-reuse wiring in blueprint_context.py.
"""

from __future__ import annotations

import json

import pytest

from services.execution.blueprint_context import ProjectContext, transform_for_module
from tests.fixtures.catalog_parity import (
    normalize_manifests,
    render_catalog_helm,
    render_catalog_manifests,
)

pytestmark = pytest.mark.unit


def _ssh_helm_payload(module, variables: dict) -> dict:
    """Build the comparable helm-payload subset from an SSH helm module."""
    return {
        "chart_ref": module.chart_ref,
        "chart_version": module.resolve_chart_version(variables),
        "namespace": module.resolve_namespace(variables),
        "release_name": module.resolve_release_name(variables),
        "create_namespace": module.create_namespace,
        "values": module.render_helm_values(variables),
    }


def _assert_helm_parity(module, catalog_rel: str, variables: dict) -> None:
    catalog = render_catalog_helm(catalog_rel, variables)
    ours = _ssh_helm_payload(module, variables)
    for key, value in ours.items():
        assert value == catalog[key], f"helm {key} mismatch: {value!r} != {catalog[key]!r}"


# DPU-mode project context (mirrors resolve_project_context for dpu-server-2).
DPU_CTX = ProjectContext(
    external_vlan_ipv4_subnet="10.10.20.0/24",
    internal_vlan_ipv4_subnet="10.10.30.0/24",
    dpu_external_vlan_ipv4="10.10.20.100",
    dpu_internal_vlan_ipv4="10.10.30.100",
    high_speed_mtu=9000,
    bnk_manifest_version="2.2.1-3.2226.0-0.0.511",
    flo_version="2.2.1",
    cert_manager_version="v1.16.1",
    is_bare_metal=True,
    is_dpu_enabled=True,
)


def _dpu_vars(ssh_path: str, base: dict) -> dict:
    """Apply the d019 transform (via the SSH-path alias) on top of base vars."""
    variables = dict(base)
    variables.update(transform_for_module(ssh_path, variables, DPU_CTX))
    return variables


# ── transform-reuse wiring (S1) ──────────────────────────────────────────────

def test_ssh_path_aliases_fire_d019_transforms():
    """The bare-metal/bnk-* aliases must resolve to the same transforms as catalog paths."""
    net = transform_for_module("bare-metal/network-setup", {}, DPU_CTX)
    assert net["namespace"] == "f5-bnk"
    assert net["tmm_data_plane_mode"] == "sriov"
    assert net["cni_type"] == "sf"
    assert net["external_nad_name"] == "sf-external"
    assert net["internal_nad_name"] == "sf-internal"

    gwc = transform_for_module("bare-metal/bnk-gatewayclass", {}, DPU_CTX)
    assert gwc["controller_name"] == "f5.com/f5-bnk-f5-cne-controller"

    prereq = transform_for_module("bare-metal/bnk-prerequisites", {}, DPU_CTX)
    assert prereq["instance_namespace"] == "f5-bnk"


# ── network-setup (module 19) ────────────────────────────────────────────────

def test_network_setup_parity_dpu():
    from modules.bare_metal.bnk_network_setup import NetworkSetupSSHModule

    variables = _dpu_vars("bare-metal/network-setup", {})
    ours = NetworkSetupSSHModule().render_manifests(variables)
    catalog = render_catalog_manifests("k8s/network-setup", variables)

    assert normalize_manifests(ours) == normalize_manifests(catalog)
    # DPU specifics are present (guards against an empty-vs-empty false pass).
    names = {m["metadata"]["name"] for m in ours}
    assert names == {"sf-external", "sf-internal"}
    assert all(m["metadata"]["namespace"] == "f5-bnk" for m in ours)


# ── bnk-cert-issuer (module 21) ──────────────────────────────────────────────

def test_cert_issuer_parity_dpu():
    from modules.bare_metal.bnk_cert_issuer import BnkCertIssuerSSHModule

    # No transform for cert-issuer; OTEL certs land in the instance namespace.
    variables = {"instance_namespace": "f5-bnk"}
    ours = BnkCertIssuerSSHModule().render_manifests(variables)
    catalog = render_catalog_manifests("k8s/bnk-cert-issuer", variables)
    assert normalize_manifests(ours) == normalize_manifests(catalog)
    kinds = sorted(m["kind"] for m in ours)
    assert kinds == ["Certificate", "Certificate", "Certificate", "ClusterIssuer", "ClusterIssuer"]


# ── cert-manager (module 20) — helm, Jetstack chart ──────────────────────────

def test_cert_manager_parity_dpu():
    from modules.bare_metal.bnk_cert_manager import CertManagerSSHModule

    variables = _dpu_vars("bare-metal/cert-manager", {"namespace": "cert-manager", "release_name": "cert-manager"})
    _assert_helm_parity(CertManagerSSHModule(), "k8s/cert-manager", variables)
    # Concrete-catalog fact: chart is Jetstack, not F5.
    assert CertManagerSSHModule().chart_ref == "oci://quay.io/jetstack/charts/cert-manager"


# ── FLO (module 22) — helm from repo.f5.com ──────────────────────────────────

def test_flo_parity_dpu():
    from modules.bare_metal.bnk_flo import BnkFloSSHModule

    base = {
        "flo_namespace": "f5-operator",
        "far_secret_name": "far-secret",
        "cluster_issuer_name": "bnk-ca-cluster-issuer",
        "jwt_token": "eyJ-test-jwt",
        "license_mode": "connected",
        "container_platform": "Generic",
    }
    variables = _dpu_vars("bare-metal/bnk-flo", base)
    _assert_helm_parity(BnkFloSSHModule(), "bnk/flo", variables)
    assert variables["flo_version"] == "2.2.1"  # injected by _transform_flo alias


# ── cneinstance (module 23) — jinja2 template, sriov + kernel ─────────────────

def test_cneinstance_parity_dpu_sriov():
    from modules.bare_metal.bnk_cneinstance import BnkCneInstanceSSHModule

    base = {
        "instance_namespace": "f5-bnk",
        "instance_name": "bnk-instance",
        "far_secret_name": "far-secret",
        "cluster_issuer_name": "bnk-ca-cluster-issuer",
        "deployment_size": "Large",
        "storage_class_name": "gp3",
    }
    variables = _dpu_vars("bare-metal/bnk-cneinstance", base)
    ours = BnkCneInstanceSSHModule().render_manifests(variables)
    catalog = render_catalog_manifests("bnk/cneinstance", variables)
    # The pinned catalog snapshot (WIP branch fix/cneinstance-sriov-dataplane) emits a
    # top-level spec.dataPlane that the released FLO 2.2 CNEInstance CRD rejects —
    # confirmed against the live CRD and clouddocs.f5.com v2.2 docs (no such field;
    # DPU/sriov is conveyed by spec.dpu + networkAttachments). The SSH render drops it
    # to match the real API, so strip it from the catalog side before asserting
    # byte-equivalence on everything else.
    for _m in catalog:
        _m.get("spec", {}).pop("dataPlane", None)
    assert normalize_manifests(ours) == normalize_manifests(catalog)
    spec = ours[0]["spec"]
    assert "dataPlane" not in spec  # dropped to match the released CRD (see above)
    assert spec["dpu"] == {"enabled": True}
    assert spec["networkAttachments"] == ["sf-external", "sf-internal"]


def test_cneinstance_parity_kernel():
    from modules.bare_metal.bnk_cneinstance import BnkCneInstanceSSHModule

    variables = {
        "instance_namespace": "f5-operator",
        "instance_name": "bnk-instance",
        "manifest_version": "2.2.1-3.2226.0-0.0.511",
        "far_secret_name": "far-secret",
        "cluster_issuer_name": "bnk-ca-cluster-issuer",
        "deployment_size": "Small",
        "storage_class_name": "gp3",
        "tmm_data_plane_mode": "kernel",
        "external_nad_name": "external-netdevice",
        "internal_nad_name": "internal-netdevice",
        "external_pci_bus_id": "0000:00:07.0",
        "internal_pci_bus_id": "0000:00:08.0",
    }
    ours = BnkCneInstanceSSHModule().render_manifests(variables)
    catalog = render_catalog_manifests("bnk/cneinstance", variables)
    assert normalize_manifests(ours) == normalize_manifests(catalog)
    assert "dataPlane" not in ours[0]["spec"]  # kernel mode omits dataPlane


# ── bnk-gatewayclass (module 25) ─────────────────────────────────────────────

def test_gatewayclass_parity_dpu():
    from modules.bare_metal.bnk_gatewayclass import BnkGatewayClassSSHModule

    variables = _dpu_vars("bare-metal/bnk-gatewayclass", {})
    ours = BnkGatewayClassSSHModule().render_manifests(variables)
    catalog = render_catalog_manifests("bnk/bnk-gatewayclass", variables)
    assert normalize_manifests(ours) == normalize_manifests(catalog)
    assert ours[0]["spec"]["controllerName"] == "f5.com/f5-bnk-f5-cne-controller"


# ── bnk-prerequisites (module 18) — structural (tofu module, no pack) ─────────

def test_prerequisites_structural_dpu():
    import base64

    from modules.bare_metal.bnk_prerequisites import BnkPrerequisitesSSHModule

    secret = base64.b64encode(b'{"sa":"key"}').decode()
    variables = _dpu_vars("bare-metal/bnk-prerequisites", {"cne_pull_secret": secret})
    assert variables["instance_namespace"] == "f5-bnk"  # from transform alias

    manifests = BnkPrerequisitesSSHModule().render_manifests(variables)
    namespaces = {m["metadata"]["name"] for m in manifests if m["kind"] == "Namespace"}
    assert namespaces == {"f5-operator", "f5-utils", "bnk-gw", "f5-bnk"}

    secrets = [m for m in manifests if m["kind"] == "Secret"]
    assert len(secrets) == 4
    for s in secrets:
        assert s["type"] == "kubernetes.io/dockerconfigjson"
        dockerjson = s["stringData"][".dockerconfigjson"]
        auth = json.loads(dockerjson)["auths"]["repo.f5.com"]["auth"]
        decoded = base64.b64decode(auth).decode()
        assert decoded == f"_json_key_base64:{secret}"


# ── bnk-prerequisites version parse (added after live e2e) ───────────────────

def test_parse_component_versions_extracts_flo():
    from modules.bare_metal.bnk_prerequisites import parse_component_versions

    out = (
        "charts/f5-cert-manager=1.16.1\n"
        "charts/f5-lifecycle-operator=2.2.1-0.0.123\n"
        "images/tmm-img=20.4.0\n"
        "garbage line without equals\n"
    )
    parsed = parse_component_versions(out)
    assert parsed["charts/f5-lifecycle-operator"] == "2.2.1-0.0.123"
    assert parsed["charts/f5-cert-manager"] == "1.16.1"
    assert "garbage line without equals" not in parsed


# ── bnk-vlans (module 24) — structural (authored fresh, no pack) ──────────────

def test_vlans_structural_dpu():
    from modules.bare_metal.bnk_vlans import BnkVlansSSHModule

    variables = _dpu_vars("bare-metal/bnk-vlans", {})
    # _transform_bnk_vlans derives self-IPs / subnets / namespace from DPU ctx.
    assert variables["namespace"] == "f5-bnk"
    assert variables["external_self_ips"] == ["10.10.20.100"]

    manifests = BnkVlansSSHModule().render_manifests(variables)
    assert [m["kind"] for m in manifests] == ["F5SPKVlan", "F5SPKVlan"]
    ext, intl = manifests
    assert ext["spec"] == {
        "name": "external", "interfaces": ["1.1"], "mtu": 9000,
        "selfip_v4s": ["10.10.20.100"], "prefixlen_v4": 24,
    }
    assert intl["spec"]["internal"] is True
    assert intl["spec"]["interfaces"] == ["1.2"]
    assert intl["spec"]["selfip_v4s"] == ["10.10.30.100"]
    assert all(m["metadata"]["namespace"] == "f5-bnk" for m in manifests)

    # Cold-start gate: the F5SPKVlan apply must wait not only for the CRD to be
    # Established but for the validating-webhook backend (f5-cne-controller) to be
    # Available in the CR namespace, else it races "failed calling webhook ...
    # connection refused". See bnk_ssh_base._wait_for_required_deployments.
    module = BnkVlansSSHModule()
    assert module.get_required_crds(variables) == ["f5-spk-vlans.k8s.f5net.com"]
    assert module.get_required_deployments(variables) == [
        {"name": "f5-cne-controller", "namespace": "f5-bnk"}
    ]
