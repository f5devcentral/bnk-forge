"""
F5 Credential Service — CRUD and connection testing for BIG-IP management credentials.

Encryption contract (mirrors SSHCredentialService):
  * Encrypt on write (create / update).
  * Decrypt only at the authentication moment (in the iControl client).
  * serialize() exposes has_password / has_token — never the raw secret.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from core.encryption import encrypt_value
from core.errors import BadRequestError, NotFoundError
from models.f5_credential import F5Credential

logger = logging.getLogger(__name__)


class F5CredentialService:
    """Service layer for F5 BIG-IP credential operations."""

    def __init__(self, db: Session):
        self.db = db

    # ================================================================
    # Helpers
    # ================================================================

    def _get(self, credential_id: int) -> F5Credential:
        cred = self.db.query(F5Credential).filter(F5Credential.id == credential_id).first()
        if not cred:
            raise NotFoundError("f5_credential", credential_id)
        return cred

    @staticmethod
    def serialize(cred: F5Credential) -> dict[str, Any]:
        """Build a response dict — never exposes the raw password or token."""
        return {
            "id": cred.id,
            "name": cred.name,
            "auth_type": cred.auth_type,
            "username": cred.username,
            "has_password": bool(cred.password_encrypted),
            "has_token": bool(cred.token_encrypted),
            "is_default": cred.is_default or False,
            "created_at": cred.created_at,
            "updated_at": cred.updated_at,
            "created_by": cred.created_by,
            "last_test_status": cred.last_test_status,
            "last_test_at": cred.last_test_at,
            "last_test_message": cred.last_test_message,
        }

    # ================================================================
    # CRUD
    # ================================================================

    def list_credentials(self) -> list[dict[str, Any]]:
        """List all F5 credentials."""
        creds = self.db.query(F5Credential).order_by(
            F5Credential.is_default.desc(), F5Credential.name
        ).all()
        return [self.serialize(c) for c in creds]

    def get_credential(self, credential_id: int) -> dict[str, Any]:
        """Get a specific F5 credential by ID."""
        return self.serialize(self._get(credential_id))

    def create_credential(self, data: Any) -> dict[str, Any]:
        """Create a new F5 credential."""
        existing = self.db.query(F5Credential).filter(F5Credential.name == data.name).first()
        if existing:
            raise BadRequestError(f"F5 credential '{data.name}' already exists")

        if getattr(data, "is_default", False):
            self.db.query(F5Credential).filter(
                F5Credential.is_default.is_(True)
            ).update({"is_default": False})

        cred = F5Credential(
            name=data.name,
            auth_type=getattr(data, "auth_type", "password"),
            username=getattr(data, "username", None),
            is_default=getattr(data, "is_default", False) or False,
            created_by=getattr(data, "created_by", None),
        )

        password = getattr(data, "password", None)
        token = getattr(data, "token", None)
        if password:
            cred.password_encrypted = encrypt_value(password)
        if token:
            cred.token_encrypted = encrypt_value(token)

        self.db.add(cred)
        self.db.flush()
        self.db.refresh(cred)
        return self.serialize(cred)

    def update_credential(self, credential_id: int, data: Any) -> dict[str, Any]:
        """Update an F5 credential."""
        cred = self._get(credential_id)

        new_name = getattr(data, "name", None)
        if new_name and new_name != cred.name:
            conflict = self.db.query(F5Credential).filter(F5Credential.name == new_name).first()
            if conflict:
                raise BadRequestError(f"F5 credential '{new_name}' already exists")
            cred.name = new_name

        is_default = getattr(data, "is_default", None)
        if is_default and not cred.is_default:
            self.db.query(F5Credential).filter(
                F5Credential.is_default.is_(True)
            ).update({"is_default": False})
            cred.is_default = True
        elif is_default is False:
            cred.is_default = False

        if getattr(data, "auth_type", None):
            cred.auth_type = data.auth_type
        if getattr(data, "username", None) is not None:
            cred.username = data.username

        password = getattr(data, "password", None)
        token = getattr(data, "token", None)
        if password:
            cred.password_encrypted = encrypt_value(password)
        if token:
            cred.token_encrypted = encrypt_value(token)

        cred.updated_at = datetime.now(UTC)
        self.db.flush()
        self.db.refresh(cred)
        return self.serialize(cred)

    def delete_credential(self, credential_id: int) -> None:
        """Delete an F5 credential."""
        cred = self._get(credential_id)
        self.db.delete(cred)

    # ================================================================
    # Connection Testing
    # ================================================================

    def test_credential(self, credential_id: int, mgmt_host: str, mgmt_port: int = 443,
                        verify_https_cert: bool = True) -> dict[str, Any]:
        """Test BIG-IP reachability using this credential.

        Persists the outcome (last_test_status / _at / _message) so the UI
        can render a colour-coded status dot without re-testing.
        """
        from services.f5.icontrol_client import IControlClient

        cred = self._get(credential_id)
        try:
            client = IControlClient(
                host=mgmt_host,
                port=mgmt_port,
                credential=cred,
                verify_https_cert=verify_https_cert,
            )
            result = client.test_reachability()
            ok = result.get("success", False)
            message = result.get("message") or result.get("error") or None
        except Exception as exc:
            ok = False
            message = str(exc)

        cred.last_test_status = "ok" if ok else "failed"
        cred.last_test_at = datetime.now(UTC)
        cred.last_test_message = message
        self.db.flush()

        return {
            "success": ok,
            "message": message,
            "last_test_status": cred.last_test_status,
            "last_test_at": cred.last_test_at.isoformat() if cred.last_test_at else None,
            "last_test_message": cred.last_test_message,
        }
