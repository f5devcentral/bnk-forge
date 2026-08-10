"""Catalog-backed Kubernetes payload builders.

Builds manifest/helm execution payloads from persisted catalog metadata
(`deploy_model` + `pack_manifest`) and workspace artifacts.
"""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import jinja2
import yaml

from services.execution.engine_interface import ModuleContext

_VAR_PATTERN = re.compile(r"\$\{\s*([a-zA-Z0-9_.-]+)\s*\}")
_JINJA_VAR_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}")
_JINJA_ENV = jinja2.Environment(
    variable_start_string="${",
    variable_end_string="}",
    block_start_string="{%",
    block_end_string="%}",
    comment_start_string="{#",
    comment_end_string="#}",
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
    undefined=jinja2.StrictUndefined,
    autoescape=False,
)


def is_catalog_k8s_context(ctx: ModuleContext) -> bool:
    """True when context has catalog metadata for first-class K8s execution."""
    module_source_kind = (ctx.module_source_kind or "").strip().lower()
    deploy_model = (ctx.deploy_model or "").strip().lower()
    return module_source_kind != "builtin" and deploy_model in {"helm", "manifests"} and isinstance(ctx.pack_manifest, dict)


def build_manifest_payload(ctx: ModuleContext, variables: dict[str, Any]) -> dict[str, Any]:
    """Build manifest payload from catalog metadata + workspace files."""
    deploy_model = (ctx.deploy_model or "").strip().lower()
    if deploy_model != "manifests":
        raise ValueError(f"Manifest payload requires deploy_model='manifests', got '{ctx.deploy_model}'")

    pack_manifest, deployment_pack, entrypoints = _unpack_manifest_sections(ctx)

    # Apply pack input defaults for variables not already set
    variables = _apply_pack_input_defaults(pack_manifest, variables)

    manifest_path = entrypoints.get("manifest_path")
    if not isinstance(manifest_path, str) or not manifest_path.strip():
        raise ValueError("pack_manifest.deployment_pack.entrypoints.manifest_path is required")

    workspace_root = _resolve_workspace_root(ctx, deployment_pack)
    manifest_root = _resolve_workspace_subpath(workspace_root, manifest_path)

    template_engine = _get_template_engine(ctx)
    if template_engine == "jinja2":
        rendered = _render_manifest_files_jinja2(manifest_root, variables)
    else:
        manifests = _load_manifest_documents(manifest_root)
        rendered = [_render_template_obj(doc, variables) for doc in manifests]

    module_name = _safe_str(pack_manifest.get("module", {}).get("name")) or ctx.path
    timeout = _resolve_timeout_seconds(deployment_pack, default=300)
    namespace = _resolve_entrypoint_str(entrypoints, "namespace", variables) or _safe_str(variables.get("namespace")) or "default"

    return {
        "module_name": module_name,
        "timeout": timeout,
        "namespace": namespace,
        "manifests": rendered,
    }


