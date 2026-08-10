"""
Cloud Authentication API Routes
Handles AWS SSO, IAM roles, GCP, Azure, and SSH authentication
"""
import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse  # Used for specific response handling
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from core.encryption import decrypt_value, encrypt_value
from core.errors import AppError, BadRequestError, NotFoundError, ServiceError, handle_exception, handle_route_errors
from database import get_db
from models import CloudCredentialTemplate, Project
from routes.auth import require_operator
from services.aws_auth_service import AWSAuthService
from services.credential_refresh_service import CredentialRefreshService
from services.ibm_cloud_service import IBMCloudService
from services.ssh_service import SSHService
from utils.validators import validate_aws_region

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cloud-auth", tags=["cloud-auth"], dependencies=[Depends(require_operator)])


# ============================================================
# Request/Response Models
# ============================================================

class _AWSRegionMixin(BaseModel):
    """Mixin that validates the region field against known AWS regions."""

    @model_validator(mode="after")
    def _validate_region(self):
        if hasattr(self, "region"):
            validate_aws_region(self.region, field_name="region")
        return self


class AWSSSOInitiateRequest(_AWSRegionMixin):
    """Request model for initiating AWS SSO device authorization"""
    start_url: str = Field(..., max_length=2048)
    region: str | None = None
    project_id: int | None = None


class AWSSSOPollRequest(_AWSRegionMixin):
    """Request model for polling AWS SSO token"""
    client_id: str = Field(..., max_length=256)
    client_secret: str = Field(..., max_length=512)
    device_code: str = Field(..., max_length=512)
    region: str | None = None
    project_id: int | None = None


class AWSCredentialsRequest(_AWSRegionMixin):
    """Request model for getting AWS account credentials"""
    access_token: str = Field(..., max_length=2048)
    account_id: str = Field(..., max_length=64)
    role_name: str = Field(..., max_length=256)
    region: str | None = None
    project_id: int


class AWSAssumeRoleRequest(_AWSRegionMixin):
    """Request model for assuming IAM role"""
    role_arn: str = Field(..., max_length=2048)
    session_name: str | None = Field("BNK-Forge-Session", max_length=128)
    duration_seconds: int | None = Field(3600, ge=900, le=43200)
    region: str | None = None
    project_id: int


class AWSListAccountsRequest(_AWSRegionMixin):
    """Request model for listing AWS accounts"""
    access_token: str = Field(..., max_length=2048)
    region: str | None = None


class AWSListRolesRequest(_AWSRegionMixin):
    """Request model for listing account roles"""
    access_token: str = Field(..., max_length=2048)
    account_id: str = Field(..., max_length=64)
    region: str | None = None


class CloudRegionsQueryRequest(BaseModel):
    provider: str = Field(..., max_length=50)
    ibmcloud_api_key: str | None = Field(default=None, max_length=8192)


class IBCosInstancesQueryRequest(BaseModel):
    ibmcloud_api_key: str = Field(..., max_length=8192)


class IBCosInstanceItem(BaseModel):
    value: str
    label: str
    crn: str


class IBCosInstancesResponse(BaseModel):
    instances: list[IBCosInstanceItem]


class CloudRegionItem(BaseModel):
    value: str
    label: str


class CloudRegionsResponse(BaseModel):
    provider: str
    regions: list[CloudRegionItem]


# ============================================================
# Cloud Region Discovery
# ============================================================


@router.post("/regions/query", response_model=CloudRegionsResponse)
@handle_route_errors("query cloud regions")
def query_cloud_regions(request: CloudRegionsQueryRequest, db: Session = Depends(get_db)):
    """Query available regions from a cloud provider using raw credentials."""
    if request.provider != "ibm":
        raise BadRequestError(f"Cloud region query is not supported for provider '{request.provider}'")

    service = IBMCloudService(db)
    regions = service.list_regions(request.ibmcloud_api_key or "")
    return {"provider": "ibm", "regions": regions}


