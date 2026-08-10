"""Unit tests for scripts/validate_catalog_content.py (standalone content-repo validator)."""
import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "validate_catalog_content.py"
_spec = importlib.util.spec_from_file_location("validate_catalog_content", _SCRIPT)
vcc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vcc)

_DIGEST = "sha256:" + "a" * 64


def _pack_manifest(path: str, version: str = "1.0.0", engine: str = "container") -> dict:
    return {
        "schema_version": 1,
        "module": {
            "name": path.rsplit("/", 1)[-1],
            "path": path,
            "version": version,
            "category": "bnk",
            "description": "test pack",
        },
        "deployment_pack": {
            "engine": engine,
            "runner_profile": "container-default",
            "working_directory": ".",
            "lifecycle": {
                "supports_init": True,
                "supports_plan": False,
                "supports_apply": True,
                "supports_destroy": True,
                "supports_refresh": False,
                "supports_drift": False,
            },
            "entrypoints": {},
        },
        "inputs": {"required": [], "optional": []},
        "outputs": {"key_outputs": []},
    }


def _artifact_manifest(version: str = "1.0.0") -> dict:
    return {
        "schema_version": 1,
        "name": "test-runner",
        "version": version,
        "kind": "container_image",
        "container_image": {
            "registry_host": "ghcr.io",
            "repository": "example/test-runner",
            "digest": _DIGEST,
        },
        "lifecycle": {"supports_apply": True, "supports_destroy": True},
        "state": {"mount_path": "/state"},
        "execution": {"engine": "container"},
        "steps": {
            "apply": [{"name": "apply", "args": ["testctl", "apply"], "timeout_seconds": 60}],
            "destroy": [{"name": "destroy", "args": ["testctl", "destroy"], "timeout_seconds": 60}],
        },
    }


def _blueprint_manifest(module_path: str, pinned: str = "1.0.0") -> dict:
    return {
        "schema_version": 1,
        "blueprint": {
            "id": "test-bp",
            "version": "1.0.0",
            "name": "Test blueprint",
            "description": "test",
        },
        "modules": [{"id": "m1", "module": module_path, "version": pinned}],
        "inputs": {"required": [], "optional": []},
    }


def _write(root: Path, rel: str, data: dict) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data))


@pytest.fixture()
def valid_repo(tmp_path):
    _write(tmp_path, "tools/testctl/bnkforge.pack.json", _pack_manifest("tools/testctl"))
    _write(tmp_path, "tools/testctl/bnkforge.artifact.json", _artifact_manifest())
    _write(tmp_path, "blueprints/demo/forge-blueprint.json", _blueprint_manifest("tools/testctl"))
    return tmp_path


class TestValidateContentRepo:
    def test_validRepo_passesWithNoErrors(self, valid_repo):
        errors, warnings = vcc.validate_content_repo(str(valid_repo), vcc.DEFAULT_ALLOWLIST)
        assert errors == []
        assert warnings == []

    def test_pathMismatch_failsWithPathError(self, tmp_path):
        _write(tmp_path, "tools/testctl/bnkforge.pack.json", _pack_manifest("tools/other"))
        _write(tmp_path, "tools/testctl/bnkforge.artifact.json", _artifact_manifest())
        errors, _ = vcc.validate_content_repo(str(tmp_path), vcc.DEFAULT_ALLOWLIST)
        assert any("does not match its directory" in e for e in errors)

    def test_containerPackWithoutArtifact_failsRequiringArtifact(self, tmp_path):
        _write(tmp_path, "tools/testctl/bnkforge.pack.json", _pack_manifest("tools/testctl"))
        errors, _ = vcc.validate_content_repo(str(tmp_path), vcc.DEFAULT_ALLOWLIST)
        assert any("requires a sibling bnkforge.artifact.json" in e for e in errors)

    def test_floatingTagDigest_failsArtifactValidation(self, tmp_path):
        artifact = _artifact_manifest()
        artifact["container_image"]["digest"] = "latest"
        _write(tmp_path, "tools/testctl/bnkforge.pack.json", _pack_manifest("tools/testctl"))
        _write(tmp_path, "tools/testctl/bnkforge.artifact.json", artifact)
        errors, _ = vcc.validate_content_repo(str(tmp_path), vcc.DEFAULT_ALLOWLIST)
        assert any("invalid artifact manifest" in e for e in errors)

    def test_disallowedRegistryHost_failsAllowlist(self, tmp_path):
        artifact = _artifact_manifest()
        artifact["container_image"]["registry_host"] = "evil.example.com"
        _write(tmp_path, "tools/testctl/bnkforge.pack.json", _pack_manifest("tools/testctl"))
        _write(tmp_path, "tools/testctl/bnkforge.artifact.json", artifact)
        errors, _ = vcc.validate_content_repo(str(tmp_path), vcc.DEFAULT_ALLOWLIST)
        assert any("invalid artifact manifest" in e for e in errors)

    def test_versionMismatch_failsBumpBothDiscipline(self, tmp_path):
        _write(tmp_path, "tools/testctl/bnkforge.pack.json", _pack_manifest("tools/testctl", version="1.0.1"))
        _write(tmp_path, "tools/testctl/bnkforge.artifact.json", _artifact_manifest(version="1.0.0"))
        errors, _ = vcc.validate_content_repo(str(tmp_path), vcc.DEFAULT_ALLOWLIST)
        assert any("bump both on release" in e for e in errors)

    def test_blueprintPinMismatch_failsPinCheck(self, valid_repo):
        _write(
            valid_repo,
            "blueprints/demo/forge-blueprint.json",
            _blueprint_manifest("tools/testctl", pinned="9.9.9"),
        )
        errors, _ = vcc.validate_content_repo(str(valid_repo), vcc.DEFAULT_ALLOWLIST)
        assert any("pins 'tools/testctl' at version '9.9.9'" in e for e in errors)

    def test_blueprintExternalRef_warnsOnly(self, tmp_path):
        _write(tmp_path, "blueprints/demo/forge-blueprint.json", _blueprint_manifest("tools/elsewhere"))
        errors, warnings = vcc.validate_content_repo(str(tmp_path), vcc.DEFAULT_ALLOWLIST)
        assert errors == []
        assert any("not a pack in this repo" in w for w in warnings)
