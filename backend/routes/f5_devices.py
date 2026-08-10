"""
API routes for F5 BIG-IP devices — CRUD + read-only probe + credential management.

All routes are read-only toward the actual BIG-IP device (D-023 Phase 1).
The probe path exercises iControl REST GET endpoints only.
"""

import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from models import User
from routes.auth import require_operator, require_project_owner, require_viewer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Request / Response schemas (inline — thin, no separate schema file needed yet)
# ---------------------------------------------------------------------------


class F5DeviceCreate(BaseModel):
    name: str
    description: str | None = None
    mgmt_host: str
    mgmt_port: int = 443
    mgmt_credential_id: int | None = None
    verify_https_cert: bool = True


class F5DeviceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    mgmt_host: str | None = None
    mgmt_port: int | None = None
    mgmt_credential_id: int | None = None
    verify_https_cert: bool | None = None


class F5DeviceResponse(BaseModel):
    id: int
    project_id: int
    name: str
    description: str | None
    mgmt_host: str
    mgmt_port: int
    mgmt_credential_id: int | None
    verify_https_cert: bool
    device_info: dict | None
    last_discovery_at: datetime | None
    last_discovery_status: str | None
    last_discovery_error: str | None
    created_at: datetime
    updated_at: datetime
    created_by: str | None


class F5DeviceListResponse(BaseModel):
    devices: list[F5DeviceResponse]


_SECRET_FIELD = {"writeOnly": True, "format": "password"}


class F5CredentialCreate(BaseModel):
    name: str
    auth_type: Literal["password", "token"] = "password"
    username: str | None = None
    password: str | None = Field(default=None, json_schema_extra=_SECRET_FIELD)
    token: str | None = Field(default=None, json_schema_extra=_SECRET_FIELD)
    is_default: bool = False


class F5CredentialUpdate(BaseModel):
    name: str | None = None
    auth_type: Literal["password", "token"] | None = None
    username: str | None = None
    password: str | None = Field(default=None, json_schema_extra=_SECRET_FIELD)
    token: str | None = Field(default=None, json_schema_extra=_SECRET_FIELD)
    is_default: bool | None = None


class F5CredentialResponse(BaseModel):
    """Credential response — never exposes raw password or token."""
    id: int
    name: str
    auth_type: str
    username: str | None
    has_password: bool
    has_token: bool
    is_default: bool
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    last_test_status: str | None
    last_test_at: datetime | None
    last_test_message: str | None


class F5CredentialListResponse(BaseModel):
    credentials: list[F5CredentialResponse]


class F5CredentialTestRequest(BaseModel):
    mgmt_host: str
    mgmt_port: int = 443
    verify_https_cert: bool = True


class F5CredentialTestResponse(BaseModel):
    success: bool
    message: str | None
    last_test_status: str | None
    last_test_at: str | None
    last_test_message: str | None


class DeviceAS3PreviewRequest(BaseModel):
    """Request body for POST /f5-devices/{device_id}/as3-preview (D-023 P4d)."""

    gateway_class_name: str = "f5-bnk"
    gateway_name: str | None = None
    target_namespace: str | None = None


class DeviceAS3UnmappedEntry(BaseModel):
    source_kind: str
    source_name: str
    source_namespace: str
    construct_type: str
    detail: str
    reason: str


class DeviceAS3PreviewResponse(BaseModel):
    """BNK preview of a classic BIG-IP device's AS3 declaration (D-023 P4d)."""

    device_id: int
    cluster_id: int | None = None
    proxy_type: str
    source_kind: str
    gatewayclass_yaml: str
    gateway_yaml: str
    httproute_yaml: str
    combined_yaml: str
    unmapped: list[DeviceAS3UnmappedEntry]
    source: dict


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

devices_router = APIRouter(
    prefix="/api/projects/{project_id}/f5-devices",
    tags=["f5-bigip"],
)

credentials_router = APIRouter(
    prefix="/api/f5-credentials",
    tags=["f5-bigip"],
)


# ---------------------------------------------------------------------------
# Device routes
# ---------------------------------------------------------------------------


@devices_router.get("", response_model=list[F5DeviceResponse], dependencies=[Depends(require_viewer)])
@handle_route_errors("list F5 BIG-IP devices")
def list_devices(
    project_id: int,
    db: Session = Depends(get_db),
) -> list[F5DeviceResponse]:
    """List all BIG-IP devices for a project."""
    from services.f5_device_service import F5DeviceService
    return F5DeviceService(db).list_devices(project_id)


@devices_router.post("", response_model=F5DeviceResponse)
@handle_route_errors("register and probe F5 BIG-IP device")
def register_and_probe(
    project_id: int,
    data: F5DeviceCreate,
    _user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
) -> F5DeviceResponse:
    """Register a BIG-IP device and immediately probe it (read-only)."""
    from services.f5_device_service import F5DeviceService
    result = F5DeviceService(db).register_and_probe(project_id, data)
    db.commit()
    return result


