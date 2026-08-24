"""
Integration tests for credential template routes — /api/credential-templates.

Covers: list, create, get, update, delete templates, and RBAC enforcement.
Endpoints delegate to CredentialTemplateService so the service is mocked.
Uses FastAPI TestClient with real SQLite DB.
"""

from datetime import UTC, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


def _make_template_response(**overrides):
    """Return a dict that looks like a CredentialTemplateResponse."""
    base = {
        "id": 1,
        "name": "AWS Production",
        "description": "Prod credentials",
        "provider": "aws",
        "aws_auth_method": "access_key",
        "aws_profile": None,
        "region": "us-east-1",
        "aws_access_key_id": "AKIA***",
        "has_aws_secret_access_key": True,
        "has_aws_session_token": False,
        "aws_sso_enabled": False,
        "aws_sso_start_url": None,
        "aws_sso_region": None,
        "aws_sso_account_id": None,
        "aws_sso_role_name": None,
        "aws_sso_authenticated_at": None,
        "aws_sso_token_expiry": None,
        "aws_credentials_expiry": None,
        "gcp_project_id": None,
        "has_gcp_credentials": False,
        "azure_subscription_id": None,
        "azure_tenant_id": None,
        "has_azure_credentials": False,
        "has_ibmcloud_api_key": False,
        "ssh_host": None,
        "ssh_port": None,
        "ssh_username": None,
        "ssh_auth_type": None,
        "has_ssh_password": False,
        "has_ssh_key": False,
        "is_default": False,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "projects_count": 0,
    }
    base.update(overrides)
    return base


class TestListCredentialTemplates:
    """GET /api/credential-templates."""

    @patch("routes.credential_templates.CredentialTemplateService")
    def test_list_templates(self, mock_svc_cls, client, viewer_headers, all_test_users):
        """Viewer can list credential templates."""
        mock_svc = MagicMock()
        mock_svc.list_templates.return_value = [
            _make_template_response(id=1, name="AWS Prod"),
            _make_template_response(id=2, name="GCP Staging", provider="gcp"),
        ]
        mock_svc_cls.return_value = mock_svc

        response = client.get("/api/credential-templates", headers=viewer_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["name"] == "AWS Prod"


class TestCreateCredentialTemplate:
    """POST /api/credential-templates."""

    @patch("routes.credential_templates.CredentialTemplateService")
    def test_create_template(self, mock_svc_cls, client, operator_headers, all_test_users):
        """Operator can create a credential template — returns 201."""
        mock_svc = MagicMock()
        mock_svc.create_template.return_value = _make_template_response(
            id=10, name="New Template", provider="aws"
        )
        mock_svc_cls.return_value = mock_svc

        response = client.post(
            "/api/credential-templates",
            json={"name": "New Template", "provider": "aws"},
            headers=operator_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "New Template"
        assert data["provider"] == "aws"
        mock_svc.create_template.assert_called_once()

    @patch("routes.credential_templates.CredentialTemplateService")
    def test_create_ibm_template(self, mock_svc_cls, client, operator_headers, all_test_users):
        mock_svc = MagicMock()
        mock_svc.create_template.return_value = _make_template_response(
            id=11,
            name="IBM Production",
            provider="ibm",
            region="us-south",
            has_ibmcloud_api_key=True,
            aws_auth_method=None,
            aws_access_key_id=None,
        )
        mock_svc_cls.return_value = mock_svc

        response = client.post(
            "/api/credential-templates",
            json={"name": "IBM Production", "provider": "ibm", "region": "us-south", "ibmcloud_api_key": "secret"},
            headers=operator_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["provider"] == "ibm"
        assert data["has_ibmcloud_api_key"] is True

    @patch("routes.credential_templates.CredentialTemplateService")
    def test_create_rejects_misspelled_provider_with_422(self, mock_svc_cls, client, operator_headers, all_test_users):
        """Issue #191: provider='ibmcloud' is a 422 at the API boundary, not a
        silently-empty template — and the service is never reached."""
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc

        response = client.post(
            "/api/credential-templates",
            json={"name": "IBM ROKs Testing", "provider": "ibmcloud",
                  "region": "us-east", "ibmcloud_api_key": "secret"},
            headers=operator_headers,
        )
        assert response.status_code == 422
        body = response.json()
        # The validation error names the bad provider and the supported set.
        detail = str(body["detail"])
        assert "ibmcloud" in detail
        assert "ibm" in detail
        mock_svc.create_template.assert_not_called()


class TestGetCredentialTemplate:
    """GET /api/credential-templates/{id}."""

    @patch("routes.credential_templates.CredentialTemplateService")
    def test_get_template(self, mock_svc_cls, client, viewer_headers, all_test_users):
        """Viewer can get a single credential template by ID."""
        mock_svc = MagicMock()
        mock_svc.get_template.return_value = _make_template_response(
            id=5, name="SSH On-Prem", provider="ssh"
        )
        mock_svc_cls.return_value = mock_svc

        response = client.get("/api/credential-templates/5", headers=viewer_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 5
        assert data["name"] == "SSH On-Prem"


class TestDeleteCredentialTemplate:
    """DELETE /api/credential-templates/{id}."""

    @patch("routes.credential_templates.CredentialTemplateService")
    def test_delete_template(self, mock_svc_cls, client, operator_headers, all_test_users):
        """Operator can delete a credential template — returns 204."""
        mock_svc = MagicMock()
        mock_svc.delete_template.return_value = None
        mock_svc_cls.return_value = mock_svc

        response = client.delete("/api/credential-templates/5", headers=operator_headers)
        assert response.status_code == 204
        mock_svc.delete_template.assert_called_once_with(5)


class TestCredentialTemplateRBAC:
    """RBAC enforcement — viewer cannot create/update/delete."""

    def test_viewer_cannot_create(self, client, viewer_headers, all_test_users):
        """Viewer cannot create templates — returns 403."""
        response = client.post(
            "/api/credential-templates",
            json={"name": "Sneaky Template", "provider": "aws"},
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_viewer_cannot_update(self, client, viewer_headers, all_test_users):
        """Viewer cannot update templates — returns 403."""
        response = client.put(
            "/api/credential-templates/1",
            json={"name": "Renamed"},
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_viewer_cannot_delete(self, client, viewer_headers, all_test_users):
        """Viewer cannot delete templates — returns 403."""
        response = client.delete(
            "/api/credential-templates/1",
            headers=viewer_headers,
        )
        assert response.status_code == 403
