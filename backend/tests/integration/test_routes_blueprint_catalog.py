"""Integration tests for blueprint catalog routes."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch


def _source_response(**overrides):
    base = {
        "id": 1,
        "name": "external-blueprints",
        "source_type": "git",
        "url": "https://github.com/example/blueprints.git",
        "branch": "main",
        "git_ref": None,
        "sync_status": "pending",
        "sync_error": None,
        "last_synced_at": None,
        "release_count": 0,
        "is_active": True,
        "description": "Blueprint source",
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    base.update(overrides)
    return base


def _release_response(**overrides):
    base = {
        "id": 1,
        "blueprint_source_id": 1,
        "source_name": "external-blueprints",
        "blueprint_id": "bnk.existing-cluster",
        "blueprint_version": "1.0.0",
        "blueprint_name": "BNK Existing Cluster",
        "blueprint_description": "Imported release",
        "category": "infrastructure",
        "cloud_provider": "ibm",
        "tags": ["ibm", "roks", "infrastructure"],
        "schema_version": 1,
        "source_path": "blueprints/existing-cluster/forge-blueprint.json",
        "source_ref": "refs/tags/v1.0.0",
        "content_sha256": "a" * 64,
        "validation_state": "valid",
        "validation_errors": None,
        "release_state": "imported",
        "state_reason": "initial import",
        "is_active": True,
        "imported_at": datetime.now(UTC).isoformat(),
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "mutable_lifecycle_fields": ["imported_at", "is_active", "release_state", "state_reason"],
    }
    base.update(overrides)
    return base


@patch("routes.blueprint_catalog.BlueprintCatalogService")
def test_list_blueprint_sources(mock_service_cls, client, viewer_headers, all_test_users):
    mock_service = MagicMock()
    mock_service.list_sources.return_value = [_source_response()]
    mock_service_cls.return_value = mock_service

    response = client.get("/api/blueprint-catalog/sources", headers=viewer_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "external-blueprints"


@patch("routes.blueprint_catalog.BlueprintCatalogService")
def test_create_blueprint_source(mock_service_cls, client, operator_headers, all_test_users):
    mock_service = MagicMock()
    mock_service.create_source.return_value = _source_response(id=5, name="new-source")
    mock_service_cls.return_value = mock_service

    response = client.post(
        "/api/blueprint-catalog/sources",
        json={
            "name": "new-source",
            "source_type": "git",
            "url": "https://git.example.com/new-source.git",
        },
        headers=operator_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["id"] == 5
    mock_service.create_source.assert_called_once()


@patch("routes.blueprint_catalog.BlueprintCatalogService")
def test_delete_blueprint_source(mock_service_cls, client, operator_headers, all_test_users):
    mock_service = MagicMock()
    mock_service.delete_source.return_value = {"success": True, "message": "deleted"}
    mock_service_cls.return_value = mock_service

    response = client.delete("/api/blueprint-catalog/sources/4", headers=operator_headers)
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    mock_service.delete_source.assert_called_once_with(4)


@patch("routes.blueprint_catalog.BlueprintSyncService")
@patch("routes.blueprint_catalog.BlueprintCatalogService")
def test_sync_blueprint_source(mock_catalog_cls, mock_sync_cls, client, operator_headers, all_test_users):
    mock_catalog = MagicMock()
    source = MagicMock()
    source.id = 7
    source.name = "source-seven"
    source.sync_status = "success"
    source.is_active = True
    mock_catalog._get_source.return_value = source
    mock_catalog_cls.return_value = mock_catalog

    mock_sync = MagicMock()
    mock_sync.sync_git_source.return_value = {
        "blueprints_found": 1,
        "releases_created": 1,
        "releases_existing": 0,
        "releases_invalid": 0,
        "errors": [],
    }
    mock_sync_cls.return_value = mock_sync

    response = client.post("/api/blueprint-catalog/sources/7/sync", headers=operator_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["success"] is True
    assert data["source_id"] == 7
    assert data["results"]["releases_created"] == 1


@patch("routes.blueprint_catalog.BlueprintCatalogService")
def test_create_blueprint_release(mock_service_cls, client, operator_headers, all_test_users):
    mock_service = MagicMock()
    mock_service.create_release.return_value = _release_response(id=11)
    mock_service_cls.return_value = mock_service

    response = client.post(
        "/api/blueprint-catalog/releases",
        json={
            "blueprint_source_id": 1,
            "manifest": {
                "schema_version": 1,
                "blueprint": {
                    "id": "bnk.existing-cluster",
                    "version": "1.0.0",
                    "name": "BNK Existing Cluster",
                },
                "modules": [
                    {
                        "id": "foundation",
                        "module": "bnk.foundation.cluster",
                        "version": "1.2.3",
                        "depends_on": [],
                        "inputs": {},
                    }
                ],
            },
        },
        headers=operator_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["id"] == 11
    mock_service.create_release.assert_called_once()


@patch("routes.blueprint_catalog.BlueprintCatalogService")
def test_update_release_state(mock_service_cls, client, operator_headers, all_test_users):
    mock_service = MagicMock()
    mock_service.update_release_state.return_value = _release_response(id=9, release_state="approved")
    mock_service_cls.return_value = mock_service

    response = client.patch(
        "/api/blueprint-catalog/releases/9/state",
        json={"release_state": "approved", "state_reason": "review passed"},
        headers=operator_headers,
    )
    assert response.status_code == 200
    assert response.json()["release_state"] == "approved"
    mock_service.update_release_state.assert_called_once()
    assert mock_service.update_release_state.call_args[0][0] == 9


@patch("routes.blueprint_catalog.BlueprintCatalogService")
def test_import_blueprint_release(mock_service_cls, client, operator_headers, all_test_users):
    mock_service = MagicMock()
    mock_service.update_release_state.return_value = _release_response(id=12, release_state="imported", is_active=True)
    mock_service_cls.return_value = mock_service

    response = client.post(
        "/api/blueprint-catalog/releases/12/import",
        json={"state_reason": "catalog import"},
        headers=operator_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["release_state"] == "imported"
    assert data["is_active"] is True


@patch("routes.blueprint_catalog.BlueprintCatalogService")
def test_delete_blueprint_release(mock_service_cls, client, operator_headers, all_test_users):
    mock_service = MagicMock()
    mock_service.delete_release.return_value = {"success": True, "message": "deleted"}
    mock_service_cls.return_value = mock_service

    response = client.delete("/api/blueprint-catalog/releases/12", headers=operator_headers)
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    mock_service.delete_release.assert_called_once_with(12)


def test_viewer_cannot_create_source(client, viewer_headers, all_test_users):
    response = client.post(
        "/api/blueprint-catalog/sources",
        json={"name": "blocked", "source_type": "git", "url": "https://git.example.com/blocked.git"},
        headers=viewer_headers,
    )
    assert response.status_code == 403


def test_unauthenticated_cannot_list_sources(client):
    response = client.get("/api/blueprint-catalog/sources")
    assert response.status_code == 401
