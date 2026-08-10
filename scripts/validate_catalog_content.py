#!/usr/bin/env python3
"""
Validate a catalog-content repo checkout (packs + blueprints) without a running Forge.

Usage:
    # From the bnk-forge repo root, against an index/content repo checkout:
    python scripts/validate_catalog_content.py /path/to/bnkctl-index

    # Custom registry allowlist (default mirrors the sync default):
    python scripts/validate_catalog_content.py /path/to/repo \
        --registry-allowlist ghcr.io,quay.io

Checks (the same validators the sync runs, so CI in a content repo can gate PRs
before a live Forge ever sees them):
  1. Every bnkforge.pack.json parses and passes validate_pack_manifest.
  2. module.path equals the pack's directory path (the sync rejects mismatches).
  3. container-engine packs ship a sibling bnkforge.artifact.json; any artifact
     manifest present passes validate_artifact_manifest (digest pinning,
     registry allowlist, argv-only steps, actions/reports/secret_files shape).
  4. artifact.version matches module.version (the bump-both discipline).
  5. Every forge-blueprint.json parses and passes validate_blueprint_manifest
     (schema + dependency graph).
  6. Blueprint module refs that point into this repo resolve to an existing
     pack directory whose module.version equals the pinned version. Refs to
     paths not in this repo are reported as warnings (external source).

Exit code: 0 if no errors (warnings allowed), 1 otherwise.
"""
import argparse
import json
import os
import sys

# Ensure backend/ is on the Python path (same pattern as generate-openapi.py)
backend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, backend_dir)

from services.blueprint_manifest_service import (  # noqa: E402
    BlueprintManifestValidationError,
    validate_blueprint_manifest,
)
from services.defaults_service import SYSTEM_DEFAULTS  # noqa: E402
from services.module_metadata import InvalidMetadataSchemaError, ModuleMetadataValidator  # noqa: E402

# Mirror the shipped system default (container.registry_host_allowlist) instead of
# hardcoding a copy, so this validator can't silently drift from what the sync
# enforces. NOTE: this is the *default*, not an operator-narrowed live allowlist —
# a deployment whose operator tightened it would reject hosts this default accepts.
# Pass --registry-allowlist to mirror a specific deployment.
DEFAULT_ALLOWLIST = [
    h.strip()
    for h in SYSTEM_DEFAULTS["container.registry_host_allowlist"]["value"].split(",")
    if h.strip()
]

PACK_FILE = "bnkforge.pack.json"
ARTIFACT_FILE = "bnkforge.artifact.json"
BLUEPRINT_FILE = "forge-blueprint.json"


def _load_json(path: str, errors: list[str]) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path}: cannot parse JSON: {exc}")
        return None
    if not isinstance(data, dict):
        errors.append(f"{path}: top level must be a JSON object")
        return None
    return data


def _find_manifests(root: str, filename: str) -> list[str]:
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        if filename in filenames:
            found.append(os.path.join(dirpath, filename))
    return sorted(found)


def validate_content_repo(root: str, allowlist: list[str]) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for the content repo at ``root``."""
    errors: list[str] = []
    warnings: list[str] = []
    validator = ModuleMetadataValidator()

    # path (as declared/discovered) -> declared module version, for blueprint pin checks
    pack_versions: dict[str, str] = {}

    for pack_path in _find_manifests(root, PACK_FILE):
        rel_dir = os.path.relpath(os.path.dirname(pack_path), root).replace(os.sep, "/")
        pack = _load_json(pack_path, errors)
        if pack is None:
            continue
        try:
            validator.validate_pack_manifest(pack)
        except InvalidMetadataSchemaError as exc:
            errors.append(f"{pack_path}: invalid pack manifest: {exc}")
            continue

        module = pack.get("module") or {}
        declared_path = module.get("path")
        if declared_path != rel_dir:
            errors.append(
                f"{pack_path}: module.path '{declared_path}' does not match its "
                f"directory '{rel_dir}' (the sync rejects this pack)"
            )
        version = module.get("version")
        if declared_path and version:
            pack_versions[declared_path] = version

        engine = (pack.get("deployment_pack") or {}).get("engine")
        artifact_path = os.path.join(os.path.dirname(pack_path), ARTIFACT_FILE)
        if not os.path.exists(artifact_path):
            if engine == "container":
                errors.append(
                    f"{pack_path}: engine 'container' requires a sibling {ARTIFACT_FILE} "
                    "(the runner image contract)"
                )
            continue

        artifact = _load_json(artifact_path, errors)
        if artifact is None:
            continue
        try:
            validator.validate_artifact_manifest(artifact, registry_host_allowlist=allowlist)
        except InvalidMetadataSchemaError as exc:
            errors.append(f"{artifact_path}: invalid artifact manifest: {exc}")
            continue
        if version and artifact.get("version") != version:
            errors.append(
                f"{artifact_path}: artifact version '{artifact.get('version')}' != "
                f"module version '{version}' in {PACK_FILE} (bump both on release)"
            )

    for bp_path in _find_manifests(root, BLUEPRINT_FILE):
        manifest = _load_json(bp_path, errors)
        if manifest is None:
            continue
        try:
            blueprint = validate_blueprint_manifest(manifest)
        except BlueprintManifestValidationError as exc:
            for issue in exc.issues:
                errors.append(f"{bp_path}: {issue.code} at {issue.path}: {issue.message}")
            continue

        for mod in blueprint.modules:
            ref, pinned = mod.module, mod.version
            if ref not in pack_versions:
                warnings.append(
                    f"{bp_path}: module ref '{ref}' is not a pack in this repo "
                    "(external source — pin cannot be checked here)"
                )
                continue
            if pack_versions[ref] != pinned:
                errors.append(
                    f"{bp_path}: module '{mod.id}' pins '{ref}' at version '{pinned}' "
                    f"but the repo's pack declares '{pack_versions[ref]}'"
                )

    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", help="Path to the content repo checkout")
    parser.add_argument(
        "--registry-allowlist",
        default=",".join(DEFAULT_ALLOWLIST),
        help="Comma-separated registry hosts artifact images may come from "
        f"(default: {','.join(DEFAULT_ALLOWLIST)})",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.root):
        print(f"error: '{args.root}' is not a directory", file=sys.stderr)
        return 2
    allowlist = [h.strip() for h in args.registry_allowlist.split(",") if h.strip()]

    errors, warnings = validate_content_repo(args.root, allowlist)
    for warning in warnings:
        print(f"WARN  {warning}")
    for error in errors:
        print(f"ERROR {error}")
    packs = len(_find_manifests(args.root, PACK_FILE))
    blueprints = len(_find_manifests(args.root, BLUEPRINT_FILE))
    print(
        f"{packs} pack(s), {blueprints} blueprint(s): "
        f"{len(errors)} error(s), {len(warnings)} warning(s)"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
