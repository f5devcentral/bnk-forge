from __future__ import annotations

from pathlib import Path

import jinja2
import pytest

from services.execution.engine_interface import ModuleContext
from services.execution.k8s_catalog_payload import build_helm_payload, build_manifest_payload, collect_outputs_from_pack
from services.module_metadata import InvalidMetadataSchemaError, ModuleMetadataValidator


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _manifest_ctx(
    tmp_path: Path,
    *,
    template_engine: str | None = None,
    manifest_file: str = "manifests/manifest.yaml",
    manifest_content: str,
) -> ModuleContext:
    workspace = tmp_path / "workspace"
    module_root = workspace / "module"
    _write_file(module_root / manifest_file, manifest_content)

    deployment_pack: dict[str, object] = {
        "engine": "kubernetes",
        "runner_profile": "kubernetes-default",
        "working_directory": "module",
        "lifecycle": {
            "supports_init": True,
            "supports_plan": True,
            "supports_apply": True,
            "supports_destroy": True,
            "supports_refresh": True,
            "supports_drift": True,
        },
        "entrypoints": {
            "manifest_path": "manifests",
            "namespace": "pack-namespace",
        },
    }
    if template_engine is not None:
        deployment_pack["template_engine"] = template_engine

    return ModuleContext(
        module_id=1,
        project_id=1,
        path="k8s/example",
        category="k8s",
        deploy_model="manifests",
        module_source_kind="git_catalog",
        workspace_path=str(workspace),
        pack_manifest={
            "module": {"name": "Example Module"},
            "deployment_pack": deployment_pack,
        },
    )


def _helm_ctx(tmp_path: Path, *, values_content: str) -> ModuleContext:
    workspace = tmp_path / "workspace"
    module_root = workspace / "module"
    _write_file(module_root / "helm/values.yaml", values_content)

    return ModuleContext(
        module_id=1,
        project_id=1,
        path="k8s/example-helm",
        category="k8s",
        deploy_model="helm",
        module_source_kind="git_catalog",
        workspace_path=str(workspace),
        pack_manifest={
            "module": {"name": "Example Helm"},
            "deployment_pack": {
                "engine": "kubernetes",
                "runner_profile": "kubernetes-default",
                "working_directory": "module",
                "lifecycle": {
                    "supports_init": True,
                    "supports_plan": True,
                    "supports_apply": True,
                    "supports_destroy": True,
                    "supports_refresh": True,
                    "supports_drift": True,
                },
                "entrypoints": {
                    "chart_ref": "oci://example/charts/app",
                    "values_path": "helm/values.yaml",
                },
            },
        },
    )


def test_build_manifest_payload_simple_mode_substitutes_variables(tmp_path: Path) -> None:
    ctx = _manifest_ctx(
        tmp_path,
        manifest_content=(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: ${name}\n"
            "  namespace: ${var.namespace}\n"
        ),
    )

    payload = build_manifest_payload(ctx, {"name": "cm-simple", "namespace": "ns-simple"})

    manifest = payload["manifests"][0]
    assert manifest["metadata"]["name"] == "cm-simple"
    assert manifest["metadata"]["namespace"] == "ns-simple"


def test_build_manifest_payload_jinja2_mode_supports_root_and_var_namespaces(tmp_path: Path) -> None:
    ctx = _manifest_ctx(
        tmp_path,
        template_engine="jinja2",
        manifest_content=(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: ${name}\n"
            "  namespace: ${var.namespace}\n"
        ),
    )

    payload = build_manifest_payload(ctx, {"name": "cm-jinja", "namespace": "ns-jinja"})

    manifest = payload["manifests"][0]
    assert manifest["metadata"]["name"] == "cm-jinja"
    assert manifest["metadata"]["namespace"] == "ns-jinja"


def test_build_manifest_payload_jinja2_mode_supports_if_conditionals(tmp_path: Path) -> None:
    ctx = _manifest_ctx(
        tmp_path,
        template_engine="jinja2",
        manifest_content=(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: cm-if\n"
            "data:\n"
            "  always: yes\n"
            "{% if enabled %}\n"
            "  feature: \"on\"\n"
            "{% endif %}\n"
        ),
    )

    payload = build_manifest_payload(ctx, {"enabled": True})
    assert payload["manifests"][0]["data"]["feature"] == "on"


