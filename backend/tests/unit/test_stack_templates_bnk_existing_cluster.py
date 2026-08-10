import json
from pathlib import Path


def _load_templates() -> list[dict]:
    templates_path = Path(__file__).resolve().parents[2] / "data" / "stack_templates.json"
    return json.loads(templates_path.read_text())


def _get_template(slug: str) -> dict:
    templates = _load_templates()
    return next(t for t in templates if t.get("slug") == slug)


def _get_module(template: dict, path: str) -> dict:
    return next(m for m in (template.get("modules") or []) if m.get("path") == path)


def test_canonical_existing_cluster_template_slug_is_unique_and_active_contract_uses_bnk_on_k8s():
    templates = _load_templates()
    slugs = [t.get("slug") for t in templates]

    assert "bnk-on-k8s" in slugs
    assert "f5-bnk-2.2" not in slugs


def test_bnk_solution_stack_dependency_points_to_canonical_existing_cluster_template():
    template = _get_template("eks-bnk-smartllm-demo")
    stack_prerequisites = [
        p for p in (template.get("prerequisites") or []) if p.get("type") == "stack_deployed"
    ]

    assert stack_prerequisites
    assert stack_prerequisites[0]["name"] == "bnk-on-k8s"


def test_bnk_existing_cluster_network_setup_defaults_match_module_contract():
    template = _get_template("bnk-on-k8s")
    network = _get_module(template, "k8s/network-setup")
    variables = network["variables"]

    assert variables["cni_type"] == "host-device"
    assert variables["external_resource_name"]
    assert variables["internal_resource_name"]
    # Compatibility-only fields are explicit empty lists in template defaults.
    assert variables["external_subnet_cidrs"] == []
    assert variables["internal_subnet_cidrs"] == []


def test_bnk_existing_cluster_cert_manager_defaults_do_not_imply_issuer_automation():
    template = _get_template("bnk-on-k8s")
    cert_manager = _get_module(template, "k8s/cert-manager")
    variables = cert_manager["variables"]

    assert variables["cert_manager_version"] == "v1.16.1"
    assert variables["release_name"] == "cert-manager"
    assert "create_cluster_issuer" not in variables
    assert "cluster_issuer_name" not in variables
    assert "ca_certificate_name" not in variables


def test_bnk_existing_cluster_has_forge_managed_cert_issuer_module_after_cert_manager():
    template = _get_template("bnk-on-k8s")
    module_paths = [m["path"] for m in template["modules"]]

    assert "k8s/cert-manager" in module_paths
    assert "k8s/bnk-cert-issuer" in module_paths
    assert module_paths.index("k8s/cert-manager") < module_paths.index("k8s/bnk-cert-issuer")


def test_bnk_existing_cluster_cert_issuer_module_defaults_match_cneinstance_contract():
    template = _get_template("bnk-on-k8s")
    cert_issuer = _get_module(template, "k8s/bnk-cert-issuer")
    cneinstance = _get_module(template, "bnk/cneinstance")

    issuer_vars = cert_issuer["variables"]
    cne_vars = cneinstance["variables"]

    assert issuer_vars["namespace"] == "cert-manager"
    assert issuer_vars["self_signed_cluster_issuer_name"] == "bnk-selfsigned-cluster-issuer"
    assert issuer_vars["ca_certificate_name"] == "bnk-ca"
    assert issuer_vars["ca_secret_name"] == "bnk-ca-secret"
    assert issuer_vars["cluster_issuer_name"] == "bnk-ca-cluster-issuer"
    # CNEInstance now sources cluster_issuer_name from the module contract,
    # so template-level override is optional and should not drift if present.
    if "cluster_issuer_name" in cne_vars:
        assert cne_vars["cluster_issuer_name"] == issuer_vars["cluster_issuer_name"]


def test_bnk_existing_cluster_cneinstance_defaults_remove_aws_specific_drift_keys():
    template = _get_template("bnk-on-k8s")
    cneinstance = _get_module(template, "bnk/cneinstance")
    variables = cneinstance["variables"]

    assert "cloud_provider" not in variables
    assert "cloud_az_subnet_mappings" not in variables
    assert "storage_class_name" not in variables


def test_bnk_existing_cluster_doc_aligned_versions_and_namespaces():
    template = _get_template("bnk-on-k8s")
    prereqs = _get_module(template, "k8s/bnk-prerequisites")
    flo = _get_module(template, "bnk/flo")
    cneinstance = _get_module(template, "bnk/cneinstance")
    gatewayclass = _get_module(template, "bnk/bnk-gatewayclass")

    assert prereqs["variables"]["bnk_manifest_version"] == "2.2.1-3.2226.0-0.0.511"
    assert prereqs["variables"]["gateway_namespace"] == "bnk-gw"
    assert cneinstance["variables"]["manifest_version"] == "2.2.1-3.2226.0-0.0.511"
    assert cneinstance["variables"]["deployment_size"] == "Small"
    assert cneinstance["variables"]["env_discovery_stop_on_fail"] is False
    assert gatewayclass["variables"]["flo_namespace"] == "f5-cne-core"
    # flo module no longer sets flo_namespace as a template variable —
    # it's produced as a module output, consumed by gatewayclass as input.
    assert "flo_namespace" not in flo["variables"]


def test_bnk_existing_cluster_vlan_defaults_match_builtin_vlan_module_inputs():
    template = _get_template("bnk-on-k8s")
    vlans = _get_module(template, "bnk/bnk-vlans")
    variables = vlans["variables"]

    assert "external_selfip" in variables
    assert "internal_selfip" in variables
    assert "external_prefixlen" in variables
    assert "internal_prefixlen" in variables
    assert "external_self_ips" not in variables
    assert "internal_self_ips" not in variables
    assert "external_subnet_cidrs" not in variables
    assert "internal_subnet_cidrs" not in variables


def test_stack_templates_json_has_no_unicode_escapes():
    """Guard (issue #441): the file must be dumped with ensure_ascii=False so
    non-ASCII (—, →, ') stay literal UTF-8 and don't churn diffs as \\uXXXX."""
    templates_path = Path(__file__).resolve().parents[2] / "data" / "stack_templates.json"
    raw = templates_path.read_text(encoding="utf-8")
    assert "\\u" not in raw, (
        "stack_templates.json contains \\uXXXX escapes — re-dump with "
        "json.dumps(..., ensure_ascii=False, indent=2)"
    )
