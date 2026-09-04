"""
Unit tests for KubernetesServiceBase._generate_azure_token.

Tests pure-Python Azure AKS OAuth2 client credentials token generation
and in-memory caching.
"""

from unittest.mock import patch

from services.kubernetes._base import KubernetesServiceBase


class TestGenerateAzureToken:
    def setup_method(self):
        # Clear token cache before each test
        KubernetesServiceBase._azure_token_cache.clear()

    def test_generates_token_and_caches(self):
        tenant_id = "11111111-2222-3333-4444-555555555555"
        client_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        client_secret = "secret-val"

        with patch("services.azure_oauth_service.request_azure_oauth_token") as mock_oauth:
            mock_oauth.return_value = {
                "access_token": "ey-fake-aks-token",
                "expires_in": 3600,
            }

            token = KubernetesServiceBase._generate_azure_token(tenant_id, client_id, client_secret)

            assert token == "ey-fake-aks-token"
            mock_oauth.assert_called_once_with(
                tenant_id=tenant_id,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "scope": "6dae42f8-4368-4678-94ff-776099604563/.default",
                },
                timeout=15,
            )

            # Second call should return cached token without calling oauth again
            mock_oauth.reset_mock()
            token2 = KubernetesServiceBase._generate_azure_token(tenant_id, client_id, client_secret)
            assert token2 == "ey-fake-aks-token"
            mock_oauth.assert_not_called()