def build_helm_payload(ctx: ModuleContext, variables: dict[str, Any]) -> dict[str, Any]:
    """Build helm payload from catalog metadata + workspace files."""
    deploy_model = (ctx.deploy_model or "").strip().lower()
    if deploy_model != "helm":
        raise ValueError(f"Helm payload requires deploy_model='helm', got '{ctx.deploy_model}'")

    pack_manifest, deployment_pack, entrypoints = _unpack_manifest_sections(ctx)

    # Apply pack input defaults for variables not already set
    variables = _apply_pack_input_defaults(pack_manifest, variables)
    chart_ref = _safe_str(entrypoints.get("chart_ref"))
    values_path = entrypoints.get("values_path") or entrypoints.get("values_file")

    needs_workspace = (
        (not chart_ref and isinstance(entrypoints.get("chart_path"), str) and bool(entrypoints.get("chart_path").strip()))
        or (isinstance(values_path, str) and bool(values_path.strip()))
    )
    workspace_root = _resolve_workspace_root(ctx, deployment_pack) if needs_workspace else None

    if not chart_ref:
        chart_path = entrypoints.get("chart_path")
        if not isinstance(chart_path, str) or not chart_path.strip():
            raise ValueError(
                "pack_manifest.deployment_pack.entrypoints requires chart_ref or chart_path for helm deploy_model"
            )
        if workspace_root is None:
            raise ValueError("Helm chart_path requires prepared workspace")
        chart_ref = _resolve_workspace_subpath(workspace_root, chart_path)

    module_name = _safe_str(pack_manifest.get("module", {}).get("name")) or ctx.path.split("/")[-1]
    release_name = (
        _resolve_entrypoint_str(entrypoints, "release_name", variables)
        or _safe_str(variables.get("release_name"))
        or module_name.replace(" ", "-")
    )
    namespace = _resolve_entrypoint_str(entrypoints, "namespace", variables) or _safe_str(variables.get("namespace")) or "default"

    values = _load_helm_values_file_if_present(workspace_root, entrypoints)
    values = _render_template_obj(values, variables)

    chart_version = _resolve_entrypoint_str(entrypoints, "chart_version", variables) or _safe_str(
        variables.get("chart_version")
    )
    create_namespace = bool(entrypoints.get("create_namespace", True))
    timeout = _resolve_timeout_seconds(deployment_pack, default=600)

    return {
        "module_name": module_name,
        "chart_ref": chart_ref,
        "release_name": release_name,
        "namespace": namespace,
        "chart_version": chart_version,
        "create_namespace": create_namespace,
        "timeout": timeout,
        "values": values,
        "helm_repos": entrypoints.get("helm_repos") if isinstance(entrypoints.get("helm_repos"), dict) else {},
    }


def collect_outputs_from_pack(ctx: ModuleContext, variables: dict[str, Any]) -> dict[str, Any]:
    """Build module outputs from pack output declarations + render variables."""
    pack = ctx.pack_manifest if isinstance(ctx.pack_manifest, dict) else {}
    outputs_section = pack.get("outputs", {})
    if not isinstance(outputs_section, dict):
        return {}

    key_outputs = outputs_section.get("key_outputs", [])
    if not isinstance(key_outputs, list):
        return {}

    deployment_pack = pack.get("deployment_pack", {})
    entrypoints = deployment_pack.get("entrypoints", {}) if isinstance(deployment_pack, dict) else {}

    # Apply pack optional-input defaults to the variables dict before resolving
    # outputs. Keeps output resolution consistent with manifest rendering
    # (which does the same in build_manifest_payload) — without this, Strategy
    # 0 templates like `value: "${gateway_name}"` fail to resolve and fall
    # through to Strategy 4 (release_name). That caused a bug where every
    # `_name`-suffixed output collapsed to the release_name of an unrelated
    # sibling module (e.g., k8s/cert-manager's release_name=cert-manager
    # polluted app/bedrock-smartllm-backend's analyzer_name/gateway_name/
    # httproute_name and then re-cascaded via auto_wire_common_variables).
    variables = _apply_pack_input_defaults(pack, variables)

    result: dict[str, Any] = {}
    for output_def in key_outputs:
        if not isinstance(output_def, dict):
            continue

        name = output_def.get("name")
        if not isinstance(name, str) or not name.strip():
            continue

        output_type = str(output_def.get("type", "string")).strip().lower()

        # Strategy 0: explicit output value declared in pack output metadata.
        # Supports templated strings and typed objects (dict/list/bool/int).
        if "value" in output_def:
            resolved_value = _resolve_output_value_from_definition(output_def["value"], variables)
            if resolved_value is not None:
                result[name] = resolved_value
                continue

        # Strategy 1: boolean gate outputs become true when apply succeeded.
        if output_type == "boolean" and (name.endswith("_ready") or name.endswith("_installed")):
            result[name] = True
            continue

        # Strategy 2: direct variable mirror.
        if name in variables:
            result[name] = variables[name]
            continue

        # Strategy 3: derive from matching entrypoint value.
        if isinstance(entrypoints, dict) and name in entrypoints:
            resolved = _resolve_output_value_from_entrypoint(entrypoints[name], variables)
            if resolved is not None:
                result[name] = resolved
                continue

        # Strategy 4: common conventions.
        if name.endswith("_namespace"):
            namespace = _resolve_output_value_from_entrypoint(entrypoints.get("namespace"), variables)
            if namespace is None:
                namespace = _safe_str(variables.get("namespace"))
            if namespace is not None:
                result[name] = namespace
                continue

        if name.endswith("_name"):
            release_name = _safe_str(entrypoints.get("release_name")) or _safe_str(variables.get("release_name"))
            if release_name is not None:
                result[name] = release_name
                continue

    return result


