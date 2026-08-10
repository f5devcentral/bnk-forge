"""IBM Cloud region discovery helpers."""

from __future__ import annotations

import logging

import requests
from sqlalchemy.orm import Session

from core.encryption import decrypt_value, encrypt_value
from core.errors import BadRequestError, NotFoundError, ServiceError
from models import CloudCredentialTemplate

logger = logging.getLogger(__name__)


IBM_IAM_TOKEN_URL = "https://iam.cloud.ibm.com/identity/token"
IBM_SEARCH_URL = "https://api.global-search-tagging.cloud.ibm.com/v3/resources/search"
IBM_RESOURCE_KEYS_URL = "https://resource-controller.cloud.ibm.com/v2/resource_keys"

IBM_REGIONS: list[dict[str, str]] = [
    {"value": "au-syd", "label": "Australia (Sydney)"},
    {"value": "br-sao", "label": "Brazil (Sao Paulo)"},
    {"value": "ca-tor", "label": "Canada (Toronto)"},
    {"value": "eu-de", "label": "EU Germany (Frankfurt)"},
    {"value": "eu-es", "label": "EU Spain (Madrid)"},
    {"value": "eu-gb", "label": "EU United Kingdom (London)"},
    {"value": "jp-osa", "label": "Japan (Osaka)"},
    {"value": "jp-tok", "label": "Japan (Tokyo)"},
    {"value": "us-east", "label": "US East (Washington DC)"},
    {"value": "us-south", "label": "US South (Dallas)"},
]


