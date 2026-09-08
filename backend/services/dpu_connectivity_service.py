"""DPU↔DPU connectivity matrix.

Pairwise ICMP sweep across the project's DPUs sourced from the named
VLAN data-plane interfaces (e.g. ``external0``, ``internal100``,
``<name><vlan_id>`` for extras) so the matrix matches the DPU project's
declared VLAN interface table.

For in-band DPUs we can't SSH to ``dpu_os_ip = 192.168.100.2`` directly
because that's the rshim tmfifo loopback. Same trick the existing
``DpuOsProbeService`` uses: open SSH to the host, ``direct-tcpip`` to
the DPU OS over tmfifo, then SSH as ubuntu through that channel. From
inside the DPU OS we then run ``ping -I <iface> <dst>`` to source from
the right VLAN interface.

BMC-mode DPUs aren't covered yet — they need a different reach path.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import paramiko
from sqlalchemy.orm import Session

from core.encryption import decrypt_value_or_none
from core.errors import BadRequestError
from models.dpu import Dpu, ProjectDpuSettings
from services.bf_conf_renderer import derive_tmfifo_dpu_host
from services.discovery_service import (
    PING_COUNT,
    PING_TIMEOUT_SEC,
    _parse_ping_output,
    _ssh_exec,
)
from services.rshim_service import open_inband_host_ssh

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────


def _strip_cidr(addr: str | None) -> str | None:
    """`192.168.10.5/24` → `192.168.10.5`. Pass-through when no slash."""
    if not addr:
        return None
    return addr.split("/", 1)[0].strip() or None


def _vlan_iface_name(name: str, vlan_id: int | None) -> str | None:
    """`(external, 0)` → `external0`. None vlan_id ⇒ no iface (skipped).

    Matches the bf.conf netplan template's naming convention which is
    what shows up in `ip -br addr` on a flashed DPU OS.
    """
    if vlan_id is None:
        return None
    return f"{name}{vlan_id}"


def _collect_endpoints(
    dpus: list[Dpu], settings: ProjectDpuSettings,
) -> list[dict[str, Any]]:
    """Build one endpoint per (DPU, configured VLAN interface) pair.

    Interface names match the DPU OS netif naming (``external<vlan_id>``,
    ``internal<vlan_id>``, ``<name><vlan_id>`` for extras) so we can
    actually pin ``ping -I`` to them. DPUs that haven't been probed
    successfully (no dpu_os_reachable=True) are skipped — we can't open
    a tunnel to them anyway.
    """
    endpoints: list[dict[str, Any]] = []

    extras: list[dict[str, Any]] = list(settings.extra_vlan_interfaces or [])

    for dpu in dpus:
        if dpu.dpu_os_reachable is not True:
            continue
        host = dpu.host_hostname or dpu.name or f"dpu-{dpu.id}"

        # External VLAN.
        ext_iface = _vlan_iface_name("external", settings.external_vlan_id)
        ext_v4 = _strip_cidr(dpu.external_vlan_ipv4)
        if ext_iface and ext_v4:
            endpoints.append({
                "node_id": int(dpu.id),
                "node_ip": str(dpu.dpu_os_ip or ""),
                "hostname": host,
                "interface": ext_iface,
                "addresses": [{"family": "inet", "address": ext_v4}],
            })

        # Internal VLAN.
        int_iface = _vlan_iface_name("internal", settings.internal_vlan_id)
        int_v4 = _strip_cidr(dpu.internal_vlan_ipv4)
        if int_iface and int_v4:
            endpoints.append({
                "node_id": int(dpu.id),
                "node_ip": str(dpu.dpu_os_ip or ""),
                "hostname": host,
                "interface": int_iface,
                "addresses": [{"family": "inet", "address": int_v4}],
            })

        # Extra VLAN interfaces — per-DPU addresses live in the DPU's
        # `extra_vlan_interfaces` list, keyed by name.
        per_dpu_extras = {
            e.get("name"): e for e in (dpu.extra_vlan_interfaces or [])
            if isinstance(e, dict) and e.get("name")
        }
        for proj_extra in extras:
            name = proj_extra.get("name")
            vlan_id = proj_extra.get("vlan_id")
            if not isinstance(name, str) or not isinstance(vlan_id, int):
                continue
            iface = _vlan_iface_name(name, vlan_id)
            if not iface:
                continue
            assigned = per_dpu_extras.get(name) or {}
            v4 = _strip_cidr(assigned.get("ipv4"))
            if not v4:
                continue
            endpoints.append({
                "node_id": int(dpu.id),
                "node_ip": str(dpu.dpu_os_ip or ""),
                "hostname": host,
                "interface": iface,
                "addresses": [{"family": "inet", "address": v4}],
            })

    return endpoints


def _resolve_os_credentials(settings: ProjectDpuSettings) -> tuple[str, str]:
    """Decrypt project default DPU OS SSH credentials."""
    if not settings.default_os_username or not settings.default_os_password_encrypted:
        raise BadRequestError(
            "Project default DPU OS credentials are required to run the "
            "DPU connectivity matrix.",
        )
    pw = decrypt_value_or_none(settings.default_os_password_encrypted)
    if not pw:
        raise BadRequestError(
            "Project default DPU OS password could not be decrypted.",
        )
    return settings.default_os_username, pw


def _ensure_settings_row(db: Session, project_id: int) -> ProjectDpuSettings:
    """Get-or-create the per-project settings row."""
    settings = (
        db.query(ProjectDpuSettings)
        .filter(ProjectDpuSettings.project_id == project_id)
        .first()
    )
    if settings is not None:
        return settings
    settings = ProjectDpuSettings(project_id=project_id)
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


def _run_dpu_pings(
    *,
    db: Session,
    src_dpu: Dpu,
    src_endpoints: list[dict[str, Any]],
    target_endpoints: list[dict[str, Any]],
    os_user: str,
    os_pw: str,
) -> list[dict[str, Any]]:
    """Open one SSH session into ``src_dpu``'s OS (via host tmfifo tunnel)
    and run a ``ping -I <iface> <dst>`` for every (src_iface × dst).

    Returns a list of result rows in the same shape the discovery
    matrix uses, so ``ConnectivityMatrix.tsx`` renders DPU output
    without changes.
    """
    if src_dpu.access_mode != "in-band":
        # BMC-mode tunnel path differs and isn't wired up yet — skip
        # gracefully rather than fail the whole matrix.
        logger.info(
            "DPU connectivity: skipping non-in-band DPU id=%s (access_mode=%s)",
            src_dpu.id, src_dpu.access_mode,
        )
        return []

    os_ip = derive_tmfifo_dpu_host(getattr(src_dpu, "rshim_device", None), dpu=src_dpu)
    results: list[dict[str, Any]] = []

    try:
        host_client = open_inband_host_ssh(db, src_dpu)
    except Exception as exc:
        logger.warning(
            "DPU connectivity: host SSH to %s for DPU id=%s failed: %s",
            src_dpu.host_node_ip, src_dpu.id, exc,
        )
        return []

    try:
        transport = host_client.get_transport()
        if transport is None:
            return []
        try:
            channel = transport.open_channel(
                "direct-tcpip", (os_ip, 22), ("127.0.0.1", 0), timeout=15,
            )
        except Exception as exc:
            logger.warning(
                "DPU connectivity: direct-tcpip to %s:22 via host %s failed: %s",
                os_ip, src_dpu.host_node_ip, exc,
            )
            return []

        dpu_client = paramiko.SSHClient()
        dpu_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            dpu_client.connect(
                os_ip, username=os_user, password=os_pw, sock=channel,
                look_for_keys=False, allow_agent=False, timeout=15,
            )
        except Exception as exc:
            logger.warning(
                "DPU connectivity: DPU OS SSH (%s, user=%s) failed: %s",
                os_ip, os_user, exc,
            )
            try:
                channel.close()
            except Exception:
                pass
            return []

        try:
            for src in src_endpoints:
                for dst in target_endpoints:
                    for addr in dst["addresses"]:
                        family = addr["family"]
                        ping_bin = "ping" if family == "inet" else "ping -6"
                        cmd = (
                            f"{ping_bin} -c {PING_COUNT} -W {PING_TIMEOUT_SEC} "
                            f"-I {src['interface']} {addr['address']} 2>&1"
                        )
                        rc, out = _ssh_exec(dpu_client, cmd)
                        parsed = _parse_ping_output(out, rc)
                        results.append({
                            "src_node_id": int(src_dpu.id),
                            "src_iface": src["interface"],
                            "dst_node_id": dst["node_id"],
                            "dst_iface": dst["interface"],
                            "family": family,
                            "dst_address": addr["address"],
                            **parsed,
                        })
        finally:
            try:
                dpu_client.close()
            except Exception:
                pass
    finally:
        try:
            host_client.close()
        except Exception:
            pass

    return results


# ── Service ───────────────────────────────────────────────────────────────


class DpuConnectivityService:
    """Project-scoped DPU↔DPU pairwise connectivity check."""

    def __init__(self, db: Session):
        self.db = db

    def mark_running(self, project_id: int) -> None:
        """Flip status to in_progress so the UI shows a spinner."""
        settings = _ensure_settings_row(self.db, project_id)
        settings.dpu_connectivity_status = "in_progress"
        self.db.commit()

    def get_matrix(self, project_id: int) -> dict[str, Any]:
        """Return the persisted matrix + status for the GET endpoint."""
        settings = (
            self.db.query(ProjectDpuSettings)
            .filter(ProjectDpuSettings.project_id == project_id)
            .first()
        )
        if settings is None:
            return {
                "status": None,
                "run_at": None,
                "matrix": None,
            }
        return {
            "status": settings.dpu_connectivity_status,
            "run_at": settings.dpu_connectivity_run_at,
            "matrix": settings.dpu_connectivity_matrix,
        }

    def run(self, project_id: int) -> dict[str, Any]:
        """Run the matrix synchronously and persist it.

        Called from the Celery task wrapper; not meant to be invoked
        from a request thread directly because each cross-DPU ping is a
        full SSH session.
        """
        settings = _ensure_settings_row(self.db, project_id)
        os_user, os_pw = _resolve_os_credentials(settings)

        dpus = (
            self.db.query(Dpu)
            .filter(Dpu.project_id == project_id)
            .all()
        )
        endpoints = _collect_endpoints(dpus, settings)

        unique_sources = {ep["node_id"] for ep in endpoints}
        if len(unique_sources) < 2:
            self._persist(
                project_id,
                {
                    "groups": {},
                    "note": (
                        "fewer than two DPUs have a reachable OS with "
                        "VLAN data-plane addresses configured"
                    ),
                },
                "skipped",
            )
            return {
                "status": "skipped",
                "matrix": {
                    "groups": {},
                    "note": (
                        "fewer than two DPUs have a reachable OS with "
                        "VLAN data-plane addresses configured"
                    ),
                },
            }

        # SQLAlchemy sessions aren't thread-safe, but `open_inband_host_ssh`
        # needs a session to resolve the host's cached credentials. Run
        # sources sequentially on the main session — at fleet sizes
        # we expect (≤8 DPUs) this completes in well under the 10-min
        # task budget; parallelism would only buy a few seconds.
        sources_by_id = {int(d.id): d for d in dpus}

        def _per_source(node_id: int) -> list[dict[str, Any]]:
            src_dpu = sources_by_id.get(node_id)
            if src_dpu is None:
                return []
            src_endpoints = [e for e in endpoints if e["node_id"] == node_id]
            target_endpoints = [e for e in endpoints if e["node_id"] != node_id]
            try:
                return _run_dpu_pings(
                    db=self.db,
                    src_dpu=src_dpu,
                    src_endpoints=src_endpoints,
                    target_endpoints=target_endpoints,
                    os_user=os_user,
                    os_pw=os_pw,
                )
            except Exception:
                logger.exception(
                    "DPU connectivity per-source crash node_id=%s", node_id,
                )
                return []

        try:
            results: list[dict[str, Any]] = []
            for node_id in sorted(unique_sources):
                results.extend(_per_source(node_id))
            matrix = {
                "groups": {
                    "dpu": {
                        "endpoints": endpoints,
                        "results": results,
                    },
                },
            }
            self._persist(project_id, matrix, "completed")
            return {"status": "completed", "matrix": matrix}
        except Exception as exc:
            logger.exception(
                "DPU connectivity matrix for project=%s failed", project_id,
            )
            err = {
                "groups": {},
                "error": f"DPU connectivity run failed: {type(exc).__name__}",
            }
            self._persist(project_id, err, "failed")
            return {"status": "failed", "matrix": err}

    def _persist(
        self, project_id: int, matrix: dict[str, Any], status: str,
    ) -> None:
        settings = _ensure_settings_row(self.db, project_id)
        settings.dpu_connectivity_matrix = matrix
        settings.dpu_connectivity_status = status
        settings.dpu_connectivity_run_at = datetime.now(UTC)
        self.db.commit()


__all__ = ["DpuConnectivityService"]
