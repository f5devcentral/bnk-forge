"""
Integration tests for state viewer routes — /api/state.

Covers: module state retrieval, resources, outputs, project module listing,
state refresh (operator), state reset (operator), and RBAC enforcement.
State file I/O is mocked via os.path.exists patches.
Uses FastAPI TestClient with real SQLite DB.
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def sample_state_module(db, all_test_users, sample_project):
    """Create a module library entry and project module for state viewer tests.

    Uses all_test_users so admin/operator/viewer are all in the DB,
    avoiding the UNIQUE constraint conflict with sample_user + all_test_users.
    """
    from tests.factories import ModuleLibraryFactory, ProjectModuleFactory

    lib = ModuleLibraryFactory(
        db,
        name="state-test-vpc",
        description="VPC module for state tests",
        category="networking",
    )
    mod = ProjectModuleFactory(
        db,
        project=sample_project,
        library_module=lib,
    )
    db.commit()
    return {"library": lib, "module": mod, "project": sample_project}


class TestGetModuleState:
    """GET /api/state/module/{module_id}."""

    @patch("routes.state_viewer.os.path.exists", return_value=False)
    def test_get_module_state_no_file(
        self, mock_exists, client, viewer_headers, sample_state_module
    ):
        """Returns exists=False when state file does not exist."""
        module = sample_state_module["module"]

        response = client.get(
            f"/api/state/module/{module.id}",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["exists"] is False
        assert data["resources"] == []
        assert data["outputs"] == {}

    def test_module_not_found(self, client, viewer_headers, sample_state_module):
        """Requesting state for nonexistent module returns 404."""
        response = client.get(
            "/api/state/module/99999",
            headers=viewer_headers,
        )
        assert response.status_code == 404

    @patch("routes.state_viewer.is_state_encrypted", return_value=True)
    @patch("routes.state_viewer.decrypt_state_async")
    @patch("routes.state_viewer.get_state_metadata", return_value={"serial": 1})
    @patch("routes.state_viewer.os.path.exists", return_value=True)
    def test_get_module_state_fallback_normalizes_outputs(
        self,
        mock_exists,
        mock_metadata,
        mock_decrypt,
        mock_is_encrypted,
        client,
        viewer_headers,
        sample_state_module,
        db,
    ):
        """Encrypted fallback path returns normalized DB outputs, not stale ~/.ssh metadata."""
        module = sample_state_module["module"]
        module.outputs = {
            "infrastructure_private_key_path": "~/.ssh/vanished.pem",
            "jumphost_ssh_command": "ssh -i ~/.ssh/vanished.pem ubuntu@1.2.3.4",
        }
        db.commit()

        async def _decrypt_fail(*_args, **_kwargs):
            return False, {"error": "cannot decrypt"}

        mock_decrypt.side_effect = _decrypt_fail

        response = client.get(f"/api/state/module/{module.id}", headers=viewer_headers)
        assert response.status_code == 200
        data = response.json()
        outputs = data["outputs"]
        assert outputs.get("infrastructure_private_key_path") is None
        assert outputs.get("jumphost_ssh_command") is None
        assert outputs.get("infrastructure_private_key_available") is False


class TestGetModuleResources:
    """GET /api/state/module/{module_id}/resources."""

    @patch("routes.state_viewer.os.path.exists", return_value=False)
    def test_get_module_resources_no_file(
        self, mock_exists, client, viewer_headers, sample_state_module
    ):
        """Returns empty resources when state file does not exist."""
        module = sample_state_module["module"]

        response = client.get(
            f"/api/state/module/{module.id}/resources",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["exists"] is False
        assert data["resources"] == []
        assert data["counts_by_type"] == {}


class TestGetModuleOutputs:
    """GET /api/state/module/{module_id}/outputs."""

    @patch("routes.state_viewer.os.path.exists", return_value=False)
    def test_get_module_outputs_no_file(
        self, mock_exists, client, viewer_headers, sample_state_module
    ):
        """Returns empty outputs when state file does not exist."""
        module = sample_state_module["module"]

        response = client.get(
            f"/api/state/module/{module.id}/outputs",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["exists"] is False
        assert data["outputs"] == {}


class TestGetProjectModulesWithState:
    """GET /api/state/project/{project_id}/modules."""

    @patch("routes.state_viewer.os.path.exists", return_value=False)
    def test_get_project_modules(
        self, mock_exists, client, viewer_headers, sample_state_module
    ):
        """Returns module list with state_exists=False when no state files exist."""
        project = sample_state_module["project"]

        response = client.get(
            f"/api/state/project/{project.id}/modules",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == project.id
        assert "modules" in data
        assert isinstance(data["modules"], list)
        assert len(data["modules"]) >= 1
        # Each module should report state_exists=False
        for mod in data["modules"]:
            assert mod["state_exists"] is False

    def test_project_not_found(self, client, viewer_headers, sample_state_module):
        """Requesting modules for nonexistent project returns 404."""
        response = client.get(
            "/api/state/project/99999/modules",
            headers=viewer_headers,
        )
        assert response.status_code == 404


class TestStateViewerRBAC:
    """RBAC enforcement — viewer cannot reset state, operator can refresh."""

    def test_viewer_cannot_reset(self, client, viewer_headers, sample_state_module):
        """Viewer cannot reset module state — DELETE returns 403."""
        module = sample_state_module["module"]
        response = client.delete(
            f"/api/state/module/{module.id}/reset",
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_viewer_cannot_refresh(self, client, viewer_headers, sample_state_module):
        """Viewer cannot refresh module state — POST returns 403."""
        module = sample_state_module["module"]
        response = client.post(
            f"/api/state/module/{module.id}/refresh",
            headers=viewer_headers,
        )
        assert response.status_code == 403

    @patch("routes.state_viewer.os.path.exists", return_value=False)
    def test_operator_can_refresh(
        self, mock_exists, client, operator_headers, sample_state_module
    ):
        """Operator can refresh module state (even when file is missing)."""
        module = sample_state_module["module"]

        response = client.post(
            f"/api/state/module/{module.id}/refresh",
            headers=operator_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["exists"] is False