@router.get("/ibm/regions", response_model=CloudRegionsResponse)
@handle_route_errors("list IBM Cloud regions")
def list_ibm_regions(template_id: int | None = None, db: Session = Depends(get_db)):
    """List IBM Cloud regions using either a selected IBM template or the default IBM template."""
    service = IBMCloudService(db)
    regions = service.list_regions_from_template(template_id=template_id)
    return {"provider": "ibm", "regions": regions}


@router.get("/regions/ibm", response_model=CloudRegionsResponse, include_in_schema=False)
@handle_route_errors("list IBM Cloud regions (legacy path)")
def list_ibm_regions_legacy(template_id: int | None = None, db: Session = Depends(get_db)):
    """Backward-compat alias for the old /regions/ibm path. Will be removed.

    Forwards to the canonical /ibm/regions handler so any caller still on
    the old path keeps working until the frontend rolls forward.
    """
    return list_ibm_regions(template_id=template_id, db=db)


@router.get("/aws/regions", response_model=CloudRegionsResponse)
@handle_route_errors("list AWS regions")
def list_aws_regions():
    """List AWS regions in the canonical {value, label} shape.

    Mirrors /ibm/regions so the frontend can drive both selectors from
    the same backend pattern. Returns the static set known by the
    backend validator (utils.validators.VALID_AWS_REGIONS) without a
    credential round-trip — AWS regions are public knowledge.
    """
    from utils.validators import VALID_AWS_REGIONS

    # Region display labels from the existing static frontend list, kept
    # here so the frontend can shed src/lib/aws-regions.ts in favor of
    # the same fetch pattern used for IBM.
    AWS_REGION_LABELS = {
        "us-east-1": "US East (N. Virginia)", "us-east-2": "US East (Ohio)",
        "us-west-1": "US West (N. California)", "us-west-2": "US West (Oregon)",
        "ca-central-1": "Canada (Central)", "ca-west-1": "Canada (Calgary)",
        "eu-central-1": "EU (Frankfurt)", "eu-central-2": "EU (Zurich)",
        "eu-west-1": "EU (Ireland)", "eu-west-2": "EU (London)",
        "eu-west-3": "EU (Paris)", "eu-north-1": "EU (Stockholm)",
        "eu-south-1": "EU (Milan)", "eu-south-2": "EU (Spain)",
        "ap-northeast-1": "Asia Pacific (Tokyo)", "ap-northeast-2": "Asia Pacific (Seoul)",
        "ap-northeast-3": "Asia Pacific (Osaka)", "ap-southeast-1": "Asia Pacific (Singapore)",
        "ap-southeast-2": "Asia Pacific (Sydney)", "ap-southeast-3": "Asia Pacific (Jakarta)",
        "ap-southeast-4": "Asia Pacific (Melbourne)", "ap-southeast-5": "Asia Pacific (Malaysia)",
        "ap-south-1": "Asia Pacific (Mumbai)", "ap-south-2": "Asia Pacific (Hyderabad)",
        "ap-east-1": "Asia Pacific (Hong Kong)", "me-south-1": "Middle East (Bahrain)",
        "me-central-1": "Middle East (UAE)", "il-central-1": "Israel (Tel Aviv)",
        "af-south-1": "Africa (Cape Town)", "sa-east-1": "South America (Sao Paulo)",
        "us-gov-west-1": "AWS GovCloud (US-West)", "us-gov-east-1": "AWS GovCloud (US-East)",
    }

    regions = sorted(
        ({"value": code, "label": AWS_REGION_LABELS.get(code, code)} for code in VALID_AWS_REGIONS),
        key=lambda r: r["label"],
    )
    return {"provider": "aws", "regions": regions}


@router.post("/ibm/cos-instances/query", response_model=IBCosInstancesResponse)
@handle_route_errors("query IBM COS instances")
def query_ibm_cos_instances(request: IBCosInstancesQueryRequest, db: Session = Depends(get_db)):
    """List IBM COS instances for a raw IBM API key."""
    service = IBMCloudService(db)
    instances = service.list_cos_instances(request.ibmcloud_api_key)
    return {"instances": instances}


# ============================================================
# AWS SSO Device Code Flow Endpoints
# ============================================================