def test_build_manifest_payload_jinja2_mode_supports_else_branch(tmp_path: Path) -> None:
    ctx = _manifest_ctx(
        tmp_path,
        template_engine="jinja2",
        manifest_content=(
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            "  name: app\n"
            "spec:\n"
            "  replicas: 1\n"
            "{% if enabled %}\n"
            "  strategy:\n"
            "    type: Recreate\n"
            "{% else %}\n"
            "  strategy:\n"
            "    type: RollingUpdate\n"
            "{% endif %}\n"
        ),
    )

    payload = build_manifest_payload(ctx, {"enabled": False})
    assert payload["manifests"][0]["spec"]["strategy"]["type"] == "RollingUpdate"


def test_build_manifest_payload_jinja2_mode_preserves_yaml_number_types(tmp_path: Path) -> None:
    ctx = _manifest_ctx(
        tmp_path,
        template_engine="jinja2",
        manifest_content=(
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            "  name: app\n"
            "spec:\n"
            "  replicas: ${replicas}\n"
        ),
    )

    payload = build_manifest_payload(ctx, {"replicas": 3})
    replicas = payload["manifests"][0]["spec"]["replicas"]
    assert replicas == 3
    assert isinstance(replicas, int)


def test_build_manifest_payload_jinja2_mode_supports_for_loops(tmp_path: Path) -> None:
    ctx = _manifest_ctx(
        tmp_path,
        template_engine="jinja2",
        manifest_content=(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: cm-loop\n"
            "data:\n"
            "{% for item in items %}\n"
            "  ${item.key}: ${item.value}\n"
            "{% endfor %}\n"
        ),
    )

    payload = build_manifest_payload(
        ctx,
        {
            "items": [
                {"key": "alpha", "value": "a"},
                {"key": "beta", "value": "b"},
            ]
        },
    )

    data = payload["manifests"][0]["data"]
    assert data == {"alpha": "a", "beta": "b"}


def test_build_manifest_payload_jinja2_mode_supports_nested_conditionals(tmp_path: Path) -> None:
    ctx = _manifest_ctx(
        tmp_path,
        template_engine="jinja2",
        manifest_content=(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: cm-nested\n"
            "data:\n"
            "{% for item in items %}\n"
            "{% if item.enabled %}\n"
            "  ${item.name}: enabled\n"
            "{% endif %}\n"
            "{% endfor %}\n"
        ),
    )

    payload = build_manifest_payload(
        ctx,
        {
            "items": [
                {"name": "first", "enabled": True},
                {"name": "second", "enabled": False},
            ]
        },
    )

    assert payload["manifests"][0]["data"] == {"first": "enabled"}


def test_build_manifest_payload_jinja2_mode_raises_on_missing_variable(tmp_path: Path) -> None:
    ctx = _manifest_ctx(
        tmp_path,
        template_engine="jinja2",
        manifest_content=(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: ${missing_name}\n"
        ),
    )

    with pytest.raises(jinja2.UndefinedError):
        build_manifest_payload(ctx, {})


def test_build_manifest_payload_jinja2_mode_filters_empty_rendered_documents(tmp_path: Path) -> None:
    ctx = _manifest_ctx(
        tmp_path,
        template_engine="jinja2",
        manifest_content=(
            "---\n"
            "{% if include_first %}\n"
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: first\n"
            "{% endif %}\n"
            "---\n"
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: second\n"
        ),
    )

    payload = build_manifest_payload(ctx, {"include_first": False})
    assert len(payload["manifests"]) == 1
    assert payload["manifests"][0]["metadata"]["name"] == "second"


def test_build_manifest_payload_jinja2_mode_supports_multi_document_yaml(tmp_path: Path) -> None:
    ctx = _manifest_ctx(
        tmp_path,
        template_engine="jinja2",
        manifest_content=(
            "---\n"
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: one\n"
            "---\n"
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: two\n"
        ),
    )

    payload = build_manifest_payload(ctx, {})
    names = [doc["metadata"]["name"] for doc in payload["manifests"]]
    assert names == ["one", "two"]


