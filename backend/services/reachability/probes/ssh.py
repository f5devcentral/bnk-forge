"""SSHProbe — reachability probe for stored SSH credentials.

Wraps the existing ``SSHTunnelManager.test_ssh_connection`` (paramiko transport
open + banner read) rather than reimplementing it. The wrapper's job is just
to translate that result into ``ProbeResult`` and classify failures into
``ErrorCategory`` so the breaker treats auth failures correctly (auth ≠
network → does not trip the breaker; resilience4j + botocore precedent).

target_id = SSHCredential.id. list_targets returns every SSHCredential row —
small set (single-digit hosts in practice), and the probe is cheap.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from models.ssh_credential import SSHCredential
from services.reachability.probe import (
    ErrorCategory,
    Probe,
    ProbeResult,
    ReachabilityState,
)


class SSHProbe(Probe):
    target_type = "ssh"
    probe_interval_seconds = 60  # SSH probes are heavier than HTTPS — slower cadence
    slow_threshold_ms = 8000
    breaker_failure_threshold = 5
    breaker_sleep_window_seconds = 60

    def __init__(self, db_session_factory: Any) -> None:
        self._session_factory = db_session_factory

    def list_targets(self, db: Session) -> list[int]:
        rows = db.query(SSHCredential.id).all()
        return [r[0] for r in rows]

    async def probe(self, target_id: int) -> ProbeResult:
        db = self._session_factory()
        try:
            cred = (
                db.query(SSHCredential).filter(SSHCredential.id == target_id).first()
            )
            if cred is None:
                return ProbeResult(
                    state=ReachabilityState.UNKNOWN,
                    checked_at=datetime.now(UTC),
                    error_context={
                        "target_name": f"ssh:{target_id}",
                        "suggested_action": "SSH credential row was deleted; will stop probing on next cycle.",
                    },
                )
            return await asyncio.to_thread(_probe_blocking, cred)
        finally:
            try:
                db.close()
            except Exception:
                pass


def _probe_blocking(cred: SSHCredential) -> ProbeResult:
    """Run the (synchronous) SSHTunnelManager test off the event loop."""
    import time as _t

    from services.ssh_tunnel_manager import get_tunnel_manager

    start = _t.monotonic()
    result = get_tunnel_manager().test_ssh_connection(
        ssh_host=cred.host,
        ssh_port=cred.port or 22,
        ssh_username=cred.username,
        ssh_auth_type=cred.auth_type or "key",
        ssh_password_encrypted=cred.password_encrypted,
        ssh_key_encrypted=cred.private_key_encrypted,
        ssh_key_passphrase_encrypted=cred.key_passphrase_encrypted,
    )
    latency_ms = (_t.monotonic() - start) * 1000
    now = datetime.now(UTC)

    if result.get("success"):
        return ProbeResult(
            state=ReachabilityState.REACHABLE,
            checked_at=now,
            latency_ms=latency_ms,
            error_context={"target_name": cred.name},
        )

    # Classify the failure. test_ssh_connection collapses paramiko's exception
    # types into a stringified message — we re-derive the category here so the
    # breaker treats auth failures correctly (resilience4j precedent).
    message = (result.get("message") or "").lower()
    category = _categorize_message(message)
    suggested = _suggested_action(cred, message, category)

    return ProbeResult(
        state=ReachabilityState.UNREACHABLE,
        checked_at=now,
        latency_ms=latency_ms,
        error_category=category,
        error_context={
            "target_name": cred.name,
            "suggested_action": suggested,
        },
    )


def _categorize_message(message: str) -> ErrorCategory:
    if "authentication failed" in message or "no ssh credentials" in message:
        return ErrorCategory.AUTH
    if "timed out" in message:
        return ErrorCategory.TIMEOUT
    if "cannot resolve" in message or "refused" in message:
        return ErrorCategory.NETWORK
    return ErrorCategory.TARGET


def _suggested_action(cred: SSHCredential, message: str, category: ErrorCategory) -> str:
    host = f"{cred.host}:{cred.port or 22}"
    if category is ErrorCategory.AUTH:
        return (
            f"SSH authentication to {cred.username}@{host} failed. "
            f"Re-check the stored key/password or the username on this credential."
        )
    if category is ErrorCategory.TIMEOUT:
        return (
            f"SSH connect to {host} timed out. "
            f"Check VPN connection or whether the jumphost is online."
        )
    if category is ErrorCategory.NETWORK:
        return (
            f"Cannot reach {host}. "
            f"Check VPN connection or DNS resolution for the jumphost."
        )
    return f"SSH probe failed: {message or 'unknown error'}"
