"""
Component tests for AzureAuthService — Service Principal auth, Device Code SSO,
token refresh, subscription discovery, and region listing.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from services.azure_auth_service import AzureAuthError, AzureAuthService, ServiceError


class TestInitiateDeviceAuth:
    @patch("services.azure_auth_service.requests.post")
    def test_initiate_success_custom_tenant(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "device_code": "dev-code-123",
            "user_code": "USER123",
            "verification_uri": "https://microsoft.com/devicelogin",
            "expires_in": 900,
            "interval": 5,
            "message": "To sign in, use a web browser to open the page https://microsoft.com/devicelogin and enter the code USER123 to authenticate.",
        }
        mock_post.return_value = mock_resp

        svc = AzureAuthService()
        result = svc.initiate_device_authorization(
            tenant_id="custom-tenant-id",
            client_id="custom-client-id",
        )

        assert result["device_code"] == "dev-code-123"
        assert result["user_code"] == "USER123"
        assert result["verification_uri"] == "https://microsoft.com/devicelogin"
        assert result["verification_uri_complete"] == "https://microsoft.com/devicelogin?otc=USER123"
        assert result["expires_in"] == 900
        assert result["interval"] == 5
        assert result["tenant_id"] == "custom-tenant-id"
        assert result["client_id"] == "custom-client-id"

        mock_post.assert_called_once()
        url = mock_post.call_args[0][0]
        assert "custom-tenant-id/oauth2/v2.0/devicecode" in url

    @patch("services.azure_auth_service.requests.post")
    def test_initiate_default_tenant_and_client(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "device_code": "dev-code-default",
            "user_code": "DEF123",
            "verification_uri": "https://microsoft.com/devicelogin",
            "expires_in": 900,
            "interval": 5,
        }
        mock_post.return_value = mock_resp

        svc = AzureAuthService()
        result = svc.initiate_device_authorization()

        assert result["tenant_id"] == "common"
        assert result["client_id"] == "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
        url = mock_post.call_args[0][0]
        assert "common/oauth2/v2.0/devicecode" in url

    @patch("services.azure_auth_service.requests.post")
    def test_initiate_failure_raises(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.json.return_value = {"error": "invalid_client", "error_description": "Invalid client ID"}
        mock_resp.text = '{"error": "invalid_client"}'
        mock_post.return_value = mock_resp

        svc = AzureAuthService()
        with pytest.raises(ServiceError, match="Azure device authorization failed"):
            svc.initiate_device_authorization()


class TestPollDeviceAuth:
    @patch("services.azure_auth_service.requests.post")
    def test_poll_pending_authorization(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {"error": "authorization_pending"}
        mock_post.return_value = mock_resp

        svc = AzureAuthService()
        result = svc.poll_for_token(device_code="dev-code-123")

        assert result["pending"] is True
        assert result["error"] == "authorization_pending"

    @patch("services.azure_auth_service.requests.post")
    def test_poll_pending_slow_down(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {"error": "slow_down"}
        mock_post.return_value = mock_resp

        svc = AzureAuthService()
        result = svc.poll_for_token(device_code="dev-code-123")

        assert result["pending"] is True

    @patch("services.azure_auth_service.requests.post")
    def test_poll_expired_token(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {
            "error": "expired_token",
            "error_description": "Device code has expired",
        }
        mock_post.return_value = mock_resp

        svc = AzureAuthService()
        with pytest.raises(ServiceError, match="expired"):
            svc.poll_for_token(device_code="dev-code-123")

    @patch("services.azure_auth_service.requests.post")
    def test_poll_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "bearer-token-xyz",
            "refresh_token": "refresh-token-abc",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        mock_post.return_value = mock_resp

        svc = AzureAuthService()
        result = svc.poll_for_token(
            device_code="dev-code-123",
            tenant_id="tenant-123",
            client_id="client-123",
        )

        assert result["pending"] is False
        assert result["access_token"] == "bearer-token-xyz"
        assert result["refresh_token"] == "refresh-token-abc"
        assert result["expires_in"] == 3600
        assert "expires_at" in result


class TestRefreshToken:
    @patch("services.azure_auth_service.requests.post")
    def test_refresh_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "access_token": "new-bearer-token",
            "refresh_token": "new-refresh-token",
            "expires_in": 3600,
        }
        mock_post.return_value = mock_resp

        svc = AzureAuthService()
        result = svc.refresh_credentials(refresh_token="old-refresh-token")

        assert result["access_token"] == "new-bearer-token"
        assert result["refresh_token"] == "new-refresh-token"
        assert result["expires_in"] == 3600

    @patch("services.azure_auth_service.requests.post")
    def test_refresh_failure_raises(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.json.return_value = {"error": "invalid_grant", "error_description": "Invalid refresh token"}
        mock_resp.text = '{"error": "invalid_grant"}'
        mock_post.return_value = mock_resp

        svc = AzureAuthService()
        with pytest.raises(ServiceError, match="Failed to refresh Azure token"):
            svc.refresh_credentials(refresh_token="bad-refresh-token")


class TestServicePrincipalToken:
    @patch("services.azure_auth_service.requests.post")
    def test_sp_token_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "access_token": "sp-access-token",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        mock_post.return_value = mock_resp

        svc = AzureAuthService()
        result = svc.acquire_service_principal_token(
            tenant_id="tenant-id",
            client_id="client-id",
            client_secret="client-secret",
        )

        assert result["access_token"] == "sp-access-token"
        assert result["expires_in"] == 3600
        mock_post.assert_called_once()
        data = mock_post.call_args[1]["data"]
        assert data["grant_type"] == "client_credentials"
        assert data["client_id"] == "client-id"
        assert data["client_secret"] == "client-secret"

    @patch("services.azure_auth_service.requests.post")
    def test_sp_token_failure_raises(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.json.return_value = {"error": "invalid_client", "error_description": "Bad secret"}
        mock_resp.text = '{"error": "invalid_client"}'
        mock_post.return_value = mock_resp

        svc = AzureAuthService()
        with pytest.raises(ServiceError, match="Service principal authentication failed"):
            svc.acquire_service_principal_token(
                tenant_id="tenant-id",
                client_id="client-id",
                client_secret="wrong-secret",
            )


class TestARMOperations:
    @patch("services.azure_auth_service.requests.get")
    def test_list_subscriptions_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "value": [
                {
                    "subscriptionId": "sub-1",
                    "displayName": "Production Sub",
                    "state": "Enabled",
                },
                {
                    "subscriptionId": "sub-2",
                    "displayName": "Dev Sub",
                    "state": "Disabled",
                },
            ]
        }
        mock_get.return_value = mock_resp

        svc = AzureAuthService()
        subs = svc.list_subscriptions(access_token="test-token")

        assert len(subs) == 2
        assert subs[0]["subscription_id"] == "sub-1"
        assert subs[0]["display_name"] == "Production Sub"
        assert subs[0]["state"] == "Enabled"

    @patch("services.azure_auth_service.requests.get")
    def test_validate_subscription_access_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "subscriptionId": "sub-1",
            "displayName": "Production Sub",
            "state": "Enabled",
        }
        mock_get.return_value = mock_resp

        svc = AzureAuthService()
        info = svc.validate_subscription_access(
            access_token="test-token",
            subscription_id="sub-1",
        )

        assert info["subscription_id"] == "sub-1"
        assert info["display_name"] == "Production Sub"
        assert info["state"] == "Enabled"

    @patch("services.azure_auth_service.requests.get")
    def test_list_locations_from_arm(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "value": [
                {"name": "eastus", "displayName": "East US"},
                {"name": "westus2", "displayName": "West US 2"},
            ]
        }
        mock_get.return_value = mock_resp

        svc = AzureAuthService()
        locs = svc.list_locations(
            access_token="test-token",
            subscription_id="sub-1",
        )

        assert len(locs) == 2
        assert locs[0] == {"value": "eastus", "label": "East US (eastus)"}
        assert locs[1] == {"value": "westus2", "label": "West US 2 (westus2)"}

    def test_list_locations_fallback(self):
        svc = AzureAuthService()
        locs = svc.get_common_azure_regions()
        assert len(locs) > 10
        values = [loc["value"] for loc in locs]
        assert "eastus" in values
        assert "westeurope" in values