@router.post("/aws/sso/initiate")
@handle_route_errors("initiate AWS SSO")
def initiate_aws_sso(request: AWSSSOInitiateRequest, db: Session = Depends(get_db)):
    """
    Initiate AWS SSO device authorization flow

    Returns device code and verification URL for user to complete authorization
    """
    auth_service = AWSAuthService()

    result = auth_service.initiate_device_authorization(
        start_url=request.start_url,
        region=request.region
    )

    logger.info(f"AWS SSO device authorization initiated for {request.start_url}")

    return {
        "success": True,
        "message": "Device authorization initiated. Please visit the verification URL and enter the code.",
        "data": {
            "user_code": result['user_code'],
            "verification_uri": result['verification_uri'],
            "verification_uri_complete": result['verification_uri_complete'],
            "expires_in": result['expires_in'],
            "interval": result['interval'],
            # These are needed for polling (don't expose to frontend, return as session data)
            "client_id": result['client_id'],
            "client_secret": result['client_secret'],
            "device_code": result['device_code'],
            "region": result['region']
        }
    }


@router.post("/aws/sso/poll")
def poll_aws_sso_token(request: AWSSSOPollRequest, db: Session = Depends(get_db)):
    """
    Poll for AWS SSO access token after user completes device authorization

    Should be called periodically until authorization is complete
    """
    try:
        auth_service = AWSAuthService()

        token_data = auth_service.poll_for_token(
            client_id=request.client_id,
            client_secret=request.client_secret,
            device_code=request.device_code,
            region=request.region,
            max_attempts=1  # Single poll attempt (frontend will retry)
        )

        # If project_id provided, store the access token
        if request.project_id:
            project = db.query(Project).filter(Project.id == request.project_id).first()
            if project:
                # Store SSO session data encrypted
                sso_session = {
                    'access_token': token_data['access_token'],
                    'refresh_token': token_data.get('refresh_token'),
                    'expires_at': token_data['expires_at'],
                    'client_id': request.client_id,
                    'client_secret': request.client_secret,
                    'region': request.region,
                    'auth_method': 'sso'
                }

                project.cloud_credentials_encrypted = encrypt_value(json.dumps(sso_session))
                project.cloud_provider = 'aws'
                db.commit()

                logger.info(f"Stored AWS SSO credentials for project {request.project_id}")

        return {
            "success": True,
            "message": "AWS SSO authentication successful",
            "data": {
                "access_token": token_data['access_token'],
                "expires_at": token_data['expires_at'],
                "has_refresh_token": token_data.get('refresh_token') is not None
            }
        }

    except AppError:
        db.rollback()
        raise
    except Exception as e:
        error_msg = str(e)

        # Return specific status for pending authorization
        if "Authorization pending" in error_msg or "AuthorizationPendingException" in error_msg:
            return JSONResponse(
                status_code=202,
                content={
                    "success": False,
                    "message": "Authorization pending",
                    "pending": True
                }
            )

        db.rollback()
        handle_exception(e, "poll AWS SSO token")


@router.post("/aws/accounts")
@handle_route_errors("list AWS accounts")
def list_aws_accounts(request: AWSListAccountsRequest):
    """List AWS accounts available to the authenticated user"""
    auth_service = AWSAuthService()

    accounts = auth_service.list_accounts(
        access_token=request.access_token,
        region=request.region
    )

    return {
        "success": True,
        "accounts": accounts,
        "count": len(accounts)
    }


@router.post("/aws/accounts/roles")
@handle_route_errors("list AWS account roles")
def list_aws_account_roles(request: AWSListRolesRequest):
    """List IAM roles available for a specific AWS account"""
    auth_service = AWSAuthService()

    roles = auth_service.list_account_roles(
        access_token=request.access_token,
        account_id=request.account_id,
        region=request.region
    )

    return {
        "success": True,
        "roles": roles,
        "count": len(roles)
    }


