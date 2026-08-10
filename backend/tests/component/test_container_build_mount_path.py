"""Regression test: _build_engine_and_ctx must honor the artifact manifest's
``state.mount_path``.

The phased roksbnkctl artifacts mount their persistent workspace at ``/work``
(``ROKSBNKCTL_HOME=/work/.roksbnkctl``). A prior bug constructed ContainerEngine
without passing ``mount_path``, so it defaulted to ``/state`` while roksbnkctl
read/wrote ``/work`` (ephemeral) — ``init`` state was lost and the next phase
failed with "workspace not initialised". These tests pin the plumbing.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from services.execution.container_engine import DEFAULT_MOUNT_PATH
from tests.factories import ModuleLibraryFactory, ProjectModuleFactory


def _container_module(db):
    lib = ModuleLibraryFactory(
        db,
        category="container",
        module_source_kind="artifact",
        execution_engine="container",
    )
    return ProjectModuleFactory(db, library_module=lib)


def _build(db, module, manifest):
    """Drive _build_engine_and_ctx with all I/O collaborators mocked."""
    from tasks import container_tasks

    wm = MagicMock()
    wm.artifact_workspace_key.return_value = "bp-1"
    wm.ensure_artifact_workspace.return_value = "/app/workspaces/1/bp-1"
    wm.artifact_workspace_host_path.return_value = "/host/1/bp-1"
    wm.artifact_workspace_volume.return_value = "bnk-forge_workspace_data"
    wm.artifact_workspace_subpath.return_value = "1/bp-1"

    with (
        patch.object(container_tasks, "_artifact_manifest", return_value=manifest),
        patch.object(container_tasks, "_registry_host", return_value="ghcr.io"),
        patch.object(container_tasks, "_resolve_runner", return_value=MagicMock()),
        patch("services.workspace_manager.WorkspaceManager", return_value=wm),
        patch(
            "services.execution.container_run_secrets.resolve_pull_authfile_for_module",
            return_value=None,
        ),
        patch("services.credentials_service.get_cloud_credentials_env", return_value={}),
    ):
        return container_tasks._build_engine_and_ctx(db, module)


@pytest.mark.component
class TestBuildEngineMountPath:
    def test_engine_uses_manifest_state_mount_path(self, db):
        module = _container_module(db)
        manifest = {"state": {"scope": "deployment", "mount_path": "/work"}}
        engine, _ctx = _build(db, module, manifest)
        assert engine.mount_path == "/work"

    def test_engine_defaults_mount_path_when_unset(self, db):
        module = _container_module(db)
        manifest = {"state": {"scope": "deployment"}}
        engine, _ctx = _build(db, module, manifest)
        assert engine.mount_path == DEFAULT_MOUNT_PATH
