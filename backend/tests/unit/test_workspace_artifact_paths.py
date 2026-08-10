"""Unit tests for artifact-component workspace pathing (container engine).

These exercise WorkspaceManager's artifact helpers, which key by
(project_id, module_id) ints and need no DB. The host-path helper is what the
DockerRunner bind-mounts, so its keying and override behavior are locked here.
"""

import os
from unittest.mock import MagicMock

import pytest

from services.workspace_manager import WorkspaceManager


@pytest.fixture()
def wm():
    # DB is unused by the artifact path helpers; a stub is sufficient.
    return WorkspaceManager(MagicMock())


@pytest.mark.unit
class TestArtifactWorkspacePaths:
    def test_in_container_path_keys_by_project_and_module(self, wm):
        path = wm.artifact_workspace_path(7, 42)
        assert path == "/app/workspaces/7/42"

    def test_apply_and_destroy_resolve_same_directory(self, wm):
        # Same (project_id, module_id) -> same durable directory, so destroy
        # reads back the state apply wrote.
        assert wm.artifact_workspace_path(7, 42) == wm.artifact_workspace_path(7, 42)

    def test_host_path_uses_default_volume_base(self, wm, monkeypatch):
        monkeypatch.delenv("WORKSPACE_HOST_BASE", raising=False)
        host = wm.artifact_workspace_host_path(7, 42)
        assert host == os.path.join(WorkspaceManager.DEFAULT_VOLUME_HOST_BASE, "7", "42")

    def test_host_path_honors_override_env(self, wm, monkeypatch):
        monkeypatch.setenv("WORKSPACE_HOST_BASE", "/custom/vol/_data")
        host = wm.artifact_workspace_host_path(7, 42)
        assert host == "/custom/vol/_data/7/42"

    def test_host_path_differs_from_in_container_path(self, wm, monkeypatch):
        # Sibling-container bind mounts must use the host path, not BASE_PATH.
        monkeypatch.delenv("WORKSPACE_HOST_BASE", raising=False)
        assert wm.artifact_workspace_host_path(7, 42) != wm.artifact_workspace_path(7, 42)

    def test_ensure_creates_directory(self, wm, tmp_path, monkeypatch):
        monkeypatch.setattr(WorkspaceManager, "BASE_PATH", str(tmp_path / "ws"))
        path = wm.ensure_artifact_workspace(3, 9)
        assert os.path.isdir(path)
        assert path == str(tmp_path / "ws" / "3" / "9")

    def test_ensure_is_idempotent(self, wm, tmp_path, monkeypatch):
        monkeypatch.setattr(WorkspaceManager, "BASE_PATH", str(tmp_path / "ws"))
        first = wm.ensure_artifact_workspace(3, 9)
        # Drop a marker to prove the directory is reused, not recreated.
        marker = os.path.join(first, "state.json")
        with open(marker, "w") as handle:
            handle.write("{}")
        second = wm.ensure_artifact_workspace(3, 9)
        assert second == first
        assert os.path.isfile(marker)


@pytest.mark.unit
class TestArtifactWorkspaceScope:
    def _module(self, module_id, path):
        m = MagicMock()
        m.id = module_id
        m.path_in_project = path
        return m

    def test_component_scope_keys_by_module_id(self, wm):
        m = self._module(42, "imported-blueprint/78/roksbnkctl/cluster")
        # Default/component scope isolates per module.
        assert wm.artifact_workspace_key(m, None) == "42"
        assert wm.artifact_workspace_key(m, "component") == "42"

    def test_deployment_scope_shares_group_across_phase_modules(self, wm):
        cluster = self._module(42, "imported-blueprint/78/roksbnkctl/cluster")
        bnk = self._module(43, "imported-blueprint/78/roksbnkctl/bnk")
        # Both phase modules of blueprint deployment 78 share one workspace key.
        assert wm.artifact_workspace_key(cluster, "deployment") == "bp-78"
        assert wm.artifact_workspace_key(bnk, "deployment") == "bp-78"
        assert wm.artifact_workspace_subpath(5, "bp-78") == "5/bp-78"

    def test_deployment_scope_falls_back_to_module_when_no_group(self, wm):
        # A standalone module (not blueprint-deployed) has no group → per-module.
        m = self._module(9, "standalone/roksbnkctl/cluster")
        assert wm.artifact_workspace_key(m, "deployment") == "9"
