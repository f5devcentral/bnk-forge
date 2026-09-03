"""
Credentials Service - Centralized credential loading for cloud providers

This service provides a single source of truth for loading cloud credentials
with proper priority handling across the application.

Priority order:
1. Project-specific credential template (v2 approach - CloudCredentialTemplate)
2. Project-specific encrypted credentials (legacy - direct encryption)
3. Global credentials from ApplicationSettings (database)
4. Fallback to environment variables (.env - bootstrap only)
"""

import json
import logging
import os
from datetime import UTC, datetime
from types import SimpleNamespace

from core.encryption import decrypt_value, decrypt_value_or_none
from models import ApplicationSetting, CloudCredentialTemplate, Project
from services.defaults_service import get_default
from services.ibm_cloud_service import IBMCloudService

logger = logging.getLogger(__name__)


class CredentialUnavailableError(Exception):
    """
    Raised when AWS credentials cannot be resolved for a project —
    either because no template/creds are bound, the SSO keys are absent,
    or the exchanged keys have expired.

    Distinct from a transient boto3/network glitch so callers can surface
    a clear credential-specific message instead of masking it as a 401.
    """

    def __init__(self, message: str, template_name: str | None = None):
        self.template_name = template_name
        super().__init__(message)


def _decrypt_credential(encrypted_value: str | None, field_name: str) -> str | None:
    """
    Safely decrypt a credential field with error handling.
    DRY helper to avoid repeated try/except blocks.

    Args:
        encrypted_value: The encrypted value to decrypt
        field_name: Name of field (for logging)

    Returns:
        Decrypted value or None if decryption fails
    """
    if not encrypted_value:
        return None
    try:
        return decrypt_value(encrypted_value)
    except Exception as e:
        logger.error(f"Error decrypting {field_name}: {e}")
        return None


# Every cloud-credential var this module manages. get_cloud_credentials_only()
# filters to exactly these — see #443 for why an allowlist and not a denylist:
# the alternative leaked DATABASE_URL / CELERY_* / REDIS_URL to third-party
# artifact images, and a denylist would re-leak on every new worker env var.
CLOUD_CREDENTIAL_ENV_KEYS = frozenset({
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_DEFAULT_REGION",
    "AWS_PROFILE",
    "IBMCLOUD_API_KEY",
    "IBMCLOUD_REGION",
    "IBMCLOUD_RESOURCE_GROUP",
    "IC_API_KEY",
    "ARM_CLIENT_ID",
    "ARM_CLIENT_SECRET",
    "ARM_SUBSCRIPTION_ID",
    "ARM_TENANT_ID",
    "ARM_LOCATION",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    "AZURE_TENANT_ID",
    "AZURE_SUBSCRIPTION_ID",
})


def get_cloud_credentials_only(project: Project, db=None, *, strict: bool = False) -> dict[str, str]:
    """Cloud credentials WITHOUT the ambient process environment.

    ``get_cloud_credentials_env`` starts from ``os.environ.copy()`` because its
    callers run worker *subprocesses*, which need PATH/HOME to work at all.
    Handing that dict to a third-party container image is a different matter
    (#443): it disclosed Forge's ``DATABASE_URL``/``CELERY_BROKER_URL``/
    ``REDIS_URL`` to every artifact, and ambient ``DOCKER_HOST``/``HOME``
    silently overrode the values an artifact manifest declared in
    ``state.home_env`` (step env is applied after home_env in the run argv).

    Keys present in the worker env AND in the allowlist are kept — that is the
    documented ".env bootstrap" path for cloud credentials, and containers
    should honour it the same way subprocesses do.
    """
    resolved = get_cloud_credentials_env(project, db, strict=strict)
    return {
        key: value
        for key, value in resolved.items()
        if key in CLOUD_CREDENTIAL_ENV_KEYS and value
    }


