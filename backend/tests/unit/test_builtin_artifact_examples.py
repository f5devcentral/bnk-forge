"""Lock the seeded builtin artifact example(s) to the SEAMS validator.

The example bnkforge.artifact.json under backend/data/artifacts/ must always
validate against the live ModuleMetadataValidator with the default registry
host allowlist — otherwise a shipped example would advertise an invalid
artifact to operators.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.module_metadata import ModuleMetadataValidator

ARTIFACTS_DIR = Path(__file__).resolve().parents[2] / "data" / "artifacts"

# The default registry host allowlist (defaults_service.SYSTEM_DEFAULTS).
DEFAULT_ALLOWLIST = ["ghcr.io", "quay.io", "docker.io", "registry.k8s.io"]


def _artifact_manifests() -> list[Path]:
    return sorted(ARTIFACTS_DIR.rglob("bnkforge.artifact.json"))


def test_at_least_one_builtin_artifact_example_exists():
    assert _artifact_manifests(), f"no builtin artifact examples found under {ARTIFACTS_DIR}"


@pytest.mark.parametrize("manifest_path", _artifact_manifests(), ids=lambda p: p.parent.name)
def test_builtin_artifact_example_validates(manifest_path: Path):
    manifest = json.loads(manifest_path.read_text())
    validator = ModuleMetadataValidator()
    graph = validator.validate_artifact_manifest(
        manifest, registry_host_allowlist=DEFAULT_ALLOWLIST
    )
    assert graph["root"] == f"{manifest['name']}@{manifest['version']}"


def test_roksbnkctl_runner_example_pins_a_digest():
    """The flagship example must pin an immutable sha256 digest, not a tag."""
    path = ARTIFACTS_DIR / "roksbnkctl-tools-runner" / "bnkforge.artifact.json"
    manifest = json.loads(path.read_text())
    digest = manifest["container_image"]["digest"]
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64
