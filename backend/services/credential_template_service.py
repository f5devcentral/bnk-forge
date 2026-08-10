"""
Credential Template Service — DB and business logic for cloud credential templates.

Extracted from routes/credential_templates.py to separate HTTP handling from domain logic.

Covers:
- Credential template CRUD (list, get, create, update, delete)
- Credential testing (AWS STS, SSH)
- AWS SSO device flow (initiate, poll, refresh, status)
- Audit logging for SSO operations
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import requests
from sqlalchemy.orm import Session

from core.encryption import decrypt_value, encrypt_value
from core.errors import AppError, BadRequestError, InternalError, NotFoundError
from models import CloudCredentialTemplate
from services.aws_auth_service import AWSAuthService
from services.defaults_service import get_default

logger = logging.getLogger(__name__)


class _AwsTestError(Exception):
    """Carrier object mimicking botocore ClientError shape for cloud observation.

    Used when the credential test fails for a non-boto3 reason (missing
    config, decryption failure, etc.) — wraps the message in the same
    ``{"Error": {"Code", "Message"}}`` shape ``services.cloud_observation``
    expects from real ClientError instances.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.response = {"Error": {"Code": code, "Message": message}}


def _test_aws_template_via_boto3(template: Any) -> dict[str, Any]:
    """Validate an AWS template using boto3 (no aws-CLI dependency).

    Calls ``sts:GetCallerIdentity`` with the template's stored credentials.
    Returns the same dict shape the legacy subprocess path returned, so
    callers (UI + observation hook) don't have to change.

    Lazy boto3 import — keeps non-AWS callers from paying the boto3 import
    cost on test_template().
    """
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError, ConnectTimeoutError, ReadTimeoutError

    from core.aws_config import adaptive_retry_config

    if template.aws_auth_method == 'access_keys':
        if not template.aws_access_key_id:
            return {"success": False, "error": "No access key ID configured",
                    "message": "Please configure AWS credentials in this template"}
        access_key = template.aws_access_key_id
        secret_key = decrypt_value(template.aws_secret_access_key_encrypted) \
            if template.aws_secret_access_key_encrypted else None
        session_token = decrypt_value(template.aws_session_token_encrypted) \
            if template.aws_session_token_encrypted else None
        session = boto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            aws_session_token=session_token,
            region_name=template.region or None,
        )
    elif template.aws_auth_method == 'profile':
        if not template.aws_profile:
            return {"success": False, "error": "No AWS profile configured",
                    "message": "Please configure AWS profile name in this template"}
        session = boto3.Session(profile_name=template.aws_profile, region_name=template.region or None)
    else:
        # SSO / other — fall back to default session, which will pick up
        # whatever creds the SSO flow already populated into the environment.
        session = boto3.Session(region_name=template.region or None)

    try:
        sts = session.client('sts', config=adaptive_retry_config())
        identity = sts.get_caller_identity()
        return {
            "success": True,
            "message": "Credentials are valid and working",
            "account_id": identity.get("Account"),
            "user_id": identity.get("UserId"),
            "arn": identity.get("Arn"),
        }
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "ClientError")
        msg = e.response.get("Error", {}).get("Message", str(e))
        friendly = {
            "ExpiredToken": ("Credentials have expired",
                             "Your AWS session token has expired. Please refresh your credentials."),
            "InvalidClientTokenId": ("Invalid credentials",
                                     "The AWS access key ID you provided does not exist."),
            "SignatureDoesNotMatch": ("Invalid secret key",
                                      "The AWS secret access key you provided is incorrect."),
        }.get(code, (code, msg))
        return {"success": False, "error": friendly[0], "message": friendly[1], "details": msg}
    except (ConnectTimeoutError, ReadTimeoutError) as e:
        return {"success": False, "error": "AWS request timed out",
                "message": "AWS API request timed out. Check network connectivity.",
                "details": str(e)}
    except BotoCoreError as e:
        return {"success": False, "error": "AWS request failed",
                "message": str(e), "details": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e),
                "message": f"Failed to test credentials: {str(e)}"}


