"""
API routes for SSH credentials — first-class on-prem/bastion access management.

SSH is an access method, not a cloud provider. These credentials can be
attached to any K8s cluster (regardless of cloud_provider) or project.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from routes.auth import require_operator, require_viewer
from services.ssh_credential_service import SSHCredentialService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ssh-credentials", tags=["ssh-credentials"])


# ============================================================================
# Pydantic Schemas
# ============================================================================

class SSHCredentialCreate(BaseModel):
    name: str
    description: str | None = None
    host: str
    port: int | None = 22
    username: str
    auth_type: str | None = "key"  # 'key' or 'password'
    password: str | None = None
    private_key: str | None = None
    key_passphrase: str | None = None
    is_default: bool | None = False


class SSHCredentialSetup(BaseModel):
    """Auto-setup: provide host/user/password, we generate a key and install it."""
    name: str
    description: str | None = None
    host: str
    port: int | None = 22
    username: str
    password: str  # one-time password for bootstrapping
    is_default: bool | None = False


class SSHCredentialUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None
    auth_type: str | None = None
    password: str | None = None
    private_key: str | None = None
    key_passphrase: str | None = None
    is_default: bool | None = None


class SSHCredentialResponse(BaseModel):
    id: int
    name: str
    description: str | None
    host: str
    port: int
    username: str
    auth_type: str
    has_password: bool
    has_private_key: bool
    is_default: bool
    created_at: datetime
    updated_at: datetime
    created_by: str | None = None
    clusters_count: int
    projects_count: int
    # Last connectivity-test result — populated by the SSH Credentials
    # page's auto-test on mount and after Create / Edit / Re-test.
    last_test_status: str | None = None
    last_test_at: datetime | None = None
    last_test_message: str | None = None

    class Config:
        from_attributes = True


# ============================================================================
# Auto-Setup Endpoint (must be before /{credential_id} routes)
# ============================================================================

@router.post("/setup", response_model=SSHCredentialResponse, status_code=201, dependencies=[Depends(require_operator)])
@handle_route_errors("setup SSH credential")
def setup_ssh_credential(data: SSHCredentialSetup, db: Session = Depends(get_db)):
    """Auto-setup SSH credential: generate key, install via password, store key only.

    1. Connect to host with provided password
    2. Generate Ed25519 keypair
    3. Install public key in ~/.ssh/authorized_keys
    4. Verify key auth works
    5. Save credential with key (password is NOT stored)

    If password auth is rejected, returns a clear error telling the user
    to provide their own SSH key instead.
    """
    result = SSHCredentialService(db).setup_credential(data)
    db.commit()
    return result


# ============================================================================
# CRUD Endpoints
# ============================================================================

@router.get("", response_model=list[SSHCredentialResponse], dependencies=[Depends(require_viewer)])
def list_ssh_credentials(db: Session = Depends(get_db)):
    """List all SSH credentials."""
    return SSHCredentialService(db).list_credentials()


@router.get("/{credential_id}", response_model=SSHCredentialResponse, dependencies=[Depends(require_viewer)])
def get_ssh_credential(credential_id: int, db: Session = Depends(get_db)):
    """Get a specific SSH credential by ID."""
    return SSHCredentialService(db).get_credential(credential_id)


@router.post("", response_model=SSHCredentialResponse, status_code=201, dependencies=[Depends(require_operator)])
@handle_route_errors("create SSH credential")
def create_ssh_credential(data: SSHCredentialCreate, db: Session = Depends(get_db)):
    """Create a new SSH credential."""
    result = SSHCredentialService(db).create_credential(data)
    db.commit()
    return result


@router.put("/{credential_id}", response_model=SSHCredentialResponse, dependencies=[Depends(require_operator)])
@handle_route_errors("update SSH credential")
def update_ssh_credential(credential_id: int, data: SSHCredentialUpdate, db: Session = Depends(get_db)):
    """Update an SSH credential."""
    result = SSHCredentialService(db).update_credential(credential_id, data)
    db.commit()
    return result


@router.delete("/{credential_id}", status_code=204, dependencies=[Depends(require_operator)])
@handle_route_errors("delete SSH credential")
def delete_ssh_credential(credential_id: int, db: Session = Depends(get_db)):
    """Delete an SSH credential."""
    SSHCredentialService(db).delete_credential(credential_id)
    db.commit()
    return None


# ============================================================================
# Testing Endpoint
# ============================================================================

@router.post("/{credential_id}/test", dependencies=[Depends(require_operator)])
def test_ssh_credential(credential_id: int, db: Session = Depends(get_db)):
    """Test SSH connectivity using this credential.

    Persists the outcome on the credential row so subsequent fetches
    return it without re-testing.
    """
    return SSHCredentialService(db).test_credential(credential_id)


@router.post("/test-all", dependencies=[Depends(require_operator)])
def test_all_ssh_credentials(db: Session = Depends(get_db)):
    """Test every SSH credential in parallel. Persists results per row."""
    return SSHCredentialService(db).test_all_credentials()


# ============================================================================
# K8s Probe — retrieve kubeconfig from remote host via SSH
# ============================================================================

@router.post("/{credential_id}/probe-kubeconfig", dependencies=[Depends(require_operator)])
@handle_route_errors("probe kubeconfig via SSH")
def probe_kubeconfig(credential_id: int, db: Session = Depends(get_db)):
    """SSH into the host and retrieve kubeconfig + available contexts.

    Tries `kubectl config view --raw`, `~/.kube/config`, and `/etc/kubernetes/admin.conf`.
    Returns base64-encoded kubeconfig and a list of discovered K8s contexts.
    """
    return SSHCredentialService(db).probe_kubeconfig(credential_id)
