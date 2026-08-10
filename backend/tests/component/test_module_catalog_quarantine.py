"""Component tests for the module-catalog quarantine path.

When a `bnkforge.pack.json` fails validation during sync, the row should land
with `is_active=False` + `validation_error` populated rather than being
silently dropped from `discover_modules` (which is what happens today).
"""

import json
from pathlib import Path

from services.module_catalog_service import discover_modules


def _write_pack(dir_path: Path, manifest: dict) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "bnkforge.pack.json").write_text(json.dumps(manifest))


def _valid_pack(name: str = "vpc") -> dict:
    return {
        "schema_version": 1,
        "module": {
            "name": name,
            "path": f"infra/aws/{name}",
            "version": "1.0.0",
            "category": "infra",
            "description": "Valid test module",
        },
        "deployment_pack": {
            "engine": "opentofu",
            "runner_profile": "opentofu-default",
            "working_directory": ".",
            "lifecycle": {
                "supports_init": True,
                "supports_plan": True,
                "supports_apply": True,
                "supports_destroy": True,
                "supports_refresh": True,
                "supports_drift": True,
            },
            "entrypoints": {"module_root": "."},
        },
        "inputs": {"required": [], "optional": []},
        "outputs": {"key_outputs": []},
    }


def _invalid_pack_runner_profile(name: str = "cne-irsa") -> dict:
    pack = _valid_pack(name)
    pack["deployment_pack"]["runner_profile"] = "aws-default"
    return pack


class TestQuarantineDiscoverModules:
    def test_invalid_pack_lands_quarantine_stub(self, tmp_path: Path):
        _write_pack(tmp_path / "aws" / "cne-irsa", _invalid_pack_runner_profile("cne-irsa"))

        modules = discover_modules(str(tmp_path), "infra")

        assert len(modules) == 1
        m = modules[0]
        assert m["name"] == "cne-irsa"
        assert m["path"] == "aws/cne-irsa"
        assert m["is_active"] is False
        assert m["validation_error"] is not None
        assert "aws-default" in m["validation_error"]
        assert "QUARANTINED" in m["description"]
        assert "quarantined" in m["tags"]

    def test_valid_pack_unaffected(self, tmp_path: Path):
        _write_pack(tmp_path / "aws" / "vpc", _valid_pack("vpc"))

        modules = discover_modules(str(tmp_path), "infra")

        assert len(modules) == 1
        m = modules[0]
        assert m["name"] == "vpc"
        assert m["is_active"] is True
        assert m["validation_error"] is None
        assert "QUARANTINED" not in (m.get("description") or "")

    def test_mixed_valid_and_invalid(self, tmp_path: Path):
        _write_pack(tmp_path / "aws" / "vpc", _valid_pack("vpc"))
        _write_pack(tmp_path / "aws" / "cne-irsa", _invalid_pack_runner_profile("cne-irsa"))

        modules = discover_modules(str(tmp_path), "infra")

        assert len(modules) == 2
        by_name = {m["name"]: m for m in modules}
        assert by_name["vpc"]["is_active"] is True
        assert by_name["vpc"]["validation_error"] is None
        assert by_name["cne-irsa"]["is_active"] is False
        assert by_name["cne-irsa"]["validation_error"] is not None

    def test_unquarantine_resets_validation_error_field(self, tmp_path: Path):
        """A previously-quarantined module that's now valid emits is_active=True + error=None."""
        _write_pack(tmp_path / "aws" / "vpc", _valid_pack("vpc"))

        modules = discover_modules(str(tmp_path), "infra")

        assert modules[0]["is_active"] is True
        assert modules[0]["validation_error"] is None
