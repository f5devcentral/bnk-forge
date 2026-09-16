"""
Azure Authentication Service
Handles Azure Entra ID device code flow, Service Principal validation, token refresh, and ARM queries
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from core.errors import AppError

logger = logging.getLogger(__name__)


class AzureAuthError(AppError):
    """Azure authentication or API communication error"""

    def __init__(self, message: str, code: str = "AZURE_AUTH_ERROR", details: dict[str, Any] | None = None):
        super().__init__(
            code=code,
            message=message,
            status_code=400,
            details=details or {},
        )


ServiceError = AzureAuthError

# Standard Azure CLI public client ID (Microsoft Azure Cross-platform Command Line Interface)
DEFAULT_AZURE_CLI_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
DEFAULT_AZURE_MANAGEMENT_SCOPE = "https://management.azure.com/.default offline_access"
ARM_API_VERSION = "2022-12-01"


class AzureAuthService:
    """Service for managing Azure authentication via Service Principals and Entra ID SSO (Device Code Flow)"""

    def initiate_device_authorization(
        self,
        tenant_id: str = "common",
        client_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Initiate Azure Entra ID device authorization flow (RFC 8628).

        Args:
            tenant_id: Azure Tenant / Directory ID (or 'common', 'organizations')
            client_id: Client / Application ID (defaults to Azure CLI public client ID)

        Returns:
            dict containing:
                - device_code: Device verification code
                - user_code: End-user verification code
                - verification_uri: Verification URL (https://microsoft.com/devicelogin)
                - verification_uri_complete: Verification URL with prefilled code if available
                - expires_in: Expiration lifetime in seconds
                - interval: Minimum polling interval in seconds
                - message: Human-readable instructions
                - client_id: Client ID used
                - tenant_id: Tenant ID used
        """
        active_client_id = (client_id or "").strip() or DEFAULT_AZURE_CLI_CLIENT_ID
        active_tenant_id = (tenant_id or "").strip() or "common"

        device_url = f"https://login.microsoftonline.com/{active_tenant_id}/oauth2/v2.0/devicecode"
        payload = {
            "client_id": active_client_id,
            "scope": DEFAULT_AZURE_MANAGEMENT_SCOPE,
        }

        try:
            resp = requests.post(device_url, data=payload, timeout=30)
            if not resp.ok:
                try:
                    err_json = resp.json()
                    err_desc = err_json.get("error_description") or err_json.get("error") or resp.text
                except Exception:
                    err_desc = resp.text
                logger.error(f"Failed to initiate Azure device code authorization: {err_desc}")
                raise ServiceError(f"Azure device authorization failed: {err_desc}")

            data = resp.json()
            user_code = data.get("user_code", "")
            verification_uri = data.get("verification_uri", "https://microsoft.com/devicelogin")

            # Construct verification_uri_complete if not returned directly
            verification_uri_complete = data.get("verification_uri_complete") or (
                f"{verification_uri}?otc={user_code}" if user_code else verification_uri
            )

            logger.info(f"Azure device authorization initiated for tenant '{active_tenant_id}'")

            return {
                "device_code": data["device_code"],
                "user_code": user_code,
                "verification_uri": verification_uri,
                "verification_uri_complete": verification_uri_complete,
                "expires_in": data.get("expires_in", 900),
                "interval": data.get("interval", 5),
                "message": data.get("message", f"To sign in, use a web browser to open the page {verification_uri} and enter the code {user_code} to authenticate."),
                "client_id": active_client_id,
                "tenant_id": active_tenant_id,
            }

        except requests.RequestException as e:
            logger.error(f"Network error initiating Azure device code: {e}")
            raise ServiceError(f"Network error communicating with Azure: {str(e)}")

    def poll_for_token(
        self,
        *,
        device_code: str,
        tenant_id: str = "common",
        client_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Poll the Azure OAuth2 token endpoint for the device code grant.

        Returns:
            dict containing:
                - pending: True if user has not completed authentication yet
                - access_token: string (when authenticated)
                - refresh_token: string (when authenticated)
                - expires_in: int
                - expires_at: ISO timestamp string
                - token_type: Bearer
        """
        active_client_id = (client_id or "").strip() or DEFAULT_AZURE_CLI_CLIENT_ID
        active_tenant_id = (tenant_id or "").strip() or "common"

        token_url = f"https://login.microsoftonline.com/{active_tenant_id}/oauth2/v2.0/token"
        payload = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": active_client_id,
            "device_code": device_code,
        }

        try:
            resp = requests.post(token_url, data=payload, timeout=30)
            data = resp.json()

            if resp.status_code != 200:
                error = data.get("error")
                if error in ("authorization_pending", "slow_down"):
                    return {"pending": True, "error": error}
                elif error == "expired_token":
                    raise ServiceError("Azure device authorization code has expired. Please initiate login again.")
                elif error == "authorization_declined":
                    raise ServiceError("Azure login was declined by the user.")
                else:
                    err_desc = data.get("error_description") or error or resp.text
                    raise ServiceError(f"Azure authentication error: {err_desc}")

            access_token = data.get("access_token")
            if not access_token:
                raise ServiceError("Azure token response did not contain an access_token")

            expires_in = data.get("expires_in", 3600)
            expires_at = (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat()

            return {
                "pending": False,
                "access_token": access_token,
                "refresh_token": data.get("refresh_token"),
                "expires_in": expires_in,
                "expires_at": expires_at,
                "token_type": data.get("token_type", "Bearer"),
            }

        except requests.RequestException as e:
            logger.error(f"Network error polling Azure token: {e}")
            raise ServiceError(f"Network error polling Azure token: {str(e)}")

    def refresh_credentials(
        self,
        *,
        refresh_token: str,
        tenant_id: str = "common",
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> dict[str, Any]:
        """
        Exchange an OAuth2 refresh token for a fresh Azure access token.
        """
        active_client_id = (client_id or "").strip() or DEFAULT_AZURE_CLI_CLIENT_ID
        active_tenant_id = (tenant_id or "").strip() or "common"

        token_url = f"https://login.microsoftonline.com/{active_tenant_id}/oauth2/v2.0/token"
        payload = {
            "grant_type": "refresh_token",
            "client_id": active_client_id,
            "refresh_token": refresh_token,
            "scope": DEFAULT_AZURE_MANAGEMENT_SCOPE,
        }
        if client_secret:
            payload["client_secret"] = client_secret

        try:
            resp = requests.post(token_url, data=payload, timeout=30)
            data = resp.json()

            if not resp.ok:
                err_desc = data.get("error_description") or data.get("error") or resp.text
                logger.error(f"Failed to refresh Azure token: {err_desc}")
                raise ServiceError(f"Failed to refresh Azure token: {err_desc}")

            access_token = data.get("access_token")
            if not access_token:
                raise ServiceError("Refreshed token response missing access_token")

            expires_in = data.get("expires_in", 3600)
            return {
                "access_token": access_token,
                "refresh_token": data.get("refresh_token") or refresh_token,
                "expires_in": expires_in,
                "expires_at": (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat(),
            }

        except requests.RequestException as e:
            logger.error(f"Network error refreshing Azure token: {e}")
            raise ServiceError(f"Network error refreshing Azure token: {str(e)}")

    def acquire_service_principal_token(
        self,
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        scope: str = "https://management.azure.com/.default",
    ) -> dict[str, Any]:
        """
        Acquire OAuth2 token using client credentials grant (Service Principal).
        """
        if not tenant_id or not client_id or not client_secret:
            raise ServiceError("Tenant ID, Client ID, and Client Secret are all required for Service Principal auth")

        token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope,
        }

        try:
            resp = requests.post(token_url, data=payload, timeout=30)
            data = resp.json()

            if not resp.ok:
                err_desc = data.get("error_description") or data.get("error") or resp.text
                logger.error(f"Service principal token exchange failed: {err_desc}")
                raise ServiceError(f"Service principal authentication failed: {err_desc}")

            access_token = data.get("access_token")
            if not access_token:
                raise ServiceError("Service principal token response missing access_token")

            expires_in = data.get("expires_in", 3600)
            return {
                "access_token": access_token,
                "expires_in": expires_in,
                "expires_at": (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat(),
            }

        except requests.RequestException as e:
            logger.error(f"Network error authenticating service principal: {e}")
            raise ServiceError(f"Network error communicating with Azure: {str(e)}")

    def list_subscriptions(self, access_token: str) -> list[dict[str, Any]]:
        """
        Query Azure Resource Manager for accessible subscriptions.
        """
        url = f"https://management.azure.com/subscriptions?api-version={ARM_API_VERSION}"
        headers = {"Authorization": f"Bearer {access_token}"}

        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if not resp.ok:
                try:
                    err_json = resp.json()
                    err_desc = err_json.get("error", {}).get("message") or resp.text
                except Exception:
                    err_desc = resp.text
                logger.error(f"Failed to list Azure subscriptions: {err_desc}")
                raise ServiceError(f"Failed to list Azure subscriptions: {err_desc}")

            data = resp.json()
            subscriptions = []
            for item in data.get("value", []):
                subscriptions.append({
                    "subscription_id": item.get("subscriptionId"),
                    "display_name": item.get("displayName") or item.get("subscriptionId"),
                    "state": item.get("state"),
                    "tenant_id": item.get("tenantId"),
                })
            return subscriptions

        except requests.RequestException as e:
            logger.error(f"Network error listing subscriptions: {e}")
            raise ServiceError(f"Network error listing Azure subscriptions: {str(e)}")

    def list_locations(self, access_token: str, subscription_id: str) -> list[dict[str, str]]:
        """
        Query Azure Resource Manager for locations/regions available to a subscription.
        """
        if not subscription_id:
            return self.get_common_azure_regions()

        url = f"https://management.azure.com/subscriptions/{subscription_id}/locations?api-version={ARM_API_VERSION}"
        headers = {"Authorization": f"Bearer {access_token}"}

        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if not resp.ok:
                logger.warning(f"Failed to fetch locations from ARM, falling back to static list: {resp.status_code}")
                return self.get_common_azure_regions()

            data = resp.json()
            locations = []
            for item in data.get("value", []):
                name = item.get("name")
                display_name = item.get("displayName") or name
                if name:
                    locations.append({
                        "value": name,
                        "label": f"{display_name} ({name})",
                    })
            locations.sort(key=lambda x: x["label"])
            return locations or self.get_common_azure_regions()

        except Exception as e:
            logger.warning(f"Error listing ARM locations, falling back to static list: {e}")
            return self.get_common_azure_regions()

    def validate_subscription_access(self, access_token: str, subscription_id: str) -> dict[str, Any]:
        """
        Verify that an access token has permission to access a specific subscription.
        """
        url = f"https://management.azure.com/subscriptions/{subscription_id}?api-version={ARM_API_VERSION}"
        headers = {"Authorization": f"Bearer {access_token}"}

        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if not resp.ok:
                try:
                    err_json = resp.json()
                    err_desc = err_json.get("error", {}).get("message") or resp.text
                except Exception:
                    err_desc = resp.text
                logger.error(f"Azure subscription validation failed: {err_desc}")
                raise ServiceError(f"Subscription validation failed: {err_desc}")

            data = resp.json()
            return {
                "success": True,
                "subscription_id": data.get("subscriptionId", subscription_id),
                "display_name": data.get("displayName", ""),
                "state": data.get("state", "Enabled"),
            }

        except requests.RequestException as e:
            logger.error(f"Network error validating subscription: {e}")
            raise ServiceError(f"Network error validating Azure subscription: {str(e)}")

    @staticmethod
    def get_common_azure_regions() -> list[dict[str, str]]:
        """Static list of common Azure regions as fallback."""
        return [
            {"value": "eastus", "label": "East US (eastus)"},
            {"value": "eastus2", "label": "East US 2 (eastus2)"},
            {"value": "westus", "label": "West US (westus)"},
            {"value": "westus2", "label": "West US 2 (westus2)"},
            {"value": "westus3", "label": "West US 3 (westus3)"},
            {"value": "centralus", "label": "Central US (centralus)"},
            {"value": "northcentralus", "label": "North Central US (northcentralus)"},
            {"value": "southcentralus", "label": "South Central US (southcentralus)"},
            {"value": "northeurope", "label": "North Europe (northeurope)"},
            {"value": "westeurope", "label": "West Europe (westeurope)"},
            {"value": "uksouth", "label": "UK South (uksouth)"},
            {"value": "ukwest", "label": "UK West (ukwest)"},
            {"value": "francecentral", "label": "France Central (francecentral)"},
            {"value": "germanywestcentral", "label": "Germany West Central (germanywestcentral)"},
            {"value": "switzerlandnorth", "label": "Switzerland North (switzerlandnorth)"},
            {"value": "norwayeast", "label": "Norway East (norwayeast)"},
            {"value": "southeastasia", "label": "Southeast Asia (southeastasia)"},
            {"value": "eastasia", "label": "East Asia (eastasia)"},
            {"value": "australiaeast", "label": "Australia East (australiaeast)"},
            {"value": "australiasoutheast", "label": "Australia Southeast (australiasoutheast)"},
            {"value": "japaneast", "label": "Japan East (japaneast)"},
            {"value": "japanwest", "label": "Japan West (japanwest)"},
            {"value": "koreacentral", "label": "Korea Central (koreacentral)"},
            {"value": "canadacentral", "label": "Canada Central (canadacentral)"},
            {"value": "brazilsouth", "label": "Brazil South (brazilsouth)"},
            {"value": "centralindia", "label": "Central India (centralindia)"},
            {"value": "southafricanorth", "label": "South Africa North (southafricanorth)"},
            {"value": "uaenorth", "label": "UAE North (uaenorth)"},
        ]
