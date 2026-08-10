"""
Integration tests for stack routes — /api/stacks.

Covers: template list/get/preview, instance CRUD, prerequisites, RBAC.
Uses FastAPI TestClient with real SQLite DB.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from models import StackInstance, StackTemplate


@pytest.fixture()
def sample_stack_template(db):
    """Create a stack template in the test DB."""
    template = StackTemplate(
        name="Test Stack",
        slug="test-stack",
        description="A test stack template for integration tests",
        category="bnk",
        cloud_provider="aws",
        icon="shield",
        color="#0078d4",
        estimated_time="15 min",
        estimated_cost="$100/mo",
        difficulty="beginner",
        modules=[
            {"module": "vpc", "order": 1, "required": True},
            {"module": "eks", "order": 2, "required": True},
        ],
        tags=["test", "aws"],
        is_active=True,
        is_featured=False,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


class TestStackTemplateList:
    """GET /api/stacks/templates."""

    def test_list_templates(self, client, admin_headers, sample_user, sample_stack_template):
        """List templates returns available stacks."""
        response = client.get("/api/stacks/templates", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_list_templates_filter_category(self, client, admin_headers, sample_user, sample_stack_template):
        """Filter templates by category."""
        response = client.get(
            "/api/stacks/templates?category=bnk", headers=admin_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert all(t.get("category") == "bnk" for t in data)

    def test_list_templates_viewer_allowed(self, client, viewer_headers, all_test_users, sample_stack_template):
        """Viewer can read templates."""
        response = client.get("/api/stacks/templates", headers=viewer_headers)
        assert response.status_code == 200

    def test_list_templates_unauthenticated(self, client, sample_stack_template):
        """Unauthenticated request returns 401."""
        response = client.get("/api/stacks/templates")
        assert response.status_code == 401


class TestStackTemplateDetail:
    """GET /api/stacks/templates/{slug}."""

    def test_get_template(self, client, admin_headers, sample_user, sample_stack_template):
        """Get template by slug returns full details."""
        response = client.get(
            "/api/stacks/templates/test-stack", headers=admin_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["slug"] == "test-stack"
        assert data["name"] == "Test Stack"
        assert data["category"] == "bnk"

    def test_get_template_not_found(self, client, admin_headers, sample_user):
        """Nonexistent slug returns 404."""
        response = client.get(
            "/api/stacks/templates/nonexistent-slug", headers=admin_headers
        )
        assert response.status_code == 404

    def test_get_template_exposes_module_engine_and_lifecycle_capabilities(
        self,
        client,
        admin_headers,
        sample_user,
        db,
        make_module_library,
    ):
        """Template detail includes additive capability metadata for known modules."""
        from models import StackTemplate

        lifecycle = {
            "supports_init": True,
            "supports_plan": False,
            "supports_apply": True,
            "supports_destroy": False,
        }
        make_module_library(
            name="ansible-pack",
            path="packs/ansible/app",
            engine_type="ansible",
            pack_manifest={"deployment_pack": {"lifecycle": lifecycle}},
        )

        template = StackTemplate(
            name="Capability Stack",
            slug="capability-stack",
            category="bnk",
            modules=[{"path": "packs/ansible/app", "name": "app"}],
            is_active=True,
        )
        db.add(template)
        db.commit()

        response = client.get(
            "/api/stacks/templates/capability-stack",
            headers=admin_headers,
        )
        assert response.status_code == 200
        module_entry = response.json()["modules"][0]
        assert module_entry["engine_type"] == "ansible"
        assert module_entry["lifecycle_capabilities"] == lifecycle

    def test_get_template_marks_missing_catalog_module_without_builtin_fallback(
        self,
        client,
        admin_headers,
        sample_user,
        db,
    ):
        """Template detail reports missing catalog modules instead of built-in fallback."""
        from models import StackTemplate

        template = StackTemplate(
            name="Builtin Fallback Stack",
            slug="builtin-fallback-stack",
            category="bnk",
            modules=[{"path": "bnk/bnk-vlans", "name": "vlans"}],
            is_active=True,
        )
        db.add(template)
        db.commit()

        response = client.get(
            "/api/stacks/templates/builtin-fallback-stack",
            headers=admin_headers,
        )
        assert response.status_code == 200
        module_entry = response.json()["modules"][0]
        assert module_entry["module_catalog_status"] == "missing"
        assert "module_catalog_message" in module_entry
        assert module_entry.get("engine_type") is None


class TestStackTemplatePreview:
    """GET /api/stacks/templates/{slug}/preview."""

    def test_preview_template(self, client, admin_headers, sample_user, sample_stack_template):
        """Preview returns module list."""
        response = client.get(
            "/api/stacks/templates/test-stack/preview", headers=admin_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert "modules" in data

    def test_preview_template_exposes_engine_and_legacy_fallback(
        self,
        client,
        admin_headers,
        sample_user,
        db,
        make_module_library,
    ):
        """Template preview surfaces capability metadata and preserves legacy fallback."""
        from models import StackTemplate

        lifecycle = {
            "supports_init": True,
            "supports_plan": False,
            "supports_apply": True,
            "supports_destroy": False,
        }
        make_module_library(
            name="ansible-pack",
            path="packs/ansible/preview",
            engine_type="ansible",
            pack_manifest={"deployment_pack": {"lifecycle": lifecycle}},
        )
        make_module_library(
            name="legacy-pack",
            path="infra/aws/legacy",
            engine_type=None,
            pack_manifest=None,
        )

        template = StackTemplate(
            name="Preview Capability Stack",
            slug="preview-capability-stack",
            modules=[
                {"path": "packs/ansible/preview", "name": "preview"},
                {"path": "infra/aws/legacy", "name": "legacy"},
            ],
            is_active=True,
        )
        db.add(template)
        db.commit()

        response = client.get(
            "/api/stacks/templates/preview-capability-stack/preview",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()

        modules = {module["name"]: module for module in data["modules"]}
        assert modules["preview"]["engine_type"] == "ansible"
        assert modules["preview"]["lifecycle_capabilities"] == lifecycle
        assert modules["legacy"]["engine_type"] == "opentofu"
        assert modules["legacy"].get("lifecycle_capabilities") is None

    def test_preview_template_marks_missing_catalog_module_without_builtin_fallback(
        self,
        client,
        admin_headers,
        sample_user,
        db,
    ):
        """Template preview reports missing catalog modules instead of built-in fallback."""
        from models import StackTemplate

        template = StackTemplate(
            name="Preview Builtin Fallback",
            slug="preview-builtin-fallback",
            modules=[{"path": "bnk/bnk-vlans", "name": "vlans"}],
            is_active=True,
        )
        db.add(template)
        db.commit()

        response = client.get(
            "/api/stacks/templates/preview-builtin-fallback/preview",
            headers=admin_headers,
        )
        assert response.status_code == 200
        module_entry = response.json()["modules"][0]
        assert module_entry["module_catalog_status"] == "missing"
        assert "module_catalog_message" in module_entry
        assert module_entry.get("engine_type") is None


class TestStackInstances:
    """Stack instance CRUD — /api/stacks/projects/{id}/stacks."""

    def test_list_instances_empty(self, client, admin_headers, sample_user, sample_project):
        """Empty project has no stack instances."""
        response = client.get(
            f"/api/stacks/projects/{sample_project.id}/stacks",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_create_instance(self, client, admin_headers, sample_user, sample_project, sample_stack_template, db):
        """Create a stack instance in a project."""
        response = client.post(
            f"/api/stacks/projects/{sample_project.id}/stacks",
            json={
                "template_id": sample_stack_template.id,
                "name": "My Test Stack",
                "variables": {"region": "us-east-1"},
            },
            headers=admin_headers,
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    def test_create_instance_viewer_denied(self, client, viewer_headers, all_test_users, sample_project, sample_stack_template):
        """Viewer cannot create stack instances."""
        response = client.post(
            f"/api/stacks/projects/{sample_project.id}/stacks",
            json={
                "template_id": sample_stack_template.id,
                "name": "Denied Stack",
            },
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_get_stack_instance_exposes_module_capability_metadata(
        self,
        client,
        admin_headers,
        sample_user,
        sample_project,
        db,
        make_module_library,
        make_project_module,
    ):
        """Stack instance detail includes module engine/capability metadata."""
        from models import StackInstance, StackTemplate

        template = StackTemplate(
            name="Instance Capability Stack",
            slug="instance-capability-stack",
            modules=[{"path": "packs/ansible/instance", "name": "instance"}],
            is_active=True,
        )
        db.add(template)
        db.flush()

        stack = StackInstance(
            project_id=sample_project.id,
            template_id=template.id,
            name="instance-capability",
            status="pending",
        )
        db.add(stack)
        db.flush()

        lifecycle = {
            "supports_init": True,
            "supports_plan": False,
            "supports_apply": True,
            "supports_destroy": False,
        }
        ansible_lib = make_module_library(
            name="ansible-pack",
            path="packs/ansible/instance",
            engine_type="ansible",
            pack_manifest={"deployment_pack": {"lifecycle": lifecycle}},
        )
        make_project_module(
            project=sample_project,
            library_module=ansible_lib,
            stack_instance_id=stack.id,
            path_in_project="packs/ansible/instance",
            deployment_order=1,
        )
        db.commit()

        response = client.get(
            f"/api/stacks/projects/{sample_project.id}/stacks/{stack.id}",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "modules" in data
        module_entry = data["modules"][0]
        assert module_entry["engine_type"] == "ansible"
        assert module_entry["lifecycle_capabilities"] == lifecycle
