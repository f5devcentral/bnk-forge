"""Component tests for governed ansible runner contract."""

import json
import os
import stat
from unittest.mock import MagicMock, patch

import pytest

from services.execution.ansible_runner import (
    GOVERNED_RUNNER_PROFILE,
    AnsibleManifestContractError,
    AnsibleRunner,
)


def _valid_manifest() -> dict:
    return {
        "schema_version": 1,
        "deployment_pack": {
            "engine": "ansible",
            "runner_profile": GOVERNED_RUNNER_PROFILE,
            "working_directory": ".",
            "entrypoints": {
                "playbook": "apply.yml",
                "destroy_playbook": "destroy.yml",
                "outputs_file": "outputs.json",
            },
            "lifecycle": {
                "supports_init": True,
                "supports_plan": True,
                "supports_apply": True,
                "supports_destroy": True,
                "supports_refresh": True,
                "supports_drift": False,
            },
        },
    }


class TestParsePackConfig:
    def test_accepts_valid_ansible_manifest(self):
        runner = AnsibleRunner()
        cfg = runner.parse_pack_config(_valid_manifest())

        assert cfg.playbook == "apply.yml"
        assert cfg.destroy_playbook == "destroy.yml"
        assert cfg.outputs_file == "outputs.json"
        assert cfg.supports_destroy is True

    def test_rejects_non_governed_runner_profile(self):
        runner = AnsibleRunner()
        manifest = _valid_manifest()
        manifest["deployment_pack"]["runner_profile"] = "custom"

        with pytest.raises(AnsibleManifestContractError, match="runner_profile"):
            runner.parse_pack_config(manifest)

    def test_rejects_disallowed_hook_keys(self):
        runner = AnsibleRunner()
        manifest = _valid_manifest()
        manifest["deployment_pack"]["hooks"] = ["echo hi"]

        with pytest.raises(AnsibleManifestContractError, match="not allowed"):
            runner.parse_pack_config(manifest)

    def test_rejects_destroy_enabled_without_destroy_playbook(self):
        runner = AnsibleRunner()
        manifest = _valid_manifest()
        manifest["deployment_pack"]["entrypoints"].pop("destroy_playbook")

        with pytest.raises(AnsibleManifestContractError, match="supports_destroy=true"):
            runner.parse_pack_config(manifest)


class TestPathAndOutputs:
    def test_resolve_workspace_subpath_blocks_escape(self, tmp_path):
        runner = AnsibleRunner()
        with pytest.raises(AnsibleManifestContractError, match="escapes workspace"):
            runner.resolve_workspace_subpath(str(tmp_path), "../outside.yml")

    def test_read_outputs_artifact_required_missing_raises(self, tmp_path):
        runner = AnsibleRunner()
        with pytest.raises(FileNotFoundError, match="outputs artifact"):
            runner.read_outputs_artifact(
                work_dir=str(tmp_path),
                outputs_file="out.json",
                required=True,
            )

    def test_read_outputs_artifact_optional_missing_returns_empty(self, tmp_path):
        runner = AnsibleRunner()
        result = runner.read_outputs_artifact(
            work_dir=str(tmp_path),
            outputs_file="out.json",
            required=False,
        )
        assert result == {}

    def test_read_outputs_artifact_requires_json_object(self, tmp_path):
        runner = AnsibleRunner()
        out_file = tmp_path / "out.json"
        out_file.write_text(json.dumps(["not", "object"]))

        with pytest.raises(ValueError, match="JSON object"):
            runner.read_outputs_artifact(
                work_dir=str(tmp_path),
                outputs_file="out.json",
                required=True,
            )


class TestVarsFileLifecycle:
    def test_run_apply_uses_0600_vars_file_and_cleans_up(self, tmp_path):
        runner = AnsibleRunner()
        observed = {"vars_path": None}

        def _fake_run(command, cwd, capture_output, text, timeout):
            assert cwd == str(tmp_path)
            vars_arg = command[-1]
            assert vars_arg.startswith("@")
            vars_path = vars_arg[1:]
            observed["vars_path"] = vars_path
            assert os.path.exists(vars_path)
            mode = stat.S_IMODE(os.stat(vars_path).st_mode)
            assert mode == 0o600
            return MagicMock(returncode=0, stdout="ok", stderr="")

        with patch("services.execution.ansible_runner.subprocess.run", side_effect=_fake_run):
            result = runner.run_apply(
                work_dir=str(tmp_path),
                playbook_path="/tmp/playbook.yml",
                variables={"token": "secret-value"},
            )

        assert result.returncode == 0
        assert observed["vars_path"] is not None
        assert os.path.exists(observed["vars_path"]) is False

    def test_run_apply_cleans_up_vars_file_when_subprocess_raises(self, tmp_path):
        runner = AnsibleRunner()
        observed = {"vars_path": None}

        def _failing_run(command, cwd, capture_output, text, timeout):
            vars_arg = command[-1]
            observed["vars_path"] = vars_arg[1:]
            raise RuntimeError("boom")

        with patch("services.execution.ansible_runner.subprocess.run", side_effect=_failing_run):
            with pytest.raises(RuntimeError, match="boom"):
                runner.run_apply(
                    work_dir=str(tmp_path),
                    playbook_path="/tmp/playbook.yml",
                    variables={"secret": "value"},
                )

        assert observed["vars_path"] is not None
        assert os.path.exists(observed["vars_path"]) is False
