"""
Integration tests for project variable mapping routes — /api/project-modules/{module_id}/variable-mappings.

Covers: list, create, update, delete variable mappings, RBAC enforcement.
Uses FastAPI TestClient with real SQLite DB.
"""

import pytest

from models import VariableMapping


class TestListVariableMappings:
    """GET /api/project-modules/{module_id}/variable-mappings."""

    def test_list_variable_mappings_empty(self, client, admin_headers, sample_module):
        """Listing mappings on a fresh module returns an empty list."""
        mod = sample_module["module"]
        response = client.get(
            f"/api/project-modules/{mod.id}/variable-mappings",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["module_id"] == mod.id
        assert data["mappings"] == []


class TestCreateVariableMapping:
    """POST /api/project-modules/{module_id}/variable-mappings."""

    def test_create_variable_mapping(self, client, admin_headers, sample_module, db):
        """Operator/admin can create a variable mapping; it appears in DB."""
        mod = sample_module["module"]
        payload = {
            "source_variable_name": "vpc_id",
            "target_variable_name": "network_id",
            "transform_type": "none",
            "is_active": True,
            "description": "Map VPC ID to network ID",
        }
        response = client.post(
            f"/api/project-modules/{mod.id}/variable-mappings",
            json=payload,
            headers=admin_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["source_variable_name"] == "vpc_id"
        assert data["target_variable_name"] == "network_id"
        assert data["transform_type"] == "none"
        assert data["is_active"] is True
        assert data["id"] > 0

        # Verify in DB
        mapping = db.query(VariableMapping).filter(VariableMapping.id == data["id"]).first()
        assert mapping is not None
        assert mapping.source_variable_name == "vpc_id"
        assert mapping.project_module_id == mod.id

    def test_create_duplicate_mapping_rejected(self, client, admin_headers, sample_module):
        """Creating a mapping with a duplicate source_variable_name returns 400."""
        mod = sample_module["module"]
        payload = {
            "source_variable_name": "dup_var",
            "target_variable_name": "target_a",
        }
        resp1 = client.post(
            f"/api/project-modules/{mod.id}/variable-mappings",
            json=payload,
            headers=admin_headers,
        )
        assert resp1.status_code == 200

        resp2 = client.post(
            f"/api/project-modules/{mod.id}/variable-mappings",
            json={"source_variable_name": "dup_var", "target_variable_name": "target_b"},
            headers=admin_headers,
        )
        assert resp2.status_code == 400


class TestUpdateVariableMapping:
    """PUT /api/project-modules/{module_id}/variable-mappings/{mapping_id}."""

    def test_update_variable_mapping(self, client, admin_headers, sample_module, db):
        """Operator/admin can update an existing variable mapping."""
        mod = sample_module["module"]
        # Create a mapping first
        create_resp = client.post(
            f"/api/project-modules/{mod.id}/variable-mappings",
            json={
                "source_variable_name": "old_source",
                "target_variable_name": "old_target",
            },
            headers=admin_headers,
        )
        assert create_resp.status_code == 200
        mapping_id = create_resp.json()["id"]

        # Update it
        update_resp = client.put(
            f"/api/project-modules/{mod.id}/variable-mappings/{mapping_id}",
            json={
                "source_variable_name": "new_source",
                "target_variable_name": "new_target",
                "transform_type": "uppercase",
                "is_active": False,
                "description": "Updated mapping",
            },
            headers=admin_headers,
        )
        assert update_resp.status_code == 200

        data = update_resp.json()
        assert data["source_variable_name"] == "new_source"
        assert data["target_variable_name"] == "new_target"
        assert data["transform_type"] == "uppercase"
        assert data["is_active"] is False
        assert data["description"] == "Updated mapping"

        # Verify in DB
        db.expire_all()
        mapping = db.query(VariableMapping).filter(VariableMapping.id == mapping_id).first()
        assert mapping.source_variable_name == "new_source"


class TestDeleteVariableMapping:
    """DELETE /api/project-modules/{module_id}/variable-mappings/{mapping_id}."""

    def test_delete_variable_mapping(self, client, admin_headers, sample_module, db):
        """Operator/admin can delete a variable mapping."""
        mod = sample_module["module"]
        # Create a mapping first
        create_resp = client.post(
            f"/api/project-modules/{mod.id}/variable-mappings",
            json={
                "source_variable_name": "to_delete",
                "target_variable_name": "will_be_gone",
            },
            headers=admin_headers,
        )
        assert create_resp.status_code == 200
        mapping_id = create_resp.json()["id"]

        # Delete it
        del_resp = client.delete(
            f"/api/project-modules/{mod.id}/variable-mappings/{mapping_id}",
            headers=admin_headers,
        )
        assert del_resp.status_code == 200
        assert del_resp.json()["success"] is True

        # Verify removed from DB
        assert db.query(VariableMapping).filter(VariableMapping.id == mapping_id).first() is None


class TestVariableMappingRBAC:
    """RBAC enforcement for variable mapping endpoints."""

    def test_viewer_cannot_create(
        self, client, viewer_headers, sample_user, sample_viewer_user, sample_module,
    ):
        """Viewer cannot create variable mappings — returns 403."""
        mod = sample_module["module"]
        response = client.post(
            f"/api/project-modules/{mod.id}/variable-mappings",
            json={
                "source_variable_name": "blocked",
                "target_variable_name": "denied",
            },
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_viewer_can_list(
        self, client, viewer_headers, sample_user, sample_viewer_user, sample_module,
    ):
        """Viewer can list variable mappings."""
        mod = sample_module["module"]
        response = client.get(
            f"/api/project-modules/{mod.id}/variable-mappings",
            headers=viewer_headers,
        )
        assert response.status_code == 200
