"""
Integration tests for cloud auth routes — /api/cloud-auth.

Covers: AWS SSO initiation, account listing, credential status,
SSH connection testing, and RBAC enforcement (operator-only).
ALL endpoints require operator role (router-level dependency).
Uses FastAPI TestClient with real SQLite DB.
"""

from unittest.mock import MagicMock, patch

import pytest


class TestAWSSSOInitiate:
    """POST /api/cloud-auth/aws/sso/initiate."""

    @patch("routes.cloud_auth.AWSAuthService")
    def test_initiate_sso(self, mock_aws_cls, client, operator_headers, all_test_users):
        """Operator can initiate AWS SSO device authorization flow."""
        mock_svc = MagicMock()
        mock_svc.initiate_device_authorization.return_value = {
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://device.sso.us-east-1.amazonaws.com/",
            "verification_uri_complete": "https://device.sso.us-east-1.amazonaws.com/?user_code=ABCD-EFGH",
            "expires_in": 600,
            "interval": 5,
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "device_code": "test-device-code",
            "region": "us-east-1",
        }
        mock_aws_cls.return_value = mock_svc

        response = client.post(
            "/api/cloud-auth/aws/sso/initiate",
            json={"start_url": "https://my-org.awsapps.com/start"},
            headers=operator_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["user_code"] == "ABCD-EFGH"
        assert data["data"]["device_code"] == "test-device-code"
        mock_svc.initiate_device_authorization.assert_called_once()


class TestIBMRegionDiscovery:
    """IBM region discovery endpoints."""

    @patch("routes.cloud_auth.IBMCloudService")
    def test_query_ibm_regions(self, mock_ibm_cls, client, operator_headers, all_test_users):
        mock_svc = MagicMock()
        mock_svc.list_regions.return_value = [
            {"value": "us-south", "label": "US South (Dallas)"},
            {"value": "eu-de", "label": "EU Germany (Frankfurt)"},
        ]
        mock_ibm_cls.return_value = mock_svc

        response = client.post(
            "/api/cloud-auth/regions/query",
            json={"provider": "ibm", "ibmcloud_api_key": "secret"},
            headers=operator_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["provider"] == "ibm"
        assert len(data["regions"]) == 2
        mock_svc.list_regions.assert_called_once_with("secret")

    @patch("routes.cloud_auth.IBMCloudService")
    def test_list_ibm_regions_from_default_or_template(self, mock_ibm_cls, client, operator_headers, all_test_users):
        mock_svc = MagicMock()
        mock_svc.list_regions_from_template.return_value = [
            {"value": "us-east", "label": "US East (Washington DC)"},
        ]
        mock_ibm_cls.return_value = mock_svc

        response = client.get(
            "/api/cloud-auth/regions/ibm?template_id=7",
            headers=operator_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["provider"] == "ibm"
        assert data["regions"][0]["value"] == "us-east"
        mock_svc.list_regions_from_template.assert_called_once_with(template_id=7)

    @patch("routes.cloud_auth.IBMCloudService")
    def test_query_ibm_cos_instances(self, mock_ibm_cls, client, operator_headers, all_test_users):
        mock_svc = MagicMock()
        mock_svc.list_cos_instances.return_value = [
            {"value": "cos-prod", "label": "cos-prod", "crn": "crn:test"},
        ]
        mock_ibm_cls.return_value = mock_svc

        response = client.post(
            "/api/cloud-auth/ibm/cos-instances/query",
            json={"ibmcloud_api_key": "secret"},
            headers=operator_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["instances"][0]["value"] == "cos-prod"
        mock_svc.list_cos_instances.assert_called_once_with("secret")


class TestAWSListAccounts:
    """POST /api/cloud-auth/aws/accounts."""

    @patch("routes.cloud_auth.AWSAuthService")
    def test_list_aws_accounts(self, mock_aws_cls, client, operator_headers, all_test_users):
        """Operator can list AWS accounts with a valid access token."""
        mock_svc = MagicMock()
        mock_svc.list_accounts.return_value = [
            {"accountId": "111111111111", "accountName": "Production", "emailAddress": "prod@example.com"},
            {"accountId": "222222222222", "accountName": "Staging", "emailAddress": "staging@example.com"},
        ]
        mock_aws_cls.return_value = mock_svc

        response = client.post(
            "/api/cloud-auth/aws/accounts",
            json={"access_token": "mock-access-token"},
            headers=operator_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["count"] == 2
        assert data["accounts"][0]["accountId"] == "111111111111"


class TestAWSCredentialsStatus:
    """GET /api/cloud-auth/aws/credentials/{project_id}."""

    def test_get_credentials_not_configured(
        self, client, operator_headers, all_test_users, sample_project
    ):
        """Returns configured=False when project has no AWS credentials."""
        response = client.get(
            f"/api/cloud-auth/aws/credentials/{sample_project.id}",
            headers=operator_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["configured"] is False

    def test_wrong_provider_default_reports_not_configured(
        self, client, operator_headers, all_test_users, db
    ):
        """An AWS project with only a GCP is_default template must report not-configured.

        Before this fix, the GCP default would be picked up and configured=True
        would be returned for the AWS project, hiding the fact that no AWS
        credentials are available.
        """
        from models import CloudCredentialTemplate, Project

        # Create an AWS project with no credential binding
        aws_project = Project(
            name="AWS No Creds",
            project_type="cloud-aws",
            cloud_provider="aws",
            environment="dev",
            backend_type="local",
            color="#000",
            icon="cloud",
            is_active=False,
        )
        db.add(aws_project)

        # Only a GCP is_default template exists — no AWS default
        gcp_default = CloudCredentialTemplate(
            name="GCP Default Template",
            provider="gcp",
            is_default=True,
        )
        db.add(gcp_default)
        db.commit()
        db.refresh(aws_project)

        response = client.get(
            f"/api/cloud-auth/aws/credentials/{aws_project.id}",
            headers=operator_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        # The GCP default must NOT be reported as configured AWS credentials
        assert data["configured"] is False
        assert data["source"] in ("none", "template")  # no default_template from wrong provider


class TestDeleteAWSCredentials:
    """DELETE /api/cloud-auth/aws/credentials/{project_id}."""

    def test_delete_credentials(
        self, client, operator_headers, all_test_users, sample_project
    ):
        """Operator can delete AWS credentials from a project."""
        response = client.delete(
            f"/api/cloud-auth/aws/credentials/{sample_project.id}",
            headers=operator_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "removed" in data["message"].lower()


class TestSSHTestConnection:
    """POST /api/cloud-auth/ssh/test."""

    @patch("routes.cloud_auth.SSHService")
    def test_ssh_test_connection_success(self, mock_ssh_cls, client, operator_headers, all_test_users):
        """Operator can test an SSH connection with valid credentials."""
        mock_svc = MagicMock()
        mock_svc.test_connection.return_value = (True, "Connection successful")
        mock_ssh_cls.return_value = mock_svc

        response = client.post(
            "/api/cloud-auth/ssh/test",
            json={
                "host": "192.168.1.100",
                "username": "deploy",
                "auth_type": "password",
                "password": "secret",
            },
            headers=operator_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        mock_svc.test_connection.assert_called_once()


class TestCloudAuthRBAC:
    """RBAC enforcement — viewer role must be denied on all endpoints."""

    def test_viewer_denied_sso_initiate(self, client, viewer_headers, all_test_users):
        """Viewer cannot initiate SSO — router-level require_operator returns 403."""
        response = client.post(
            "/api/cloud-auth/aws/sso/initiate",
            json={"start_url": "https://my-org.awsapps.com/start"},
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_viewer_denied_list_accounts(self, client, viewer_headers, all_test_users):
        """Viewer cannot list AWS accounts — returns 403."""
        response = client.post(
            "/api/cloud-auth/aws/accounts",
            json={"access_token": "mock-token"},
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_viewer_denied_get_credentials(
        self, client, viewer_headers, all_test_users, sample_project
    ):
        """Viewer cannot read credential status — returns 403."""
        response = client.get(
            f"/api/cloud-auth/aws/credentials/{sample_project.id}",
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_viewer_denied_ssh_test(self, client, viewer_headers, all_test_users):
        """Viewer cannot test SSH connections — returns 403."""
        response = client.post(
            "/api/cloud-auth/ssh/test",
            json={
                "host": "10.0.0.1",
                "username": "root",
                "auth_type": "password",
                "password": "hack",
            },
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_viewer_denied_ibm_region_query(self, client, viewer_headers, all_test_users):
        response = client.post(
            "/api/cloud-auth/regions/query",
            json={"provider": "ibm", "ibmcloud_api_key": "secret"},
            headers=viewer_headers,
        )
        assert response.status_code == 403