def get_cloud_credentials_env(project: Project, db=None, *, strict: bool = False) -> dict[str, str]:
    """
    Get cloud credentials as environment variables for subprocess execution.

    This function loads credentials in priority order:
    1. Project-specific credential template (v2 approach)
    2. Project-specific encrypted credentials (legacy)
    3. Global credentials from ApplicationSettings (database)
    4. Fallback to environment variables (.env - bootstrap only)

    Args:
        project: The Project model instance
        db: Database session (optional, required for templates and global settings)
        strict: When True, raise CredentialUnavailableError for absent or expired
                SSO credentials rather than returning a partial env dict.
                Defaults to False so the ~19 non-connectivity callers retain
                their existing graceful-degradation behaviour unchanged.
                The EKS connectivity path passes strict=True.

    Returns:
        Dict of environment variables to inject into subprocess
    """
    env = os.environ.copy()

    # Load global credentials from database (if available)
    global_creds_loaded = False
    if db:
        try:
            access_key_setting = db.query(ApplicationSetting).filter(
                ApplicationSetting.key == 'aws.access_key_id'
            ).first()

            secret_key_setting = db.query(ApplicationSetting).filter(
                ApplicationSetting.key == 'aws.secret_access_key'
            ).first()

            session_token_setting = db.query(ApplicationSetting).filter(
                ApplicationSetting.key == 'aws.session_token'
            ).first()

            region_setting = db.query(ApplicationSetting).filter(
                ApplicationSetting.key == 'aws.region'
            ).first()

            profile_setting = db.query(ApplicationSetting).filter(
                ApplicationSetting.key == 'aws.profile'
            ).first()

            # Override environment with current credentials from database
            if access_key_setting and access_key_setting.value:
                env['AWS_ACCESS_KEY_ID'] = access_key_setting.value
                global_creds_loaded = True

            if secret_key_setting and secret_key_setting.value:
                # Decrypt if encrypted
                secret_value = secret_key_setting.value
                if secret_key_setting.is_encrypted:
                    secret_value = decrypt_value_or_none(secret_value) or secret_value
                env['AWS_SECRET_ACCESS_KEY'] = secret_value

            if session_token_setting and session_token_setting.value:
                # Decrypt if encrypted
                token_value = session_token_setting.value
                if session_token_setting.is_encrypted:
                    token_value = decrypt_value_or_none(token_value) or token_value
                env['AWS_SESSION_TOKEN'] = token_value

            if region_setting and region_setting.value:
                env['AWS_DEFAULT_REGION'] = region_setting.value

            if profile_setting and profile_setting.value:
                env['AWS_PROFILE'] = profile_setting.value

        except Exception as e:
            logger.warning(f"Failed to load global credentials from database: {e}")

    # Priority 1: Check if project uses a credential template (v2)
    if project.credential_template_id and db:
        try:
            template = db.query(CloudCredentialTemplate).filter(
                CloudCredentialTemplate.id == project.credential_template_id
            ).first()

            if template:
                logger.info(f"Using credential template '{template.name}' for project {project.name}")

                if template.provider == 'aws':
                    # Access Keys method
                    if template.aws_auth_method == 'access_keys':
                        if template.aws_access_key_id:
                            env['AWS_ACCESS_KEY_ID'] = template.aws_access_key_id
                        secret = _decrypt_credential(template.aws_secret_access_key_encrypted, 'AWS secret key')
                        if secret:
                            env['AWS_SECRET_ACCESS_KEY'] = secret
                        token = _decrypt_credential(template.aws_session_token_encrypted, 'AWS session token')
                        if token:
                            env['AWS_SESSION_TOKEN'] = token

                    # Profile method
                    elif template.aws_auth_method == 'profile' and template.aws_profile:
                        env['AWS_PROFILE'] = template.aws_profile

                    # SSO method - use cached credentials from SSO authentication
                    elif template.aws_auth_method == 'sso' and template.aws_sso_enabled:
                        # When strict=True (connectivity path), raise a structured
                        # error for absent or expired SSO keys so the caller gets a
                        # clear credential message.  When strict=False (the default,
                        # used by all task/deploy callers), fall through silently as
                        # before — those callers expect graceful degradation.
                        if strict and not template.aws_access_key_id:
                            raise CredentialUnavailableError(
                                f"SSO credentials for template '{template.name}' have not been exchanged yet. "
                                "Please authenticate via the credential template SSO flow.",
                                template_name=template.name,
                            )
                        if strict and template.aws_credentials_expiry and datetime.now(UTC) > template.aws_credentials_expiry:
                            raise CredentialUnavailableError(
                                f"SSO credentials for template '{template.name}' have expired. "
                                "Please re-authenticate via the credential template SSO flow.",
                                template_name=template.name,
                            )

                        env['AWS_ACCESS_KEY_ID'] = template.aws_access_key_id
                        secret = _decrypt_credential(template.aws_secret_access_key_encrypted, 'SSO AWS secret key')
                        if secret:
                            env['AWS_SECRET_ACCESS_KEY'] = secret
                        token = _decrypt_credential(template.aws_session_token_encrypted, 'SSO AWS session token')
                        if token:
                            env['AWS_SESSION_TOKEN'] = token

                    # Set region from template
                    if template.region:
                        env['AWS_DEFAULT_REGION'] = template.region

                    # Mirror the resolved AWS credentials as Terraform variables so that
                    # module provider blocks using var.aws_access_key_id / var.aws_region
                    # are populated without persisting creds in project variable storage.
                    # Only set a TF_VAR_* when the corresponding AWS_* key was actually set.
                    for aws_key, tf_var in (
                        ('AWS_ACCESS_KEY_ID', 'TF_VAR_aws_access_key_id'),
                        ('AWS_SECRET_ACCESS_KEY', 'TF_VAR_aws_secret_access_key'),
                        ('AWS_SESSION_TOKEN', 'TF_VAR_aws_session_token'),
                        ('AWS_DEFAULT_REGION', 'TF_VAR_aws_region'),
                    ):
                        if env.get(aws_key):
                            env[tf_var] = env[aws_key]

                    return env

                if template.provider == 'ibm':
                    api_key = _decrypt_credential(template.ibmcloud_api_key_encrypted, 'IBM Cloud API key')
                    if api_key:
                        env['IC_API_KEY'] = api_key
                        env['IBMCLOUD_API_KEY'] = api_key
                    if template.ibm_cos_instance_name and db:
                        try:
                            access_key_id, secret_access_key = IBMCloudService(db).ensure_cos_hmac_credentials(template)
                            env['AWS_ACCESS_KEY_ID'] = access_key_id
                            env['AWS_SECRET_ACCESS_KEY'] = secret_access_key
                        except Exception as e:
                            logger.error(f"Failed to derive IBM COS HMAC credentials: {e}")
                    if template.region:
                        env['IBMCLOUD_REGION'] = template.region
                        env.setdefault('AWS_DEFAULT_REGION', template.region)
                    if template.ibmcloud_resource_group:
                        env['IBMCLOUD_RESOURCE_GROUP'] = template.ibmcloud_resource_group
                    return env

                if template.provider == 'azure':
                    if template.azure_subscription_id:
                        env['ARM_SUBSCRIPTION_ID'] = template.azure_subscription_id
                        env['AZURE_SUBSCRIPTION_ID'] = template.azure_subscription_id
                    if template.azure_tenant_id:
                        env['ARM_TENANT_ID'] = template.azure_tenant_id
                        env['AZURE_TENANT_ID'] = template.azure_tenant_id
                    if template.azure_client_id:
                        env['ARM_CLIENT_ID'] = template.azure_client_id
                        env['AZURE_CLIENT_ID'] = template.azure_client_id
                    if template.azure_client_secret_encrypted:
                        secret = _decrypt_credential(template.azure_client_secret_encrypted, 'Azure Client Secret')
                        if secret:
                            env['ARM_CLIENT_SECRET'] = secret
                            env['AZURE_CLIENT_SECRET'] = secret
                    if template.region:
                        env['ARM_LOCATION'] = template.region
                    return env

        except CredentialUnavailableError:
            raise  # propagate structured credential signals — don't swallow them
        except Exception as e:
            logger.error(f"Error loading credential template: {e}")

    # Priority 2: Try project-specific encrypted credentials (legacy)
    if project.cloud_credentials_encrypted:
        try:
            cred_json = decrypt_value(project.cloud_credentials_encrypted)
            if cred_json:
                cred_data = json.loads(cred_json)

                # AWS Profile auth
                if cred_data.get('profile'):
                    env['AWS_PROFILE'] = cred_data['profile']
                    if cred_data.get('region'):
                        env['AWS_DEFAULT_REGION'] = cred_data['region']
                    return env

                # Access Key auth
                if cred_data.get('access_key_id'):
                    env['AWS_ACCESS_KEY_ID'] = cred_data['access_key_id']
                if cred_data.get('secret_access_key'):
                    env['AWS_SECRET_ACCESS_KEY'] = cred_data['secret_access_key']
                if cred_data.get('session_token'):
                    env['AWS_SESSION_TOKEN'] = cred_data['session_token']
                    # SSO credentials need region - use system default if not specified
                    region = cred_data.get('region') or project.region or get_default(db, "cloud.aws.default_region")
                    env['AWS_DEFAULT_REGION'] = region

                return env
        except Exception as e:
            logger.error(f"Error getting project credentials: {e}")

    # Priority 2.5: Fall back to the is_default credential template when the project
    # has neither an explicit credential_template_id nor project-specific creds.
    # This makes projects registered without an explicit template binding (e.g. via
    # awsbnkctl forge register) work automatically if a default template exists.
    #
    # Safety rule: only use a default template whose provider matches the project's
    # cloud_provider.  A GCP is_default template must never inject credentials into
    # an AWS project — that would cause a wrong-provider 401 with a misleading error.
    # If the project's cloud_provider is unknown, skip the fallback entirely.
    if db and not project.credential_template_id and not project.cloud_credentials_encrypted:
        project_provider = getattr(project, "cloud_provider", None)
        if project_provider:
            try:
                default_template = db.query(CloudCredentialTemplate).filter(
                    CloudCredentialTemplate.is_default.is_(True),
                    CloudCredentialTemplate.provider == project_provider,
                ).first()
                if default_template:
                    logger.info(
                        "Project '%s' has no credential binding — falling back to "
                        "is_default template '%s' (id=%s, provider=%s)",
                        project.name, default_template.name, default_template.id,
                        default_template.provider,
                    )
                    # Re-use Priority 1 path by temporarily setting the attribute
                    # on a throwaway namespace so the resolution logic is not duplicated.
                    # We call ourselves with a shim project that has the template id set.
                    shim = SimpleNamespace(**{
                        col.key: getattr(project, col.key)
                        for col in project.__class__.__table__.columns
                    })
                    shim.credential_template_id = default_template.id
                    shim.cloud_credentials_encrypted = None
                    return get_cloud_credentials_env(shim, db, strict=strict)  # type: ignore[arg-type]
            except CredentialUnavailableError:
                raise
            except Exception as e:
                logger.error("Error loading is_default credential template fallback: %s", e)

    # Priority 3: Set region from project if not already set (AWS projects only)
    if project.cloud_provider == "aws" and project.region and 'AWS_DEFAULT_REGION' not in env:
        env['AWS_DEFAULT_REGION'] = project.region

    if global_creds_loaded:
        logger.info(f"Using global credentials from database for project {project.name}")
    else:
        logger.info(f"Using global credentials from environment (.env) for project {project.name}")

    return env


