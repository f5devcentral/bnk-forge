"""Synthesize a `bnkforge.pack.json` for an imported registry/git module.

The output is a dict in the same shape that `ModuleMetadataValidator.validate_pack_manifest`
accepts. We delegate validation to that class — no schema duplication here.

User overrides on description / sensitive flags are preserved across re-imports
by reading them from the `metadata_overrides` JSON column on `ModuleLibrary` and
splicing them in after auto-scrape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from services.registry_variable_scraper import ScrapedOutput, ScrapedVariable, ScrapeResult


class ModuleIdentity(TypedDict):
    name: str
    catalog_path: str  # e.g. "registry/terraform/hashicorp/aws/vpc"
    version: str
    provider: str
    description: str


_LIFECYCLE_ALL_TRUE = {
    "supports_init": True,
    "supports_plan": True,
    "supports_apply": True,
    "supports_destroy": True,
    "supports_refresh": True,
    "supports_drift": True,
}

_ENGINE_DEPLOYMENT_PACK: dict[str, dict] = {
    "opentofu": {
        "engine": "opentofu",
        "runner_profile": "opentofu-default",
        "working_directory": ".",
        "lifecycle": _LIFECYCLE_ALL_TRUE,
        "entrypoints": {"module_root": "."},
    },
    "kubernetes": {
        "engine": "kubernetes",
        "runner_profile": "kubernetes-default",
        "working_directory": ".",
        "lifecycle": _LIFECYCLE_ALL_TRUE,
        "entrypoints": {"chart_path": "."},
    },
    "ansible": {
        "engine": "ansible",
        "runner_profile": "ansible-default",
        "working_directory": ".",
        "lifecycle": _LIFECYCLE_ALL_TRUE,
        "entrypoints": {"playbook": "site.yml"},
    },
}


def infer_engine(module_dir: Path, engine_override: str | None = None) -> str:
    """Infer the deployment engine from the module directory contents.

    Priority:
    1. Explicit ``engine_override`` — accepted as-is (caller validates).
    2. ``Chart.yaml`` at module root → ``kubernetes`` (Helm chart).
    3. Any ``*.tf`` files at module root → ``opentofu``.
    4. Any ``*.yml``/``*.yaml`` file containing a ``hosts:`` key → ``ansible`` playbook.
    5. Fall back to ``opentofu``.
    """
    if engine_override:
        return engine_override

    if not module_dir.is_dir():
        return "opentofu"

    # Helm chart
    if (module_dir / "Chart.yaml").is_file() or (module_dir / "chart.yaml").is_file():
        return "kubernetes"

    # OpenTofu / Terraform
    if any(module_dir.glob("*.tf")):
        return "opentofu"

    # Ansible playbook — look for a YAML file at root that has a `hosts:` key
    for yaml_path in sorted(module_dir.glob("*.yml")) or sorted(module_dir.glob("*.yaml")):
        try:
            text = yaml_path.read_text(encoding="utf-8", errors="replace")
            if "hosts:" in text:
                return "ansible"
        except OSError:
            continue

    return "opentofu"


def _normalize_default(default: object | None) -> object | None:
    """python-hcl2 wraps interpolation expressions in `${...}`. Pass through
    primitives as-is; if a default is a complex HCL expression, store as None
    so the user is forced to supply a value (safer than passing through a
    Terraform expression we can't evaluate)."""
    if default is None:
        return None
    if isinstance(default, (str, int, float, bool)):
        return default
    if isinstance(default, list):
        return [_normalize_default(x) for x in default]
    if isinstance(default, dict):
        return {k: _normalize_default(v) for k, v in default.items()}
    return None


def _input_entry(var: ScrapedVariable, overrides: dict[str, dict]) -> dict:
    """Build a pack-manifest input entry from a scraped variable + overrides."""
    user_override = overrides.get(var.name) or {}
    description = user_override.get("description") or var.description or var.name
    sensitive = bool(user_override.get("sensitive", var.sensitive))

    entry: dict = {
        "name": var.name,
        "type": var.type or "any",
        "description": description,
        "source": "user",
        "sensitive": sensitive,
    }
    if var.has_default and not sensitive:
        entry["default"] = _normalize_default(var.default)
    return entry


def _output_entry(out: ScrapedOutput, overrides: dict[str, dict]) -> dict:
    user_override = overrides.get(out.name) or {}
    description = user_override.get("description") or out.description or out.name
    sensitive = bool(user_override.get("sensitive", out.sensitive))
    return {
        "name": out.name,
        "type": "any",
        "description": description,
        "sensitive": sensitive,
    }


def synthesize(
    *,
    identity: ModuleIdentity,
    scraped: ScrapeResult,
    sha256: str,
    source_type: str,
    upstream_url: str,
    overrides: dict | None = None,
    module_dir: Path | None = None,
    engine_override: str | None = None,
) -> dict:
    """Build a pack-manifest dict ready for `validate_pack_manifest()`.

    `overrides` shape:
        {
          "variables": {"<var_name>": {"description"?: str, "sensitive"?: bool}, ...},
          "outputs":   {"<out_name>": {"description"?: str, "sensitive"?: bool}, ...},
        }

    Engine is inferred from `module_dir` contents (Chart.yaml → kubernetes,
    *.tf → opentofu, ansible playbook → ansible) unless `engine_override` is
    supplied, in which case that value is used directly.
    """
    overrides = overrides or {}
    var_overrides = overrides.get("variables", {}) if isinstance(overrides.get("variables"), dict) else {}
    out_overrides = overrides.get("outputs", {}) if isinstance(overrides.get("outputs"), dict) else {}

    # Variables without defaults are "required"; everything else is "optional".
    required: list[dict] = []
    optional: list[dict] = []
    for var in scraped.variables:
        entry = _input_entry(var, var_overrides)
        if var.has_default:
            optional.append(entry)
        else:
            required.append(entry)

    outputs = [_output_entry(o, out_overrides) for o in scraped.outputs]

    engine = infer_engine(module_dir or Path(), engine_override)
    deployment_pack = dict(_ENGINE_DEPLOYMENT_PACK.get(engine, _ENGINE_DEPLOYMENT_PACK["opentofu"]))
    # Shallow-copy nested dicts so callers can't mutate the template.
    deployment_pack["lifecycle"] = dict(deployment_pack["lifecycle"])
    deployment_pack["entrypoints"] = dict(deployment_pack["entrypoints"])

    manifest: dict = {
        "schema_version": 1,
        "module": {
            "name": identity["name"],
            "path": identity["catalog_path"],
            "version": identity["version"],
            "category": "infra",
            "provider": identity["provider"],
            "description": identity["description"] or identity["name"],
            "tags": [],
            "supported_platforms": ["any"],
        },
        "deployment_pack": deployment_pack,
        "dependencies": {"required": [], "optional": []},
        "inputs": {"required": required, "optional": optional},
        "outputs": {"key_outputs": outputs},
        "_synthesized": {
            "source_type": source_type,
            "upstream_url": upstream_url,
            "version": identity["version"],
            "sha256": sha256,
            "fetched_at": datetime.now(UTC).isoformat(),
            "required_providers": scraped.required_providers,
            "inferred_engine": engine,
        },
    }
    return manifest