@devices_router.get("/{device_id}", response_model=F5DeviceResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("get F5 BIG-IP device")
def get_device(
    project_id: int,
    device_id: int,
    db: Session = Depends(get_db),
) -> F5DeviceResponse:
    """Get a specific BIG-IP device."""
    from services.f5_device_service import F5DeviceService
    return F5DeviceService(db).get_device(device_id)


@devices_router.put("/{device_id}", response_model=F5DeviceResponse)
@handle_route_errors("update F5 BIG-IP device")
def update_device(
    project_id: int,
    device_id: int,
    data: F5DeviceUpdate,
    _user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
) -> F5DeviceResponse:
    """Update BIG-IP device configuration."""
    from services.f5_device_service import F5DeviceService
    result = F5DeviceService(db).update_device(device_id, data)
    db.commit()
    return result


@devices_router.delete("/{device_id}", status_code=204)
@handle_route_errors("delete F5 BIG-IP device")
def delete_device(
    project_id: int,
    device_id: int,
    _user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
) -> None:
    """Delete a BIG-IP device."""
    from services.f5_device_service import F5DeviceService
    F5DeviceService(db).delete_device(device_id)
    db.commit()


@devices_router.post("/{device_id}/probe", response_model=F5DeviceResponse)
@handle_route_errors("probe F5 BIG-IP device")
def probe_device(
    project_id: int,
    device_id: int,
    _user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
) -> F5DeviceResponse:
    """Re-probe an existing BIG-IP device and refresh its cached facts."""
    from services.f5_device_service import F5DeviceService
    result = F5DeviceService(db).probe_device(device_id)
    db.commit()
    return result


@devices_router.post("/{device_id}/as3-preview", response_model=DeviceAS3PreviewResponse)
@handle_route_errors("preview AS3 declaration as BNK Gateway API")
def preview_device_as3(
    project_id: int,
    device_id: int,
    body: DeviceAS3PreviewRequest,
    _user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
) -> DeviceAS3PreviewResponse:
    """Preview a classic BIG-IP device's AS3 declaration as BNK Gateway API (D-023 P4d).

    Read-only: fetches the full AS3 declaration from the device and runs it
    through the shared AS3→BNK parser. The declaration body is never logged.

    Returns GatewayClass + Gateway + HTTPRoute YAML plus unmapped[] for all
    constructs that cannot be automatically mapped (D-017).
    """
    from services.f5_device_service import F5DeviceService
    result = F5DeviceService(db).preview_as3_as_bnk(
        device_id,
        gateway_class_name=body.gateway_class_name,
        gateway_name=body.gateway_name,
        target_namespace=body.target_namespace,
    )
    return DeviceAS3PreviewResponse(**result)


# ---------------------------------------------------------------------------
# Credential routes
# ---------------------------------------------------------------------------


@credentials_router.get("", response_model=list[F5CredentialResponse], dependencies=[Depends(require_viewer)])
@handle_route_errors("list F5 credentials")
def list_credentials(db: Session = Depends(get_db)) -> list[F5CredentialResponse]:
    """List all F5 credentials."""
    from services.f5_credential_service import F5CredentialService
    return F5CredentialService(db).list_credentials()


@credentials_router.post("", response_model=F5CredentialResponse)
@handle_route_errors("create F5 credential")
def create_credential(
    data: F5CredentialCreate,
    _user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> F5CredentialResponse:
    """Create a new F5 credential."""
    from services.f5_credential_service import F5CredentialService
    result = F5CredentialService(db).create_credential(data)
    db.commit()
    return result


@credentials_router.get("/{credential_id}", response_model=F5CredentialResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("get F5 credential")
def get_credential(credential_id: int, db: Session = Depends(get_db)) -> F5CredentialResponse:
    """Get a specific F5 credential."""
    from services.f5_credential_service import F5CredentialService
    return F5CredentialService(db).get_credential(credential_id)


@credentials_router.put("/{credential_id}", response_model=F5CredentialResponse)
@handle_route_errors("update F5 credential")
def update_credential(
    credential_id: int,
    data: F5CredentialUpdate,
    _user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> F5CredentialResponse:
    """Update an F5 credential."""
    from services.f5_credential_service import F5CredentialService
    result = F5CredentialService(db).update_credential(credential_id, data)
    db.commit()
    return result


@credentials_router.delete("/{credential_id}", status_code=204)
@handle_route_errors("delete F5 credential")
def delete_credential(
    credential_id: int,
    _user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> None:
    """Delete an F5 credential."""
    from services.f5_credential_service import F5CredentialService
    F5CredentialService(db).delete_credential(credential_id)
    db.commit()


@credentials_router.post("/{credential_id}/test", response_model=F5CredentialTestResponse)
@handle_route_errors("test F5 credential")
def test_credential(
    credential_id: int,
    data: F5CredentialTestRequest,
    _user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> F5CredentialTestResponse:
    """Test a BIG-IP credential by attempting to authenticate and fetching version info."""
    from services.f5_credential_service import F5CredentialService
    result = F5CredentialService(db).test_credential(
        credential_id,
        mgmt_host=data.mgmt_host,
        mgmt_port=data.mgmt_port,
        verify_https_cert=data.verify_https_cert,
    )
    db.commit()
    return result