def get_gcp_service_account_info(project: Project | None, db=None) -> dict | None:
    """
    Return the parsed GCP service-account JSON for a project, or None.

    The SA key is stored encrypted on the project's CloudCredentialTemplate
    (``gcp_credentials_encrypted``) or the default GCP template.  Mint an access
    token from this value via ``google.oauth2.service_account.Credentials`` to
    authenticate the Python kubernetes client to GKE without needing the
    ``gke-gcloud-auth-plugin`` binary inside the container.
    """
    template = None
    if db:
        if project and getattr(project, "credential_template_id", None):
            template = db.query(CloudCredentialTemplate).filter(
                CloudCredentialTemplate.id == project.credential_template_id
            ).first()

        if not template or template.provider != "gcp" or not template.gcp_credentials_encrypted:
            template = db.query(CloudCredentialTemplate).filter(
                CloudCredentialTemplate.provider == "gcp",
                CloudCredentialTemplate.is_default == True,
            ).first()

    if template and template.provider == "gcp" and template.gcp_credentials_encrypted:
        sa_json = _decrypt_credential(template.gcp_credentials_encrypted, "GCP SA JSON")
        if sa_json:
            try:
                return json.loads(sa_json)
            except json.JSONDecodeError as e:
                logger.error(f"GCP credentials for template '{template.name}' are not valid JSON: {e}")

    # Fallback to environment variable
    gcp_sa_env = os.getenv("GCP_SERVICE_ACCOUNT_KEY") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if gcp_sa_env:
        try:
            return json.loads(gcp_sa_env)
        except json.JSONDecodeError as e:
            logger.error(f"GCP credentials from environment are not valid JSON: {e}")

    return None


