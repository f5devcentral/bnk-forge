"""
Regression tests for #82 — cli-bnkctl destroy must use the APPLIED config.

`run_cli_destroy` built its context with `_build_cli_context`, which re-renders
cluster.yaml from the project's *current* form variables. `BnkctlEngine.destroy`
then overwrote the workspace cluster.yaml with that render before running
`awsbnkctl down -f <cfg>`. If `cluster_name` (or any identity variable) had been
edited after apply, `down` targeted a config that no longer matched
`.awsbnkctl/<name>/state.env` — reporting success while the real EKS cluster
stayed up, unmanaged, and billing.

Two halves are pinned here:
  1. the task layer no longer renders cluster.yaml on the destroy path, and
  2. the engine reads the applied file rather than rewriting it, and refuses
     outright when it is absent instead of fabricating one.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

APPLIED_YAML = """\
apiVersion: bnk.f5.com/v1
kind: ClusterTopology
metadata:
  name: bnk-demo-applied
  region: ap-southeast-2
pattern: external-only
"""


def _make_module(project_id: int = 42) -> MagicMock:
    module = MagicMock()
    module.id = 1
    module.project_id = project_id
    module.path_in_project = "cli-bnkctl/awsbnkctl/bnk-demo"
    # The operator renamed the cluster after apply — this is the trigger.
    module.variables = {"cluster_name": "renamed-after-apply", "region": "ap-southeast-2"}
    module.variable_overrides = {}
    module.library_module = MagicMock()
    module.library_module.path = "cli-bnkctl/awsbnkctl/bnk-demo"
    module.library_module.variables_schema = []
    module.library_module.category = "cli-bnkctl"
    module.project = MagicMock()
    module.project.id = project_id
    module.project.name = "test-project"
    return module


@pytest.mark.unit
class TestBuildCliContextForDestroy:
    def test_destroy_context_does_not_render_cluster_yaml(self):
        """for_destroy=True must leave cluster_yaml unset, so nothing overwrites the applied file."""
        from tasks.cli_tasks import _build_cli_context

        module = _make_module()
        with (
            patch("tasks.cli_tasks.SecretsService") as MockSecrets,
            patch("tasks.cli_tasks.get_cloud_credentials_env", return_value={}),
        ):
            MockSecrets.return_value.prepare_secrets_for_execution.return_value = ({}, [])
            ctx = _build_cli_context(MagicMock(), module, for_destroy=True)

        assert "cluster_yaml" not in ctx.variables, (
            "destroy context carried a rendered cluster.yaml — the engine would write it "
            "over the applied config and could target the wrong cluster"
        )

    def test_apply_context_still_renders_cluster_yaml(self):
        """The default path is unchanged: apply/plan still need a fresh render."""
        from tasks.cli_tasks import _build_cli_context

        module = _make_module()
        with (
            patch("tasks.cli_tasks.SecretsService") as MockSecrets,
            patch("tasks.cli_tasks.get_cloud_credentials_env", return_value={}),
        ):
            MockSecrets.return_value.prepare_secrets_for_execution.return_value = ({}, [])
            ctx = _build_cli_context(MagicMock(), module)

        assert "cluster_yaml" in ctx.variables
        doc = yaml.safe_load(ctx.variables["cluster_yaml"])
        assert doc["metadata"]["name"] == "renamed-after-apply"


@pytest.mark.unit
class TestEngineDestroyUsesAppliedConfig:
    def _ctx(self, project_id: int) -> SimpleNamespace:
        return SimpleNamespace(
            module_id=1,
            project_id=project_id,
            path="cli-bnkctl/awsbnkctl/bnk-demo",
            category="cli-bnkctl",
            # What the form says NOW — deliberately different from the applied config.
            variables={"name": "renamed-after-apply", "bnkctl_tool": "awsbnkctl"},
            credentials_env={},
        )

    def _engine(self, tmp_path: Path):
        from services.execution.cli_engine import BnkctlEngine

        engine = BnkctlEngine(MagicMock())
        engine._WORKSPACE_ROOT = str(tmp_path)
        return engine

    def test_destroy_passes_applied_config_untouched(self, tmp_path):
        """`down -f` must receive the applied cluster.yaml, byte-for-byte."""
        engine = self._engine(tmp_path)
        ctx = self._ctx(project_id=42)
        workspace = tmp_path / "42" / "awsbnkctl"
        workspace.mkdir(parents=True)
        cfg = workspace / "cluster.yaml"
        cfg.write_text(APPLIED_YAML)

        captured: dict = {}

        def _fake_run(ctx_, args, env, cwd, on_output=None):
            captured["args"] = args
            return 0, "destroyed"

        with (
            patch.object(engine, "_run_streaming_with_ctx", side_effect=_fake_run),
            patch.object(engine, "_update_stage"),
            patch("services.execution.cli_engine.shutil.which", return_value="/usr/local/bin/awsbnkctl"),
        ):
            result = engine.destroy(ctx)

        assert result.success is True
        assert cfg.read_text() == APPLIED_YAML, (
            "destroy rewrote the applied cluster.yaml — this is the #82 orphaning bug"
        )
        assert str(cfg) in captured["args"]
        # And the config handed to the tool still names the cluster that exists.
        assert yaml.safe_load(cfg.read_text())["metadata"]["name"] == "bnk-demo-applied"

    def test_destroy_refuses_when_applied_config_missing(self, tmp_path):
        """No applied config → refuse. Never fabricate one from current form variables."""
        engine = self._engine(tmp_path)
        ctx = self._ctx(project_id=99)

        with (
            patch.object(engine, "_run_streaming_with_ctx") as mock_run,
            patch.object(engine, "_update_stage"),
            patch("services.execution.cli_engine.shutil.which", return_value="/usr/local/bin/awsbnkctl"),
        ):
            result = engine.destroy(ctx)

        assert result.success is False
        assert "refusing to destroy" in (result.error_message or "")
        mock_run.assert_not_called(), "awsbnkctl down ran without an applied config"
        # Nothing was written in place of the missing config.
        assert not (tmp_path / "99" / "awsbnkctl" / "cluster.yaml").exists()

    def test_explicit_cluster_yaml_restores_a_lost_workspace(self, tmp_path):
        """An operator can hand the applied config back when the workspace is gone.

        Safe only because the destroy context no longer renders one: with
        for_destroy=True nothing populates cluster_yaml from the project form, so
        a value here was set deliberately on the module.
        """
        engine = self._engine(tmp_path)
        ctx = self._ctx(project_id=77)
        ctx.variables["cluster_yaml"] = APPLIED_YAML

        captured: dict = {}

        def _fake_run(ctx_, args, env, cwd, on_output=None):
            captured["args"] = args
            return 0, "destroyed"

        with (
            patch.object(engine, "_run_streaming_with_ctx", side_effect=_fake_run),
            patch.object(engine, "_update_stage"),
            patch("services.execution.cli_engine.shutil.which", return_value="/usr/local/bin/awsbnkctl"),
        ):
            result = engine.destroy(ctx)

        assert result.success is True
        cfg = tmp_path / "77" / "awsbnkctl" / "cluster.yaml"
        assert cfg.read_text() == APPLIED_YAML
        assert str(cfg) in captured["args"]

    def test_form_variables_alone_still_refuse(self, tmp_path):
        """The #82 guarantee: form vars must never be turned into a destroy config."""
        engine = self._engine(tmp_path)
        ctx = self._ctx(project_id=78)
        # Exactly what the old code would have rendered from -- and no cluster_yaml.
        ctx.variables["cluster_name"] = "renamed-after-apply"

        with (
            patch.object(engine, "_run_streaming_with_ctx") as mock_run,
            patch.object(engine, "_update_stage"),
            patch("services.execution.cli_engine.shutil.which", return_value="/usr/local/bin/awsbnkctl"),
        ):
            result = engine.destroy(ctx)

        assert result.success is False
        mock_run.assert_not_called()
        assert not (tmp_path / "78" / "awsbnkctl" / "cluster.yaml").exists()

    def test_applied_cluster_name_read_from_config(self, tmp_path):
        from services.execution.cli_engine import BnkctlEngine

        cfg = tmp_path / "cluster.yaml"
        cfg.write_text(APPLIED_YAML)
        assert BnkctlEngine._applied_cluster_name(cfg) == "bnk-demo-applied"

    def test_applied_cluster_name_tolerates_malformed_config(self, tmp_path):
        """A malformed config must not block a destroy — the tool still gets the path."""
        from services.execution.cli_engine import BnkctlEngine

        cfg = tmp_path / "cluster.yaml"
        cfg.write_text("{{ not yaml at all")
        assert BnkctlEngine._applied_cluster_name(cfg) is None
