"""Idempotent bare-metal host registration shared across entry points.

Two flows want the same outcome — "make sure a BareMetalHost exists for
this (project, host_ip), creating it from the data I have if not":

  * Discovery → Register Host (per-row + bulk on the Discovery tab).
    Snapshot comes from the DiscoveredNode's resolved SSH credentials.

  * DPU tab → Register Host (per-DPU action on in-band rows). Snapshot
    comes from the DPU row's `host_ssh_*` fields (saved cred FK or
    inline username + password/key).

Both routes need:
  - dedup on (project_id, host_ip) — re-clicking is silent;
  - SSH credential resolution: use a saved row when there is one, else
    synthesise a `Discovery: <hostname>` SSHCredential so the
    BareMetalHost FK has a target;
  - host-name uniqueness on the (project_id, name) index.

This module is the single source of truth. Both services build a
snapshot dict in the same shape and delegate here.
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from sqlalchemy.orm import Session

from models.bare_metal import BareMetalHost
from models.ssh_credential import SSHCredential
from schemas.bare_metal import BareMetalHostCreate

logger = logging.getLogger(__name__)


class CredentialSnapshot(TypedDict, total=False):
    """Shape both DiscoveryService and DpuService produce.

    `credential_id` is the preferred path — when set, we reuse that
    SSHCredential row directly. The encrypted blobs are only consulted
    when synthesising a row from inline credentials.
    """

    credential_id: int | None
    username: str | None
    port: int | None
    auth_type: str | None
    password_encrypted: str | None
    private_key_encrypted: str | None
    key_passphrase_encrypted: str | None


def register_or_lookup(
    db: Session,
    *,
    project_id: int,
    host_ip: str,
    hostname: str | None,
    snapshot: CredentialSnapshot,
    name: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Find or create a BareMetalHost row.

    Returns ``{host_id, status, name, host_ip}`` where status is
    ``"exists"`` (already registered, no change) or ``"created"`` (new).

    `description` flows through to the new row when one is created;
    existing rows are left untouched.
    """
    from services.bare_metal.host_service import BareMetalHostService

    # Idempotency: same project + host_ip → return existing.
    existing = (
        db.query(BareMetalHost)
        .filter(
            BareMetalHost.project_id == project_id,
            BareMetalHost.host_ip == host_ip,
        )
        .first()
    )
    if existing is not None:
        return {
            "host_id": existing.id,
            "status": "exists",
            "name": existing.name,
            "host_ip": existing.host_ip,
        }

    cred_id = snapshot.get("credential_id")
    if cred_id is None:
        cred_id = _synthesise_ssh_credential(
            db, snapshot=snapshot, host_ip=host_ip,
            label_base=hostname or host_ip,
        )

    base_name = name or hostname or host_ip
    final_name = _unique_bare_metal_host_name(db, project_id, base_name)

    result = BareMetalHostService(db).create_host(
        project_id,
        BareMetalHostCreate(
            name=final_name,
            description=description,
            host_ip=host_ip,
            ssh_credential_id=cred_id,
            ssh_port=int(snapshot.get("port") or 22),
        ),
    )
    logger.info(
        "Registered bare-metal host %s (project=%s, ip=%s)",
        result.id, project_id, host_ip,
    )
    return {
        "host_id": result.id,
        "status": "created",
        "name": result.name,
        "host_ip": result.host_ip,
    }


def _unique_bare_metal_host_name(
    db: Session, project_id: int, base_name: str,
) -> str:
    """Return a name unique on (project_id, BareMetalHost.name).

    Most discovered hostnames are unique within a project, but defend
    against the rare case where the operator has a manually-created
    bare-metal host that happens to share the discovered hostname.
    """
    candidate = base_name
    existing = (
        db.query(BareMetalHost.id)
        .filter(
            BareMetalHost.project_id == project_id,
            BareMetalHost.name == candidate,
        )
        .first()
    )
    if existing is None:
        return candidate
    n = 2
    while True:
        candidate = f"{base_name} ({n})"
        if (
            db.query(BareMetalHost.id)
            .filter(
                BareMetalHost.project_id == project_id,
                BareMetalHost.name == candidate,
            )
            .first()
        ) is None:
            return candidate
        n += 1
        if n > 100:  # paranoia; should never loop this far
            return f"{base_name} ({project_id})"


def _synthesise_ssh_credential(
    db: Session,
    *,
    snapshot: CredentialSnapshot,
    host_ip: str,
    label_base: str,
) -> int | None:
    """Mint an SSHCredential from inline credentials.

    `BareMetalHost.ssh_credential_id` is a FK only — there's no inline
    credential storage on the table. When the discovery / DPU row used
    inline username + password (no saved row), we create a new
    `Discovery: <label>` SSHCredential so the bare-metal host has
    something to point at. We record `host=host_ip` on the cred row so
    UI labels render `ubuntu@10.0.0.10` instead of an empty hostname.

    Returns the new id, or None when there's no usable credential at
    all (no username, or no password + no key) — caller leaves
    ssh_credential_id null and the operator decides what to do.
    """
    username = snapshot.get("username")
    password_enc = snapshot.get("password_encrypted")
    key_enc = snapshot.get("private_key_encrypted")
    if not username or (not password_enc and not key_enc):
        return None

    cred = SSHCredential(
        name=_unique_ssh_credential_name(db, f"Discovery: {label_base}"),
        host=host_ip,
        port=int(snapshot.get("port") or 22),
        username=username,
        auth_type=(snapshot.get("auth_type") or "password"),
        password_encrypted=password_enc,
        private_key_encrypted=key_enc,
        key_passphrase_encrypted=snapshot.get("key_passphrase_encrypted"),
    )
    db.add(cred)
    db.flush()
    logger.info(
        "Synthesised SSH credential %s ('%s') for bare-metal host registration",
        cred.id, cred.name,
    )
    return cred.id


def _unique_ssh_credential_name(db: Session, base: str) -> str:
    candidate = base
    n = 2
    while (
        db.query(SSHCredential.id)
        .filter(SSHCredential.name == candidate)
        .first()
    ) is not None:
        candidate = f"{base} ({n})"
        n += 1
        if n > 100:
            break
    return candidate


__all__ = [
    "CredentialSnapshot",
    "register_or_lookup",
]