@router.post("/aws/credentials")
@handle_route_errors("get AWS credentials")
def get_aws_credentials(request: AWSCredentialsRequest, db: Session = Depends(get_db)):
    """
    Get temporary AWS credentials for a specific account and role

    Stores credentials in project for future use
    """
    auth_service = AWSAuthService()

    credentials = auth_service.get_account_credentials(
        access_token=request.access_token,
        account_id=request.account_id,
        role_name=request.role_name,
        region=request.region
    )

    # Store credentials in project
    project = db.query(Project).filter(Project.id == request.project_id).first()
    if not project:
        raise NotFoundError("project", request.project_id)

    # Encrypt and store credentials
    cred_data = {
        'access_key_id': credentials['access_key_id'],
        'secret_access_key': credentials['secret_access_key'],
        'session_token': credentials['session_token'],
        'expiration': credentials['expiration'],
        'account_id': request.account_id,
        'role_name': request.role_name,
        'region': request.region,
        'auth_method': 'sso'
    }

    project.cloud_credentials_encrypted = encrypt_value(json.dumps(cred_data))
    project.cloud_provider = 'aws'
    db.commit()

    logger.info(f"Stored AWS credentials for project {request.project_id}")

    return {
        "success": True,
        "message": "AWS credentials obtained and stored successfully",
        "data": {
            "account_id": request.account_id,
            "role_name": request.role_name,
            "expiration": credentials['expiration']
        }
    }


@router.post("/aws/assume-role")
@handle_route_errors("assume AWS role")
def assume_aws_role(request: AWSAssumeRoleRequest, db: Session = Depends(get_db)):
    """
    Assume an IAM role using STS

    Useful for cross-account access or role chaining
    """
    auth_service = AWSAuthService()

    credentials = auth_service.assume_role(
        role_arn=request.role_arn,
        session_name=request.session_name,
        duration_seconds=request.duration_seconds,
        region=request.region
    )

    # Store credentials in project
    project = db.query(Project).filter(Project.id == request.project_id).first()
    if not project:
        raise NotFoundError("project", request.project_id)

    # Encrypt and store credentials
    cred_data = {
        'access_key_id': credentials['access_key_id'],
        'secret_access_key': credentials['secret_access_key'],
        'session_token': credentials['session_token'],
        'expiration': credentials['expiration'],
        'role_arn': request.role_arn,
        'region': request.region,
        'auth_method': 'assume_role'
    }

    project.cloud_credentials_encrypted = encrypt_value(json.dumps(cred_data))
    project.cloud_provider = 'aws'
    db.commit()

    logger.info(f"Assumed IAM role for project {request.project_id}")

    return {
        "success": True,
        "message": "Successfully assumed IAM role",
        "data": {
            "role_arn": request.role_arn,
            "expiration": credentials['expiration']
        }
    }