def _unpack_manifest_sections(ctx: ModuleContext) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    pack_manifest = ctx.pack_manifest if isinstance(ctx.pack_manifest, dict) else None
    if not pack_manifest:
        raise ValueError("Catalog-backed K8s execution requires module pack_manifest metadata")

    deployment_pack = pack_manifest.get("deployment_pack")
    if not isinstance(deployment_pack, dict):
        raise ValueError("pack_manifest.deployment_pack must be an object")

    entrypoints = deployment_pack.get("entrypoints")
    if not isinstance(entrypoints, dict):
        raise ValueError("pack_manifest.deployment_pack.entrypoints must be an object")

    return pack_manifest, deployment_pack, entrypoints


def _resolve_workspace_root(ctx: ModuleContext, deployment_pack: dict[str, Any]) -> str:
    if not isinstance(ctx.workspace_path, str) or not ctx.workspace_path.strip():
        raise ValueError("Catalog-backed K8s execution requires a prepared module workspace")

    working_directory = deployment_pack.get("working_directory")
    if not isinstance(working_directory, str) or not working_directory.strip():
        raise ValueError("pack_manifest.deployment_pack.working_directory must be a non-empty string")

    workspace_root = _resolve_workspace_subpath(ctx.workspace_path, working_directory)
    if not os.path.isdir(workspace_root):
        raise ValueError(
            f"pack_manifest working_directory does not exist in workspace: {working_directory}"
        )

    return workspace_root


def _resolve_workspace_subpath(root: str, relative_path: str) -> str:
    base = Path(root).resolve()
    target = (base / relative_path).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise ValueError(f"Path escapes workspace root: {relative_path}")
    return str(target)


def _load_manifest_documents(path: str) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        raise ValueError(f"Manifest path does not exist in workspace: {path}")

    files: list[Path]
    if target.is_file():
        files = [target]
    else:
        files = sorted(
            p for p in target.rglob("*")
            if p.is_file() and p.suffix.lower() in {".yml", ".yaml", ".json"}
        )

    manifests: list[dict[str, Any]] = []
    for file_path in files:
        raw = file_path.read_text(encoding="utf-8")
        docs = yaml.safe_load_all(raw) if file_path.suffix.lower() in {".yml", ".yaml"} else [json.loads(raw)]
        for doc in docs:
            if doc is None:
                continue
            if not isinstance(doc, dict):
                raise ValueError(
                    f"Manifest document rendered to {type(doc).__name__}, expected dict. "
                    f"Check Jinja2 template syntax in {file_path}"
                )
            manifests.append(doc)

    if not manifests:
        raise ValueError(f"No manifest documents found at {path}")
    return manifests