def get_azure_service_principal_info(project: Project | None, db=None) -> tuple[str, str, str] | None:
    """
    Return (tenant_id, client_id, client_secret) for an Azure project or global default, or None.

    Resolves credentials from:
    1. Project's CloudCredentialTemplate (via credential_template_id)
    2. Global default Azure CloudCredentialTemplate (is_default=True)
    3. Environment variables (AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET)
    """
    template = None
    if db:
        if project and getattr(project, "credential_template_id", None):
            template = db.query(CloudCredentialTemplate).filter(
                CloudCredentialTemplate.id == project.credential_template_id
            ).first()

        if not template or template.provider != "azure" or not template.azure_credentials_encrypted:
            template = db.query(CloudCredentialTemplate).filter(
                CloudCredentialTemplate.provider == "azure",
                CloudCredentialTemplate.is_default == True,
            ).first()

    if template and template.provider == "azure" and template.azure_credentials_encrypted and template.azure_tenant_id:
        creds_json = _decrypt_credential(template.azure_credentials_encrypted, "Azure credentials")
        if creds_json:
            try:
                creds = json.loads(creds_json)
                client_id = creds.get("client_id") or creds.get("clientId")
                client_secret = creds.get("client_secret") or creds.get("clientSecret")
                if client_id and client_secret:
                    return (template.azure_tenant_id, client_id, client_secret)
            except json.JSONDecodeError as e:
                logger.error(f"Azure credentials for template '{template.name}' are not valid JSON: {e}")

    # Fallback to environment variables
    env_tenant = os.getenv("AZURE_TENANT_ID") or os.getenv("ARM_TENANT_ID")
    env_client_id = os.getenv("AZURE_CLIENT_ID") or os.getenv("ARM_CLIENT_ID")
    env_client_secret = os.getenv("AZURE_CLIENT_SECRET") or os.getenv("ARM_CLIENT_SECRET")
    if env_tenant and env_client_id and env_client_secret:
        return (env_tenant, env_client_id, env_client_secret)

    return None