@router.get("/aws/credentials/{project_id}")
@handle_route_errors("get AWS credentials metadata")
def get_project_aws_credentials(project_id: int, db: Session = Depends(get_db)):
    """
    Get stored AWS credentials metadata for a project.

    Returns credential metadata (never the actual keys).  Considers both the
    project-level encrypted blob and any bound credential template, so the
    ``configured`` field truthfully reflects whether AWS credentials are
    available to this project.  A ``source`` discriminator indicates where
    the credentials come from ("project" | "template" | "default_template").
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError("project", project_id)

    # --- Source 1: project-level encrypted blob (legacy / project-SSO flow) ---
    if project.cloud_credentials_encrypted:
        cred_json = decrypt_value(project.cloud_credentials_encrypted)
        if not cred_json:
            return {
                "success": True,
                "configured": False,
                "source": "project",
                "message": "Failed to decrypt credentials"
            }
        cred_data = json.loads(cred_json)
        return {
            "success": True,
            "configured": True,
            "source": "project",
            "data": {
                "auth_method": cred_data.get('auth_method'),
                "account_id": cred_data.get('account_id'),
                "role_name": cred_data.get('role_name'),
                "role_arn": cred_data.get('role_arn'),
                "region": cred_data.get('region'),
                "expiration": cred_data.get('expiration'),
                "has_session_token": 'session_token' in cred_data
            }
        }

    # --- Source 2: explicit credential template binding ---
    template: CloudCredentialTemplate | None = None
    source = "template"
    if project.credential_template_id:
        template = db.query(CloudCredentialTemplate).filter(
            CloudCredentialTemplate.id == project.credential_template_id
        ).first()

    # --- Source 3: is_default template fallback ---
    # Only use a default template whose provider matches the project's cloud_provider.
    # A GCP is_default template must never be reported as configured for an AWS project —
    # that would surface configured=True with wrong-provider credentials.
    if template is None and project.cloud_provider:
        template = db.query(CloudCredentialTemplate).filter(
            CloudCredentialTemplate.is_default.is_(True),
            CloudCredentialTemplate.provider == project.cloud_provider,
        ).first()
        if template:
            source = "default_template"

    if template is None:
        return {
            "success": True,
            "configured": False,
            "source": "none",
            "message": "No AWS credentials configured for this project"
        }

    # Reflect the template's credential state honestly
    has_credentials = bool(template.aws_access_key_id)
    credentials_expired = False
    if template.aws_credentials_expiry:
        credentials_expired = datetime.now(UTC) > template.aws_credentials_expiry

    return {
        "success": True,
        "configured": has_credentials and not credentials_expired,
        "source": source,
        "data": {
            "template_id": template.id,
            "template_name": template.name,
            "provider": template.provider,
            "auth_method": template.aws_auth_method,
            "region": template.region,
            "has_credentials": has_credentials,
            "credentials_expired": credentials_expired,
            "credentials_expiry": (
                template.aws_credentials_expiry.isoformat()
                if template.aws_credentials_expiry else None
            ),
            "can_refresh": bool(template.aws_sso_refresh_token_encrypted),
        }
    }


@router.delete("/aws/credentials/{project_id}")
@handle_route_errors("delete AWS credentials")
def delete_project_aws_credentials(project_id: int, db: Session = Depends(get_db)):
    """Remove AWS credentials from a project"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError("project", project_id)

    project.cloud_credentials_encrypted = None
    project.cloud_provider = None
    db.commit()

    logger.info(f"Removed AWS credentials from project {project_id}")

    return {
        "success": True,
        "message": "AWS credentials removed successfully"
    }


# ============================================================
# SSH Authentication Endpoints
# ============================================================

class SSHConfigureRequest(BaseModel):
    """Request model for configuring SSH credentials"""
    project_id: int
    host: str = Field(..., max_length=500)
    port: int | None = Field(22, ge=1, le=65535)
    username: str = Field(..., max_length=255)
    auth_type: str = Field(..., pattern=r"^(password|key)$")
    password: str | None = Field(None, max_length=500)
    private_key: str | None = Field(None, max_length=16384)
    passphrase: str | None = Field(None, max_length=500)


class SSHTestRequest(BaseModel):
    """Request model for testing SSH connection"""
    host: str = Field(..., max_length=500)
    port: int | None = Field(22, ge=1, le=65535)
    username: str = Field(..., max_length=255)
    auth_type: str = Field(..., pattern=r"^(password|key)$")
    password: str | None = Field(None, max_length=500)
    private_key: str | None = Field(None, max_length=16384)
    passphrase: str | None = Field(None, max_length=500)


class SSHExecuteRequest(BaseModel):
    """Request model for executing SSH command"""
    project_id: int
    command: str = Field(..., max_length=4096)
    timeout: int | None = Field(30, ge=1, le=300)


class SSHKeyGenerateRequest(BaseModel):
    """Request model for generating SSH key pair"""
    key_type: str | None = Field("rsa", pattern=r"^(rsa|ed25519|ecdsa)$")
    bits: int | None = Field(2048, ge=1024, le=8192)