def _render_manifest_files_jinja2(path: str, variables: dict[str, Any]) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        raise ValueError(f"Manifest path does not exist in workspace: {path}")

    files: list[Path]
    if target.is_file():
        files = [target]
    else:
        files = sorted(
            p for p in target.rglob("*")
            if p.is_file() and p.suffix.lower() in {".yml", ".yaml", ".json"}
        )

    manifests: list[dict[str, Any]] = []
    for file_path in files:
        rendered_text = _render_jinja2_text(file_path.read_text(encoding="utf-8"), variables)
        docs = (
            yaml.safe_load_all(rendered_text)
            if file_path.suffix.lower() in {".yml", ".yaml"}
            else [json.loads(rendered_text)]
        )
        for doc in docs:
            if doc is None:
                continue
            if not isinstance(doc, dict):
                raise ValueError(
                    f"Manifest document rendered to {type(doc).__name__}, expected dict. "
                    f"Check Jinja2 template syntax in {file_path}"
                )
            manifests.append(doc)

    if not manifests:
        raise ValueError(f"No manifest documents found at {path}")
    return manifests


def _load_helm_values_file_if_present(workspace_root: str | None, entrypoints: dict[str, Any]) -> dict[str, Any]:
    values_path = entrypoints.get("values_path") or entrypoints.get("values_file")
    if not isinstance(values_path, str) or not values_path.strip():
        return {}
    if workspace_root is None:
        raise ValueError("Helm values file requires prepared workspace")

    full_path = _resolve_workspace_subpath(workspace_root, values_path)
    if not os.path.exists(full_path):
        raise ValueError(f"Helm values file not found in workspace: {values_path}")

    with open(full_path, encoding="utf-8") as f:
        loaded = yaml.safe_load(f)
    return loaded if isinstance(loaded, dict) else {}


def _resolve_timeout_seconds(deployment_pack: dict[str, Any], *, default: int) -> int:
    timeout = deployment_pack.get("timeout_seconds")
    if isinstance(timeout, int) and timeout > 0:
        return timeout
    return default


