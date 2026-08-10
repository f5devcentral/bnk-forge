"""
Unit tests for _build_cli_context secret materialization wiring.

Verifies that _build_cli_context calls SecretsService.prepare_secrets_for_execution
with the correct workspace path, resolves bnk_far_archive / bnk_jwt from the
returned mapping, and passes workspace-relative paths to the renderer.

All external I/O (DB, SecretsService, credentials) is mocked so no real crypto
or filesystem access occurs.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml


@pytest.mark.unit
class TestBuildCliContextSecretMaterialization:
    """Test that _build_cli_context wires SecretsService correctly."""

    def _make_module(self, project_id: int = 42, module_path: str = "cli-bnkctl/awsbnkctl/bnk-demo") -> MagicMock:
        """Build a minimal mock ProjectModule."""
        module = MagicMock()
        module.id = 1
        module.project_id = project_id
        module.path_in_project = module_path
        module.variables = {}
        module.variable_overrides = {}
        module.library_module = MagicMock()
        module.library_module.path = module_path
        module.library_module.variables_schema = []
        module.library_module.category = "cli-bnkctl"
        module.project = MagicMock()
        module.project.id = project_id
        module.project.name = "test-project"
        return module

    def _mock_db(self) -> MagicMock:
        return MagicMock()

    def test_prepare_secrets_called_with_correct_workspace(self):
        """SecretsService.prepare_secrets_for_execution is called with the engine workspace path."""
        project_id = 42
        expected_workspace = f"/app/projects/{project_id}/awsbnkctl"

        module = self._make_module(project_id=project_id)
        db = self._mock_db()

        with (
            patch("tasks.cli_tasks.SecretsService") as MockSecrets,
            patch("tasks.cli_tasks.get_cloud_credentials_env", return_value={}),
        ):
            mock_svc = MockSecrets.return_value
            # No secrets — dry-run path
            mock_svc.prepare_secrets_for_execution.return_value = ({}, [])

            from tasks.cli_tasks import _build_cli_context
            _build_cli_context(db, module)

            mock_svc.prepare_secrets_for_execution.assert_called_once_with(
                project_id=project_id,
                work_dir=expected_workspace,
                module_path="cli-bnkctl/awsbnkctl/bnk-demo",
            )

    def test_no_bnk_block_when_secrets_absent(self):
        """cluster_yaml has no bnk: block when SecretsService returns no secrets."""
        module = self._make_module()
        db = self._mock_db()

        with (
            patch("tasks.cli_tasks.SecretsService") as MockSecrets,
            patch("tasks.cli_tasks.get_cloud_credentials_env", return_value={}),
        ):
            MockSecrets.return_value.prepare_secrets_for_execution.return_value = ({}, [])

            from tasks.cli_tasks import _build_cli_context
            ctx = _build_cli_context(db, module)

        cluster_yaml = ctx.variables.get("cluster_yaml", "")
        config = yaml.safe_load(cluster_yaml)
        assert "bnk" not in config

    def test_bnk_block_present_when_both_secrets_returned(self):
        """cluster_yaml includes bnk: block when SecretsService returns both mapped paths."""
        project_id = 7
        workspace = f"/app/projects/{project_id}/awsbnkctl"
        far_abs = f"{workspace}/secrets/cne_pull_64.json"
        jwt_abs = f"{workspace}/secrets/license.jwt"

        module = self._make_module(project_id=project_id)
        db = self._mock_db()

        with (
            patch("tasks.cli_tasks.SecretsService") as MockSecrets,
            patch("tasks.cli_tasks.get_cloud_credentials_env", return_value={}),
        ):
            MockSecrets.return_value.prepare_secrets_for_execution.return_value = (
                {"bnk_far_archive": far_abs, "bnk_jwt": jwt_abs},
                [far_abs, jwt_abs],
            )

            from tasks.cli_tasks import _build_cli_context
            ctx = _build_cli_context(db, module)

        cluster_yaml = ctx.variables.get("cluster_yaml", "")
        config = yaml.safe_load(cluster_yaml)

        assert "bnk" in config
        assert config["bnk"]["farArchive"] == "./secrets/cne_pull_64.json"
        assert config["bnk"]["jwt"] == "./secrets/license.jwt"

    def test_bnk_paths_are_workspace_relative(self):
        """Paths in the bnk: block are relative to the workspace (start with ./)."""
        project_id = 99
        workspace = f"/app/projects/{project_id}/awsbnkctl"

        module = self._make_module(project_id=project_id)
        db = self._mock_db()

        with (
            patch("tasks.cli_tasks.SecretsService") as MockSecrets,
            patch("tasks.cli_tasks.get_cloud_credentials_env", return_value={}),
        ):
            MockSecrets.return_value.prepare_secrets_for_execution.return_value = (
                {
                    "bnk_far_archive": f"{workspace}/secrets/cne_pull_64.json",
                    "bnk_jwt": f"{workspace}/secrets/license.jwt",
                },
                [],
            )

            from tasks.cli_tasks import _build_cli_context
            ctx = _build_cli_context(db, module)

        config = yaml.safe_load(ctx.variables["cluster_yaml"])
        assert config["bnk"]["farArchive"].startswith("./")
        assert config["bnk"]["jwt"].startswith("./")

    def test_graceful_when_prepare_secrets_raises(self):
        """Context is still built (without bnk: block) when SecretsService raises."""
        module = self._make_module()
        db = self._mock_db()

        with (
            patch("tasks.cli_tasks.SecretsService") as MockSecrets,
            patch("tasks.cli_tasks.get_cloud_credentials_env", return_value={}),
        ):
            MockSecrets.return_value.prepare_secrets_for_execution.side_effect = RuntimeError("DB exploded")

            from tasks.cli_tasks import _build_cli_context
            ctx = _build_cli_context(db, module)

        # Must not raise; cluster_yaml must still be rendered without bnk: block
        cluster_yaml = ctx.variables.get("cluster_yaml", "")
        config = yaml.safe_load(cluster_yaml)
        assert config is not None
        assert "bnk" not in config

    def test_only_far_archive_no_bnk_block(self):
        """When only bnk_far_archive is returned (no jwt), bnk: block is still absent."""
        project_id = 5
        workspace = f"/app/projects/{project_id}/awsbnkctl"

        module = self._make_module(project_id=project_id)
        db = self._mock_db()

        with (
            patch("tasks.cli_tasks.SecretsService") as MockSecrets,
            patch("tasks.cli_tasks.get_cloud_credentials_env", return_value={}),
        ):
            MockSecrets.return_value.prepare_secrets_for_execution.return_value = (
                {"bnk_far_archive": f"{workspace}/secrets/cne_pull_64.json"},
                [],
            )

            from tasks.cli_tasks import _build_cli_context
            ctx = _build_cli_context(db, module)

        config = yaml.safe_load(ctx.variables["cluster_yaml"])
        assert "bnk" not in config