@router.post("/ssh/configure")
@handle_route_errors("configure SSH")
def configure_ssh(request: SSHConfigureRequest, db: Session = Depends(get_db)):
    """
    Configure SSH credentials for a project

    Stores encrypted SSH credentials for infrastructure access
    """
    project = db.query(Project).filter(Project.id == request.project_id).first()
    if not project:
        raise NotFoundError("project", request.project_id)

    # Validate SSH connection first
    ssh_service = SSHService()
    success, message = ssh_service.test_connection(
        host=request.host,
        port=request.port,
        username=request.username,
        auth_type=request.auth_type,
        password=request.password,
        private_key=request.private_key,
        passphrase=request.passphrase
    )

    if not success:
        raise BadRequestError(f"SSH connection test failed: {message}")

    # Store encrypted credentials
    project.infra_enabled = True
    project.infra_host = request.host
    project.infra_ssh_port = request.port
    project.infra_ssh_username = request.username
    project.infra_auth_type = request.auth_type

    if request.auth_type == "password" and request.password:
        project.infra_ssh_password_encrypted = encrypt_value(request.password)
        project.infra_ssh_key_encrypted = None

    elif request.auth_type == "key" and request.private_key:
        project.infra_ssh_key_encrypted = encrypt_value(request.private_key)
        project.infra_ssh_password_encrypted = None

        # Store passphrase if provided (encrypted)
        if request.passphrase:
            # Store passphrase in cloud_credentials_encrypted as SSH metadata
            ssh_metadata = {
                'passphrase': request.passphrase,
                'key_configured': True
            }
            project.cloud_credentials_encrypted = encrypt_value(json.dumps(ssh_metadata))

    db.commit()

    logger.info(f"SSH credentials configured for project {request.project_id}")

    return {
        "success": True,
        "message": "SSH credentials configured successfully",
        "data": {
            "host": request.host,
            "port": request.port,
            "username": request.username,
            "auth_type": request.auth_type
        }
    }


@router.post("/ssh/test")
@handle_route_errors("test SSH connection")
def test_ssh_connection(request: SSHTestRequest):
    """
    Test SSH connection without storing credentials

    Useful for validating credentials before saving
    """
    ssh_service = SSHService()

    success, message = ssh_service.test_connection(
        host=request.host,
        port=request.port,
        username=request.username,
        auth_type=request.auth_type,
        password=request.password,
        private_key=request.private_key,
        passphrase=request.passphrase
    )

    if success:
        return {
            "success": True,
            "message": message
        }
    else:
        raise BadRequestError(f"SSH connection test failed: {message}")


@router.post("/ssh/execute")
@handle_route_errors("execute SSH command")
def execute_ssh_command(request: SSHExecuteRequest, db: Session = Depends(get_db)):
    """
    Execute a command on remote host using project's SSH credentials

    Requires SSH to be configured for the project
    """
    project = db.query(Project).filter(Project.id == request.project_id).first()
    if not project:
        raise NotFoundError("project", request.project_id)

    if not project.infra_enabled:
        raise BadRequestError("SSH not configured for this project")

    # Decrypt credentials
    password = None
    private_key = None
    passphrase = None

    if project.infra_auth_type == "password":
        password = decrypt_value(project.infra_ssh_password_encrypted)

    elif project.infra_auth_type == "key":
        private_key = decrypt_value(project.infra_ssh_key_encrypted)

        # Get passphrase if stored
        if project.cloud_credentials_encrypted:
            metadata_json = decrypt_value(project.cloud_credentials_encrypted)
            if metadata_json:
                try:
                    metadata = json.loads(metadata_json)
                    passphrase = metadata.get('passphrase')
                except (json.JSONDecodeError, TypeError, KeyError):
                    pass

    # Execute command
    ssh_service = SSHService()
    result = ssh_service.execute_command(
        host=project.infra_host,
        port=project.infra_ssh_port,
        username=project.infra_ssh_username,
        command=request.command,
        auth_type=project.infra_auth_type,
        password=password,
        private_key=private_key,
        passphrase=passphrase,
        timeout=request.timeout
    )

    if result['success']:
        return {
            "success": True,
            "stdout": result['stdout'],
            "stderr": result['stderr'],
            "exit_code": result['exit_code']
        }
    else:
        raise ServiceError("ssh", result['error'])