def test_build_manifest_payload_jinja2_mode_supports_json_manifest(tmp_path: Path) -> None:
    ctx = _manifest_ctx(
        tmp_path,
        template_engine="jinja2",
        manifest_file="manifests/manifest.json",
        manifest_content='{"apiVersion":"v1","kind":"ConfigMap","metadata":{"name":"${name}"}}',
    )

    payload = build_manifest_payload(ctx, {"name": "json-jinja"})
    assert payload["manifests"][0]["metadata"]["name"] == "json-jinja"


def test_build_manifest_payload_jinja2_mode_supports_mixed_substitution_and_blocks(tmp_path: Path) -> None:
    ctx = _manifest_ctx(
        tmp_path,
        template_engine="jinja2",
        manifest_content=(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: ${name}\n"
            "data:\n"
            "{% if enabled %}\n"
            "  state: ${state}\n"
            "{% endif %}\n"
        ),
    )

    payload = build_manifest_payload(
        ctx,
        {
            "enabled": True,
            "name": "mixed-manifest",
            "state": "active",
        },
    )

    manifest = payload["manifests"][0]
    assert manifest["metadata"]["name"] == "mixed-manifest"
    assert manifest["data"]["state"] == "active"


def test_build_manifest_payload_without_template_engine_uses_simple_renderer(tmp_path: Path) -> None:
    ctx = _manifest_ctx(
        tmp_path,
        manifest_content=(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: ${missing_name}\n"
        ),
    )

    payload = build_manifest_payload(ctx, {})
    assert payload["manifests"][0]["metadata"]["name"] == "${missing_name}"


def test_build_manifest_payload_jinja2_mode_raises_when_rendered_doc_is_not_dict(tmp_path: Path) -> None:
    ctx = _manifest_ctx(
        tmp_path,
        template_engine="jinja2",
        manifest_content=(
            "- one\n"
            "- two\n"
        ),
    )

    with pytest.raises(ValueError, match="expected dict"):
        build_manifest_payload(ctx, {})


def test_build_manifest_payload_simple_mode_raises_when_manifest_doc_is_not_dict(tmp_path: Path) -> None:
    ctx = _manifest_ctx(
        tmp_path,
        manifest_content=(
            "- one\n"
            "- two\n"
        ),
    )

    with pytest.raises(ValueError, match="expected dict"):
        build_manifest_payload(ctx, {})


def test_build_manifest_payload_rejects_path_traversal_in_manifest_path(tmp_path: Path) -> None:
    ctx = _manifest_ctx(
        tmp_path,
        manifest_content=(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: safe\n"
        ),
    )
    deployment_pack = ctx.pack_manifest["deployment_pack"]
    entrypoints = deployment_pack["entrypoints"]
    entrypoints["manifest_path"] = "../../etc/passwd"

    with pytest.raises(ValueError, match="Path escapes workspace root"):
        build_manifest_payload(ctx, {})


def test_build_helm_payload_uses_simple_renderer(tmp_path: Path) -> None:
    ctx = _helm_ctx(
        tmp_path,
        values_content=(
            "replicaCount: 2\n"
            "namespace: ${namespace}\n"
            "image:\n"
            "  tag: ${var.image_tag}\n"
        ),
    )

    payload = build_helm_payload(ctx, {"namespace": "helm-ns", "image_tag": "1.2.3"})

    assert payload["values"]["namespace"] == "helm-ns"
    assert payload["values"]["image"]["tag"] == "1.2.3"


def test_collect_outputs_boolean_gate(tmp_path: Path) -> None:
    ctx = _manifest_ctx(
        tmp_path,
        manifest_content=(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: gate\n"
        ),
    )
    ctx.pack_manifest["outputs"] = {
        "key_outputs": [
            {"name": "namespaces_ready", "type": "boolean"},
            {"name": "gateway_installed", "type": "boolean"},
        ]
    }

    outputs = collect_outputs_from_pack(ctx, {})

    assert outputs["namespaces_ready"] is True
    assert outputs["gateway_installed"] is True


