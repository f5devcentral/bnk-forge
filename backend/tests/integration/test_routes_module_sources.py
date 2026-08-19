"""
Integration tests for module source routes — /api/module-sources.

Covers: list, get, create, delete sources, RBAC enforcement.
Uses FastAPI TestClient with real SQLite DB. ModuleSourceService is mocked.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from routes.module_sources import ModuleSourceResponse


def _make_source_response(**overrides):
    """Build a sample ModuleSourceResponse-compatible dict."""
    base = {
        "id": 1,
        "name": "test-source",
        "source_type": "git",
        "url": "https://github.com/example/modules.git",
        "branch": "main",
        "git_ref": None,
        "auth_type": "none",
        "credential_type": "none",
        "credential_scope": None,
        "credential_capabilities": None,
        "credential_metadata": None,
        "credential_expires_at": None,
        "credential_last_rotated_at": None,
        "credential_validation_status": "unknown",
        "credential_last_validated_at": None,
        "credential_validation_error": None,
        "has_secret": False,
        "last_synced_at": None,
        "sync_status": "pending",
        "sync_error": None,
        "module_count": 0,
        "is_active": True,
        # Both computed by ModuleSourceService._serialize_source() rather than
        # stored on the model. Mirrored here so the mocked service returns what
        # the real one does -- is_default is required by ModuleSourceResponse,
        # and omitting it made every test using this helper 500.
        "is_default": False,
        "credential_recommendation": None,
        "auto_sync": False,
        "sync_interval_hours": 24,
        "description": "Test module source",
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    base.update(overrides)
    # Fail here, in the helper, if the response schema gains a required field.
    # These tests patch ModuleSourceService entirely, so a stale fixture is
    # invisible until FastAPI rejects the response -- which is how is_default
    # went unnoticed (#130).
    ModuleSourceResponse.model_validate(base)
    return base


class TestListModuleSources:
    """GET /api/module-sources."""

    @patch("routes.module_sources.ModuleSourceService")
    def test_list_sources(self, mock_svc_cls, client, viewer_headers, all_test_users):
        """Viewer can list all module sources."""
        mock_svc = MagicMock()
        mock_svc.list_sources.return_value = [
            _make_source_response(id=1, name="source-a"),
            _make_source_response(id=2, name="source-b"),
        ]
        mock_svc_cls.return_value = mock_svc

        response = client.get("/api/module-sources", headers=viewer_headers)
        assert response.status_code == 200

        data = response.json()
        assert len(data) == 2
        assert data[0]["name"] == "source-a"
        mock_svc.list_sources.assert_called_once()


class TestGetModuleSource:
    """GET /api/module-sources/{source_id}."""

    @patch("routes.module_sources.ModuleSourceService")
    def test_get_source(self, mock_svc_cls, client, viewer_headers, all_test_users):
        """Viewer can get a single module source by ID."""
        mock_svc = MagicMock()
        mock_svc.get_source.return_value = _make_source_response(id=5, name="single-source")
        mock_svc_cls.return_value = mock_svc

        response = client.get("/api/module-sources/5", headers=viewer_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["id"] == 5
        assert data["name"] == "single-source"
        mock_svc.get_source.assert_called_once_with(5)


class TestCreateModuleSource:
    """POST /api/module-sources."""

    @patch("routes.module_sources.ModuleSourceService")
    def test_create_source(self, mock_svc_cls, client, operator_headers, all_test_users):
        """Operator can create a new module source."""
        mock_svc = MagicMock()
        mock_svc.create_source.return_value = _make_source_response(
            id=10, name="new-source", url="https://git.example.com/repo.git",
        )
        mock_svc_cls.return_value = mock_svc

        response = client.post(
            "/api/module-sources",
            json={
                "name": "new-source",
                "source_type": "git",
                "url": "https://git.example.com/repo.git",
            },
            headers=operator_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["name"] == "new-source"
        mock_svc.create_source.assert_called_once()


class TestDeleteModuleSource:
    """DELETE /api/module-sources/{source_id}."""

    @patch("routes.module_sources.ModuleSourceService")
    def test_delete_source(self, mock_svc_cls, client, operator_headers, all_test_users):
        """Operator can delete a module source."""
        mock_svc = MagicMock()
        mock_svc.delete_source.return_value = {"success": True, "message": "Deleted"}
        mock_svc_cls.return_value = mock_svc

        response = client.delete("/api/module-sources/5", headers=operator_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        mock_svc.delete_source.assert_called_once_with(5)


class TestSyncModuleSource:
    """POST /api/module-sources/{source_id}/sync."""

    @patch("routes.module_sources.ModuleSourceService")
    def test_sync_source_returns_richer_manifest_results(self, mock_svc_cls, client, operator_headers, all_test_users):
        mock_svc = MagicMock()
        mock_svc.sync_source.return_value = {
            "success": True,
            "source_id": 7,
            "source_name": "manifest-source",
            "sync_status": "success",
            "results": {
                "modules_found": 2,
                "modules_created": 1,
                "modules_updated": 0,
                "errors": ["Error [manifest_parse_validate] packs/bad/bnkforge.pack.json: invalid manifest"],
                "sync_mode": "manifest",
                "manifest_sync_used": True,
                "pack_manifests_discovered": 2,
                "stale_modules_inactivated": 1,
                "blueprint_auto_sync": {
                    "source_id": 3,
                    "source_name": "manifest-source blueprints",
                    "created": True,
                    "sync_status": "success",
                    "results": {
                        "blueprints_found": 1,
                        "releases_created": 1,
                        "releases_existing": 0,
                        "releases_invalid": 0,
                        "errors": [],
                    },
                },
                "pack_errors": [
                    {"path": "packs/bad", "stage": "manifest_parse_validate", "message": "invalid manifest"}
                ],
            },
        }
        mock_svc_cls.return_value = mock_svc

        response = client.post("/api/module-sources/7/sync", headers=operator_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert data["results"]["sync_mode"] == "manifest"
        assert data["results"]["manifest_sync_used"] is True
        assert data["results"]["pack_manifests_discovered"] == 2
        assert data["results"]["stale_modules_inactivated"] == 1
        assert data["results"]["blueprint_auto_sync"]["results"]["blueprints_found"] == 1
        assert data["results"]["pack_errors"][0]["path"] == "packs/bad"
        mock_svc.sync_source.assert_called_once_with(7)


class TestValidateModuleSourceCredentials:
    @patch("routes.module_sources.ModuleSourceService")
    def test_validate_credentials(self, mock_svc_cls, client, operator_headers, all_test_users):
        mock_svc = MagicMock()
        mock_svc.validate_source_credentials.return_value = {
            "success": True,
            "source_id": 7,
            "source_name": "github-private-source",
            "credential_type": "github_app",
            "credential_scope": "github_app_installation:67890:repo:example-org/infra-modules",
            "credential_validation_status": "valid",
            "credential_last_validated_at": datetime.now(UTC).isoformat(),
            "provider": "github",
            "validation": {
                "repository_full_name": "example-org/infra-modules",
                "repository_private": True,
                "permissions": {"contents": "read"},
                "token_expires_at": "2030-01-01T00:00:00Z",
            },
        }
        mock_svc_cls.return_value = mock_svc

        response = client.post("/api/module-sources/7/validate-credentials", headers=operator_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["credential_type"] == "github_app"
        assert data["provider"] == "github"
        mock_svc.validate_source_credentials.assert_called_once()

    @patch("routes.module_sources.ModuleSourceService")
    def test_validate_gitlab_credentials_success_shape(self, mock_svc_cls, client, operator_headers, all_test_users):
        mock_svc = MagicMock()
        mock_svc.validate_source_credentials.return_value = {
            "success": True,
            "source_id": 11,
            "source_name": "gitlab-private-source",
            "credential_type": "gitlab_project_token",
            "credential_scope": "gitlab_project:gitlab.example.com:repo:team/repo",
            "credential_validation_status": "valid",
            "credential_last_validated_at": datetime.now(UTC).isoformat(),
            "provider": "gitlab",
            "validation": {
                "host": "gitlab.example.com",
                "project_path": "team/repo",
                "group_path": None,
                "project_id": 123,
                "project_visibility": "private",
                "default_branch": "main",
                "namespace_full_path": "team",
                "token_class": "project_access_token",
            },
        }
        mock_svc_cls.return_value = mock_svc

        response = client.post("/api/module-sources/11/validate-credentials", headers=operator_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["provider"] == "gitlab"
        assert data["credential_type"] == "gitlab_project_token"
        assert data["validation"]["token_class"] == "project_access_token"

    @patch("routes.module_sources.ModuleSourceService")
    def test_validate_gitlab_credentials_failure_shape(self, mock_svc_cls, client, operator_headers, all_test_users):
        from core.errors import BadRequestError

        mock_svc = MagicMock()
        mock_svc.validate_source_credentials.side_effect = BadRequestError(
            "GitLab credential validation failed [authz_scope_failure]: denied",
            code="GITLAB_VALIDATION_FAILED",
        )
        mock_svc_cls.return_value = mock_svc

        response = client.post("/api/module-sources/11/validate-credentials", headers=operator_headers)
        assert response.status_code == 400
        payload = response.json()
        assert payload["error"]["code"] == "GITLAB_VALIDATION_FAILED"
        assert "GitLab credential validation failed" in payload["error"]["message"]

    @patch("routes.module_sources.ModuleSourceService")
    def test_validate_ssh_credentials_success_shape(self, mock_svc_cls, client, operator_headers, all_test_users):
        mock_svc = MagicMock()
        mock_svc.validate_source_credentials.return_value = {
            "success": True,
            "source_id": 12,
            "source_name": "internal-git-source",
            "credential_type": "ssh_deploy_key",
            "credential_scope": "ssh_host:git.internal.example.com:repo:team/repo.git",
            "credential_validation_status": "valid",
            "credential_last_validated_at": datetime.now(UTC).isoformat(),
            "provider": "ssh",
            "validation": {
                "provider": "ssh",
                "host": "git.internal.example.com",
                "repo_path": "team/repo.git",
                "reachability": "reachable",
                "host_key_trust": "trusted",
                "auth": "valid",
            },
        }
        mock_svc_cls.return_value = mock_svc

        response = client.post("/api/module-sources/12/validate-credentials", headers=operator_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["provider"] == "ssh"
        assert data["credential_type"] == "ssh_deploy_key"
        assert data["validation"]["host_key_trust"] == "trusted"

    @patch("routes.module_sources.ModuleSourceService")
    def test_validate_oauth_credentials_success_shape(self, mock_svc_cls, client, operator_headers, all_test_users):
        mock_svc = MagicMock()
        mock_svc.validate_source_credentials.return_value = {
            "success": True,
            "source_id": 13,
            "source_name": "oauth-source",
            "credential_type": "oauth_token",
            "credential_scope": "oauth:github:repo:example-org/repo",
            "credential_validation_status": "valid",
            "credential_last_validated_at": datetime.now(UTC).isoformat(),
            "provider": "github",
            "validation": {
                "provider": "github",
                "recommendation_tier": "supported_non_default",
                "recommended_default_credential_types": ["github_app", "ssh_deploy_key"],
            },
        }
        mock_svc_cls.return_value = mock_svc

        response = client.post("/api/module-sources/13/validate-credentials", headers=operator_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["provider"] == "github"
        assert data["credential_type"] == "oauth_token"
        assert data["validation"]["recommendation_tier"] == "supported_non_default"


class TestModuleSourceRBAC:
    """RBAC enforcement for module source endpoints."""

    def test_viewer_cannot_create(self, client, viewer_headers, all_test_users):
        """Viewer cannot create a module source — returns 403."""
        response = client.post(
            "/api/module-sources",
            json={
                "name": "blocked-source",
                "source_type": "git",
                "url": "https://git.example.com/repo.git",
            },
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_viewer_cannot_delete(self, client, viewer_headers, all_test_users):
        """Viewer cannot delete a module source — returns 403."""
        response = client.delete("/api/module-sources/1", headers=viewer_headers)
        assert response.status_code == 403

    def test_unauthenticated_cannot_list(self, client):
        """Unauthenticated request returns 401."""
        response = client.get("/api/module-sources")
        assert response.status_code == 401