def _render_template_obj(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {k: _render_template_obj(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [_render_template_obj(v, variables) for v in value]
    if isinstance(value, str):
        return _render_string_value(value, variables)
    return deepcopy(value)


def _render_string_value(text: str, variables: dict[str, Any]) -> Any:
    """Render variable references in a string, preserving types for pure references.

    When the entire string is a single variable reference (e.g. "${replicas}" or
    "{{ replicas }}") with no surrounding text, return the variable's native
    Python type (int, float, bool, None, dict, list, etc).

    When the string contains interpolated variables mixed with other text
    (e.g. "prefix-${name}-suffix"), return a string with substitutions applied.

    This is critical for Helm values: YAML like `replicaCount: ${replicas}`
    must resolve to `replicaCount: 3` (int), not `replicaCount: "3"` (string),
    otherwise Helm schema validation fails.
    """
    # Check if the entire string is exactly one variable reference (no surrounding text)
    # Pattern: optional whitespace + ${var} or {{var}} + optional whitespace
    simple_match = _VAR_PATTERN.fullmatch(text.strip())
    jinja_match = _JINJA_VAR_PATTERN.fullmatch(text.strip()) if not simple_match else None

    if simple_match or jinja_match:
        # Pure variable reference — return the native value, preserving type
        match = simple_match or jinja_match
        token = match.group(1)  # type: ignore[union-attr]
        resolved = _lookup_var(token, variables)
        # Distinguish "variable not found" from "variable is None"
        var_exists = _var_exists(token, variables)
        if var_exists:
            # Return native type (int, float, bool, dict, list, None, etc)
            return deepcopy(resolved) if isinstance(resolved, (dict, list)) else resolved
        # Variable not found — return original placeholder unchanged
        return text

    # Mixed content (text + variables) — must stringify for interpolation
    return _render_simple_string(text, variables)


def _render_simple_string(text: str, variables: dict[str, Any]) -> str:
    """Render variable references in a string, always returning a string.

    Used for interpolated strings where variables are embedded in surrounding text.
    For pure variable references that should preserve type, use _render_string_value.
    """
    def _replace(match: re.Match[str]) -> str:
        token = match.group(1)
        value = _lookup_var(token, variables)
        return str(value) if value is not None else match.group(0)

    out = _VAR_PATTERN.sub(_replace, text)
    out = _JINJA_VAR_PATTERN.sub(_replace, out)
    return out


def _render_template_string(text: str, variables: dict[str, Any]) -> str:
    """Backward-compatible wrapper for simple string substitution."""
    return _render_simple_string(text, variables)


def _render_jinja2_text(text: str, variables: dict[str, Any]) -> str:
    render_context = {**variables, "var": variables}
    return _JINJA_ENV.from_string(text).render(render_context)


def _lookup_var(token: str, variables: dict[str, Any]) -> Any:
    if token in variables:
        return variables[token]
    if token.startswith("var."):
        key = token[4:]
        return variables.get(key)
    return variables.get(token)


def _var_exists(token: str, variables: dict[str, Any]) -> bool:
    """Check if a variable exists (as opposed to being None)."""
    if token in variables:
        return True
    if token.startswith("var."):
        key = token[4:]
        return key in variables
    return token in variables


def _apply_pack_input_defaults(pack_manifest: dict[str, Any], variables: dict[str, Any]) -> dict[str, Any]:
    """Apply default values from pack_manifest.inputs for variables not already set.

    Pack manifests declare inputs with defaults at ``pack_manifest.inputs.optional``
    (and ``pack_manifest.inputs.required``).  The variable assembler does not read
    these — it only handles project/module/auto-wired variables.  This function fills
    in any gaps so that values.yaml references like ``${controller_replicas}`` resolve
    to their declared defaults (e.g. ``1``) instead of passing through as literal
    strings (which breaks Helm schema validation).
    """
    inputs_section = pack_manifest.get("inputs")
    if not isinstance(inputs_section, dict):
        return variables

    result = dict(variables)  # shallow copy — don't mutate the original
    for input_def in [*inputs_section.get("required", []), *inputs_section.get("optional", [])]:
        if not isinstance(input_def, dict):
            continue
        name = input_def.get("name")
        if not name or name in result:
            continue
        if "default" in input_def:
            result[name] = input_def["default"]

    return result


def _get_template_engine(ctx: ModuleContext) -> str:
    pack = ctx.pack_manifest if isinstance(ctx.pack_manifest, dict) else {}
    deployment_pack = pack.get("deployment_pack", {})
    if not isinstance(deployment_pack, dict):
        return "simple"
    return str(deployment_pack.get("template_engine", "simple")).strip().lower()


def _safe_str(value: Any) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _resolve_entrypoint_str(entrypoints: dict[str, Any], key: str, variables: dict[str, Any]) -> str | None:
    """Read an entrypoint value and render any ${var} references against variables."""
    raw = _safe_str(entrypoints.get(key))
    if raw is None:
        return None

    rendered = _render_simple_string(raw, variables)
    if _VAR_PATTERN.search(rendered):
        return None

    normalized = rendered.strip()
    return normalized or None


def _resolve_output_value_from_entrypoint(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, str):
        rendered = _render_simple_string(value, variables)
        template = value.strip()
        unresolved_simple = _VAR_PATTERN.fullmatch(template) is not None and rendered == value
        unresolved_jinja = _JINJA_VAR_PATTERN.fullmatch(template) is not None and rendered == value
        if unresolved_simple or unresolved_jinja:
            return None
        return rendered
    if value is None:
        return None
    return deepcopy(value)


def _resolve_output_value_from_definition(value: Any, variables: dict[str, Any]) -> Any:
    rendered = _render_template_obj(value, variables)
    if _contains_unresolved_template(rendered):
        return None
    return rendered


def _contains_unresolved_template(value: Any) -> bool:
    if isinstance(value, str):
        return _VAR_PATTERN.search(value) is not None or _JINJA_VAR_PATTERN.search(value) is not None
    if isinstance(value, dict):
        return any(_contains_unresolved_template(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_unresolved_template(v) for v in value)
    return False