class CredentialTemplateService:
    """Service layer for cloud credential template operations."""

    def __init__(self, db: Session):
        self.db = db

    # ================================================================
    # Lookups / Helpers
    # ================================================================

    def _get_template(self, template_id: int) -> CloudCredentialTemplate:
        template = self.db.query(CloudCredentialTemplate).filter(
            CloudCredentialTemplate.id == template_id
        ).first()
        if not template:
            raise NotFoundError("template", template_id)
        return template

    def _create_audit_log(self, action: str, template: CloudCredentialTemplate,
                          status: str, details: dict = None, request=None):
        """Helper to create audit log for SSO actions."""
        try:
            from services.audit_service import log_from_request
            log_from_request(
                self.db, action=action, resource_type="credential_template",
                request=request, resource_id=str(template.id),
                resource_name=template.name, status=status, details=details,
            )
        except Exception as e:
            logger.error(f"Failed to create audit log: {e}")
            pass

    @staticmethod
    def serialize_template(template: CloudCredentialTemplate) -> dict:
        """Build a response dict for a credential template."""
        return {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "provider": template.provider,
            "aws_auth_method": template.aws_auth_method,
            "aws_profile": template.aws_profile,
            "region": template.region,
            "aws_access_key_id": template.aws_access_key_id,
            "has_aws_secret_access_key": bool(template.aws_secret_access_key_encrypted),
            "has_aws_session_token": bool(template.aws_session_token_encrypted),
            "aws_sso_enabled": template.aws_sso_enabled or False,
            "aws_sso_start_url": template.aws_sso_start_url,
            "aws_sso_region": template.aws_sso_region,
            "aws_sso_account_id": template.aws_sso_account_id,
            "aws_sso_role_name": template.aws_sso_role_name,
            "aws_sso_authenticated_at": template.aws_sso_authenticated_at,
            "aws_sso_token_expiry": template.aws_sso_token_expiry,
            "aws_credentials_expiry": template.aws_credentials_expiry,
            "gcp_project_id": template.gcp_project_id,
            "has_gcp_credentials": bool(template.gcp_credentials_encrypted),
            "azure_subscription_id": template.azure_subscription_id,
            "azure_tenant_id": template.azure_tenant_id,
            "has_azure_credentials": bool(template.azure_credentials_encrypted),
            "has_ibmcloud_api_key": bool(template.ibmcloud_api_key_encrypted),
            "ibmcloud_resource_group": template.ibmcloud_resource_group,
            "ibm_cos_instance_name": template.ibm_cos_instance_name,
            "has_tfc_api_token": bool(template.tfc_api_token_encrypted),
            "tfc_hostname": template.tfc_hostname,
            "ssh_host": template.ssh_host,
            "ssh_port": template.ssh_port,
            "ssh_username": template.ssh_username,
            "ssh_auth_type": template.ssh_auth_type,
            "has_ssh_password": bool(template.ssh_password_encrypted),
            "has_ssh_key": bool(template.ssh_key_encrypted),
            "is_default": template.is_default or False,
            "created_at": template.created_at,
            "updated_at": template.updated_at,
            "projects_count": len(template.projects) if template.projects else 0,
            # Passive cloud-API observation (RFC connectivity Phase 2).
            "last_successful_call_at": template.last_successful_call_at,
            "last_error_at": template.last_error_at,
            "last_error_code": template.last_error_code,
            "last_error_message": template.last_error_message,
        }

    # ================================================================
    # CRUD
    # ================================================================

    def list_templates(self, provider: str | None = None) -> list[dict]:
        """List all credential templates, optionally filtered by provider."""
        query = self.db.query(CloudCredentialTemplate)
        if provider:
            query = query.filter(CloudCredentialTemplate.provider == provider)
        templates = query.order_by(
            CloudCredentialTemplate.is_default.desc(), CloudCredentialTemplate.name
        ).all()
        return [self.serialize_template(t) for t in templates]

    def get_template(self, template_id: int) -> dict:
        """Get a specific credential template by ID."""
        return self.serialize_template(self._get_template(template_id))

    def create_template(self, template_data) -> dict:
        """Create a new credential template."""
        # Duplicate name check
        existing = self.db.query(CloudCredentialTemplate).filter(
            CloudCredentialTemplate.name == template_data.name
        ).first()
        if existing:
            raise BadRequestError(f"Credential template '{template_data.name}' already exists")

        # Default management
        if template_data.is_default:
            self.db.query(CloudCredentialTemplate).filter(
                CloudCredentialTemplate.provider == template_data.provider,
                CloudCredentialTemplate.is_default
            ).update({"is_default": False})

        region = template_data.region
        if template_data.provider == "aws":
            region = region or get_default(self.db, "cloud.aws.default_region")

        template = CloudCredentialTemplate(
            name=template_data.name,
            description=template_data.description,
            provider=template_data.provider,
            aws_auth_method=template_data.aws_auth_method,
            aws_profile=template_data.aws_profile,
            region=region,
            aws_access_key_id=template_data.aws_access_key_id,
            aws_sso_enabled=template_data.aws_sso_enabled,
            aws_sso_start_url=template_data.aws_sso_start_url,
            aws_sso_region=template_data.aws_sso_region,
            aws_sso_account_id=template_data.aws_sso_account_id,
            aws_sso_role_name=template_data.aws_sso_role_name,
            gcp_project_id=template_data.gcp_project_id,
            azure_subscription_id=template_data.azure_subscription_id,
            azure_tenant_id=template_data.azure_tenant_id,
            ibmcloud_resource_group=getattr(template_data, "ibmcloud_resource_group", None) or "default",
            ibm_cos_instance_name=getattr(template_data, "ibm_cos_instance_name", None),
            tfc_hostname=getattr(template_data, "tfc_hostname", None) or (
                "app.terraform.io" if getattr(template_data, "tfc_api_token", None) else None
            ),
            ssh_host=template_data.ssh_host,
            ssh_port=template_data.ssh_port or 22,
            ssh_username=template_data.ssh_username,
            ssh_auth_type=template_data.ssh_auth_type,
            is_default=template_data.is_default
        )

        # Encrypt sensitive fields
        if template_data.aws_secret_access_key:
            template.aws_secret_access_key_encrypted = encrypt_value(template_data.aws_secret_access_key)
        if template_data.aws_session_token:
            template.aws_session_token_encrypted = encrypt_value(template_data.aws_session_token)
        if template_data.gcp_credentials:
            template.gcp_credentials_encrypted = encrypt_value(template_data.gcp_credentials)
        if template_data.azure_credentials:
            template.azure_credentials_encrypted = encrypt_value(template_data.azure_credentials)
        if template_data.ibmcloud_api_key:
            template.ibmcloud_api_key_encrypted = encrypt_value(template_data.ibmcloud_api_key)
        if getattr(template_data, "tfc_api_token", None):
            template.tfc_api_token_encrypted = encrypt_value(template_data.tfc_api_token)
        if template_data.ssh_password:
            template.ssh_password_encrypted = encrypt_value(template_data.ssh_password)
        if template_data.ssh_private_key:
            template.ssh_key_encrypted = encrypt_value(template_data.ssh_private_key)
        if template_data.ssh_key_passphrase:
            template.ssh_key_passphrase_encrypted = encrypt_value(template_data.ssh_key_passphrase)

        self.db.add(template)
        self.db.flush()
        self.db.refresh(template)
        return self.serialize_template(template)

    def update_template(self, template_id: int, template_data) -> dict:
        """Update a credential template."""
        template = self._get_template(template_id)

        if template_data.name and template_data.name != template.name:
            existing = self.db.query(CloudCredentialTemplate).filter(
                CloudCredentialTemplate.name == template_data.name
            ).first()
            if existing:
                raise BadRequestError(f"Credential template '{template_data.name}' already exists")

        if template_data.is_default and not template.is_default:
            self.db.query(CloudCredentialTemplate).filter(
                CloudCredentialTemplate.provider == template.provider,
                CloudCredentialTemplate.is_default
            ).update({"is_default": False})

        update_data = template_data.model_dump(exclude_unset=True)

        # Handle encrypted fields
        encrypted_map = {
            "aws_secret_access_key": "aws_secret_access_key_encrypted",
            "aws_session_token": "aws_session_token_encrypted",
            "gcp_credentials": "gcp_credentials_encrypted",
            "azure_credentials": "azure_credentials_encrypted",
            "ibmcloud_api_key": "ibmcloud_api_key_encrypted",
            "tfc_api_token": "tfc_api_token_encrypted",
            "ssh_password": "ssh_password_encrypted",
            "ssh_private_key": "ssh_key_encrypted",
            "ssh_key_passphrase": "ssh_key_passphrase_encrypted",
        }
        for plain_key, enc_attr in encrypted_map.items():
            if plain_key in update_data and update_data[plain_key]:
                setattr(template, enc_attr, encrypt_value(update_data.pop(plain_key)))
            else:
                update_data.pop(plain_key, None)

        for key, value in update_data.items():
            if hasattr(template, key):
                setattr(template, key, value)

        template.updated_at = datetime.now(UTC)
        self.db.flush()
        self.db.refresh(template)
        return self.serialize_template(template)

    def delete_template(self, template_id: int) -> None:
        """Delete a credential template."""
        template = self._get_template(template_id)
        projects_using = len(template.projects) if template.projects else 0
        if projects_using > 0:
            raise BadRequestError(
                f"Cannot delete template. {projects_using} project(s) are using this credential template. "
                f"Please update those projects to use a different template first."
            )
        self.db.delete(template)

    # ================================================================
    # Credential Testing
    # ================================================================

    def test_template(self, template_id: int) -> dict[str, Any]:
        """Test if credential template credentials are valid.

        Stamps the cloud_credential_templates row with success/error
        observation columns (RFC connectivity Phase 2) so the UI can
        glance at "AWS access OK as of X" / "AWS access failed: Code"
        without re-clicking Test.
        """
        template = self._get_template(template_id)
        result = self._test_template_inner(template)
        # Observation: only meaningful for AWS templates (SSH gets its own
        # observation via the SSH probe in the reachability registry).
        if template.provider == 'aws':
            from services.cloud_observation import record_aws_observation
            if result.get("success"):
                record_aws_observation(template_id, success=True)
            else:
                # Synthesize a minimal "error" object so the helper can
                # extract a code/message — subprocess errors aren't
                # botocore ClientError, so we pass a typed sentinel.
                err = _AwsTestError(
                    code=result.get("error") or "TestFailed",
                    message=(result.get("message") or result.get("details") or "")[:1000],
                )
                record_aws_observation(template_id, success=False, error=err)
        return result

    def _test_template_inner(self, template: Any) -> dict[str, Any]:
        """Execute the credential test and return the raw result dict."""

        # SSH provider
        if template.provider == 'ssh':
            from services.ssh_tunnel_manager import get_tunnel_manager
            tunnel_mgr = get_tunnel_manager()
            return tunnel_mgr.test_ssh_connection(
                ssh_host=template.ssh_host,
                ssh_port=template.ssh_port or 22,
                ssh_username=template.ssh_username,
                ssh_auth_type=template.ssh_auth_type or "password",
                ssh_password_encrypted=template.ssh_password_encrypted,
                ssh_key_encrypted=template.ssh_key_encrypted,
                ssh_key_passphrase_encrypted=template.ssh_key_passphrase_encrypted,
            )

        if template.provider == 'ibm':
            if not template.ibmcloud_api_key_encrypted:
                return {
                    "success": False,
                    "error": "No IBM Cloud API key configured",
                    "message": "Please configure an IBM Cloud API key in this template",
                }

            api_key = decrypt_value(template.ibmcloud_api_key_encrypted)
            if not api_key:
                raise InternalError("Failed to decrypt IBM Cloud API key")

            try:
                response = requests.post(
                    "https://iam.cloud.ibm.com/identity/token",
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

                if response.ok:
                    token_data = response.json()
                    expires_in = token_data.get("expires_in")
                    iam_id = token_data.get("iam_id") or token_data.get("sub")
                    result = {
                        "success": True,
                        "message": "IBM Cloud API key is valid and working",
                    }
                    if iam_id:
                        result["iam_id"] = iam_id
                    if expires_in is not None:
                        result["expires_in"] = expires_in
                    return result

                try:
                    error_payload = response.json()
                    error_description = error_payload.get("error_description") or error_payload.get("errorMessage")
                    error_code = error_payload.get("errorCode") or error_payload.get("error")
                except ValueError:
                    error_payload = None
                    error_description = response.text.strip() or None
                    error_code = None

                if response.status_code in {400, 401}:
                    return {
                        "success": False,
                        "error": "Invalid IBM Cloud API key",
                        "message": error_description or "The IBM Cloud API key is invalid or expired.",
                        "details": error_code or error_description or response.text,
                    }

                return {
                    "success": False,
                    "error": "Credential test failed",
                    "message": error_description or f"IBM IAM request failed with status {response.status_code}",
                    "details": error_code or response.text,
                }
            except requests.Timeout:
                return {
                    "success": False,
                    "error": "Request timed out",
                    "message": "IBM Cloud IAM request timed out after 10 seconds",
                }
            except requests.RequestException as e:
                return {
                    "success": False,
                    "error": "Credential test failed",
                    "message": f"Failed to test IBM Cloud credentials: {str(e)}",
                }

        if template.provider != 'aws':
            raise BadRequestError(
                f"Credential testing only supported for AWS and SSH. This template is {template.provider}")

        return _test_aws_template_via_boto3(template)

    # ================================================================
    # AWS SSO Flow
    # ================================================================

    @staticmethod
    def _has_complete_sso_config(template: Any) -> bool:
        """Return True when the template has all four fields required to run SSO device auth."""
        return bool(
            template.aws_sso_start_url
            and template.aws_sso_region
            and template.aws_sso_account_id
            and template.aws_sso_role_name
        )

    def initiate_sso(self, template_id: int) -> dict[str, Any]:
        """Initiate AWS SSO device code flow.

        Allowed when the template has a complete SSO configuration (start_url,
        sso_region, account_id, role_name), regardless of aws_sso_enabled.  This
        lets users re-authenticate after the token-refresh PR changed auth_method
        to 'access_keys' / aws_sso_enabled=False while preserving SSO config fields.
        """
        template = self._get_template(template_id)

        if not self._has_complete_sso_config(template):
            raise BadRequestError("SSO configuration incomplete. Please provide SSO start URL, region, account ID, and role name.")

        auth_service = AWSAuthService()
        result = auth_service.initiate_device_authorization(
            start_url=template.aws_sso_start_url, region=template.aws_sso_region
        )

        template.aws_sso_client_id = result['client_id']
        template.aws_sso_client_secret_encrypted = encrypt_value(result['client_secret'])

        self._create_audit_log("sso_auth_initiated", template, "success",
                               {"sso_region": template.aws_sso_region,
                                "account_id": template.aws_sso_account_id})

        return {
            "success": True,
            "message": "SSO device authorization initiated",
            "data": {
                "user_code": result['user_code'],
                "verification_uri": result['verification_uri'],
                "verification_uri_complete": result['verification_uri_complete'],
                "expires_in": result['expires_in'],
                "interval": result['interval'],
                "device_code": result['device_code']
            }
        }

    def poll_sso(self, template_id: int, device_code: str) -> dict[str, Any]:
        """Poll for SSO authentication completion. Returns dict or raises."""
        template = self._get_template(template_id)

        if not template.aws_sso_client_id or not template.aws_sso_client_secret_encrypted:
            raise BadRequestError("SSO authentication not initiated. Please call authenticate-sso first.")

        # Already authenticated recently?
        if template.aws_sso_authenticated_at and template.aws_sso_access_token_encrypted:
            if template.aws_sso_authenticated_at > datetime.now(UTC) - timedelta(minutes=5):
                return {
                    "success": True,
                    "message": "SSO authentication already complete",
                    "data": {
                        "authenticated_at": template.aws_sso_authenticated_at.isoformat(),
                        "token_expiry": template.aws_sso_token_expiry.isoformat() if template.aws_sso_token_expiry else None,
                        "credentials_expiry": template.aws_credentials_expiry.isoformat() if template.aws_credentials_expiry else None,
                        "has_credentials": template.aws_access_key_id is not None
                    }
                }

        client_secret = decrypt_value(template.aws_sso_client_secret_encrypted)
        if not client_secret:
            raise InternalError("Failed to decrypt client secret")

        auth_service = AWSAuthService()

        try:
            token_data = auth_service.poll_for_token(
                client_id=template.aws_sso_client_id,
                client_secret=client_secret,
                device_code=device_code,
                region=template.aws_sso_region,
                max_attempts=1
            )
        except Exception as e:
            error_msg = str(e)
            self._create_audit_log("sso_auth_failed", template, "failed", {"error": error_msg})
            if "Authorization pending" in error_msg or "AuthorizationPendingException" in error_msg:
                # Signal to route layer to return 202
                return {"success": False, "message": "Authorization pending", "pending": True}
            raise InternalError(error_msg)

        # Store SSO tokens
        template.aws_sso_access_token_encrypted = encrypt_value(token_data['access_token'])
        if token_data.get('refresh_token'):
            template.aws_sso_refresh_token_encrypted = encrypt_value(token_data['refresh_token'])

        expires_in = token_data.get('expires_in', 28800)
        template.aws_sso_token_expiry = datetime.now(UTC) + timedelta(seconds=expires_in)
        template.aws_sso_authenticated_at = datetime.now(UTC)

        # SSO token exchange succeeded — clear any stale failed-observation badge.
        _now = datetime.now(UTC)
        template.last_successful_call_at = _now
        template.last_error_at = None
        template.last_error_code = None
        template.last_error_message = None

        # Get account credentials — wrapped separately so SSO token is saved even if
        # the role credential exchange fails (e.g. wrong account_id/role_name)
        if template.aws_sso_account_id and template.aws_sso_role_name:
            try:
                credentials = auth_service.get_account_credentials(
                    access_token=token_data['access_token'],
                    account_id=template.aws_sso_account_id,
                    role_name=template.aws_sso_role_name,
                    region=template.aws_sso_region
                )
                template.aws_access_key_id = credentials['access_key_id']
                template.aws_secret_access_key_encrypted = encrypt_value(credentials['secret_access_key'])
                template.aws_session_token_encrypted = encrypt_value(credentials['session_token'])
                template.aws_credentials_expiry = datetime.fromisoformat(credentials['expiration'])
            except Exception as e:
                logger.warning(f"SSO token obtained but credential exchange failed: {e}")

        self._create_audit_log("sso_auth_completed", template, "success", {
            "account_id": template.aws_sso_account_id,
            "role_name": template.aws_sso_role_name,
            "token_expiry": template.aws_sso_token_expiry.isoformat() if template.aws_sso_token_expiry else None,
            "credentials_expiry": template.aws_credentials_expiry.isoformat() if template.aws_credentials_expiry else None
        })

        return {
            "success": True,
            "message": "SSO authentication successful",
            "data": {
                "authenticated_at": template.aws_sso_authenticated_at.isoformat(),
                "token_expiry": template.aws_sso_token_expiry.isoformat() if template.aws_sso_token_expiry else None,
                "credentials_expiry": template.aws_credentials_expiry.isoformat() if template.aws_credentials_expiry else None,
                "has_credentials": template.aws_access_key_id is not None
            }
        }

    def refresh_sso(self, template_id: int) -> dict[str, Any]:
        """Refresh AWS SSO credentials using stored refresh token."""
        template = self._get_template(template_id)

        if not template.aws_sso_refresh_token_encrypted:
            raise BadRequestError("No refresh token available. Please re-authenticate.")

        refresh_token = decrypt_value(template.aws_sso_refresh_token_encrypted)
        client_secret = decrypt_value(template.aws_sso_client_secret_encrypted)
        if not refresh_token or not client_secret:
            raise InternalError("Failed to decrypt SSO credentials")

        auth_service = AWSAuthService()
        try:
            token_data = auth_service.refresh_credentials(
                refresh_token=refresh_token,
                client_id=template.aws_sso_client_id,
                client_secret=client_secret,
                region=template.aws_sso_region
            )
        except AppError:
            raise
        except Exception as e:
            self._create_audit_log("sso_credentials_refresh_failed", template, "failed",
                                   {"error": str(e)})
            raise InternalError(f"Failed to refresh SSO credentials: {str(e)}")

        template.aws_sso_access_token_encrypted = encrypt_value(token_data['access_token'])
        if token_data.get('refresh_token'):
            template.aws_sso_refresh_token_encrypted = encrypt_value(token_data['refresh_token'])

        expires_in = token_data.get('expires_in', 28800)
        template.aws_sso_token_expiry = datetime.now(UTC) + timedelta(seconds=expires_in)

        if template.aws_sso_account_id and template.aws_sso_role_name:
            credentials = auth_service.get_account_credentials(
                access_token=token_data['access_token'],
                account_id=template.aws_sso_account_id,
                role_name=template.aws_sso_role_name,
                region=template.aws_sso_region
            )
            template.aws_access_key_id = credentials['access_key_id']
            template.aws_secret_access_key_encrypted = encrypt_value(credentials['secret_access_key'])
            template.aws_session_token_encrypted = encrypt_value(credentials['session_token'])
            template.aws_credentials_expiry = datetime.fromisoformat(credentials['expiration'])

        # Credential refresh succeeded — clear any stale failed-observation badge.
        _now = datetime.now(UTC)
        template.last_successful_call_at = _now
        template.last_error_at = None
        template.last_error_code = None
        template.last_error_message = None

        self._create_audit_log("sso_credentials_refreshed", template, "success", {
            "token_expiry": template.aws_sso_token_expiry.isoformat() if template.aws_sso_token_expiry else None,
            "credentials_expiry": template.aws_credentials_expiry.isoformat() if template.aws_credentials_expiry else None
        })

        return {
            "success": True,
            "message": "SSO credentials refreshed successfully",
            "data": {
                "token_expiry": template.aws_sso_token_expiry.isoformat() if template.aws_sso_token_expiry else None,
                "credentials_expiry": template.aws_credentials_expiry.isoformat() if template.aws_credentials_expiry else None
            }
        }

    def get_sso_status(self, template_id: int) -> dict[str, Any]:
        """Get SSO authentication status for a credential template."""
        template = self._get_template(template_id)

        if not template.aws_sso_enabled:
            return {"success": True, "sso_enabled": False,
                    "message": "SSO is not enabled for this template"}

        now = datetime.now(UTC)
        is_authenticated = template.aws_sso_authenticated_at is not None
        token_expired = now > template.aws_sso_token_expiry if template.aws_sso_token_expiry else False
        credentials_expired = now > template.aws_credentials_expiry if template.aws_credentials_expiry else False
        can_refresh = template.aws_sso_refresh_token_encrypted is not None

        return {
            "success": True,
            "sso_enabled": True,
            "is_authenticated": is_authenticated,
            "authenticated_at": template.aws_sso_authenticated_at.isoformat() if template.aws_sso_authenticated_at else None,
            "token_expired": token_expired,
            "token_expiry": template.aws_sso_token_expiry.isoformat() if template.aws_sso_token_expiry else None,
            "credentials_expired": credentials_expired,
            "credentials_expiry": template.aws_credentials_expiry.isoformat() if template.aws_credentials_expiry else None,
            "has_credentials": template.aws_access_key_id is not None,
            "can_refresh": can_refresh,
            "needs_reauth": (token_expired and not can_refresh) or (credentials_expired and token_expired)
        }
