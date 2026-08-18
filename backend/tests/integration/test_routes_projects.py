"""
Integration tests for project routes — /api/projects.

Covers: CRUD, partial update, cascade delete, RBAC enforcement, activate.
Uses FastAPI TestClient with real SQLite DB.
"""

from unittest.mock import MagicMock, patch

import pytest

from models import Project


class TestProjectCreate:
    """POST /api/projects."""

    def test_create_project(self, client, admin_headers, sample_user, db):
        """Admin can create a project, it appears in DB."""
        payload = {
            "name": "Integration Test Project",
            "description": "Created by integration test",
            "project_type": "kubernetes",
            "environment": "dev",
            "backend_type": "local",
        }
        response = client.post("/api/projects", json=payload, headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert data["project_id"] > 0
        assert data["name"] == "Integration Test Project"

        # Verify in DB
        proj = db.query(Project).filter(Project.id == data["project_id"]).first()
        assert proj is not None
        assert proj.name == "Integration Test Project"
        assert proj.environment == "dev"

    def test_create_project_missing_name(self, client, admin_headers, sample_user, db):
        """Missing required field returns 422, no project created."""
        count_before = db.query(Project).count()
        response = client.post(
            "/api/projects",
            json={"description": "no name"},
            headers=admin_headers,
        )
        assert response.status_code == 422
        assert db.query(Project).count() == count_before

    def test_create_project_unauthorized(self, client):
        """POST without auth returns 401."""
        response = client.post(
            "/api/projects",
            json={"name": "Should fail", "project_type": "kubernetes"},
        )
        assert response.status_code == 401

    def test_create_project_viewer_denied(self, client, viewer_headers, all_test_users, db):
        """Viewer cannot create projects — returns 403."""
        count_before = db.query(Project).count()
        response = client.post(
            "/api/projects",
            json={
                "name": "Viewer Project",
                "project_type": "kubernetes",
                "environment": "dev",
                "backend_type": "local",
            },
            headers=viewer_headers,
        )
        assert response.status_code == 403
        assert db.query(Project).count() == count_before

    def test_create_project_operator_allowed(self, client, operator_headers, all_test_users, db):
        """Operator can create projects."""
        response = client.post(
            "/api/projects",
            json={
                "name": "Operator Project",
                "project_type": "kubernetes",
                "environment": "dev",
                "backend_type": "local",
            },
            headers=operator_headers,
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_create_project_defaults_target_platform_profile_generic_onprem(self, client, admin_headers, sample_user):
        """Kubernetes/on-prem create should explicitly normalize to generic_onprem baseline."""
        payload = {
            "name": "Create Baseline Truth",
            "description": "verify default target platform baseline",
            "project_type": "kubernetes",
            "cloud_provider": "on-prem",
            "environment": "dev",
            "backend_type": "local",
        }

        create_response = client.post("/api/projects", json=payload, headers=admin_headers)
        assert create_response.status_code == 200
        project_id = create_response.json()["project_id"]

        detail_response = client.get(f"/api/projects/{project_id}", headers=admin_headers)
        assert detail_response.status_code == 200
        assert detail_response.json()["target_platform_profile"] == "generic_onprem"


class TestProjectRead:
    """GET /api/projects and /api/projects/{id}."""

    def test_list_projects(self, client, admin_headers, sample_user, sample_project):
        """Lists all projects including pre-created one."""
        response = client.get("/api/projects", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert "projects" in data
        assert data["total"] >= 1
        names = [p["name"] for p in data["projects"]]
        assert "Test Project" in names

    def test_list_projects_includes_ibm_credential_summary_fields(self, client, admin_headers, sample_user, db):
        """IBM-backed projects expose enough credential template summary to prefill module dialogs."""
        from core.encryption import encrypt_value
        from models import CloudCredentialTemplate, Project

        template = CloudCredentialTemplate(
            name="IBM Cloud",
            provider="ibm",
            region="us-south",
            ibmcloud_resource_group="platform-rg",
            ibmcloud_api_key_encrypted=encrypt_value("secret"),
        )
        db.add(template)
        db.flush()

        project = Project(
            name="IBM Project",
            description="IBM project",
            project_type="cloud-ibm",
            cloud_provider="ibm",
            region="us-south",
            backend_type="local",
            color="#2563eb",
            icon="",
            credential_template_id=template.id,
            user_id=sample_user.id,
        )
        db.add(project)
        db.commit()

        response = client.get("/api/projects", headers=admin_headers)
        assert response.status_code == 200

        ibm_project = next(item for item in response.json()["projects"] if item["id"] == project.id)
        assert ibm_project["credential_template"]["has_ibmcloud_api_key"] is True
        assert ibm_project["credential_template"]["ibmcloud_resource_group"] == "platform-rg"

    def test_list_projects_empty(self, client, admin_headers, sample_user):
        """Empty project list returns 200 with zero results."""
        response = client.get("/api/projects", headers=admin_headers)
        assert response.status_code == 200
        assert response.json()["total"] >= 0

    def test_get_project(self, client, admin_headers, sample_user, sample_project):
        """Get single project returns all expected fields."""
        response = client.get(
            f"/api/projects/{sample_project.id}", headers=admin_headers
        )
        assert response.status_code == 200

        data = response.json()
        assert data["id"] == sample_project.id
        assert data["name"] == "Test Project"
        assert data["project_type"] == "kubernetes"
        assert "created_at" in data

    def test_get_project_not_found(self, client, admin_headers, sample_user):
        """Nonexistent project returns 404."""
        response = client.get("/api/projects/99999", headers=admin_headers)
        assert response.status_code == 404

    def test_get_project_detail_derives_generic_onprem_target_platform_for_legacy_null(
        self,
        client,
        admin_headers,
        sample_user,
        sample_project,
        db,
    ):
        """Legacy kubernetes/on-prem rows with null target platform should serialize as generic_onprem."""
        sample_project.target_platform_profile = None
        db.commit()

        response = client.get(f"/api/projects/{sample_project.id}", headers=admin_headers)
        assert response.status_code == 200
        assert response.json()["target_platform_profile"] == "generic_onprem"

    def test_viewer_can_read(self, client, viewer_headers, all_test_users, sample_project):
        """Viewer can read projects."""
        response = client.get("/api/projects", headers=viewer_headers)
        assert response.status_code == 200


def _credential_template(db):
    """A real cloud_credential_templates row.

    project.credential_template_id is a FK, so a synthetic id trips
    "FOREIGN KEY constraint failed" before the assertion under test is reached.
    """
    from models.system import CloudCredentialTemplate

    tpl = CloudCredentialTemplate(name="test-cred-template", provider="ibmcloud")
    db.add(tpl)
    db.flush()
    return tpl


class TestProjectUpdate:
    """PUT /api/projects/{id}."""

    def test_update_project(self, client, admin_headers, sample_user, sample_project, db):
        """Admin can update project name and description."""
        response = client.put(
            f"/api/projects/{sample_project.id}",
            json={"name": "Updated Name", "description": "Updated desc"},
            headers=admin_headers,
        )
        assert response.status_code == 200

        # Verify in DB
        db.refresh(sample_project)
        assert sample_project.name == "Updated Name"
        assert sample_project.description == "Updated desc"

    def test_update_project_partial(self, client, admin_headers, sample_user, sample_project, db):
        """Partial update only changes specified fields."""
        original_type = sample_project.project_type
        response = client.put(
            f"/api/projects/{sample_project.id}",
            json={"description": "Only desc changed"},
            headers=admin_headers,
        )
        assert response.status_code == 200

        db.refresh(sample_project)
        assert sample_project.description == "Only desc changed"
        assert sample_project.project_type == original_type

    def test_partial_update_keeps_platform_routing_fields(
        self, client, admin_headers, sample_user, sample_project, db
    ):
        """The same always-true hasattr() was nulling these three on every update.

        They sit thirty lines above the credential guards in the same method, so
        a fix that stopped at the credential fields would still have left every
        partial update wiping the project's platform routing.
        """
        sample_project.target_platform_profile = "kubernetes-onprem"
        sample_project.platform_provider = "ibmcloud"
        sample_project.management_boundary = "customer"
        db.commit()

        response = client.put(
            f"/api/projects/{sample_project.id}",
            json={"description": "unrelated change"},
            headers=admin_headers,
        )
        assert response.status_code == 200

        db.refresh(sample_project)
        assert sample_project.target_platform_profile == "kubernetes-onprem"
        assert sample_project.platform_provider == "ibmcloud"
        assert sample_project.management_boundary == "customer"

    def test_partial_update_keeps_credential_template(
        self, client, admin_headers, sample_user, sample_project, db
    ):
        """A partial update must not detach the project's credential template.

        hasattr() is always True for a declared Pydantic field, so the old check
        fired on every request and wrote the default of None. Any update that did
        not mention the template silently cleared it — and the template is where
        IBMCLOUD_API_KEY comes from, so every module that ran afterwards had no
        cloud credentials.
        """
        tpl = _credential_template(db)
        sample_project.credential_template_id = tpl.id
        db.commit()

        response = client.put(
            f"/api/projects/{sample_project.id}",
            json={"description": "unrelated change"},
            headers=admin_headers,
        )
        assert response.status_code == 200

        db.refresh(sample_project)
        assert sample_project.credential_template_id == tpl.id, (
            "partial update cleared the credential template; every subsequent "
            "module would run without cloud credentials"
        )

    def test_credential_template_can_still_be_cleared_explicitly(
        self, client, admin_headers, sample_user, sample_project, db
    ):
        """Sending an explicit null still detaches it — omission is not the same
        as an explicit clear, and callers must retain the ability to do both."""
        tpl = _credential_template(db)
        sample_project.credential_template_id = tpl.id
        db.commit()

        response = client.put(
            f"/api/projects/{sample_project.id}",
            json={"credential_template_id": None},
            headers=admin_headers,
        )
        assert response.status_code == 200

        db.refresh(sample_project)
        assert sample_project.credential_template_id is None

    def test_update_project_viewer_denied(self, client, viewer_headers, all_test_users, sample_project):
        """Viewer cannot update projects."""
        response = client.put(
            f"/api/projects/{sample_project.id}",
            json={"name": "Hacked"},
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_update_project_defaults_target_platform_profile_generic_onprem_when_unspecified(
        self,
        client,
        admin_headers,
        sample_user,
        sample_project,
        db,
    ):
        """Kubernetes/on-prem update should normalize baseline when target field omitted."""
        sample_project.target_platform_profile = None
        db.commit()

        update_response = client.put(
            f"/api/projects/{sample_project.id}",
            json={"description": "touch project without platform field"},
            headers=admin_headers,
        )
        assert update_response.status_code == 200

        detail_response = client.get(f"/api/projects/{sample_project.id}", headers=admin_headers)
        assert detail_response.status_code == 200
        assert detail_response.json()["target_platform_profile"] == "generic_onprem"


class TestProjectDelete:
    """DELETE /api/projects/{id}."""

    def test_delete_project(self, client, admin_headers, sample_user, sample_project, db):
        """Admin can delete a project (force), it disappears from DB."""
        pid = sample_project.id
        mock_lock_svc = MagicMock()
        mock_lock_svc.list_held.return_value = []
        mock_lock_svc.is_held.return_value = False

        with patch("services.module_lock.ModuleLockService", return_value=mock_lock_svc), \
             patch("services.workspace_manager.WorkspaceManager") as MockWM, \
             patch("services.project_service.invalidate_cache"):
            MockWM.return_value.cleanup_project_workspaces.return_value = 0
            response = client.delete(
                f"/api/projects/{pid}?force=true", headers=admin_headers
            )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

        assert db.query(Project).filter(Project.id == pid).first() is None

    def test_delete_project_viewer_denied(self, client, viewer_headers, all_test_users, sample_project, db):
        """Viewer cannot delete projects."""
        response = client.delete(
            f"/api/projects/{sample_project.id}", headers=viewer_headers
        )
        assert response.status_code == 403
        assert db.query(Project).filter(Project.id == sample_project.id).first() is not None

    def test_delete_project_not_found(self, client, admin_headers, sample_user):
        """Deleting nonexistent project returns 404."""
        response = client.delete("/api/projects/99999", headers=admin_headers)
        assert response.status_code == 404


class TestProjectActivate:
    """POST /api/projects/{id}/activate."""

    def test_activate_project(self, client, admin_headers, sample_user, sample_project, db):
        """Activating a project sets is_active=True."""
        response = client.post(
            f"/api/projects/{sample_project.id}/activate", headers=admin_headers
        )
        assert response.status_code == 200

        db.refresh(sample_project)
        assert sample_project.is_active is True


class TestProjectDeleteGuardOverHTTP:
    """The teardown guard and module_state at the route boundary.

    Service-level tests cannot see these: FastAPI filters every response
    through its response_model, so a field the service emits but the schema
    does not declare is silently dropped before it reaches a client.
    """

    def _applied_module(self, db, project, library):
        from models import ProjectModule

        mod = ProjectModule(
            project_id=project.id,
            module_library_id=library.id,
            path_in_project="infra/live",
            status="applied",
        )
        db.add(mod)
        db.commit()
        return mod

    def test_module_state_reaches_client_on_detail(
        self, client, admin_headers, sample_user, sample_project, db
    ):
        """GET /{id} carries a COMPUTED module_state.

        Asserting "clean" here would be vacuous: it is also the schema default,
        so it passes even if the service stops emitting the field. Drive a
        non-default value instead.
        """
        sample_project.deployed_count = 2
        sample_project.failed_count = 0
        db.commit()

        response = client.get(f"/api/projects/{sample_project.id}", headers=admin_headers)
        assert response.status_code == 200
        assert response.json()["module_state"] == "in_progress"

        sample_project.failed_count = 1
        db.commit()
        response = client.get(f"/api/projects/{sample_project.id}", headers=admin_headers)
        assert response.json()["module_state"] == "failed"

    def test_module_state_reaches_client_on_list(
        self, client, admin_headers, sample_user, sample_project, db
    ):
        """GET / carries a COMPUTED module_state on each item."""
        sample_project.deployed_count = 1
        sample_project.failed_count = 0
        db.commit()

        response = client.get("/api/projects", headers=admin_headers)
        assert response.status_code == 200
        items = response.json()["projects"]
        mine = [i for i in items if i["id"] == sample_project.id]
        assert mine, "expected the sample project in the listing"
        assert mine[0]["module_state"] == "in_progress"

    def test_delete_blocked_with_409_when_module_still_owns_infra(
        self, client, admin_headers, sample_user, sample_module, db
    ):
        """An applied module blocks deletion with an actionable 409."""
        project = sample_module["project"]
        self._applied_module(db, project, sample_module["library"])

        response = client.delete(f"/api/projects/{project.id}", headers=admin_headers)
        assert response.status_code == 409

        err = response.json()["error"]
        assert err["details"]["requires_force"] is True
        undestroyed = err["details"]["undestroyed_modules"]
        assert [m["status"] for m in undestroyed] == ["applied"]
        assert db.query(Project).filter(Project.id == project.id).first() is not None

    def test_delete_allowed_with_force(
        self, client, admin_headers, sample_user, sample_module, db
    ):
        """force=true is the documented override and still deletes."""
        project = sample_module["project"]
        self._applied_module(db, project, sample_module["library"])

        response = client.delete(
            f"/api/projects/{project.id}?force=true", headers=admin_headers
        )
        assert response.status_code == 200
        assert db.query(Project).filter(Project.id == project.id).first() is None
