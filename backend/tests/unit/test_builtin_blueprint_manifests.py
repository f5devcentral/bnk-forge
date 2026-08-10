"""Unit tests: all bundled forge-blueprint.json manifests validate against BlueprintManifest."""

import json
from pathlib import Path

import pytest

from schemas.blueprints import BlueprintManifest

_BLUEPRINTS_DIR = Path(__file__).parent.parent.parent / "data" / "blueprints"
_EXPECTED_COUNT = 13


def _all_manifest_paths() -> list[Path]:
    return sorted(_BLUEPRINTS_DIR.rglob("forge-blueprint.json"))


@pytest.mark.unit
def test_builtin_blueprint_manifest_count():
    """Exactly 13 bundled manifests exist."""
    paths = _all_manifest_paths()
    assert len(paths) == _EXPECTED_COUNT, (
        f"Expected {_EXPECTED_COUNT} bundled forge-blueprint.json files, found {len(paths)}:\n"
        + "\n".join(str(p.relative_to(_BLUEPRINTS_DIR.parent.parent)) for p in paths)
    )


@pytest.mark.unit
@pytest.mark.parametrize("manifest_path", _all_manifest_paths(), ids=lambda p: p.parent.name)
def test_builtin_blueprint_manifest_validates(manifest_path: Path):
    """Each forge-blueprint.json validates against BlueprintManifest without errors."""
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = BlueprintManifest.model_validate(raw)
    # Basic sanity checks
    assert manifest.blueprint.id == manifest_path.parent.name, (
        f"blueprint.id '{manifest.blueprint.id}' must match directory name '{manifest_path.parent.name}'"
    )
    assert manifest.schema_version == 1
    assert len(manifest.modules) >= 1
    for mod in manifest.modules:
        assert mod.version, f"Module '{mod.id}' in {manifest_path.parent.name} must have a version"
