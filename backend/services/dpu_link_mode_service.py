"""Convert a DPU's port link mode (IB → Ethernet) via mlxconfig.

The seeded bf.conf templates assume Ethernet uplinks (`p0` / `p1`
netplan stanzas, OVS bridges, LAG over Ethernet). InfiniBand-mode DPUs
won't render usable network config from those templates, so the DPU
tab gates flash + SSH-to-DPU on IB rows and surfaces this action in
their Actions menu.

Two execution paths share a single mlxconfig + reset sequence:

* **In-band** — SSH to the DPU's host (the same connection the rshim
  probe already uses), run mlxconfig there, reboot via rshim SW_RESET.
  Always available when the DPU is registered as in-band.

* **BMC** — SSH into the DPU OS (`dpu_os_ip` from the BMC ARP probe)
  and run mlxconfig there, then `mlxfwreset -l 3` to apply. The DPU OS
  must already be reachable (SSH'd into it) — otherwise we can't run
  mlxconfig at all. The action surfaces a clear precondition error so
  the operator knows to run *Probe DPU OS* first.

Both paths leave the actual link-mode discovery to the next *Discover*
the operator runs — the DPU reboots take 1-2 minutes and we don't want
to block this task on that.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import paramiko
from sqlalchemy.orm import Session

from core.encryption import decrypt_value_or_none
from core.errors import BadRequestError, NotFoundError
from models.dpu import Dpu, ProjectDpuSettings
from services.dpu_service import _derive_link_mode

logger = logging.getLogger(__name__)


@dataclass
class ConvertResult:
    """Outcome of one Convert-to-Ethernet attempt.

    `log` accumulates each command + its rc/stdout/stderr so the UI's
    *Details* affordance can show the operator exactly what happened.
    """

    ok: bool
    log: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


class DpuLinkModeService:
    def __init__(self, db: Session):
        self.db = db

    # ── Public API ─────────────────────────────────────────────────────────

    def convert_to_ethernet(self, project_id: int, dpu_id: int) -> ConvertResult:
        """Flip both DPU ports to LINK_TYPE=ETH(2) and reboot the DPU.

        Synchronous — issues mlxconfig and the reset, then returns. The
        DPU's actual reboot completes asynchronously over the next
        1-2 minutes; the operator runs *Discover* afterwards to refresh
        `mlxconfig_settings` on the DPU row.

        Raises BadRequestError on precondition failures (wrong access
        mode preconditions, DPU not currently in IB mode, OS not yet
        probed for BMC path). Captures every other failure in the
        ConvertResult so the UI can show a Details popover.
        """
        dpu = self._require_dpu(project_id, dpu_id)

        # Refuse when there's nothing to do — keeps the menu safe even
        # if the operator clicks Convert on a row that's already eth.
        current_mode = _derive_link_mode(dpu.last_discovery_payload)
        if current_mode not in ("ib", "mixed"):
            raise BadRequestError(
                f"DPU {dpu.id} is not in InfiniBand mode (current link_mode="
                f"{current_mode!r}). Run Discover to refresh, or pick a DPU "
                "whose link mode badge says InfiniBand."
            )

        if dpu.access_mode == "in-band":
            return self._convert_inband(dpu)
        return self._convert_bmc(dpu)

    # ── In-band: host SSH → mlxconfig → rshim SW_RESET ─────────────────────

    def _convert_inband(self, dpu: Dpu) -> ConvertResult:
        from services.rshim_service import (
            _short_bdf_for_mst,
            open_inband_host_ssh,
        )

        if not dpu.host_node_ip:
            raise BadRequestError(
                f"DPU {dpu.id} has no host_node_ip — cannot reach the host."
            )

        log: list[dict[str, Any]] = []
        try:
            client = open_inband_host_ssh(self.db, dpu)
        except Exception as exc:  # noqa: BLE001
            return ConvertResult(
                ok=False, error=f"SSH to host {dpu.host_node_ip} failed: {exc}",
            )

        try:
            short_bdf = _short_bdf_for_mst(dpu.pci_address)
            if not short_bdf:
                return ConvertResult(
                    ok=False, error=(
                        f"DPU {dpu.id} has no pci_address — cannot resolve the "
                        "MST device. Run Discover to populate it."
                    ),
                )

            rshim = (dpu.rshim_device or "rshim0")
            return _run_mlxconfig_then_reset(
                client,
                short_bdf=short_bdf,
                reset_cmd=(
                    # rshim SW_RESET reboots the DPU's ARM SoC; mlxconfig
                    # values are loaded at the next FW boot. Use sudo on
                    # Ubuntu hosts (root-only /dev/rshimN/misc).
                    f"sudo -n sh -c 'echo \"SW_RESET 1\" > /dev/{rshim}/misc'"
                ),
                log=log,
                use_sudo=True,
            )
        finally:
            try:
                client.close()
            except Exception:
                pass

    # ── BMC: DPU-OS SSH → mlxconfig → mlxfwreset ───────────────────────────

    def _convert_bmc(self, dpu: Dpu) -> ConvertResult:
        if dpu.dpu_os_status != "reachable" or not dpu.dpu_os_ip:
            raise BadRequestError(
                "Can't convert this BMC-mode DPU yet: the DPU OS isn't "
                "reachable. Run *Probe DPU OS* first — Convert needs an "
                "OS-side SSH session to call mlxconfig (the BMC's busybox "
                "doesn't ship it)."
            )
        os_user, os_pw = self._resolve_os_credentials(dpu)
        if not os_user or not os_pw:
            raise BadRequestError(
                "Can't convert: project DPU settings are missing the DPU OS "
                "default username or password. Set them in DPU Project "
                "Settings and try again."
            )

        log: list[dict[str, Any]] = []
        try:
            client = _connect_dpu_os(dpu.dpu_os_ip, os_user, os_pw)
        except Exception as exc:  # noqa: BLE001
            return ConvertResult(
                ok=False, error=f"SSH to DPU OS at {dpu.dpu_os_ip} failed: {exc}",
            )

        try:
            # The DPU OS sees only its own NIC silicon, so any pciconf0
            # entry under /dev/mst/ is the right one — no BDF dance needed.
            return _run_mlxconfig_then_reset(
                client,
                short_bdf=None,
                reset_cmd=(
                    "sudo -n mlxfwreset -d "
                    "$(ls /dev/mst/mt*pciconf0 2>/dev/null | head -1) "
                    "-l 3 --sync 1 -y reset 2>&1"
                ),
                log=log,
                use_sudo=True,
            )
        finally:
            try:
                client.close()
            except Exception:
                pass

    # ── Internals ──────────────────────────────────────────────────────────

    def _require_dpu(self, project_id: int, dpu_id: int) -> Dpu:
        dpu = (
            self.db.query(Dpu)
            .filter(Dpu.id == dpu_id, Dpu.project_id == project_id)
            .first()
        )
        if dpu is None:
            raise NotFoundError("Dpu", dpu_id)
        return dpu

    def _resolve_os_credentials(self, dpu: Dpu) -> tuple[str | None, str | None]:
        settings = (
            self.db.query(ProjectDpuSettings)
            .filter(ProjectDpuSettings.project_id == dpu.project_id)
            .first()
        )
        if settings is None:
            return None, None
        pw = (
            decrypt_value_or_none(settings.default_os_password_encrypted)
            if settings.default_os_password_encrypted else None
        )
        return settings.default_os_username, pw


# ── Shared mlxconfig + reset sequence ───────────────────────────────────────


def _run_mlxconfig_then_reset(
    client: paramiko.SSHClient,
    *,
    short_bdf: str | None,
    reset_cmd: str,
    log: list[dict[str, Any]],
    use_sudo: bool,
) -> ConvertResult:
    """Resolve the MST device, set LINK_TYPE_P1/P2 to ETH, then issue reset.

    `short_bdf` is None when the caller is already on a single-DPU view
    (DPU OS) and doesn't need to disambiguate. Otherwise we awk-match
    against `mst status -v` using the function-stripped short BDF.
    """
    sudo = "sudo -n " if use_sudo else ""

    # 1. Make sure /dev/mst/* exists (no-op if mst is already started).
    rc, out, err = _exec(client, f"{sudo}mst start 2>&1 || true", timeout=20)
    log.append(_log_entry("mst start", rc, out, err))

    # 2. Resolve the MST device path. On the DPU OS only its own DPU is
    #    visible, so the first pciconf0 entry is the right one. On a host
    #    awk-match `mst status -v` against the bus:dev short form.
    if short_bdf:
        resolve_cmd = (
            f"{sudo}mst status -v 2>/dev/null | "
            f"awk -v bdf='{short_bdf}' "
            "'$0 ~ bdf {for (i=1;i<=NF;i++) "
            "if ($i ~ /^\\/dev\\/mst\\//) print $i}' | head -1"
        )
    else:
        resolve_cmd = "ls /dev/mst/mt*pciconf0 2>/dev/null | head -1"
    rc, mst_dev, err = _exec(client, resolve_cmd, timeout=15)
    log.append(_log_entry("resolve MST device", rc, mst_dev, err))
    mst_dev = mst_dev.strip()
    if not mst_dev:
        return ConvertResult(
            ok=False, log=log,
            error=(
                "Could not resolve the MST device — `mst status -v` had no "
                "matching entry. Check that MFT is installed on the host "
                "(use *Install rshim + MFT* for in-band DPUs) and that the "
                "DPU is visible to lspci."
            ),
        )

    # 3. Flip both ports to ETH(2). `-y` skips the confirmation prompt
    #    (otherwise mlxconfig blocks waiting on stdin).
    cmd = (
        f"{sudo}mlxconfig -d {mst_dev} -y s "
        "LINK_TYPE_P1=2 LINK_TYPE_P2=2 2>&1"
    )
    rc, out, err = _exec(client, cmd, timeout=60)
    log.append(_log_entry(cmd, rc, out, err))
    if rc != 0:
        return ConvertResult(
            ok=False, log=log,
            error=(
                f"mlxconfig set LINK_TYPE_P1/P2=2 failed (exit={rc}): "
                f"{(err or out).strip()[:300] or '<no output>'}"
            ),
        )

    # 4. Apply the change by resetting the DPU. SW_RESET (in-band) and
    #    mlxfwreset -l 3 (BMC path) both reboot the SoC; the operator's
    #    next *Discover* picks up the new mlxconfig values.
    rc, out, err = _exec(client, reset_cmd, timeout=120)
    log.append(_log_entry(reset_cmd, rc, out, err))
    if rc != 0:
        return ConvertResult(
            ok=False, log=log,
            error=(
                "mlxconfig succeeded but the reset to apply it failed "
                f"(exit={rc}): {(err or out).strip()[:300] or '<no output>'}. "
                "The new LINK_TYPE values will take effect on the DPU's "
                "next reboot."
            ),
        )
    return ConvertResult(ok=True, log=log)


def _connect_dpu_os(host: str, username: str, password: str) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        host, username=username, password=password,
        timeout=15, allow_agent=False, look_for_keys=False,
    )
    return client


def _exec(
    client: paramiko.SSHClient, command: str, *, timeout: int = 30,
) -> tuple[int, str, str]:
    _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    rc = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    return rc, out, err


def _log_entry(command: str, rc: int, out: str, err: str) -> dict[str, Any]:
    return {"command": command, "exit_code": rc, "stdout": out, "stderr": err}


__all__ = ["ConvertResult", "DpuLinkModeService"]