def test_collect_outputs_mirrors_variables(tmp_path: Path) -> None:
    ctx = _manifest_ctx(
        tmp_path,
        manifest_content=(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: mirror\n"
        ),
    )
    ctx.pack_manifest["outputs"] = {
        "key_outputs": [
            {"name": "app_namespace", "type": "string"},
            {"name": "app_name", "type": "string"},
        ]
    }

    outputs = collect_outputs_from_pack(
        ctx,
        {
            "app_namespace": "demo-ns",
            "app_name": "demo-app",
        },
    )

    assert outputs == {
        "app_namespace": "demo-ns",
        "app_name": "demo-app",
    }


def test_collect_outputs_from_entrypoint_templates(tmp_path: Path) -> None:
    ctx = _manifest_ctx(
        tmp_path,
        manifest_content=(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: entry\n"
        ),
    )
    ctx.pack_manifest["deployment_pack"]["entrypoints"]["app_namespace"] = "${var.namespace}"
    ctx.pack_manifest["deployment_pack"]["entrypoints"]["app_name"] = "${name}"
    ctx.pack_manifest["outputs"] = {
        "key_outputs": [
            {"name": "app_namespace", "type": "string"},
            {"name": "app_name", "type": "string"},
        ]
    }

    outputs = collect_outputs_from_pack(ctx, {"namespace": "bnk-system", "name": "demo"})

    assert outputs == {
        "app_namespace": "bnk-system",
        "app_name": "demo",
    }


def test_collect_outputs_does_not_fallback_name_outputs_to_module_display_name(tmp_path: Path) -> None:
    ctx = _manifest_ctx(
        tmp_path,
        manifest_content=(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: nad\n"
        ),
    )
    ctx.pack_manifest["module"]["name"] = "Network Attachment Definitions"
    ctx.pack_manifest["outputs"] = {
        "key_outputs": [
            {"name": "external_nad_name", "type": "string", "description": "External NAD name"},
            {"name": "internal_nad_name", "type": "string", "description": "Internal NAD name"},
            {"name": "nads_ready", "type": "boolean", "description": "NADs ready"},
        ]
    }

    outputs = collect_outputs_from_pack(ctx, {})

    assert outputs == {"nads_ready": True}


def test_collect_outputs_uses_explicit_output_values_from_pack_definition(tmp_path: Path) -> None:
    ctx = _manifest_ctx(
        tmp_path,
        manifest_content=(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: explicit\n"
        ),
    )
    ctx.pack_manifest["outputs"] = {
        "key_outputs": [
            {
                "name": "external_nad_name",
                "type": "string",
                "description": "External NAD name",
                "value": "${external_name}",
            },
            {
                "name": "internal_nad_name",
                "type": "string",
                "description": "Internal NAD name",
                "value": "internal-netdevice",
            },
            {
                "name": "network_meta",
                "type": "map",
                "description": "Rendered metadata",
                "value": {
                    "namespace": "${namespace}",
                    "names": ["${external_name}", "internal-netdevice"],
                },
            },
        ]
    }

    outputs = collect_outputs_from_pack(
        ctx,
        {
            "external_name": "external-netdevice",
            "namespace": "f5-operator",
        },
    )

    assert outputs == {
        "external_nad_name": "external-netdevice",
        "internal_nad_name": "internal-netdevice",
        "network_meta": {
            "namespace": "f5-operator",
            "names": ["external-netdevice", "internal-netdevice"],
        },
    }


def test_collect_outputs_skips_explicit_output_value_when_template_unresolved(tmp_path: Path) -> None:
    ctx = _manifest_ctx(
        tmp_path,
        manifest_content=(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: unresolved\n"
        ),
    )
    ctx.pack_manifest["outputs"] = {
        "key_outputs": [
            {
                "name": "external_nad_name",
                "type": "string",
                "description": "External NAD name",
                "value": "${external_name}",
            }
        ]
    }

    outputs = collect_outputs_from_pack(ctx, {})

    assert outputs == {}