@router.post("/ssh/generate-key")
@handle_route_errors("generate SSH key")
def generate_ssh_key(request: SSHKeyGenerateRequest):
    """
    Generate a new SSH key pair

    Returns both private and public keys for user to save
    """
    ssh_service = SSHService()

    key_pair = ssh_service.generate_key_pair(
        key_type=request.key_type,
        bits=request.bits
    )

    return {
        "success": True,
        "message": "SSH key pair generated successfully",
        "data": key_pair
    }


@router.get("/ssh/credentials/{project_id}")
@handle_route_errors("get SSH credentials metadata")
def get_ssh_credentials_metadata(project_id: int, db: Session = Depends(get_db)):
    """
    Get SSH credentials metadata for a project

    Returns configuration details (not actual credentials)
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError("project", project_id)

    if not project.infra_enabled:
        return {
            "success": True,
            "configured": False,
            "message": "SSH not configured for this project"
        }

    return {
        "success": True,
        "configured": True,
        "data": {
            "host": project.infra_host,
            "port": project.infra_ssh_port,
            "username": project.infra_ssh_username,
            "auth_type": project.infra_auth_type,
            "has_password": project.infra_ssh_password_encrypted is not None,
            "has_key": project.infra_ssh_key_encrypted is not None
        }
    }


@router.delete("/ssh/credentials/{project_id}")
@handle_route_errors("delete SSH credentials")
def delete_ssh_credentials(project_id: int, db: Session = Depends(get_db)):
    """Remove SSH credentials from a project"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError("project", project_id)

    project.infra_enabled = False
    project.infra_host = None
    project.infra_ssh_port = 22
    project.infra_ssh_username = None
    project.infra_ssh_password_encrypted = None
    project.infra_ssh_key_encrypted = None
    project.infra_auth_type = "password"

    db.commit()

    logger.info(f"Removed SSH credentials from project {project_id}")

    return {
        "success": True,
        "message": "SSH credentials removed successfully"
    }


# ============================================================
# Credential Refresh Endpoints
# ============================================================

@router.get("/credentials/status/{project_id}")
@handle_route_errors("get credential status")
def get_credential_status(project_id: int, db: Session = Depends(get_db)):
    """
    Get credential expiration status for a project

    Returns information about credential expiration and refresh capability
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError("project", project_id)

    refresh_service = CredentialRefreshService()
    status = refresh_service.get_credential_status(project)

    if not status:
        return {
            "success": True,
            "configured": False,
            "message": "No credentials configured for this project"
        }

    return {
        "success": True,
        "configured": True,
        "status": status
    }


@router.post("/credentials/refresh/{project_id}")
@handle_route_errors("refresh credentials")
def refresh_credentials(project_id: int, db: Session = Depends(get_db)):
    """
    Manually refresh credentials for a project

    Forces an immediate credential refresh
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError("project", project_id)

    refresh_service = CredentialRefreshService()
    success = refresh_service.force_refresh_project(project_id)

    if success:
        return {
            "success": True,
            "message": "Credentials refreshed successfully"
        }
    else:
        raise BadRequestError("Failed to refresh credentials. Check if refresh is supported for this auth method.")


@router.get("/credentials/check-all")
@handle_route_errors("check all credentials")
def check_all_credentials(db: Session = Depends(get_db)):
    """
    Check expiration status for all projects

    Returns summary of credential status across all projects
    """
    projects = db.query(Project).filter(
        Project.cloud_credentials_encrypted.isnot(None)
    ).all()

    refresh_service = CredentialRefreshService()
    results = []

    for project in projects:
        status = refresh_service.get_credential_status(project)
        if status:
            results.append({
                "project_id": project.id,
                "project_name": project.name,
                "status": status
            })

    # Count summary
    expired_count = sum(1 for r in results if r['status'].get('is_expired'))
    expiring_soon_count = sum(1 for r in results if r['status'].get('is_expiring_soon'))

    return {
        "success": True,
        "total_projects": len(results),
        "expired_count": expired_count,
        "expiring_soon_count": expiring_soon_count,
        "projects": results
    }
