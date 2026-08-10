"""
F5 Fleet Member registration helper (D-023 Phase 1 / D-022 soft dependency).

FleetMember is defined in D-022 Phase 1 (unmerged as of D-023 Phase 1).
This module is capability-guarded: if FleetMember is not importable, all
calls are no-ops. Device registration NEVER hard-fails due to this absence.

When D-022 merges, the import will succeed automatically — no code change needed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from models.f5_device import F5BIGIPDevice

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Capability probe — performed once at import time
# ---------------------------------------------------------------------------

try:
    from models import FleetMember  # type: ignore[attr-defined]
    _FLEET_AVAILABLE = True
    logger.debug("f5.fleet_integration: FleetMember available (D-022 landed)")
except ImportError:
    FleetMember = None  # type: ignore[assignment,misc]
    _FLEET_AVAILABLE = False
    logger.debug(
        "f5.fleet_integration: FleetMember not available (D-022 pending) — "
        "fleet registration will be skipped"
    )


def fleet_available() -> bool:
    """Return True if the FleetMember model is importable."""
    return _FLEET_AVAILABLE


def try_register_fleet_member(
    db: Session,
    device: F5BIGIPDevice,
    source: str = "device",
) -> None:
    """Register (or update) a FleetMember row for the given BIG-IP device.

    If D-022's FleetMember is not yet present, logs once and returns silently.
    Never raises — the caller (probe / scan) must not fail because of this.

    Args:
        db: SQLAlchemy session (caller owns the transaction).
        device: F5BIGIPDevice instance with device_info already populated.
        source: "device" (direct registration) | "cis" (detected via CIS scan).
    """
    if not _FLEET_AVAILABLE:
        logger.debug(
            "fleet_integration: skip FleetMember registration for device %s "
            "(D-022 not yet merged)",
            device.id,
        )
        return

    try:
        _do_register(db, device, source)
    except Exception as exc:
        # Intentionally broad — fleet registration is best-effort
        logger.warning(
            "fleet_integration: FleetMember registration failed for device %s: %s",
            device.id, exc,
        )


def _do_register(db: Session, device: F5BIGIPDevice, source: str) -> None:
    """Inner registration — only called when FleetMember is available."""
    device_info: dict[str, Any] = device.device_info or {}

    # Build labels from device facts
    labels: dict[str, str] = {
        "vendor": "f5",
        "kind": "bigip",
        "source": source,
    }
    if device_info.get("model"):
        labels["model"] = str(device_info["model"])
    if device_info.get("version"):
        labels["version"] = str(device_info["version"])
    if device_info.get("ha_state"):
        labels["ha_state"] = str(device_info["ha_state"])

    # Use the reconcile helper if D-022 exports one; otherwise upsert manually
    try:
        from models import reconcile_fleet_member  # type: ignore[attr-defined]
        reconcile_fleet_member(
            db=db,
            member_type="f5-bigip",
            external_id=f"f5-bigip-{device.id}",
            name=device.name,
            project_id=device.project_id,
            labels=labels,
        )
        logger.info(
            "fleet_integration: registered FleetMember for BIG-IP device %s", device.id
        )
        return
    except ImportError:
        pass

    # Fallback upsert if reconcile_fleet_member is not exported
    existing = (
        db.query(FleetMember)
        .filter_by(member_type="f5-bigip", external_id=f"f5-bigip-{device.id}")
        .first()
    )
    if existing:
        existing.name = device.name
        existing.labels = labels
    else:
        member = FleetMember(
            member_type="f5-bigip",
            external_id=f"f5-bigip-{device.id}",
            name=device.name,
            project_id=device.project_id,
            labels=labels,
        )
        db.add(member)
    db.flush()
    logger.info(
        "fleet_integration: upserted FleetMember for BIG-IP device %s", device.id
    )