class IBMCloudService:
    """Minimal IBM Cloud API wrapper for region discovery."""

    def __init__(self, db: Session):
        self.db = db

    def resolve_effective_template(self, template_id: int | None = None) -> CloudCredentialTemplate:
        """Resolve the IBM credential template for region discovery.

        Resolution order:
        1. Explicit template_id if provided
        2. Default IBM template
        """
        template: CloudCredentialTemplate | None = None

        if template_id is not None:
            template = self.db.query(CloudCredentialTemplate).filter(CloudCredentialTemplate.id == template_id).first()
            if not template:
                raise NotFoundError("credential template", template_id)
            if template.provider != "ibm":
                raise BadRequestError("Selected credential template is not an IBM Cloud template")
        else:
            template = self.db.query(CloudCredentialTemplate).filter(
                CloudCredentialTemplate.provider == "ibm",
                CloudCredentialTemplate.is_default.is_(True),
            ).first()
            if not template:
                raise BadRequestError(
                    "No default IBM credential template configured. Mark an IBM credential template as default first."
                )

        if not template.ibmcloud_api_key_encrypted:
            raise BadRequestError("IBM credential template does not contain an API key")

        return template

    def list_regions_from_template(self, template_id: int | None = None) -> list[dict[str, str]]:
        template = self.resolve_effective_template(template_id)
        api_key = decrypt_value(template.ibmcloud_api_key_encrypted)
        if not api_key:
            raise ServiceError("ibm_cloud", "Failed to decrypt IBM Cloud API key")
        # Pass the template through so the IAM token exchange uses the cache.
        self._exchange_api_key(api_key, template=template)
        return IBM_REGIONS

    def list_regions(self, api_key: str) -> list[dict[str, str]]:
        if not api_key:
            raise BadRequestError("IBM Cloud API key is required")

        # A successful IAM token exchange is sufficient to prove the API key is
        # valid for this UX flow. IBM does not provide a stable public regions
        # discovery endpoint suitable for this interaction, so we return the
        # backend-owned canonical region catalog after validating the key.
        self._exchange_api_key(api_key)
        return IBM_REGIONS

    def ensure_cos_hmac_credentials(self, template: CloudCredentialTemplate) -> tuple[str, str]:
        """Create or reuse COS HMAC credentials for a selected COS instance."""
        if template.ibm_cos_hmac_access_key_id and template.ibm_cos_hmac_secret_access_key_encrypted:
            secret = decrypt_value(template.ibm_cos_hmac_secret_access_key_encrypted)
            if secret:
                logger.info(
                    "Reusing stored IBM COS HMAC credentials for template_id=%s instance=%s",
                    template.id,
                    template.ibm_cos_instance_name,
                )
                return template.ibm_cos_hmac_access_key_id, secret

        api_key = decrypt_value(template.ibmcloud_api_key_encrypted) if template.ibmcloud_api_key_encrypted else None
        if not api_key:
            raise BadRequestError("IBM credential template does not contain an API key")
        if not template.ibm_cos_instance_name:
            raise BadRequestError("IBM credential template does not define a COS instance name")

        access_token = self._exchange_api_key(api_key, template=template)
        logger.info(
            "IBM COS HMAC derivation started for template_id=%s instance_name=%s",
            template.id,
            template.ibm_cos_instance_name,
        )
        cos_instance = self._find_cos_instance(access_token, template.ibm_cos_instance_name)
        logger.info(
            "IBM COS instance resolved for template_id=%s instance_name=%s crn=%s",
            template.id,
            cos_instance["name"],
            cos_instance["crn"],
        )

        credential_name = f"bnk-forge-template-{template.id}-cos-hmac"
        hmac = self._find_existing_cos_hmac_resource_key(access_token, credential_name) or self._create_cos_hmac_resource_key(
            access_token,
            cos_instance["crn"],
            credential_name,
        )
        logger.info(
            "IBM COS HMAC credentials ready for template_id=%s credential_name=%s access_key_suffix=%s",
            template.id,
            credential_name,
            hmac["access_key_id"][-6:] if hmac.get("access_key_id") else "unknown",
        )

        template.ibm_cos_instance_crn = cos_instance["crn"]
        template.ibm_cos_hmac_access_key_id = hmac["access_key_id"]
        template.ibm_cos_hmac_secret_access_key_encrypted = encrypt_value(hmac["secret_access_key"])
        self.db.flush()
        self.db.refresh(template)
        return hmac["access_key_id"], hmac["secret_access_key"]

    def _find_cos_instance(self, access_token: str, instance_name: str) -> dict[str, str]:
        logger.info("Searching IBM COS instances for instance_name=%s", instance_name)
        response = requests.post(
            IBM_SEARCH_URL,
            params={"limit": 10},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json={"query": f'type:resource-instance AND service_name:cloud-object-storage AND name:{instance_name}'},
            timeout=15,
        )
        if response.status_code in {401, 403}:
            raise BadRequestError("IBM Cloud API key is not authorized to search for COS instances")
        if not response.ok:
            raise ServiceError("ibm_cloud", f"IBM Cloud COS instance lookup failed with status {response.status_code}")

        payload = response.json()
        items = payload.get("items") or []
        if not items:
            logger.warning("IBM COS instance not found for instance_name=%s", instance_name)
            raise NotFoundError("IBM COS instance", instance_name)

        item = items[0]
        return {
            "name": item.get("name") or instance_name,
            "crn": item.get("crn") or item.get("resource_id") or "",
        }

    def _create_cos_hmac_resource_key(self, access_token: str, source_crn: str, credential_name: str) -> dict[str, str]:
        if not source_crn:
            raise ServiceError("ibm_cloud", "IBM COS instance CRN is required to create HMAC credentials")

        logger.info("Creating IBM COS HMAC resource key credential_name=%s source_crn=%s", credential_name, source_crn)

        response = requests.post(
            IBM_RESOURCE_KEYS_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json={
                "name": credential_name,
                # IBM Resource Controller expects the instance CRN under "source"
                # (not "source_crn"); the wrong key yields HTTP 400
                # "source field is missing or has invalid data type."
                "source": source_crn,
                "role": "Writer",
                "parameters": {"HMAC": True},
            },
            timeout=20,
        )
        if response.status_code == 409:
            logger.info("IBM COS HMAC resource key already exists credential_name=%s; attempting reuse", credential_name)
            existing = self._find_existing_cos_hmac_resource_key(access_token, credential_name)
            if existing:
                return existing
            raise ServiceError("ibm_cloud", f"COS service credential '{credential_name}' already exists but could not be reused")
        if response.status_code in {401, 403}:
            raise BadRequestError("IBM Cloud API key is not authorized to create COS service credentials")
        if not response.ok:
            detail = ""
            try:
                detail = response.json().get("message", "")
            except Exception:
                detail = response.text[:200]
            raise ServiceError(
                "ibm_cloud",
                f"IBM COS HMAC credential creation failed with status {response.status_code}"
                + (f": {detail}" if detail else ""),
            )

        payload = response.json()
        credentials = payload.get("credentials") or {}
        hmac = credentials.get("cos_hmac_keys") or {}
        access_key_id = hmac.get("access_key_id")
        secret_access_key = hmac.get("secret_access_key")
        if not access_key_id or not secret_access_key:
            logger.error("IBM COS HMAC creation succeeded but response omitted HMAC keys credential_name=%s", credential_name)
            raise ServiceError("ibm_cloud", "IBM COS service credential response did not include HMAC keys")

        logger.info("IBM COS HMAC resource key created credential_name=%s access_key_suffix=%s", credential_name, access_key_id[-6:])
        return {
            "access_key_id": access_key_id,
            "secret_access_key": secret_access_key,
        }

    def _find_existing_cos_hmac_resource_key(self, access_token: str, credential_name: str) -> dict[str, str] | None:
        logger.info("Looking up existing IBM COS HMAC resource key credential_name=%s", credential_name)
        response = requests.get(
            IBM_RESOURCE_KEYS_URL,
            params={"name": credential_name},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            timeout=15,
        )
        if not response.ok:
            logger.warning(
                "IBM COS HMAC resource key lookup failed credential_name=%s status=%s",
                credential_name,
                response.status_code,
            )
            return None

        payload = response.json()
        resources = payload.get("resources") or []
        for resource in resources:
            credentials = resource.get("credentials") or {}
            hmac = credentials.get("cos_hmac_keys") or {}
            access_key_id = hmac.get("access_key_id")
            secret_access_key = hmac.get("secret_access_key")
            if access_key_id and secret_access_key:
                logger.info("Found reusable IBM COS HMAC resource key credential_name=%s access_key_suffix=%s", credential_name, access_key_id[-6:])
                return {
                    "access_key_id": access_key_id,
                    "secret_access_key": secret_access_key,
                }
        logger.info("No reusable IBM COS HMAC resource key found credential_name=%s", credential_name)
        return None

    def list_cos_instances(self, api_key: str) -> list[dict[str, str]]:
        if not api_key:
            raise BadRequestError("IBM Cloud API key is required")

        access_token = self._exchange_api_key(api_key)
        logger.info("Listing IBM COS instances via Search API")
        response = requests.post(
            IBM_SEARCH_URL,
            params={"limit": 100},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json={"query": "type:resource-instance AND service_name:cloud-object-storage"},
            timeout=15,
        )
        if response.status_code in {401, 403}:
            raise BadRequestError("IBM Cloud API key is not authorized to search for COS instances")
        if not response.ok:
            raise ServiceError("ibm_cloud", f"IBM Cloud COS instance lookup failed with status {response.status_code}")

        payload = response.json()
        items = payload.get("items") or []
        instances = []
        for item in items:
            name = item.get("name")
            crn = item.get("crn")
            if not name or not crn:
                continue
            instances.append({
                "value": name,
                "label": name,
                "crn": crn,
            })
        logger.info("IBM COS instance query returned %s instance(s)", len(instances))
        return sorted(instances, key=lambda inst: inst["label"].lower())

    def _exchange_api_key(self, api_key: str, *, template: CloudCredentialTemplate | None = None) -> str:
        """Exchange an API key for an IAM bearer token, with optional caching.

        When *template* is provided and it has a stored, non-expired
        ibm_iam_token_encrypted with at least 60s of headroom, return that
        token directly and skip the IAM round trip. Otherwise hit the IAM
        endpoint, store the response on the template (best-effort, swallows
        write errors so a stateless caller still works), and return.
        """
        from datetime import UTC, datetime, timedelta

        # Cached path
        if template is not None and template.ibm_iam_token_encrypted and template.ibm_iam_token_expiry:
            now = datetime.now(UTC)
            expiry = template.ibm_iam_token_expiry
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
            if expiry - now > timedelta(seconds=60):
                cached = decrypt_value(template.ibm_iam_token_encrypted)
                if cached:
                    return cached

        try:
            response = requests.post(
                IBM_IAM_TOKEN_URL,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                data={
                    "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                    "apikey": api_key,
                },
                timeout=10,
            )
        except requests.Timeout as e:
            raise ServiceError("ibm_cloud", "IBM Cloud IAM token request timed out") from e
        except requests.RequestException as e:
            raise ServiceError("ibm_cloud", f"Failed to contact IBM Cloud IAM: {str(e)}") from e

        if response.status_code in {400, 401}:
            raise BadRequestError("IBM Cloud API key is invalid or expired")
        if not response.ok:
            raise ServiceError("ibm_cloud", f"IBM Cloud IAM token request failed with status {response.status_code}")

        payload = response.json()
        access_token = payload.get("access_token")
        if not access_token:
            raise ServiceError("ibm_cloud", "IBM Cloud IAM response did not include an access token")

        # Best-effort cache write. IBM IAM tokens are typically valid for
        # ~3600s; honor the server-stated expires_in when present.
        if template is not None and self.db is not None:
            try:
                expires_in = int(payload.get("expires_in") or 3600)
                template.ibm_iam_token_encrypted = encrypt_value(access_token)
                template.ibm_iam_token_expiry = datetime.now(UTC) + timedelta(seconds=expires_in)
                self.db.flush()
            except Exception as e:
                logger.warning(f"Failed to cache IBM IAM token on template {template.id}: {e}")

        return access_token
