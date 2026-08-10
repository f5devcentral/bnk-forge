"""Component tests for AnsibleEngine bounded semantics."""

from unittest.mock import MagicMock, patch

import pytest

from services.execution.ansible_engine import AnsibleEngine
from services.execution.ansible_runner import AnsibleRunner
from services.execution.engine_interface import ModuleContext


def _make_ctx(**overrides):
    defaults = dict(
        module_id=1,
        project_id=1,
        path="packs/ansible/web",
        category="infra",
        variables={"env": "dev"},
        credentials_env={},
    )
    defaults.update(overrides)
    return ModuleContext(**defaults)


def _make_module(*, supports_destroy: bool = False, outputs: list | None = None):
    module = MagicMock()
    module.id = 1
    module.project = MagicMock()
    module.library_module = MagicMock()
    module.library_module.pack_manifest = {
        "deployment_pack": {
            "engine": "ansible",
            "runner_profile": "ansible-default",
            "working_directory": ".",
            "entrypoints": {
                "playbook": "apply.yml",
                "outputs_file": "outputs.json",
                **({"destroy_playbook": "destroy.yml"} if supports_destroy else {}),
            },
            "lifecycle": {
                "supports_init": True,
                "supports_plan": True,
                "supports_apply": True,
                "supports_destroy": supports_destroy,
                "supports_refresh": True,
                "supports_drift": False,
            },
        },
        "outputs": {
            "key_outputs": outputs or [],
        },
    }
    return module


def _db_factory_with(module):
    db = MagicMock()
    factory = MagicMock(return_value=db)
    engine = AnsibleEngine(factory, runner=AnsibleRunner())
    return engine, db


class TestAnsibleEngineApply:
    def test_apply_reads_outputs_artifact_when_declared(self):
        module = _make_module(outputs=[{"name": "endpoint"}])
        engine, db = _db_factory_with(module)
        pack_cfg = engine._runner.parse_pack_config(module.library_module.pack_manifest)

        with patch.object(engine, "_load_module", return_value=module), \
            patch.object(engine, "_prepare_execution_paths", return_value=(
                pack_cfg,
                "/tmp/work",
                "/tmp/work/apply.yml",
            )):
            with patch.object(engine._runner, "run_apply", return_value=MagicMock(returncode=0, stdout="ok", stderr="")), \
                patch.object(engine._runner, "read_outputs_artifact", return_value={"endpoint": "https://svc"}) as read_outputs:
                result = engine.apply(_make_ctx())

        assert result.success is True
        assert result.outputs == {"endpoint": "https://svc"}
        read_outputs.assert_called_once_with(
            work_dir="/tmp/work",
            outputs_file="outputs.json",
            required=True,
        )
        db.close.assert_called_once()

    def test_apply_fails_when_outputs_required_but_no_outputs_file(self):
        module = _make_module(outputs=[{"name": "endpoint"}])
        module.library_module.pack_manifest["deployment_pack"]["entrypoints"].pop("outputs_file")
        engine, _db = _db_factory_with(module)
        pack_cfg = engine._runner.parse_pack_config(module.library_module.pack_manifest)

        with patch.object(engine, "_load_module", return_value=module), \
            patch.object(engine, "_prepare_execution_paths", return_value=(
                pack_cfg,
                "/tmp/work",
                "/tmp/work/apply.yml",
            )):
            with patch.object(engine._runner, "run_apply", return_value=MagicMock(returncode=0, stdout="ok", stderr="")):
                result = engine.apply(_make_ctx())

        assert result.success is False
        assert "requires outputs artifact" in (result.error_message or "")


class TestAnsibleEngineDestroy:
    def test_destroy_rejected_when_not_supported(self):
        module = _make_module(supports_destroy=False)
        engine, _db = _db_factory_with(module)
        pack_cfg = engine._runner.parse_pack_config(module.library_module.pack_manifest)

        with patch.object(engine, "_load_module", return_value=module), \
            patch.object(engine, "_prepare_execution_paths", return_value=(
                pack_cfg,
                "/tmp/work",
                "/tmp/work/apply.yml",
            )):
            result = engine.destroy(_make_ctx())

        assert result.success is False
        assert "supports_destroy=false" in (result.error_message or "")

    def test_destroy_runs_destroy_playbook_when_supported(self):
        module = _make_module(supports_destroy=True)
        engine, _db = _db_factory_with(module)
        pack_cfg = engine._runner.parse_pack_config(module.library_module.pack_manifest)

        with patch.object(engine, "_load_module", return_value=module), \
            patch.object(engine, "_prepare_execution_paths", return_value=(
                pack_cfg,
                "/tmp/work",
                "/tmp/work/apply.yml",
            )), \
            patch("services.execution.ansible_engine.os.path.exists", return_value=True):
            with patch.object(engine._runner, "resolve_workspace_subpath", return_value="/tmp/work/destroy.yml"), \
                patch.object(engine._runner, "run_apply", return_value=MagicMock(returncode=0, stdout="destroyed", stderr="")) as run_apply:
                result = engine.destroy(_make_ctx())

        assert result.success is True
        run_apply.assert_called_once_with(
            work_dir="/tmp/work",
            playbook_path="/tmp/work/destroy.yml",
            variables={"env": "dev"},
        )


class TestAnsibleEngineGetOutputs:
    def test_get_outputs_raises_when_required_outputs_artifact_missing(self):
        module = _make_module(outputs=[{"name": "endpoint"}])
        engine, _db = _db_factory_with(module)
        pack_cfg = engine._runner.parse_pack_config(module.library_module.pack_manifest)

        with patch.object(engine, "_load_module", return_value=module), \
            patch.object(engine, "_prepare_execution_paths", return_value=(
                pack_cfg,
                "/tmp/work",
                "/tmp/work/apply.yml",
            )), \
            patch.object(engine._runner, "read_outputs_artifact", side_effect=FileNotFoundError("missing outputs")):
            with pytest.raises(FileNotFoundError, match="missing outputs"):
                engine.get_outputs(_make_ctx())

    def test_get_outputs_returns_empty_when_outputs_optional_and_missing(self):
        module = _make_module(outputs=[])
        engine, _db = _db_factory_with(module)
        pack_cfg = engine._runner.parse_pack_config(module.library_module.pack_manifest)

        with patch.object(engine, "_load_module", return_value=module), \
            patch.object(engine, "_prepare_execution_paths", return_value=(
                pack_cfg,
                "/tmp/work",
                "/tmp/work/apply.yml",
            )), \
            patch.object(engine._runner, "read_outputs_artifact", side_effect=FileNotFoundError("missing outputs")):
            result = engine.get_outputs(_make_ctx())

        assert result == {}
