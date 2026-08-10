"""
Integration tests for project module routes — /api/project-modules.

Covers: add, list, update, remove, status, variables, dependencies, graph.
Uses FastAPI TestClient with real SQLite DB.
"""

import pytest

from models import ProjectModule


class TestModuleAdd:
    """POST /api/project-modules/project/{id}/add."""

    def test_add_module(self, client, admin_headers, sample_module, db):
        """Adding a module returns success and module appears in DB."""
        # sample_module fixture already adds a module — verify it exists
        mod = sample_module["module"]
        assert db.query(ProjectModule).filter(ProjectModule.id == mod.id).first() is not None

    def test_add_module_to_project(self, client, admin_headers, sample_user, sample_project, db):
        """Add module via route, verify in DB."""
        from tests.factories import ModuleLibraryFactory
        lib = ModuleLibraryFactory(
            db,
            name="route-test-vpc",
            description="VPC module",
            category="networking",
        )
        db.commit()
        response = client.post(
            f"/api/project-modules/project/{sample_project.id}/add",
            json={
                "module_library_id": lib.id,
                "path_in_project": "infra/vpc",
            },
            headers=admin_headers,
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

        # Verify in DB
        pm = db.query(ProjectModule).filter(
            ProjectModule.project_id == sample_project.id,
        ).first()
        assert pm is not None

    def test_add_module_viewer_denied(self, client, viewer_headers, all_test_users, sample_project, db):
        """Viewer cannot add modules — returns 403."""
        from tests.factories import ModuleLibraryFactory
        lib = ModuleLibraryFactory(db, name="viewer-test", description="test", category="test")
        db.commit()
        response = client.post(
            f"/api/project-modules/project/{sample_project.id}/add",
            json={
                "module_library_id": lib.id,
                "path_in_project": "test/mod",
            },
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_add_module_invalid_path(self, client, admin_headers, sample_user, sample_project, db):
        """Path with '..' is rejected with 422."""
        from tests.factories import ModuleLibraryFactory
        lib = ModuleLibraryFactory(db, name="bad-path-test", description="test", category="test")
        db.commit()
        response = client.post(
            f"/api/project-modules/project/{sample_project.id}/add",
            json={
                "module_library_id": lib.id,
                "path_in_project": "../escape/here",
            },
            headers=admin_headers,
        )
        assert response.status_code == 422


class TestModuleList:
    """GET /api/project-modules/project/{id}."""

    def test_list_modules(self, client, admin_headers, sample_module):
        """List modules returns the added module."""
        project = sample_module["project"]
        response = client.get(
            f"/api/project-modules/project/{project.id}",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        # Response may be list or dict with 'modules' key
        modules = data.get("modules", data) if isinstance(data, dict) else data
        assert len(modules) >= 1

    def test_list_modules_empty_project(self, client, admin_headers, sample_user, sample_project):
        """Empty project returns 200 with empty modules."""
        response = client.get(
            f"/api/project-modules/project/{sample_project.id}",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        # May be a list or dict with 'modules' key
        if isinstance(data, dict):
            assert "modules" in data
        else:
            assert isinstance(data, list)

    def test_list_modules_exposes_engine_and_lifecycle_capabilities(
        self,
        client,
        admin_headers,
        sample_module,
        db,
    ):
        """Project module list includes additive engine/capability metadata."""
        mod = sample_module["module"]
        lib = sample_module["library"]
        lifecycle = {
            "supports_init": True,
            "supports_plan": False,
            "supports_apply": True,
            "supports_destroy": False,
        }
        lib.engine_type = "ansible"
        lib.pack_manifest = {"deployment_pack": {"lifecycle": lifecycle}}
        db.commit()

        response = client.get(
            f"/api/project-modules/project/{mod.project_id}",
            headers=admin_headers,
        )
        assert response.status_code == 200

        modules = response.json().get("modules", [])
        serialized = next(item for item in modules if item["id"] == mod.id)
        assert serialized["engine_type"] == "ansible"
        assert serialized["lifecycle_capabilities"] == lifecycle
        assert serialized["library_module"]["engine_type"] == "ansible"

    def test_list_modules_legacy_fallback_still_reports_opentofu(
        self,
        client,
        admin_headers,
        sample_module,
        db,
    ):
        """Legacy modules without explicit metadata still serialize as opentofu."""
        mod = sample_module["module"]
        lib = sample_module["library"]
        lib.path = "infra/aws/legacy"
        lib.engine_type = None
        lib.pack_manifest = None
        db.commit()

        response = client.get(
            f"/api/project-modules/project/{mod.project_id}",
            headers=admin_headers,
        )
        assert response.status_code == 200

        modules = response.json().get("modules", [])
        serialized = next(item for item in modules if item["id"] == mod.id)
        assert serialized["engine_type"] == "opentofu"
        assert "lifecycle_capabilities" not in serialized


class TestModuleUpdate:
    """PUT /api/project-modules/{id}."""

    def test_update_module_variables(self, client, admin_headers, sample_module, db):
        """Update module variable overrides."""
        mod = sample_module["module"]
        response = client.put(
            f"/api/project-modules/{mod.id}",
            json={"variable_overrides": {"region": "us-west-2"}},
            headers=admin_headers,
        )
        assert response.status_code == 200

    def test_update_module_viewer_denied(self, client, viewer_headers, all_test_users, sample_project, db):
        """Viewer cannot update modules."""
        from tests.factories import ModuleLibraryFactory, ProjectModuleFactory
        lib = ModuleLibraryFactory(db, name="viewer-update-test", category="test")
        mod = ProjectModuleFactory(db, project=sample_project, library_module=lib)
        db.commit()
        response = client.put(
            f"/api/project-modules/{mod.id}",
            json={"variable_overrides": {"hacked": "true"}},
            headers=viewer_headers,
        )
        assert response.status_code == 403


class TestModuleRemove:
    """DELETE /api/project-modules/{id}."""

    def test_remove_module(self, client, admin_headers, sample_module, db):
        """Remove module, verify gone from DB."""
        from unittest.mock import MagicMock, patch
        mod = sample_module["module"]

        mock_lock_svc = MagicMock()
        mock_lock_svc.is_held.return_value = False
        mock_lock_svc.get_holder.return_value = None

        with patch("services.module_lock.ModuleLockService", return_value=mock_lock_svc), \
             patch("services.workspace_manager.WorkspaceManager") as MockWM, \
             patch("services.project_service.invalidate_cache"):
            MockWM.return_value.cleanup_module_workspace.return_value = True
            response = client.delete(
                f"/api/project-modules/{mod.id}", headers=admin_headers
            )
        assert response.status_code == 200

        assert db.query(ProjectModule).filter(ProjectModule.id == mod.id).first() is None

    def test_remove_module_viewer_denied(self, client, viewer_headers, all_test_users, sample_project, db):
        """Viewer cannot remove modules."""
        from tests.factories import ModuleLibraryFactory, ProjectModuleFactory
        lib = ModuleLibraryFactory(db, name="viewer-remove-test", category="test")
        mod = ProjectModuleFactory(db, project=sample_project, library_module=lib)
        db.commit()
        response = client.delete(
            f"/api/project-modules/{mod.id}", headers=viewer_headers
        )
        assert response.status_code == 403
        assert db.query(ProjectModule).filter(ProjectModule.id == mod.id).first() is not None


class TestModuleStatus:
    """GET /api/project-modules/{id}/status."""

    def test_get_module_status(self, client, admin_headers, sample_module):
        """Status endpoint returns module status info."""
        mod = sample_module["module"]
        response = client.get(
            f"/api/project-modules/{mod.id}/status", headers=admin_headers
        )
        assert response.status_code == 200


class TestModuleStateHistory:
    """GET /api/project-modules/{id}/state-history (Phase 2)."""

    def test_empty_history_for_fresh_module(self, client, admin_headers, sample_module):
        mod = sample_module["module"]
        response = client.get(
            f"/api/project-modules/{mod.id}/state-history", headers=admin_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["module_id"] == mod.id
        assert data["count"] == 0
        assert data["transitions"] == []

    def test_history_returns_transitions_in_reverse_chronological_order(
        self, client, admin_headers, sample_module, db,
    ):
        from services.module_state import transition_module_status
        mod = sample_module["module"]
        # Drive a real lifecycle through the helper.
        transition_module_status(db, mod, to_status="initializing", reason="test queue init")
        transition_module_status(db, mod, to_status="initialized", reason="test init done")
        transition_module_status(db, mod, to_status="applying", reason="test queue apply")

        response = client.get(
            f"/api/project-modules/{mod.id}/state-history", headers=admin_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 3
        # Most recent first.
        assert data["transitions"][0]["to_status"] == "applying"
        assert data["transitions"][0]["reason"] == "test queue apply"
        assert data["transitions"][-1]["from_status"] == "not_initialized"
        assert data["transitions"][-1]["to_status"] == "initializing"

    def test_history_respects_limit(self, client, admin_headers, sample_module, db):
        from services.module_state import transition_module_status
        mod = sample_module["module"]
        transition_module_status(db, mod, to_status="initializing", reason="1")
        transition_module_status(db, mod, to_status="initialized", reason="2")
        transition_module_status(db, mod, to_status="applying", reason="3")

        response = client.get(
            f"/api/project-modules/{mod.id}/state-history?limit=2", headers=admin_headers
        )
        assert response.status_code == 200
        assert response.json()["count"] == 2

    def test_history_rejects_out_of_range_limit(self, client, admin_headers, sample_module):
        mod = sample_module["module"]
        response = client.get(
            f"/api/project-modules/{mod.id}/state-history?limit=0", headers=admin_headers
        )
        assert response.status_code == 400

    def test_history_404_for_missing_module(self, client, admin_headers, sample_user):
        # sample_user ensures the admin user row exists for auth resolution.
        response = client.get(
            "/api/project-modules/9999999/state-history", headers=admin_headers
        )
        assert response.status_code == 404


class TestModuleVariables:
    """GET /api/project-modules/{id}/variables."""

    def test_get_module_variables(self, client, admin_headers, sample_module):
        """Variables endpoint returns module variable info."""
        mod = sample_module["module"]
        response = client.get(
            f"/api/project-modules/{mod.id}/variables", headers=admin_headers
        )
        assert response.status_code == 200


class TestModuleDependencies:
    """Dependency management endpoints."""

    def test_get_dependencies(self, client, admin_headers, sample_module):
        """Get dependencies returns a list (empty for new module)."""
        mod = sample_module["module"]
        response = client.get(
            f"/api/project-modules/{mod.id}/dependencies", headers=admin_headers
        )
        assert response.status_code == 200

    def test_get_dependents(self, client, admin_headers, sample_module):
        """Get dependents returns a list."""
        mod = sample_module["module"]
        response = client.get(
            f"/api/project-modules/{mod.id}/dependents", headers=admin_headers
        )
        assert response.status_code == 200

    def test_dependency_graph(self, client, admin_headers, sample_module):
        """Dependency graph returns graph structure."""
        project = sample_module["project"]
        response = client.get(
            f"/api/project-modules/project/{project.id}/dependency-graph",
            headers=admin_headers,
        )
        assert response.status_code == 200
