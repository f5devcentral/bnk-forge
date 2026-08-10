"""Scrape variables.tf and outputs.tf for an imported registry module.

Uses python-hcl2 (already in requirements.txt) to parse `*.tf` files in the
module root. Submodules and tests directories are intentionally skipped — the
public-facing inputs/outputs of a module are what's at the root.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import hcl2

logger = logging.getLogger(__name__)

# Files we never scrape: tests, submodules, examples.
_SKIP_DIRS = {"tests", "test", "examples", "modules"}


@dataclass
class ScrapedVariable:
    name: str
    type: str = "any"
    description: str = ""
    default: object | None = None
    has_default: bool = False
    sensitive: bool = False
    nullable: bool = True


@dataclass
class ScrapedOutput:
    name: str
    description: str = ""
    sensitive: bool = False


@dataclass
class ScrapeResult:
    variables: list[ScrapedVariable] = field(default_factory=list)
    outputs: list[ScrapedOutput] = field(default_factory=list)
    required_providers: list[str] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)


def _stringify_type(raw: object) -> str:
    """HCL2 returns variable types as raw strings or as `${type(...)}` interpolations.

    For UI/UX we want a stable readable form. Falls back to "any" if the type isn't
    a string (e.g. when hcl2 surfaces it as a list of tokens).
    """
    if raw is None:
        return "any"
    if isinstance(raw, str):
        # python-hcl2 wraps types in "${...}". Strip that for readability.
        if raw.startswith("${") and raw.endswith("}"):
            return raw[2:-1]
        return raw
    return "any"


def _coerce_block(value: object) -> dict[str, object]:
    """python-hcl2 wraps single-block values in a list. Normalize to dict."""
    if isinstance(value, list):
        if not value:
            return {}
        first = value[0]
        return first if isinstance(first, dict) else {}
    if isinstance(value, dict):
        return value
    return {}


def _parse_file(path: Path) -> dict[str, object]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return hcl2.load(fh)
    except Exception as exc:
        logger.warning("Failed to parse %s: %s", path, exc)
        return {}


def scrape(module_dir: Path) -> ScrapeResult:
    """Walk *.tf files in `module_dir` (root only) and collect variables + outputs."""
    result = ScrapeResult()
    if not module_dir.is_dir():
        result.parse_warnings.append(f"module_dir {module_dir} does not exist")
        return result

    tf_files = [
        p for p in sorted(module_dir.glob("*.tf"))
        if p.is_file() and p.parent.name not in _SKIP_DIRS
    ]
    if not tf_files:
        result.parse_warnings.append("no *.tf files found at module root")
        return result

    for tf_path in tf_files:
        parsed = _parse_file(tf_path)
        if not parsed:
            continue

        for var_block in parsed.get("variable", []) or []:
            if not isinstance(var_block, dict):
                continue
            for var_name, var_attrs in var_block.items():
                attrs = _coerce_block(var_attrs)
                has_default = "default" in attrs
                result.variables.append(
                    ScrapedVariable(
                        name=var_name,
                        type=_stringify_type(attrs.get("type")),
                        description=str(attrs.get("description") or ""),
                        default=attrs.get("default") if has_default else None,
                        has_default=has_default,
                        sensitive=bool(attrs.get("sensitive", False)),
                        nullable=bool(attrs.get("nullable", True)),
                    )
                )

        for out_block in parsed.get("output", []) or []:
            if not isinstance(out_block, dict):
                continue
            for out_name, out_attrs in out_block.items():
                attrs = _coerce_block(out_attrs)
                result.outputs.append(
                    ScrapedOutput(
                        name=out_name,
                        description=str(attrs.get("description") or ""),
                        sensitive=bool(attrs.get("sensitive", False)),
                    )
                )

        for terraform_block in parsed.get("terraform", []) or []:
            if not isinstance(terraform_block, dict):
                continue
            providers = terraform_block.get("required_providers")
            providers_dict = _coerce_block(providers)
            for prov_name in providers_dict:
                if prov_name not in result.required_providers:
                    result.required_providers.append(prov_name)

    return result
