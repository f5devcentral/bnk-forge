"""
Integration tests for module registry routes — /api/registry.

Covers: search modules, get module detail, get versions, import module, RBAC.
All endpoints delegate to RegistryService (mocked).
Uses FastAPI TestClient with real SQLite DB.
"""

from unittest.mock import MagicMock, patch

import pytest


class TestSearchRegistryModules:
    """GET /api/registry/search."""

    @patch("routes.registry.RegistryService")
    def test_search_modules(self, mock_svc_cls, client, viewer_headers, all_test_users):
        """Viewer can search registry modules."""
        mock_svc = MagicMock()
        mock_svc.search_modules.return_value = {
            "modules": [
                {
                    "id": "hashicorp/consul/aws",
                    "namespace": "hashicorp",
                    "name": "consul",
                    "provider": "aws",
                    "version": "0.11.0",
                    "description": "HashiCorp Consul cluster on AWS",
                    "downloads": 500000,
                    "verified": True,
                },
                {
                    "id": "terraform-aws-modules/vpc/aws",
                    "namespace": "terraform-aws-modules",
                    "name": "vpc",
                    "provider": "aws",
                    "version": "5.1.0",
                    "description": "AWS VPC Terraform module",
                    "downloads": 2000000,
                    "verified": True,
                },
            ],
            "total": 2,
        }
        mock_svc_cls.return_value = mock_svc

        response = client.get(
            "/api/registry/search?q=aws",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "modules" in data
        assert len(data["modules"]) == 2
        assert data["modules"][0]["namespace"] == "hashicorp"
        mock_svc.search_modules.assert_called_once()


class TestGetRegistryModuleDetail:
    """GET /api/registry/modules/{ns}/{name}/{provider}."""

    @patch("routes.registry.RegistryService")
    def test_get_module_detail(self, mock_svc_cls, client, viewer_headers, all_test_users):
        """Viewer can get detailed info for a specific registry module."""
        mock_svc = MagicMock()
        mock_svc.get_module_details.return_value = {
            "id": "hashicorp/consul/aws",
            "namespace": "hashicorp",
            "name": "consul",
            "provider": "aws",
            "version": "0.11.0",
            "description": "HashiCorp Consul cluster on AWS",
            "source": "https://github.com/hashicorp/terraform-aws-consul",
            "published_at": "2023-06-01T00:00:00Z",
            "downloads": 500000,
            "verified": True,
            "inputs": [{"name": "cluster_size", "type": "number", "default": "3"}],
            "outputs": [{"name": "consul_endpoint", "description": "Consul UI address"}],
        }
        mock_svc_cls.return_value = mock_svc

        response = client.get(
            "/api/registry/modules/hashicorp/consul/aws",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["namespace"] == "hashicorp"
        assert data["name"] == "consul"
        assert data["provider"] == "aws"
        assert len(data["inputs"]) == 1

    @patch("routes.registry.RegistryService")
    def test_get_module_not_found(self, mock_svc_cls, client, viewer_headers, all_test_users):
        """Requesting nonexistent module returns 404."""
        mock_svc = MagicMock()
        mock_svc.get_module_details.return_value = None
        mock_svc_cls.return_value = mock_svc

        response = client.get(
            "/api/registry/modules/fake/nonexistent/aws",
            headers=viewer_headers,
        )
        assert response.status_code == 404


class TestGetRegistryModuleVersions:
    """GET /api/registry/modules/{ns}/{name}/{provider}/versions."""

    @patch("routes.registry.RegistryService")
    def test_get_module_versions(self, mock_svc_cls, client, viewer_headers, all_test_users):
        """Viewer can list available versions for a registry module."""
        mock_svc = MagicMock()
        mock_svc.get_module_versions.return_value = [
            {"version": "5.1.0", "published_at": "2023-08-01"},
            {"version": "5.0.0", "published_at": "2023-06-15"},
            {"version": "4.0.2", "published_at": "2023-03-01"},
        ]
        mock_svc_cls.return_value = mock_svc

        response = client.get(
            "/api/registry/modules/terraform-aws-modules/vpc/aws/versions",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["namespace"] == "terraform-aws-modules"
        assert data["name"] == "vpc"
        assert data["provider"] == "aws"
        assert len(data["versions"]) == 3


class TestImportRegistryModule:
    """POST /api/registry/import."""

    @patch("routes.registry.RegistryService")
    def test_import_module(self, mock_svc_cls, client, operator_headers, all_test_users):
        """Operator can import a module from the registry into the library."""
        mock_module = MagicMock()
        mock_module.id = 10
        mock_module.name = "consul"
        mock_module.category = "networking"
        mock_module.provider = "aws"
        mock_module.version = "0.11.0"
        mock_module.description = "HashiCorp Consul cluster"
        mock_module.latest_version = "0.11.0"
        mock_module.update_available = False

        mock_svc = MagicMock()
        mock_svc.import_module_from_registry.return_value = mock_module
        mock_svc_cls.return_value = mock_svc

        response = client.post(
            "/api/registry/import",
            json={
                "namespace": "hashicorp",
                "name": "consul",
                "provider": "aws",
                "version": "0.11.0",
            },
            headers=operator_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["module"]["name"] == "consul"
        assert data["module"]["provider"] == "aws"
        mock_svc.import_module_from_registry.assert_called_once()


class TestRegistryRBAC:
    """RBAC enforcement — viewer cannot import modules."""

    def test_viewer_cannot_import(self, client, viewer_headers, all_test_users):
        """Viewer cannot import registry modules — returns 403."""
        response = client.post(
            "/api/registry/import",
            json={
                "namespace": "hashicorp",
                "name": "consul",
                "provider": "aws",
            },
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_unauthenticated_search(self, client):
        """Unauthenticated request to search returns 401."""
        response = client.get("/api/registry/search?q=vpc")
        assert response.status_code == 401