def test_collect_outputs_empty_when_no_outputs_section(tmp_path: Path) -> None:
    ctx = _manifest_ctx(
        tmp_path,
        manifest_content=(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: none\n"
        ),
    )

    assert collect_outputs_from_pack(ctx, {"namespace": "demo"}) == {}


def test_collect_outputs_from_real_demo_namespace_pack(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    module_root = workspace / "module"
    _write_file(
        module_root / "manifests/namespace.yaml",
        (
            "apiVersion: v1\n"
            "kind: Namespace\n"
            "metadata:\n"
            "  name: ${app_namespace}\n"
        ),
    )

    ctx = ModuleContext(
        module_id=1,
        project_id=1,
        path="app/demo-namespace",
        category="k8s",
        deploy_model="manifests",
        module_source_kind="git_catalog",
        workspace_path=str(workspace),
        pack_manifest={
            "module": {"name": "demo-namespace"},
            "deployment_pack": {
                "engine": "kubernetes",
                "runner_profile": "kubernetes-default",
                "working_directory": "module",
                "entrypoints": {
                    "manifest_path": "manifests",
                    "namespace": "${app_namespace}",
                },
            },
            "outputs": {
                "key_outputs": [
                    {"name": "app_namespace", "type": "string"},
                    {"name": "namespaces_ready", "type": "boolean"},
                ]
            },
        },
    )

    outputs = collect_outputs_from_pack(ctx, {"app_namespace": "demo-tenant-a"})

    assert outputs == {
        "app_namespace": "demo-tenant-a",
        "namespaces_ready": True,
    }


def _deployment_pack_for_validation(engine: str) -> dict[str, object]:
    entrypoints_by_engine = {
        "kubernetes": {"manifest_path": "manifests"},
        "opentofu": {"module_root": "./"},
        "ansible": {"playbook": "site.yml"},
        "script": {"apply_script": "apply.sh", "outputs_file": "outputs.json"},
    }

    runner_profile_by_engine = {
        "kubernetes": "kubernetes-default",
        "opentofu": "opentofu-default",
        "ansible": "ansible-default",
        "script": "script-restricted",
    }

    return {
        "engine": engine,
        "runner_profile": runner_profile_by_engine[engine],
        "working_directory": "module",
        "lifecycle": {
            "supports_init": True,
            "supports_plan": True,
            "supports_apply": True,
            "supports_destroy": True,
            "supports_refresh": True,
            "supports_drift": True,
        },
        "entrypoints": entrypoints_by_engine[engine],
    }


def test_validate_pack_deployment_pack_section_allows_missing_template_engine_for_kubernetes() -> None:
    validator = ModuleMetadataValidator()
    deployment_pack = _deployment_pack_for_validation("kubernetes")

    validator._validate_pack_deployment_pack_section({"deployment_pack": deployment_pack})


@pytest.mark.parametrize("template_engine", ["simple", "jinja2"])
def test_validate_pack_deployment_pack_section_accepts_template_engine_for_kubernetes(template_engine: str) -> None:
    validator = ModuleMetadataValidator()
    deployment_pack = _deployment_pack_for_validation("kubernetes")
    deployment_pack["template_engine"] = template_engine

    validator._validate_pack_deployment_pack_section({"deployment_pack": deployment_pack})


def test_validate_pack_deployment_pack_section_rejects_template_engine_for_non_kubernetes() -> None:
    validator = ModuleMetadataValidator()
    deployment_pack = _deployment_pack_for_validation("opentofu")
    deployment_pack["template_engine"] = "simple"

    with pytest.raises(InvalidMetadataSchemaError, match="only valid when deployment_pack.engine is 'kubernetes'"):
        validator._validate_pack_deployment_pack_section({"deployment_pack": deployment_pack})


def test_validate_pack_deployment_pack_section_rejects_invalid_template_engine_value() -> None:
    validator = ModuleMetadataValidator()
    deployment_pack = _deployment_pack_for_validation("kubernetes")
    deployment_pack["template_engine"] = "mustache"

    with pytest.raises(InvalidMetadataSchemaError, match="Invalid deployment_pack.template_engine"):
        validator._validate_pack_deployment_pack_section({"deployment_pack": deployment_pack})
