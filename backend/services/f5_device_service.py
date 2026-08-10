"""
F5 Device Service — CRUD and read-only probe for classic BIG-IP devices.

Discovery contract (D-017 — no silent empty success):
  * register_and_probe() / probe_device() always set last_discovery_status.
  * On probe failure, last_discovery_error is populated with a non-empty message.
  * device_info is set to {} (empty dict) on failure — never left stale from
    a previous successful probe when the current probe clearly failed.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from core.errors import BadRequestError, NotFoundError
from models.f5_credential import F5Credential
from models.f5_device import F5BIGIPDevice
from services.f5.fleet_integration import try_register_fleet_member

logger = logging.getLogger(__name__)


class F5DeviceService:
    """Service layer for F5 BIG-IP device operations."""

    def __init__(self, db: Session):
        self.db = db

    # ================================================================
    # Helpers
    # ================================================================

    def _get_device(self, device_id: int) -> F5BIGIPDevice:
        device = self.db.query(F5BIGIPDevice).filter(F5BIGIPDevice.id == device_id).first()
        if not device:
            raise NotFoundError("f5_bigip_device", device_id)
        return device

    @staticmethod
    def serialize(device: F5BIGIPDevice) -> dict[str, Any]:
        """Build a response dict for a BIG-IP device."""
        return {
            "id": device.id,
            "project_id": device.project_id,
            "name": device.name,
            "description": device.description,
            "mgmt_host": device.mgmt_host,
            "mgmt_port": device.mgmt_port,
            "mgmt_credential_id": device.mgmt_credential_id,
            "verify_https_cert": device.verify_https_cert,
            "device_info": device.device_info,
            "last_discovery_at": device.last_discovery_at,
            "last_discovery_status": device.last_discovery_status,
            "last_discovery_error": device.last_discovery_error,
            "created_at": device.created_at,
            "updated_at": device.updated_at,
            "created_by": device.created_by,
        }

    # ================================================================
    # CRUD
    # ================================================================

    def list_devices(self, project_id: int) -> list[dict[str, Any]]:
        """List all BIG-IP devices for a project."""
        devices = (
            self.db.query(F5BIGIPDevice)
            .filter(F5BIGIPDevice.project_id == project_id)
            .order_by(F5BIGIPDevice.name)
            .all()
        )
        return [self.serialize(d) for d in devices]

    def get_device(self, device_id: int) -> dict[str, Any]:
        """Get a specific BIG-IP device by ID."""
        return self.serialize(self._get_device(device_id))

    def create_device(self, project_id: int, data: Any) -> dict[str, Any]:
        """Create a new BIG-IP device record (does NOT probe — use register_and_probe)."""
        existing = (
            self.db.query(F5BIGIPDevice)
            .filter(
                F5BIGIPDevice.project_id == project_id,
                F5BIGIPDevice.name == data.name,
            )
            .first()
        )
        if existing:
            raise BadRequestError(f"BIG-IP device '{data.name}' already exists in this project")

        cred_id = getattr(data, "mgmt_credential_id", None)
        if cred_id:
            cred = self.db.query(F5Credential).filter(F5Credential.id == cred_id).first()
            if not cred:
                raise BadRequestError(f"F5 credential {cred_id} not found")

        device = F5BIGIPDevice(
            project_id=project_id,
            name=data.name,
            description=getattr(data, "description", None),
            mgmt_host=data.mgmt_host,
            mgmt_port=getattr(data, "mgmt_port", 443) or 443,
            mgmt_credential_id=cred_id,
            verify_https_cert=getattr(data, "verify_https_cert", True),
            created_by=getattr(data, "created_by", None),
        )

        self.db.add(device)
        self.db.flush()
        self.db.refresh(device)
        return self.serialize(device)

    def update_device(self, device_id: int, data: Any) -> dict[str, Any]:
        """Update a BIG-IP device record."""
        device = self._get_device(device_id)

        for field in ("name", "description", "mgmt_host", "mgmt_port",
                      "mgmt_credential_id", "verify_https_cert"):
            val = getattr(data, field, None)
            if val is not None:
                setattr(device, field, val)

        device.updated_at = datetime.now(UTC)
        self.db.flush()
        self.db.refresh(device)
        return self.serialize(device)

    def delete_device(self, device_id: int) -> None:
        """Delete a BIG-IP device record."""
        device = self._get_device(device_id)
        self.db.delete(device)

    # ================================================================
    # Probe
    # ================================================================

    def probe_device(self, device_id: int) -> dict[str, Any]:
        """Probe a registered BIG-IP device and cache its facts.

        D-017 contract: failure is NEVER a silent empty success.
          * On success: last_discovery_status="completed", device_info populated.
          * On failure: last_discovery_status="failed", last_discovery_error non-empty.
        """
        from services.f5.icontrol_client import IControlClient

        device = self._get_device(device_id)
        device.last_discovery_at = datetime.now(UTC)
        device.last_discovery_status = "probing"
        device.last_discovery_error = None
        self.db.flush()

        if not device.mgmt_credential_id:
            device.last_discovery_status = "failed"
            device.last_discovery_error = "No credential attached to device"
            self.db.flush()
            return self.serialize(device)

        cred = self.db.query(F5Credential).filter(
            F5Credential.id == device.mgmt_credential_id
        ).first()
        if not cred:
            device.last_discovery_status = "failed"
            device.last_discovery_error = f"Credential {device.mgmt_credential_id} not found"
            self.db.flush()
            return self.serialize(device)

        try:
            client = IControlClient(
                host=device.mgmt_host,
                port=device.mgmt_port,
                credential=cred,
                verify_https_cert=device.verify_https_cert,
            )
            device_info = client.get_device_info()
            device.device_info = device_info
            device.last_discovery_status = "completed"
            device.last_discovery_error = None
        except Exception as exc:
            logger.warning(
                "BIG-IP probe failed for device %s (%s): %s",
                device_id, device.mgmt_host, exc,
            )
            device.device_info = {}  # clear stale — never silently show old data
            device.last_discovery_status = "failed"
            device.last_discovery_error = str(exc) or "Probe failed with unknown error"

        device.last_discovery_at = datetime.now(UTC)
        self.db.flush()
        self.db.refresh(device)

        # Fleet member registration (soft-dep on D-022 FleetMember)
        if device.last_discovery_status == "completed":
            try_register_fleet_member(self.db, device)

        return self.serialize(device)

    def register_and_probe(self, project_id: int, data: Any) -> dict[str, Any]:
        """Create a device record and immediately probe it.

        Convenience wrapper used by the POST /f5-devices endpoint.
        Returns probe results regardless of outcome.
        """
        result = self.create_device(project_id, data)
        device_id = result["id"]
        return self.probe_device(device_id)

    # ================================================================
    # AS3 preview (read-only import — D-023 P4d)
    # ================================================================

    def preview_as3_as_bnk(
        self,
        device_id: int,
        *,
        gateway_class_name: str = "f5-bnk",
        gateway_name: str | None = None,
        target_namespace: str | None = None,
    ) -> dict[str, Any]:
        """Fetch the device's full AS3 declaration and preview it as BNK Gateway API.

        Read-only: uses IControlClient (not IControlWriteClient). The full
        declaration body is never logged (D-017 secret-safe invariant).

        Returns a dict mirroring ProxyTranslateResponse fields:
          device_id, cluster_id (None), proxy_type, source_kind,
          gatewayclass_yaml, gateway_yaml, httproute_yaml, combined_yaml,
          unmapped, source.
        """
        from services.f5.icontrol_client import IControlClient
        from services.proxy_translate_cis_service import translate_cis_to_bnk

        device = self._get_device(device_id)

        if not device.mgmt_credential_id:
            raise BadRequestError("Device has no credential attached; cannot preview AS3.")

        cred = self.db.query(F5Credential).filter(
            F5Credential.id == device.mgmt_credential_id
        ).first()
        if not cred:
            raise BadRequestError(
                f"Credential {device.mgmt_credential_id} not found; cannot preview AS3."
            )

        iclient = IControlClient(
            host=device.mgmt_host,
            port=device.mgmt_port,
            credential=cred,
            verify_https_cert=device.verify_https_cert,
        )
        declaration = iclient.get_as3_declaration_full()

        if not declaration:
            return {
                "device_id": device_id,
                "cluster_id": None,
                "proxy_type": "cis-bigip",
                "source_kind": "AS3Device",
                "gatewayclass_yaml": "",
                "gateway_yaml": "",
                "httproute_yaml": "",
                "combined_yaml": "",
                "unmapped": [{
                    "source_kind": "AS3Device",
                    "source_name": device.name,
                    "source_namespace": "(device)",
                    "construct_type": "as3_parse_error",
                    "detail": "empty declaration",
                    "reason": "Device returned an empty AS3 declaration. "
                              "AS3 may not be installed or no tenants are configured.",
                }],
                "source": {
                    "proxy_type": "cis-bigip",
                    "source_kind": "AS3Device",
                    "device_id": device_id,
                    "device_name": device.name,
                },
            }

        # Add source metadata so the parser knows where each unmapped entry came from
        decl_with_meta = dict(declaration)
        decl_with_meta["_source_kind"] = "AS3Device"
        decl_with_meta["_source_name"] = device.name
        decl_with_meta["_source_namespace"] = "(device)"

        result = translate_cis_to_bnk(
            virtualservers=[],
            transportservers=[],
            as3_declarations=[decl_with_meta],
            gateway_class_name=gateway_class_name,
            gateway_name=gateway_name,
            target_namespace=target_namespace,
        )

        return {
            "device_id": device_id,
            "cluster_id": None,
            "proxy_type": "cis-bigip",
            "source_kind": "AS3Device",
            "gatewayclass_yaml": result.gatewayclass_yaml,
            "gateway_yaml": result.gateway_yaml,
            "httproute_yaml": result.httproute_yaml,
            "combined_yaml": result.combined_yaml,
            "unmapped": [
                {
                    "source_kind": u.source_kind,
                    "source_name": u.source_name,
                    "source_namespace": u.source_namespace,
                    "construct_type": u.construct,
                    "detail": u.detail,
                    "reason": u.reason,
                }
                for u in result.unmapped
            ],
            "source": {
                **result.source,
                "device_id": device_id,
                "device_name": device.name,
            },
        }
