"""
API routes for managing cloud credential templates.

Thin HTTP handlers delegating to CredentialTemplateService.
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, model_validator
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from routes.auth import require_operator, require_viewer
from services.credential_template_service import CredentialTemplateService
from utils.validators import validate_aws_region, validate_azure_region, validate_gcp_region, validate_ibm_region

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/credential-templates", tags=["credential-templates"])


# ============================================================================
# Pydantic Schemas
# ============================================================================

class CredentialTemplateBase(BaseModel):
    name: str
    description: str | None = None
    provider: str
    aws_auth_method: str | None = None
    aws_profile: str | None = None
    region: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_session_token: str | None = None
    aws_sso_enabled: bool | None = False
    aws_sso_start_url: str | None = None
    aws_sso_region: str | None = None
    aws_sso_account_id: str | None = None
    aws_sso_role_name: str | None = None
    gcp_credentials: str | None = None
    gcp_project_id: str | None = None
    azure_auth_method: str | None = None
    azure_subscription_id: str | None = None
    azure_tenant_id: str | None = None
    azure_client_id: str | None = None
    azure_client_secret: str | None = None
    azure_credentials: str | None = None
    ibmcloud_api_key: str | None = None
    ibmcloud_resource_group: str | None = "default"
    ibm_cos_instance_name: str | None = None
    tfc_api_token: str | None = None
    tfc_hostname: str | None = None
    ssh_host: str | None = None
    ssh_port: int | None = 22
    ssh_username: str | None = None
    ssh_auth_type: str | None = None
    ssh_password: str | None = None
    ssh_private_key: str | None = None
    ssh_key_passphrase: str | None = None
    is_default: bool | None = False

    @model_validator(mode="after")
    def _validate_regions(self):
        if self.provider == "aws":
            validate_aws_region(self.region, field_name="region")
            validate_aws_region(self.aws_sso_region, field_name="aws_sso_region")
        if self.provider == "ibm":
            validate_ibm_region(self.region, field_name="region")
            if not self.ibmcloud_api_key:
                raise ValueError("IBM Cloud API key is required for IBM credential templates")
        if self.provider == "azure":
            validate_azure_region(self.region, field_name="region")
        if self.provider == "gcp":
            validate_gcp_region(self.region, field_name="region")
        return self


class CredentialTemplateCreate(CredentialTemplateBase):
    pass


class CredentialTemplateUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    provider: str | None = None
    aws_auth_method: str | None = None
    aws_profile: str | None = None
    region: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_session_token: str | None = None
    aws_sso_enabled: bool | None = None
    aws_sso_start_url: str | None = None
    aws_sso_region: str | None = None
    aws_sso_account_id: str | None = None
    aws_sso_role_name: str | None = None
    gcp_credentials: str | None = None
    gcp_project_id: str | None = None
    azure_auth_method: str | None = None
    azure_subscription_id: str | None = None
    azure_tenant_id: str | None = None
    azure_client_id: str | None = None
    azure_client_secret: str | None = None
    azure_credentials: str | None = None
    ibmcloud_api_key: str | None = None
    ibmcloud_resource_group: str | None = None
    ibm_cos_instance_name: str | None = None
    tfc_api_token: str | None = None
    tfc_hostname: str | None = None
    ssh_host: str | None = None
    ssh_port: int | None = None
    ssh_username: str | None = None
    ssh_auth_type: str | None = None
    ssh_password: str | None = None
    ssh_private_key: str | None = None
    ssh_key_passphrase: str | None = None
    is_default: bool | None = None

    @model_validator(mode="after")
    def _validate_regions(self):
        validate_aws_region(self.aws_sso_region, field_name="aws_sso_region")
        if self.region and self.provider == "aws":
            validate_aws_region(self.region, field_name="region")
        if self.region and self.provider == "ibm":
            validate_ibm_region(self.region, field_name="region")
        if self.region and self.provider == "azure":
            validate_azure_region(self.region, field_name="region")
        if self.region and self.provider == "gcp":
            validate_gcp_region(self.region, field_name="region")
        return self


class CredentialTemplateResponse(BaseModel):
    id: int
    name: str
    description: str | None
    provider: str
    aws_auth_method: str | None
    aws_profile: str | None
    region: str | None
    aws_access_key_id: str | None
    has_aws_secret_access_key: bool
    has_aws_session_token: bool
    aws_sso_enabled: bool
    aws_sso_start_url: str | None
    aws_sso_region: str | None
    aws_sso_account_id: str | None
    aws_sso_role_name: str | None
    aws_sso_authenticated_at: datetime | None
    aws_sso_token_expiry: datetime | None
    aws_credentials_expiry: datetime | None
    gcp_project_id: str | None
    has_gcp_credentials: bool
    azure_auth_method: str | None = None
    azure_subscription_id: str | None = None
    azure_tenant_id: str | None = None
    azure_client_id: str | None = None
    has_azure_client_secret: bool = False
    has_azure_credentials: bool = False
    azure_sso_authenticated_at: datetime | None = None
    azure_sso_token_expiry: datetime | None = None
    has_ibmcloud_api_key: bool
    ibmcloud_resource_group: str | None = None
    ibm_cos_instance_name: str | None = None
    has_tfc_api_token: bool = False
    tfc_hostname: str | None = None
    ssh_host: str | None = None
    ssh_port: int | None = None
    ssh_username: str | None = None
    ssh_auth_type: str | None = None
    has_ssh_password: bool = False
    has_ssh_key: bool = False
    is_default: bool
    created_at: datetime
    updated_at: datetime
    projects_count: int
    # Passive cloud-API observation (RFC connectivity Phase 2).
    last_successful_call_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None

    class Config:
        from_attributes = True


class SSOPollRequest(BaseModel):
    device_code: str


# ============================================================================
# CRUD Endpoints
# ============================================================================

@router.get("", response_model=list[CredentialTemplateResponse], dependencies=[Depends(require_viewer)])
def list_credential_templates(provider: str | None = None, db: Session = Depends(get_db)):
    """List all credential templates."""
    return CredentialTemplateService(db).list_templates(provider=provider)


@router.get("/{template_id}", response_model=CredentialTemplateResponse, dependencies=[Depends(require_viewer)])
def get_credential_template(template_id: int, db: Session = Depends(get_db)):
    """Get a specific credential template by ID."""
    return CredentialTemplateService(db).get_template(template_id)


@router.post("", response_model=CredentialTemplateResponse, status_code=201, dependencies=[Depends(require_operator)])
@handle_route_errors("create credential template")
def create_credential_template(template_data: CredentialTemplateCreate, db: Session = Depends(get_db)):
    """Create a new credential template."""
    result = CredentialTemplateService(db).create_template(template_data)
    db.commit()
    return result


@router.put("/{template_id}", response_model=CredentialTemplateResponse, dependencies=[Depends(require_operator)])
@handle_route_errors("update credential template")
def update_credential_template(template_id: int, template_data: CredentialTemplateUpdate, db: Session = Depends(get_db)):
    """Update a credential template."""
    result = CredentialTemplateService(db).update_template(template_id, template_data)
    db.commit()
    return result


@router.delete("/{template_id}", status_code=204, dependencies=[Depends(require_operator)])
@handle_route_errors("delete credential template")
def delete_credential_template(template_id: int, db: Session = Depends(get_db)):
    """Delete a credential template."""
    CredentialTemplateService(db).delete_template(template_id)
    db.commit()
    return None


# ============================================================================
# Testing & SSO Endpoints
# ============================================================================

@router.post("/{template_id}/test", dependencies=[Depends(require_operator)])
def test_credential_template(template_id: int, db: Session = Depends(get_db)):
    """Test if credential template credentials are valid."""
    return CredentialTemplateService(db).test_template(template_id)


@router.post("/{template_id}/authenticate-sso", dependencies=[Depends(require_operator)])
@handle_route_errors("initiate SSO authentication")
def initiate_sso_authentication(template_id: int, db: Session = Depends(get_db)):
    """Initiate AWS SSO device code flow."""
    result = CredentialTemplateService(db).initiate_sso(template_id)
    db.commit()
    return result


@router.post("/{template_id}/poll-sso", dependencies=[Depends(require_operator)])
@handle_route_errors("poll SSO authentication")
def poll_sso_authentication(template_id: int, request: SSOPollRequest, db: Session = Depends(get_db)):
    """Poll for SSO authentication completion."""
    result = CredentialTemplateService(db).poll_sso(template_id, request.device_code)
    db.commit()
    # Service returns pending=True for 202 responses
    if not result.get("success") and result.get("pending"):
        return JSONResponse(status_code=202, content=result)
    return result


@router.post("/{template_id}/refresh-sso", dependencies=[Depends(require_operator)])
@handle_route_errors("refresh SSO credentials")
def refresh_sso_credentials(template_id: int, db: Session = Depends(get_db)):
    """Refresh AWS SSO credentials using stored refresh token."""
    result = CredentialTemplateService(db).refresh_sso(template_id)
    db.commit()
    return result


@router.get("/{template_id}/sso-status", dependencies=[Depends(require_viewer)])
def get_sso_status(template_id: int, db: Session = Depends(get_db)):
    """Get SSO authentication status."""
    return CredentialTemplateService(db).get_sso_status(template_id)
