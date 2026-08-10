"""In-band DPU rshim install + probe service.

Rshim is the Linux kernel module + userspace daemon that exposes a
BlueField DPU to the host it sits in as a set of character devices under
``/dev/rshim0/``. Every in-band action Forge does on a DPU (probe,
flash, serial console, factory reset) ultimately routes through rshim
on the host — so we need a way to:

  * check whether rshim is installed + running on a host (probe_status);
  * install + enable the rshim package when it isn't (install_rshim).

Both paths honour the project's optional jumphost: when the project has
an ``ssh_credential_id`` configured, the SSH connection to the host
tunnels through the jumphost via paramiko direct-tcpip. When no
jumphost is configured the connection is direct. Call sites don't
branch on it — :func:`services.dpu_tunnel.resolve_project_jumphost_cred`
returns ``None`` in the no-jumphost case and the helpers fall back to
direct access.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import paramiko
from sqlalchemy.orm import Session

from core.encryption import decrypt_value_or_none
from core.errors import BadRequestError, NotFoundError
from models.dpu import Dpu
from models.ssh_credential import SSHCredential
from services.dpu_tunnel import resolve_project_jumphost_cred
from services.ssh.paramiko_utils import load_private_key_from_content

logger = logging.getLogger(__name__)

SSH_CONNECT_TIMEOUT = 15
# apt-get install + DKMS build of kernel-mft-dkms can run 3-5 minutes on
# slower hosts; allow headroom so the command doesn't time out mid-compile.
SSH_COMMAND_TIMEOUT = 600


class RshimNotReadyError(Exception):
    """Raised when `/dev/rshim0/*` is not usable on the BMC and can't be
    brought up. Message is diagnostic (includes recent journal lines when
    available) and safe to show to the operator.
    """


def ensure_rshim_active_on_bmc(
    client: paramiko.SSHClient,
    *,
    device: str = "misc",
) -> None:
    """Verify rshim is running on an OpenBMC and start it if not.

    OpenBMC ships rshim as a systemd unit that is usually disabled at
    factory; nothing populates `/dev/rshim0/` until the unit is
    activated. Flash and serial-console paths both need the device nodes
    under `/dev/rshim0/`, so both callers guard with this helper.

    The BMC runs as root, so no sudo. Raises `RshimNotReadyError` with a
    human-readable message (plus the last 20 lines of `journalctl -u
    rshim`) when the device node still isn't present — almost always
    because the x86 host already owns rshim and the BMC lost the race.
    """

    def run(cmd: str, timeout: int = 10) -> tuple[int, str, str]:
        stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        rc = stdout.channel.recv_exit_status()
        return rc, out.strip(), err.strip()

    # Always `restart` (not just `start`). Works whether the unit is
    # inactive, failed, or active-but-stuck-in-drop-mode — the last
    # case happens when rshim probed earlier while a different
    # backend had the endpoint, so `start` was a no-op. A clean
    # restart forces a fresh probe every time.
    rc, _out, _ = run(f"test -e /dev/rshim0/{device}")
    if rc != 0:
        run("systemctl restart rshim", timeout=15)
        # USB-based rshim on OpenBMC can take several seconds after service
        # start before /dev/rshim0/* device nodes appear. Poll instead of
        # a single fixed sleep.
        for _attempt in range(10):
            run("sleep 1")
            rc, _out, _ = run(f"test -e /dev/rshim0/{device}")
            if rc == 0:
                break
    if rc == 0:
        return
    _, state, _ = run("systemctl is-active rshim 2>/dev/null; true")

    # Device still missing — collect diagnostics before raising.
    _, journal, _ = run(
        "journalctl -u rshim -n 40 --no-pager 2>/dev/null | tail -n 20; true",
        timeout=15,
    )
    lc = journal.lower()
    ownership_markers = (
        "already owned",
        "already used",
        "resource busy",
        "another backend already attached",
        "entering drop mode",
    )
    if any(m in lc for m in ownership_markers):
        raise RshimNotReadyError(
            "The DPU's rshim is already attached by another backend — "
            "most likely the rshim service running on the x86 host "
            "where this DPU is physically installed. Only one side "
            "(host or BMC) can own rshim at a time, and the host won "
            "the race.\n"
            "\n"
            "Two ways to fix this — pick whichever fits your setup:\n"
            "\n"
            "Option 1 — Release rshim on the host (keep BMC access)\n"
            "------------------------------------------------------\n"
            "1. Identify the x86 host this DPU is plugged into.\n"
            "   On each candidate worker node:\n"
            "       lspci -d 15b3: | grep -i bluefield\n"
            "   The host that lists a BlueField DPU with a matching\n"
            "   PCIe address is the one holding rshim.\n"
            "\n"
            "2. On that host, stop (and optionally disable) the\n"
            "   rshim service so the BMC can claim the device:\n"
            "       sudo systemctl stop rshim\n"
            "       sudo systemctl disable rshim   # survive reboot\n"
            "\n"
            "3. On the BMC, force a re-probe so rshim attaches to\n"
            "   the now-free device. OpenBMC for BF-3 ships two\n"
            "   daemons (`rshim` + `xyz.openbmc_project.Software.Rshim`\n"
            "   which runs /usr/bin/bf-dpu-rshim); if both were\n"
            "   disabled, enable the one you prefer and restart:\n"
            "       systemctl restart rshim\n"
            "       ls /dev/rshim0/    # expect boot console misc rshim\n"
            "\n"
            "4. Back in bnk-forge, click Flash again.\n"
            "\n"
            "Only rshim ownership changes. This does NOT put the DPU\n"
            "into zero-trust mode — zero-trust is a separate,\n"
            "persistent mlxconfig setting unrelated to who currently\n"
            "holds the rshim channel.\n"
            "\n"
            "Option 2 — Switch this DPU to in-band access\n"
            "--------------------------------------------\n"
            "Since the host already runs rshim and is reachable,\n"
            "you can flash through the host instead of fighting for\n"
            "BMC ownership. The host's rshim stays enabled and Forge\n"
            "SSHes into it to drive the flash.\n"
            "\n"
            "1. Delete this DPU (and any other BMC-mode DPUs plugged\n"
            "   into the same host) from the DPUs list.\n"
            "\n"
            "2. Go to the Discovery tab, add the HOST's IP (not the\n"
            "   DPU's BMC IP) with host SSH credentials (or pick a\n"
            "   saved SSH credential), and run discovery.\n"
            "\n"
            "3. On the discovered host row, use \"Register DPU\" to\n"
            "   register every in-band DPU it carries. The new rows\n"
            "   show up with access_mode=in-band and flash routes\n"
            "   through the host's rshim automatically.\n"
            "\n"
            "Recent BMC rshim log:\n"
            + journal,
        )
    raise RshimNotReadyError(
        f"rshim state={state!r} but /dev/rshim0/{device} is not present.\n\n"
        f"Recent BMC rshim log:\n{journal}",
    )


def ensure_rshim_active_on_host(
    client: paramiko.SSHClient,
    *,
    device: str = "misc",
    rshim_device: str = "rshim0",
) -> None:
    """Verify rshim is running on the x86 host carrying an in-band DPU.

    Mirrors `ensure_rshim_active_on_bmc` but for the host side: the
    host runs Ubuntu, so systemctl calls need `sudo -n`. Typical
    failure modes are the same — rshim service stopped, or the BMC
    currently owns the USB rshim channel. `rshim_device` selects which
    `/dev/rshimN` to check on multi-DPU hosts.

    Raises `RshimNotReadyError` with a message that walks the operator
    through the inverse remediation: either release rshim on the BMC
    (so the host wins the next race) or delete the in-band DPU rows
    and re-register them under BMC mode.
    """

    def run(cmd: str, timeout: int = 10) -> tuple[int, str, str]:
        stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        rc = stdout.channel.recv_exit_status()
        return rc, out.strip(), err.strip()

    # Always `restart` (not just `start`). Works whether the unit is
    # inactive, failed, or active-but-stuck-in-drop-mode — the last
    # case happens when rshim probed earlier while the BMC had the
    # endpoint, so `start` was a no-op. A clean restart forces a
    # fresh probe every time.
    rc, _out, _ = run(f"test -e /dev/{rshim_device}/{device}")
    if rc != 0:
        run("sudo -n systemctl restart rshim", timeout=15)
        run("sleep 1")
        rc, _out, _ = run(f"test -e /dev/{rshim_device}/{device}")
    if rc == 0:
        return
    _, state, _ = run("sudo -n systemctl is-active rshim 2>/dev/null; true")

    _, journal, _ = run(
        "sudo -n journalctl -u rshim -n 40 --no-pager 2>/dev/null | tail -n 20; true",
        timeout=15,
    )
    lc = journal.lower()
    ownership_markers = (
        "already owned",
        "already used",
        "resource busy",
        "another backend already attached",
        "entering drop mode",
    )
    if any(m in lc for m in ownership_markers):
        raise RshimNotReadyError(
            "The DPU's rshim is already attached by another backend — "
            "most likely the rshim service running on the DPU's BMC. "
            "Only one side (host or BMC) can own rshim at a time, and "
            "the BMC won the race.\n"
            "\n"
            "Two ways to fix this — pick whichever fits your setup:\n"
            "\n"
            "Option 1 — Release rshim on the BMC (keep in-band access)\n"
            "---------------------------------------------------------\n"
            "1. SSH into the DPU's BMC (OpenBMC runs as root; no sudo).\n"
            "\n"
            "2. Stop BOTH rshim units — OpenBMC for BF-3 ships two\n"
            "   daemons that race for the USB endpoint:\n"
            "       systemctl stop rshim\n"
            "       systemctl disable rshim\n"
            "       systemctl stop xyz.openbmc_project.Software.Rshim\n"
            "       systemctl disable xyz.openbmc_project.Software.Rshim\n"
            "   Confirm nothing is left:\n"
            "       ps | grep -E 'rshim|bf-dpu-rshim'\n"
            "\n"
            "3. On the host, force a re-probe so rshim picks up the\n"
            "   now-free device:\n"
            "       sudo systemctl restart rshim\n"
            "       ls /dev/rshim0/    # expect boot console misc rshim\n"
            "\n"
            "4. Back in bnk-forge, click Flash again.\n"
            "\n"
            "Only rshim ownership changes. This does NOT put the DPU\n"
            "into zero-trust mode — zero-trust is a separate,\n"
            "persistent mlxconfig setting unrelated to who currently\n"
            "holds the rshim channel.\n"
            "\n"
            "Option 2 — Switch this DPU back to BMC access\n"
            "---------------------------------------------\n"
            "If the BMC must keep owning rshim (e.g. you want to\n"
            "reach the DPU without going through the host at all),\n"
            "re-register the DPU in BMC mode:\n"
            "\n"
            "1. Delete this DPU (and any other in-band DPUs on the\n"
            "   same host that should move to BMC mode) from the\n"
            "   DPUs list.\n"
            "\n"
            "2. Go to the Discovery tab, add the BMC's IP with BMC\n"
            "   credentials (Nvidia's default is `root` / `0penBmc`\n"
            "   on a fresh BF-3), and run discovery.\n"
            "\n"
            "3. On the discovered BMC row, use \"Register DPU\" to\n"
            "   register the DPU. The new row shows up with\n"
            "   access_mode=bmc and flash routes through the BMC's\n"
            "   rshim.\n"
            "\n"
            "Recent host rshim log:\n"
            + journal,
        )
    raise RshimNotReadyError(
        f"rshim state={state!r} but /dev/{rshim_device}/{device} is not present on "
        f"the host.\n\nRecent host rshim log:\n{journal}",
    )


# ─── Public result dataclasses ──────────────────────────────────────────────

@dataclass
class RshimStatus:
    """Snapshot of rshim + MFT state on an in-band DPU's host.

    Both are needed for a "fully ready" host: rshim for register-level
    access to the DPU (boot, serial console, etc.) and MFT (mst +
    mlxfwmanager) for FW identity / configuration queries.
    """

    installed: bool
    active: bool
    device_present: bool
    mft_installed: bool = False
    mst_running: bool = False
    message: str = ""


@dataclass
class RshimInstallResult:
    ok: bool
    log: list[dict[str, Any]] = field(default_factory=list)
    status: RshimStatus | None = None
    error: str | None = None


@dataclass
class InbandInventory:
    """What we can learn about an in-band DPU from its host via rshim+lspci.

    `raw_*` fields carry the full command output (kept for forensic view
    in the UI / support dumps). The rest are best-effort parses for the
    denormalized DPU columns (serial, oob0_mac, fw_version).
    """

    rshim_device_present: bool
    raw_rshim_misc: str = ""
    raw_lspci: str = ""
    raw_mlxconfig: str = ""
    raw_sysfs: str = ""
    opn: str | None = None
    dev_info: str | None = None
    bf_mode: str | None = None
    boot_mode: str | None = None
    up_time_seconds: int | None = None
    uuid: str | None = None
    mac: str | None = None
    serial: str | None = None
    psid: str | None = None
    fw_version: str | None = None
    part_number: str | None = None
    pci_description: str | None = None
    # Richer identity — populated from mlxfwmanager (when MFT is installed)
    # or the lspci subsystem line as a fallback.
    sku: str | None = None           # e.g. "900-9D3B6-00CV-AA0" (mlnx OPN)
    description: str | None = None   # e.g. "BlueField-3 P-Series DPU ..."
    base_mac: str | None = None      # oob-side base MAC from Base MAC/GUID
    subsystem_id: str | None = None  # e.g. "15b3:0041"
    pci_id: str | None = None        # e.g. "15b3:a2dc"
    # mlxconfig -q highlights — the full dict is preserved in
    # mlxconfig_settings so the UI can render everything on demand,
    # and the most-useful fields are denormalized for the identity view.
    mlxconfig_device_type: str | None = None
    mlxconfig_pci_device: str | None = None
    mlxconfig_settings: dict[str, str] = field(default_factory=dict)


# ─── Service ────────────────────────────────────────────────────────────────

class RshimService:
    def __init__(self, db: Session):
        self.db = db

    def probe_status(self, project_id: int, dpu_id: int) -> RshimStatus:
        dpu = self._require_inband_dpu(project_id, dpu_id)
        try:
            client = self._open_host_ssh(project_id, dpu)
        except Exception as exc:  # noqa: BLE001 — persist before re-raising
            self._persist_rshim_unreachable(dpu, str(exc))
            raise
        try:
            rshim_map = _enumerate_rshim_devices(client)
            # Multi-DPU hosts: persist 192.168.{100+N}.1/30 on each
            # tmfifo_netN so the host side matches what bf.cfg renders.
            # No-op on single-DPU hosts.
            _ensure_host_tmfifo_ips(client, rshim_map)
            rshim_device = _select_rshim_device(rshim_map, dpu.pci_address)
            self._persist_rshim_device(dpu, rshim_device)
            state = _probe_rshim_state(client, rshim_device=rshim_device)
            doca = _probe_doca_host_status(client)
        finally:
            client.close()
        self._persist_rshim_state(dpu, state)
        if doca is not None:
            dpu.doca_status = doca
            self.db.commit()
        return state

    def enable_on_bmc(self, project_id: int, dpu_id: int) -> RshimInstallResult:
        """Persistently enable rshim on the DPU's BMC, with conflict detection.

        OpenBMC ships the rshim unit pre-installed but usually disabled.
        This action:
          1. `systemctl enable rshim`  (persistent across BMC reboot).
          2. `systemctl restart rshim` (kicks the daemon into the claim race).
          3. Waits a few seconds so the drop-mode transitions settle.
          4. Reads `journalctl -u rshim` and treats any ownership-conflict
             marker as a failure, even if `/dev/rshim0/misc` momentarily
             appears — the host's rshim daemon typically wins the race a
             beat later, leaving the BMC unusable.

        This avoids the common false-success where the device flashes
        into existence briefly and bnk-forge reports "enabled" but the
        user then sees "rshim: inactive" on the next discover.
        """
        from services.dpu_credentials import ssh_connect_bmc_with_fallback
        from services.dpu_tunnel import resolve_project_jumphost_cred

        dpu = self._require_bmc_dpu(project_id, dpu_id)
        log: list[dict[str, Any]] = []

        # Progress breadcrumb — rendered by the UI under the blue
        # running… badge so the operator sees which step is executing.
        # Mirrors the in-band install path's on_progress pattern.
        def _report(msg: str) -> None:
            try:
                dpu.last_discovery_error = msg
                self.db.commit()
            except Exception:  # noqa: BLE001 — progress must never fail the action
                logger.debug("progress write failed for DPU %s", dpu.id)

        _report("Connecting to BMC…")

        try:
            user, pw = _resolve_bmc_credentials(self.db, dpu)
        except BadRequestError as exc:
            self._persist_rshim_unreachable(dpu, str(exc))
            return RshimInstallResult(ok=False, error=str(exc))

        jumphost_cred = resolve_project_jumphost_cred(self.db, dpu.project_id)
        try:
            client = ssh_connect_bmc_with_fallback(
                dpu.bmc_ip, user, pw, timeout=15, jumphost_cred=jumphost_cred,
            )
        except Exception as exc:  # noqa: BLE001
            msg = f"BMC SSH failed: {exc}"
            self._persist_rshim_unreachable(dpu, msg)
            return RshimInstallResult(ok=False, error=msg)

        try:
            _report("Enabling rshim service (persistent across BMC reboot)…")
            rc, out, err = _exec(client, "systemctl enable rshim", timeout=15)
            log.append(_log_entry("systemctl enable rshim", rc, out, err))

            # Capture the BMC's clock just before the restart so we can
            # filter the journal to lines produced by THIS attempt only.
            # Without this, stale "entering drop mode" / "already owned"
            # lines from an earlier wrestle would flag a fresh success as
            # a conflict.
            _rc_t, t0, _ = _exec(client, "date +%s", timeout=5)
            since_epoch = (t0 or "").strip() or "1"

            _report("Restarting rshim…")
            rc, out, err = _exec(client, "systemctl restart rshim", timeout=20)
            log.append(_log_entry("systemctl restart rshim", rc, out, err))

            _report("Waiting for rshim to claim the DPU…")
            # Poll for up to 20s — BF-3 rshim userspace commonly needs
            # several seconds after "USB device detected" before the
            # /dev/rshim0/ nodes are mounted. A fixed sleep 3 was too
            # short and gave false negatives on healthy BMCs.
            _exec(
                client,
                "for i in $(seq 1 20); do "
                "test -e /dev/rshim0/misc && exit 0; "
                "sleep 1; done; exit 1",
                timeout=30,
            )

            _report("Checking device state + log…")
            state = _probe_rshim_state_on_bmc(client)
            rc_j, journal, _ = _exec(
                client,
                f"journalctl -u rshim --since @{since_epoch} --no-pager "
                "2>/dev/null | tail -n 30; true",
                timeout=15,
            )
            log.append(_log_entry(
                f"journalctl -u rshim --since @{since_epoch}", rc_j, journal, "",
            ))

            # Extra diagnostics only captured when the device didn't show
            # up — useful context for the Details popover when the
            # journal alone doesn't explain the failure.
            if not state.device_present:
                rc_ls, ls_out, _ = _exec(
                    client,
                    "ls -la /dev/rshim0/ 2>&1; "
                    "echo '---'; "
                    "systemctl status rshim --no-pager 2>&1 | tail -n 15",
                    timeout=10,
                )
                log.append(_log_entry("rshim state diagnostics", rc_ls, ls_out, ""))
            else:
                ls_out = ""
        finally:
            client.close()

        conflict = _rshim_ownership_conflict(journal)
        self._persist_rshim_state(dpu, state)

        if conflict:
            msg = _bmc_rshim_conflict_message(journal)
            return RshimInstallResult(ok=False, log=log, status=state, error=msg)

        if not state.device_present:
            msg = (
                "rshim device /dev/rshim0 did not appear on BMC\n"
                "\n"
                "The rshim service restarted cleanly on the BMC but the "
                "device node never came up within 20s, and the log shows "
                "no ownership-contention markers either — likely a USB/"
                "driver issue on the BMC side.\n"
                "\n"
                f"Recent BMC rshim log:\n{journal}\n"
                "\n"
                f"BMC diagnostics:\n{ls_out}"
            )
            return RshimInstallResult(ok=False, log=log, status=state, error=msg)

        return RshimInstallResult(ok=True, log=log, status=state, error=None)

    def disable_rshim(self, project_id: int, dpu_id: int) -> RshimInstallResult:
        """Stop + disable rshim on whichever side owns this DPU.

        For in-band DPUs → the host. For BMC DPUs → the BMC. Runs
        `systemctl stop rshim && systemctl disable rshim` and re-probes
        state. Releases rshim so the opposite side can claim it.

        Disruptive: any active Serial Console / Flash session on this
        DPU is interrupted. Callers should confirm with the operator.
        """
        dpu = self._require_dpu(project_id, dpu_id)
        log: list[dict[str, Any]] = []

        def _report(msg: str) -> None:
            try:
                dpu.last_discovery_error = msg
                self.db.commit()
            except Exception:  # noqa: BLE001
                logger.debug("progress write failed for DPU %s", dpu.id)

        if dpu.access_mode == "in-band":
            if not dpu.host_node_ip:
                raise BadRequestError(
                    f"DPU {dpu.id} has no host_node_ip — cannot reach the host."
                )
            _report(f"Connecting to host {dpu.host_node_ip}…")
            try:
                client = self._open_host_ssh(project_id, dpu)
            except Exception as exc:  # noqa: BLE001
                msg = f"Host SSH failed: {exc}"
                self._persist_rshim_unreachable(dpu, msg)
                return RshimInstallResult(ok=False, error=msg)

            try:
                _report("Stopping rshim…")
                rc, out, err = _exec(
                    client, "sudo -n systemctl stop rshim", timeout=15,
                )
                log.append(_log_entry("systemctl stop rshim", rc, out, err))
                _report("Disabling rshim (persistent)…")
                rc, out, err = _exec(
                    client, "sudo -n systemctl disable rshim", timeout=15,
                )
                log.append(_log_entry("systemctl disable rshim", rc, out, err))
                _report("Checking device state…")
                state = _probe_rshim_state(client)
            finally:
                client.close()

            self._persist_rshim_state(dpu, state)
            # Success means rshim is NOT active any more — the device node
            # should be gone. If it's somehow still present, something
            # didn't take effect.
            ok = not state.device_present and not state.active
            return RshimInstallResult(
                ok=ok, log=log, status=state,
                error=None if ok else "rshim is still active after stop/disable",
            )

        if dpu.access_mode == "bmc":
            from services.dpu_credentials import ssh_connect_bmc_with_fallback
            from services.dpu_tunnel import resolve_project_jumphost_cred

            if not dpu.bmc_ip:
                raise BadRequestError(
                    f"DPU {dpu.id} has no bmc_ip — cannot reach the BMC."
                )
            _report(f"Connecting to BMC {dpu.bmc_ip}…")
            try:
                user, pw = _resolve_bmc_credentials(self.db, dpu)
            except BadRequestError as exc:
                self._persist_rshim_unreachable(dpu, str(exc))
                return RshimInstallResult(ok=False, error=str(exc))

            jumphost_cred = resolve_project_jumphost_cred(self.db, dpu.project_id)
            try:
                client = ssh_connect_bmc_with_fallback(
                    dpu.bmc_ip, user, pw, timeout=15, jumphost_cred=jumphost_cred,
                )
            except Exception as exc:  # noqa: BLE001
                msg = f"BMC SSH failed: {exc}"
                self._persist_rshim_unreachable(dpu, msg)
                return RshimInstallResult(ok=False, error=msg)

            try:
                _report("Stopping rshim on BMC…")
                rc, out, err = _exec(client, "systemctl stop rshim", timeout=15)
                log.append(_log_entry("systemctl stop rshim", rc, out, err))
                _report("Disabling rshim on BMC (persistent)…")
                rc, out, err = _exec(client, "systemctl disable rshim", timeout=15)
                log.append(_log_entry("systemctl disable rshim", rc, out, err))
                _report("Checking device state…")
                state = _probe_rshim_state_on_bmc(client)
            finally:
                client.close()

            self._persist_rshim_state(dpu, state)
            ok = not state.device_present and not state.active
            return RshimInstallResult(
                ok=ok, log=log, status=state,
                error=None if ok else "rshim is still active after stop/disable",
            )

        raise BadRequestError(
            f"DPU {dpu.id} access_mode={dpu.access_mode!r} is not supported."
        )

    def probe_status_on_bmc(self, dpu: Dpu) -> RshimStatus | None:
        """Read-only rshim probe on the DPU's BMC side.

        Used by Discover for BMC-mode DPUs so the UI can show whether the
        BMC currently owns rshim (so actions like Flash / Serial Console
        will work). Swallows every exception — Discover must not fail
        because the BMC SSH side is unreachable; instead we persist
        `unreachable` with the reason. Returns the snapshot (or None on
        SSH failure) so callers can log what happened.
        """
        if dpu.access_mode != "bmc" or not dpu.bmc_ip:
            return None

        # Lazy imports — these services have their own heavy deps.
        from services.dpu_credentials import ssh_connect_bmc_with_fallback
        from services.dpu_tunnel import resolve_project_jumphost_cred

        try:
            user, pw = _resolve_bmc_credentials(self.db, dpu)
        except BadRequestError as exc:
            self._persist_rshim_unreachable(dpu, str(exc))
            return None

        jumphost_cred = resolve_project_jumphost_cred(self.db, dpu.project_id)
        try:
            client = ssh_connect_bmc_with_fallback(
                dpu.bmc_ip, user, pw, timeout=15, jumphost_cred=jumphost_cred,
            )
        except Exception as exc:  # noqa: BLE001 — capture every failure
            self._persist_rshim_unreachable(dpu, f"BMC SSH failed: {exc}")
            return None

        try:
            state = _probe_rshim_state_on_bmc(client)
        finally:
            client.close()
        self._persist_rshim_state(dpu, state)
        return state

    def probe_inventory(self, project_id: int, dpu_id: int) -> InbandInventory:
        """Collect DPU identity + firmware from the host via rshim + lspci.

        Returns `rshim_device_present=False` (with raw fields empty) when
        the matching /dev/rshim*/misc isn't there yet — caller decides
        whether that's a "needs install" case or a flat-out failure.
        """
        dpu = self._require_inband_dpu(project_id, dpu_id)
        try:
            client = self._open_host_ssh(project_id, dpu)
        except Exception as exc:  # noqa: BLE001 — persist before re-raising
            self._persist_rshim_unreachable(dpu, str(exc))
            raise
        try:
            # Map every /dev/rshim*/misc on the host to its DPU's PCI BDF
            # and persist the index that matches THIS DPU. Multi-DPU hosts
            # expose rshim0, rshim1, ... — without this mapping every action
            # below would silently target rshim0 (the wrong DPU).
            rshim_map = _enumerate_rshim_devices(client)
            # Persist 192.168.{100+N}.1/30 on the host's tmfifo_netN
            # interfaces so multi-DPU hosts match what bf.cfg rendered on
            # the DPU side. No-op on single-DPU hosts.
            _ensure_host_tmfifo_ips(client, rshim_map)
            rshim_device = _select_rshim_device(rshim_map, dpu.pci_address)
            self._persist_rshim_device(dpu, rshim_device)

            state = _probe_rshim_state(client, rshim_device=rshim_device)
            # Persist rshim readiness on every probe (ready OR inactive) —
            # this is the Discover path, so the UI row needs to reflect the
            # outcome even when /dev/rshimN isn't present yet.
            self._persist_rshim_state(dpu, state)
            if not state.device_present:
                return InbandInventory(rshim_device_present=False)

            misc_path = f"/dev/{rshim_device}/misc"
            # DISPLAY_LEVEL 2 exposes OPN / UUID / MAC in the misc node.
            # Writes go to misc as a single line; needs root.
            _exec(
                client,
                f"sudo -n sh -c 'echo DISPLAY_LEVEL 2 > {misc_path}' 2>/dev/null || true",
            )
            _, misc_raw, _ = _exec(
                client,
                f"sudo -n cat {misc_path} 2>/dev/null || cat {misc_path}",
            )
            # Restore default display level so we leave no side effects.
            _exec(
                client,
                f"sudo -n sh -c 'echo DISPLAY_LEVEL 0 > {misc_path}' 2>/dev/null || true",
            )

            lspci_raw = ""
            sysfs_raw = ""
            if dpu.pci_address:
                # Match the bus:device only — user-supplied pci addresses
                # sometimes lack the function suffix.
                _, lspci_raw, _ = _exec(
                    client,
                    f"lspci -s '{dpu.pci_address}.' -vvv 2>/dev/null || lspci -vvv",
                )
                # Sysfs gives us vendor/device/subsystem IDs in hex. Check
                # the bus:device function .0 first since that's where the
                # first PF sits.
                _, sysfs_raw, _ = _exec(
                    client,
                    " ".join([
                        f"d=/sys/bus/pci/devices/{dpu.pci_address}.0;",
                        "[ -d \"$d\" ] || d=$(ls -d /sys/bus/pci/devices/"
                        f"{dpu.pci_address}* 2>/dev/null | head -1);",
                        "[ -d \"$d\" ] && for f in vendor device "
                        "subsystem_vendor subsystem_device class; do",
                        "  printf '%s=%s\\n' \"$f\" \"$(cat \"$d/$f\" 2>/dev/null)\";",
                        "done || true",
                    ]),
                )

            # `mlxconfig -d <mst> q` gives far more data than mlxfwmanager
            # (device type, PCI device name, plus every Configuration / Next
            # Boot field the firmware exposes). Needs MST running — so we
            # kick `mst start` first, look up the right /dev/mst/* path via
            # `mst status -v`, and then run the query.
            pci_arg = dpu.pci_address or ""
            # `mst status -v` prints short BDFs ("00:10.0"), so awk-match
            # against the function-stripped short form regardless of how
            # `pci_address` was stored. Discovery now captures the domain
            # prefix ("0000:00:10"), which would never match mst output.
            mst_match = _short_bdf_for_mst(pci_arg)
            mlx_cmd = "if command -v mlxconfig >/dev/null 2>&1; then\n"
            mlx_cmd += (
                "  echo '### mst start:'; sudo -n mst start 2>&1 || true; echo;\n"
                "  echo '### mst status -v:'; sudo -n mst status -v 2>&1 || true; echo;\n"
            )
            if pci_arg:
                # Translate the PCI address (e.g. 00:10) to the MST device
                # whose `mst status -v` row contains it. Typical BF3 path:
                # /dev/mst/mt41692_pciconf0 — chip prefix + suffix vary so
                # we resolve dynamically.
                mlx_cmd += (
                    f"  MSTDEV=$(sudo -n mst status -v 2>/dev/null | "
                    f"  awk -v bdf='{mst_match}' "
                    "  '$0 ~ bdf {for (i=1;i<=NF;i++) "
                    "  if ($i ~ /^\\/dev\\/mst\\//) print $i}' | head -1);\n"
                    "  echo \"### mlxconfig -d $MSTDEV q:\"; "
                    "  [ -n \"$MSTDEV\" ] && sudo -n mlxconfig -d "
                    "  \"$MSTDEV\" q 2>&1 || "
                    "  echo '(no MST device resolved — mst_pci kernel module may not be loaded)'; echo;\n"
                )
            else:
                mlx_cmd += (
                    "  MSTDEV=$(ls /dev/mst/mt*pciconf0 2>/dev/null | head -1);\n"
                    "  echo \"### mlxconfig -d $MSTDEV q:\"; "
                    "  [ -n \"$MSTDEV\" ] && sudo -n mlxconfig -d "
                    "  \"$MSTDEV\" q 2>&1 || "
                    "  echo '(no MST device)'; echo;\n"
                )
            mlx_cmd += "else\n  echo 'mlxconfig not installed';\nfi"
            _, mlx_raw, _ = _exec(client, mlx_cmd)
        finally:
            client.close()

        return _parse_inband_inventory(misc_raw, lspci_raw, mlx_raw, sysfs_raw)

    def install(self, project_id: int, dpu_id: int) -> RshimInstallResult:
        dpu = self._require_inband_dpu(project_id, dpu_id)

        # Write progress line to last_discovery_error between steps so the
        # UI (which polls every 2s while running) renders it under the
        # running badge. Cleared on completion by the caller.
        def on_progress(msg: str) -> None:
            try:
                dpu.last_discovery_error = msg
                self.db.commit()
            except Exception:  # noqa: BLE001 — progress must never fail install
                logger.debug("progress write failed for DPU %s", dpu.id)
                try:
                    self.db.rollback()
                except Exception:
                    pass

        client = self._open_host_ssh(project_id, dpu)
        try:
            result = _run_install(client, on_progress=on_progress)
            logger.info(
                "rshim install on host %s for DPU %s: ok=%s, steps=%d",
                dpu.host_node_ip, dpu.id, result.ok, len(result.log),
            )
            return result
        finally:
            client.close()

    # ── Internals ──────────────────────────────────────────────────────────

    def _persist_rshim_device(self, dpu: Dpu, rshim_device: str) -> None:
        """Record which /dev/rshimN belongs to this DPU.

        Stored without the `/dev/` prefix (e.g. "rshim0") so callers can
        construct full paths as `/dev/{rshim_device}/...`. Idempotent —
        only writes when the value changed, to avoid touching `updated_at`
        on every probe.
        """
        if dpu.rshim_device != rshim_device:
            dpu.rshim_device = rshim_device
            self.db.commit()

    def _persist_rshim_state(self, dpu: Dpu, status: RshimStatus) -> None:
        """Write the rshim readiness snapshot onto the DPU row.

        Collapses every non-ready outcome to "inactive" — the detail text
        carries the specifics (not installed, service dead, device missing,
        owner conflict, …) so the UI can show the precise reason without
        us maintaining a wider state machine.
        """
        from datetime import UTC, datetime

        dpu.rshim_state = "ready" if (status.active and status.device_present) else "inactive"
        dpu.rshim_state_detail = status.message or None
        dpu.rshim_checked_at = datetime.now(UTC)
        self.db.commit()

    def _persist_rshim_unreachable(self, dpu: Dpu, reason: str) -> None:
        """Record a failure to even reach the host (SSH / network / auth)."""
        from datetime import UTC, datetime

        dpu.rshim_state = "unreachable"
        dpu.rshim_state_detail = reason or None
        dpu.rshim_checked_at = datetime.now(UTC)
        self.db.commit()

    def _require_dpu(self, project_id: int, dpu_id: int) -> Dpu:
        dpu = (
            self.db.query(Dpu)
            .filter(Dpu.id == dpu_id, Dpu.project_id == project_id)
            .first()
        )
        if dpu is None:
            raise NotFoundError("dpu", dpu_id)
        return dpu

    def _require_inband_dpu(self, project_id: int, dpu_id: int) -> Dpu:
        dpu = (
            self.db.query(Dpu)
            .filter(Dpu.id == dpu_id, Dpu.project_id == project_id)
            .first()
        )
        if dpu is None:
            raise NotFoundError("dpu", dpu_id)
        if dpu.access_mode != "in-band":
            raise BadRequestError(
                f"DPU {dpu.id} is not in-band (access_mode={dpu.access_mode!r}) — "
                "rshim only applies to host-managed DPUs."
            )
        if not dpu.host_node_ip:
            raise BadRequestError(
                f"DPU {dpu.id} has no host_node_ip — cannot reach the host."
            )
        return dpu

    def _require_bmc_dpu(self, project_id: int, dpu_id: int) -> Dpu:
        dpu = (
            self.db.query(Dpu)
            .filter(Dpu.id == dpu_id, Dpu.project_id == project_id)
            .first()
        )
        if dpu is None:
            raise NotFoundError("dpu", dpu_id)
        if dpu.access_mode != "bmc":
            raise BadRequestError(
                f"DPU {dpu.id} is not BMC-mode (access_mode={dpu.access_mode!r})."
            )
        if not dpu.bmc_ip:
            raise BadRequestError(
                f"DPU {dpu.id} has no bmc_ip — cannot reach the BMC."
            )
        return dpu

    def _open_host_ssh(self, project_id: int, dpu: Dpu) -> paramiko.SSHClient:
        cred = _resolve_host_credential(self.db, dpu)
        jumphost_cred = resolve_project_jumphost_cred(self.db, project_id)

        sock = None
        jumphost_client: paramiko.SSHClient | None = None
        if jumphost_cred is not None:
            jumphost_client = _connect_jumphost(jumphost_cred)
            try:
                transport = jumphost_client.get_transport()
                if transport is None:
                    raise RuntimeError("jumphost transport unavailable")
                sock = transport.open_channel(
                    "direct-tcpip",
                    (dpu.host_node_ip, cred["port"]),
                    ("127.0.0.1", 0),
                    timeout=SSH_CONNECT_TIMEOUT,
                )
            except Exception:
                jumphost_client.close()
                raise

        host_client = paramiko.SSHClient()
        host_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            _connect(host_client, dpu.host_node_ip, cred, sock=sock)
        except Exception:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            if jumphost_client is not None:
                jumphost_client.close()
            raise

        if jumphost_client is not None:
            _bind_jumphost_lifetime(host_client, jumphost_client)
        return host_client


# ─── Credential resolution ──────────────────────────────────────────────────

def _resolve_bmc_credentials(db: Session, dpu: Dpu) -> tuple[str, str]:
    """DPU-level BMC creds take precedence over project OOB defaults.

    Matches the resolution used by DpuDiscoveryService; kept as a module
    helper so the rshim BMC probe can share the same fallback without
    introducing a cross-service import cycle.
    """
    from models.dpu import ProjectDpuSettings

    user = (
        decrypt_value_or_none(dpu.bmc_username_encrypted)
        if dpu.bmc_username_encrypted else None
    )
    pw = (
        decrypt_value_or_none(dpu.bmc_password_encrypted)
        if dpu.bmc_password_encrypted else None
    )

    if not user or not pw:
        settings = (
            db.query(ProjectDpuSettings)
            .filter(ProjectDpuSettings.project_id == dpu.project_id)
            .first()
        )
        if settings:
            if not user:
                user = settings.default_oob_username
            if not pw and settings.default_oob_password_encrypted:
                pw = decrypt_value_or_none(settings.default_oob_password_encrypted)

    if pw and not user:
        user = "root"
    if not user or not pw:
        raise BadRequestError(
            "No BMC password configured — set a per-DPU password or a project default OOB password."
        )
    return user, pw


def _resolve_host_credential(db: Session, dpu: Dpu) -> dict[str, Any]:
    """Return a flat credential dict for SSH-ing into the DPU's host.

    Precedence:
      1. Inline host_ssh_* fields on the DPU row (populated either by
         register-from-discovery or by an explicit edit in the UI).
      2. Saved SSHCredential referenced by ``host_ssh_credential_id``.
      3. Raise — the DPU has no usable credential.
    """
    if dpu.host_ssh_username and (
        dpu.host_ssh_password_encrypted or dpu.host_ssh_private_key_encrypted
    ):
        return {
            "username": dpu.host_ssh_username,
            "port": int(dpu.host_ssh_port or 22),
            "auth_type": (dpu.host_ssh_auth_type or "key").lower(),
            "password": decrypt_value_or_none(dpu.host_ssh_password_encrypted),
            "private_key": decrypt_value_or_none(dpu.host_ssh_private_key_encrypted),
            "key_passphrase": decrypt_value_or_none(dpu.host_ssh_key_passphrase_encrypted),
        }

    if dpu.host_ssh_credential_id is None:
        raise BadRequestError(
            f"DPU {dpu.id} has no host SSH credential set. Edit the DPU and "
            "either enter a username + password/key directly, or pick a "
            "saved SSH credential."
        )
    cred = db.get(SSHCredential, dpu.host_ssh_credential_id)
    if cred is None:
        raise BadRequestError(
            f"Saved SSH credential {dpu.host_ssh_credential_id} (referenced "
            f"by DPU {dpu.id}) no longer exists."
        )
    return {
        "username": cred.username,
        "port": int(cred.port or 22),
        "auth_type": (cred.auth_type or "key").lower(),
        "password": decrypt_value_or_none(cred.password_encrypted),
        "private_key": decrypt_value_or_none(cred.private_key_encrypted),
        "key_passphrase": decrypt_value_or_none(cred.key_passphrase_encrypted),
    }


# ─── Paramiko plumbing ──────────────────────────────────────────────────────

def _connect_jumphost(jumphost_cred: dict[str, Any]) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    # resolve_project_jumphost_cred already decrypted — use decrypt_value only
    # if fields were stored encrypted here. The cred dict is already plaintext.
    _connect(client, jumphost_cred["host"], jumphost_cred)
    return client


def _connect(
    client: paramiko.SSHClient,
    host: str,
    cred: dict[str, Any],
    *,
    sock: paramiko.Channel | None = None,
) -> None:
    auth_type = (cred.get("auth_type") or "key").lower()
    pkey = None
    if auth_type == "key" and cred.get("private_key"):
        pkey = load_private_key_from_content(
            cred["private_key"], cred.get("key_passphrase"),
        )
    client.connect(
        hostname=host,
        port=int(cred.get("port") or 22),
        username=cred["username"],
        password=cred.get("password") if auth_type == "password" else None,
        pkey=pkey,
        timeout=SSH_CONNECT_TIMEOUT,
        allow_agent=False,
        look_for_keys=False,
        sock=sock,
    )


def _bind_jumphost_lifetime(
    target: paramiko.SSHClient, jumphost: paramiko.SSHClient,
) -> None:
    original_close = target.close

    def _close_cascade() -> None:
        try:
            original_close()
        finally:
            try:
                jumphost.close()
            except Exception:
                pass

    target.close = _close_cascade  # type: ignore[method-assign]


# ─── Command helpers ────────────────────────────────────────────────────────

def _exec(
    client: paramiko.SSHClient, command: str, *, timeout: int = SSH_COMMAND_TIMEOUT,
) -> tuple[int, str, str]:
    _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    rc = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    return rc, out, err


def _log_entry(command: str, rc: int, out: str, err: str) -> dict[str, Any]:
    return {
        "command": command,
        "exit_code": rc,
        "stdout": out,
        "stderr": err,
    }


def _short_bdf_for_mst(pci_address: str | None) -> str:
    """Return a short `bus:device` form suitable for matching `mst status -v`.

    `mst status -v` always prints short BDFs (`00:10.0`) regardless of
    the host's PCI domain configuration. `dpu.pci_address` may carry
    the domain prefix (`0000:00:10`) since discovery switched to
    `lspci -Dnn`. Stripping every segment except the last two restores
    a substring that's reliably present in mst output.

    Empty input returns "" so callers' `if pci_arg:` guards still work.
    """
    if not pci_address:
        return ""
    # Drop any trailing function suffix first ("0000:00:10.2" → "0000:00:10")
    # then keep just the trailing bus:dev pair.
    base = pci_address.rsplit(".", 1)[0] if "." in pci_address else pci_address
    parts = base.split(":")
    return ":".join(parts[-2:]) if len(parts) >= 2 else base


def _enumerate_rshim_devices(client: paramiko.SSHClient) -> dict[str, str]:
    """Return a mapping of `pci_bdf → rshimN` for every present rshim device.

    The rshim daemon exposes one character-device tree per attached DPU
    under `/dev/rshim0`, `/dev/rshim1`, ... The `misc` node inside each
    contains a `DEV_NAME` line whose value encodes the PCI BDF for in-band
    rshim instances (`pcie-0000:00:01.2`). We pair the index with the BDF
    so callers can pick the right rshim for a known `dpu.pci_address`.

    Returns an empty dict when no rshim devices are present (rshim service
    not started, no DPUs visible, etc.) — callers fall back to "rshim0".
    """
    rc, out, _ = _exec(
        client,
        # Glob expands client-side under sh; keep it compact and fail-safe.
        # `2>/dev/null` swallows the "no match" path and the per-file
        # permission errors we hit when only some misc nodes are root-only.
        "for d in /dev/rshim*/misc; do "
        "  [ -e \"$d\" ] || continue; "
        "  idx=$(printf '%s' \"$d\" | sed -e 's|^/dev/||' -e 's|/misc$||'); "
        "  dn=$(grep -m1 '^DEV_NAME' \"$d\" 2>/dev/null "
        "       || sudo -n grep -m1 '^DEV_NAME' \"$d\" 2>/dev/null); "
        "  printf '%s|%s\\n' \"$idx\" \"$dn\"; "
        "done",
        timeout=10,
    )
    if rc != 0 or not out:
        return {}

    mapping: dict[str, str] = {}
    for line in out.splitlines():
        if "|" not in line:
            continue
        idx, dn = line.split("|", 1)
        idx = idx.strip()
        dn = dn.strip()
        if not idx or not dn:
            continue
        # `DEV_NAME    pcie-0000:00:01.2`  → strip the column whitespace
        # and the `pcie-` prefix that in-band BlueField rshim instances
        # always carry. The BDF value (with function suffix) becomes the
        # map key, and we also index by the function-stripped form so
        # callers comparing against `dpu.pci_address` (which is stored
        # without function) always hit a key.
        parts = dn.split()
        if len(parts) < 2:
            continue
        value = parts[1]
        if value.startswith("pcie-"):
            value = value[len("pcie-"):]
        mapping[value] = idx
        base = value.rsplit(".", 1)[0] if "." in value else value
        mapping.setdefault(base, idx)
    return mapping


def _select_rshim_device(
    rshim_map: dict[str, str], pci_address: str | None,
) -> str:
    """Resolve the rshim index that matches a DPU's PCI BDF.

    Returns "rshim0" when:
      * the DPU has no recorded `pci_address` yet (very early discovery), or
      * the host exposes no rshim devices at all (mapping empty), or
      * the mapping has entries but none match (orphan DPU row, stale data).

    The fallback keeps single-DPU hosts working without any change to
    behaviour — they always landed on rshim0 already.
    """
    if not rshim_map:
        return "rshim0"
    if not pci_address:
        # Single-rshim host? Use it; otherwise default safely to rshim0.
        if len(set(rshim_map.values())) == 1:
            return next(iter(rshim_map.values()))
        return "rshim0"
    if pci_address in rshim_map:
        return rshim_map[pci_address]
    # Fall back to the function-stripped form (`0000:00:01` ↦ rshimN even
    # when the misc DEV_NAME was `0000:00:01.2`).
    base = pci_address.rsplit(".", 1)[0] if "." in pci_address else pci_address
    return rshim_map.get(base, "rshim0")


_HOST_TMFIFO_NETPLAN = "/etc/netplan/50-bnk-forge-tmfifo.yaml"


def _build_host_tmfifo_netplan(indexes: set[int]) -> str:
    """Render the netplan YAML for a set of rshim indexes."""
    body = [
        "# Managed by bnk-forge — DO NOT EDIT.",
        "# Each /dev/rshimN exposes a tmfifo_netN to the host. The rshim",
        "# driver only auto-assigns 192.168.100.1/30 to tmfifo_net0; this",
        "# file persists the matching /30 on every additional tmfifo_netN",
        "# so multi-DPU hosts can reach 192.168.{100+N}.2 (the DPU side)",
        "# reliably across reboots.",
        "network:",
        "  version: 2",
        "  renderer: networkd",
        "  ethernets:",
    ]
    for n in sorted(indexes):
        body.extend([
            f"    tmfifo_net{n}:",
            "      dhcp4: false",
            "      dhcp6: false",
            "      addresses:",
            f"        - 192.168.{100 + n}.1/30",
        ])
    return "\n".join(body) + "\n"


def _ensure_host_tmfifo_ips(
    client: paramiko.SSHClient, rshim_map: dict[str, str],
) -> None:
    """Persist 192.168.{100+N}.1/30 on every tmfifo_netN via netplan.

    NVIDIA documents two host-side patterns for multi-DPU configuration:
    a single ``br_tmfifo`` bridge (one /24) or per-interface /30 subnets.
    We use /30s — they match the IP every bf.cfg renders on the DPU side
    (``derive_tmfifo_dpu_ip``) and avoid the host-side MAC-rewrite the
    bridge approach requires (every BlueField ships with the same default
    tmfifo MAC, which a Linux bridge would reject).

    The rshim kernel driver auto-assigns ``192.168.100.1/30`` to
    ``tmfifo_net0`` only; ``tmfifo_net1+`` come up bare with link-local
    only. Single-DPU hosts therefore need no intervention — we skip
    early. Multi-DPU hosts get a single ``50-bnk-forge-tmfifo.yaml`` that
    pins every interface's IP.

    Best-effort: any failure (no passwordless sudo, netplan missing,
    apply error) is logged and swallowed so a Discover never fails on a
    host that just can't be persistently configured. The config is also
    valid for the next reboot even if the live ``netplan apply`` errors.
    """
    indexes: set[int] = set()
    for name in set(rshim_map.values()):
        if not name.startswith("rshim"):
            continue
        try:
            n = int(name[len("rshim"):])
        except ValueError:
            continue
        if 0 <= n <= 100:
            indexes.add(n)
    if len(indexes) < 2:
        # Single-DPU host — kernel default on tmfifo_net0 is enough.
        return

    target = _build_host_tmfifo_netplan(indexes)

    # Idempotency: skip the write + apply when the file already matches.
    rc, current, _ = _exec(
        client,
        f"sudo -n cat {_HOST_TMFIFO_NETPLAN} 2>/dev/null || true",
        timeout=10,
    )
    if rc == 0 and current.strip() == target.strip():
        return

    # Pipe via base64 so multi-line YAML survives shell quoting cleanly.
    encoded = base64.b64encode(target.encode("utf-8")).decode("ascii")
    rc, _out, err = _exec(
        client,
        f"printf '%s' '{encoded}' | base64 -d | "
        f"sudo -n tee {_HOST_TMFIFO_NETPLAN} > /dev/null && "
        f"sudo -n chmod 600 {_HOST_TMFIFO_NETPLAN}",
        timeout=15,
    )
    if rc != 0:
        logger.warning(
            "bnk-forge: could not write %s (multi-DPU tmfifo IPs not "
            "persisted): %s",
            _HOST_TMFIFO_NETPLAN, err.strip() or f"exit={rc}",
        )
        return

    # `netplan apply` does a diff and only bounces interfaces whose
    # config actually changed — safe to call from a probe path.
    rc, out, err = _exec(client, "sudo -n netplan apply", timeout=30)
    if rc != 0:
        logger.warning(
            "bnk-forge: netplan apply failed (config written, will take "
            "effect on next reboot): %s",
            (err or out).strip() or f"exit={rc}",
        )
        return
    logger.info(
        "bnk-forge: applied multi-rshim tmfifo IPs via %s for rshim indexes %s",
        _HOST_TMFIFO_NETPLAN, sorted(indexes),
    )


def _probe_rshim_state(
    client: paramiko.SSHClient, *, rshim_device: str = "rshim0",
) -> RshimStatus:
    """Check whether rshim is installed + running on the host.

    `device_present` is the authoritative "usable" signal — when
    /dev/{rshim_device}/misc exists, Forge can talk to the DPU via rshim.
    """
    _, _, _ = _exec(client, ":")  # warm up (also validates the channel)

    rc_unit, _, _ = _exec(client, "systemctl list-unit-files rshim.service 2>/dev/null | grep -q rshim.service")
    installed_unit = rc_unit == 0

    rc_active, active_out, _ = _exec(
        client, "systemctl is-active rshim 2>/dev/null || true",
    )
    active = active_out.strip() == "active"

    rc_dev, _, _ = _exec(client, f"test -e /dev/{rshim_device}/misc")
    device_present = rc_dev == 0

    # Also accept dpkg/rpm package presence as "installed" so we can show
    # the right action label even when the unit file isn't registered yet.
    rc_pkg, _, _ = _exec(
        client,
        "command -v dpkg >/dev/null 2>&1 && dpkg -s rshim 2>/dev/null | grep -q '^Status: install ok installed' "
        "|| (command -v rpm >/dev/null 2>&1 && rpm -q rshim >/dev/null 2>&1)",
    )
    package_installed = rc_pkg == 0

    installed = installed_unit or package_installed

    # MFT detection — mlxfwmanager binary exists + `mst status` shows any
    # /dev/mst device (means the MST kernel module is loaded + running).
    rc_mft, _, _ = _exec(client, "command -v mlxfwmanager >/dev/null 2>&1")
    mft_installed = rc_mft == 0
    rc_mst, _, _ = _exec(client, "ls /dev/mst/mt* >/dev/null 2>&1")
    mst_running = rc_mst == 0

    if device_present:
        if mft_installed and mst_running:
            msg = f"rshim + MFT ready (/dev/{rshim_device})"
        elif mft_installed:
            msg = f"rshim ready (/dev/{rshim_device}); MFT installed but mst not started"
        else:
            msg = f"rshim ready (/dev/{rshim_device}); MFT not installed"
    elif active:
        msg = f"rshim service is active but /dev/{rshim_device} is not present yet"
    elif installed:
        msg = "rshim installed but not active"
    else:
        msg = "rshim not installed"
    return RshimStatus(
        installed=installed,
        active=active,
        device_present=device_present,
        mft_installed=mft_installed,
        mst_running=mst_running,
        message=msg,
    )


_RSHIM_OWNERSHIP_MARKERS: tuple[str, ...] = (
    "already owned",
    "already used",
    "resource busy",
    "another backend already attached",
    "entering drop mode",
)


def _rshim_ownership_conflict(journal: str) -> bool:
    """Heuristic: any of the well-known rshim-userspace contention
    markers appear in a recent journal tail.

    Same markers apply on both sides — these come from the rshim daemon,
    not OpenBMC or the host kernel — so one helper serves both Install-
    on-host and Enable-on-BMC.
    """
    lc = journal.lower()
    return any(m in lc for m in _RSHIM_OWNERSHIP_MARKERS)


def _host_rshim_conflict_message(journal: str) -> str:
    """Operator-facing remediation when the host can't hold rshim because
    the BMC already owns it — mirror of the BMC-side conflict message.

    First line is a short headline (used by the UI's failed-badge
    extractor) and the rest is the full remediation + journal tail
    shown inside the Details popover.
    """
    return (
        "another rshim backend has already attached\n"
        "\n"
        "The host rshim log shows another rshim backend is already using "
        "this DPU — almost certainly the rshim service on the DPU's BMC. "
        "Only one side (host or BMC) can own rshim at a time.\n"
        "\n"
        "To make the host the stable owner:\n"
        "\n"
        "1. On the DPU's BMC, stop (and disable) the rshim service:\n"
        "       systemctl stop rshim\n"
        "       systemctl disable rshim\n"
        "\n"
        "2. Click \"Install rshim + MFT\" here again.\n"
        "\n"
        "Alternative — if the BMC is supposed to own rshim, leave it "
        "enabled on the BMC and register this DPU in bnk-forge as "
        "access_mode=bmc instead (delete + re-register from the BMC IP).\n"
        "\n"
        "Recent host rshim log:\n"
        f"{journal}"
    )


def _bmc_rshim_conflict_message(journal: str) -> str:
    """Operator-facing remediation when the BMC can't hold rshim because
    the host already owns it.

    First line is a short headline (used by the UI's error-row extractor)
    and the rest is the full remediation + journal tail shown in the
    Details popover.
    """
    return (
        "another rshim backend has already attached\n"
        "\n"
        "The BMC log shows another rshim backend is already using this "
        "DPU — almost certainly the rshim service on the x86 host where "
        "this DPU is plugged in. Only one side (host or BMC) can own "
        "rshim at a time.\n"
        "\n"
        "To make the BMC the stable owner:\n"
        "\n"
        "1. On the host, stop (and disable) the rshim service:\n"
        "       sudo systemctl stop rshim\n"
        "       sudo systemctl disable rshim\n"
        "\n"
        "2. Click \"Enable rshim on BMC\" here again.\n"
        "\n"
        "Alternative — if the host is supposed to own rshim, leave it "
        "enabled on the host and register this DPU in bnk-forge as "
        "access_mode=in-band instead (delete + re-register via the host "
        "discovery flow).\n"
        "\n"
        "Recent BMC rshim log:\n"
        f"{journal}"
    )


def _probe_rshim_state_on_bmc(client: paramiko.SSHClient) -> RshimStatus:
    """Read-only rshim probe for an OpenBMC side.

    BMC runs as root (no sudo) and doesn't ship MFT — we only report
    whether the rshim service is installed/active and whether the device
    node is present. `device_present` is the authoritative signal the UI
    pill uses.
    """
    _, _, _ = _exec(client, ":")  # warm up

    rc_unit, _, _ = _exec(
        client,
        "systemctl list-unit-files rshim.service 2>/dev/null | grep -q rshim.service",
    )
    installed = rc_unit == 0

    rc_active, active_out, _ = _exec(
        client, "systemctl is-active rshim 2>/dev/null || true",
    )
    active = active_out.strip() == "active"

    rc_dev, _, _ = _exec(client, "test -e /dev/rshim0/misc")
    device_present = rc_dev == 0

    if device_present:
        msg = "rshim ready on BMC"
    elif active:
        msg = "rshim service is active on BMC but /dev/rshim0 is not present yet"
    elif installed:
        msg = "rshim installed on BMC but not active"
    else:
        msg = "rshim not installed on BMC"

    # MFT is host-side only — always False on BMC.
    return RshimStatus(
        installed=installed,
        active=active,
        device_present=device_present,
        mft_installed=False,
        mst_running=False,
        message=msg,
    )


# ─── Install flow ───────────────────────────────────────────────────────────

# `sudo -n` = non-interactive; fail fast if passwordless sudo is not set up
# rather than hanging for a password prompt that will never come.
_APT_INSTALL_STEPS: list[tuple[str, str]] = [
    ("apt update", "sudo -n apt-get update -y"),
    ("apt install rshim", "sudo -n DEBIAN_FRONTEND=noninteractive apt-get install -y rshim"),
]
# `restart` (not `start --now`) after `enable` — `start` is a no-op when
# the unit is already running, so a daemon stuck in drop mode or with a
# stale device view would never re-probe and the install would silently
# report failure. Splitting the two forces a clean restart every time.
_ENABLE_STEPS: list[tuple[str, str]] = [
    ("enable rshim", "sudo -n systemctl enable rshim"),
    ("restart rshim", "sudo -n systemctl restart rshim"),
    ("wait for /dev/rshim0/misc", "for i in $(seq 1 20); do test -e /dev/rshim0/misc && exit 0; sleep 1; done; exit 1"),
]


def _run_install(
    client: paramiko.SSHClient,
    *,
    on_progress: Callable[[str], None] | None = None,
) -> RshimInstallResult:
    """Install rshim + MFT, reporting each major step via on_progress.

    on_progress is called with a short human-readable line
    ("Installing rshim...", "Compiling kernel-mft-dkms...", etc.) that
    the caller can persist so the UI shows live progress.
    """
    def _report(msg: str) -> None:
        if on_progress is not None:
            on_progress(msg)
        logger.info("rshim install: %s", msg)

    _report("Checking host state…")

    # Early exit: if rshim + MFT + mst are all up, skip every step.
    pre = _probe_rshim_state(client)
    if pre.device_present and pre.mft_installed and pre.mst_running:
        return RshimInstallResult(
            ok=True,
            log=[_log_entry("rshim + MFT already ready", 0, pre.message, "")],
            status=pre,
        )

    log: list[dict[str, Any]] = []
    apt_available: bool | None = None

    def _check_apt() -> bool:
        nonlocal apt_available
        if apt_available is not None:
            return apt_available
        rc, out, _ = _exec(client, "command -v apt-get >/dev/null 2>&1 && echo apt || echo no")
        log.append(_log_entry("detect apt", rc, out, ""))
        apt_available = out.strip() == "apt"
        return apt_available

    # ── rshim install (fatal if missing) ──────────────────────────────
    if not pre.installed:
        if not _check_apt():
            return RshimInstallResult(
                ok=False, log=log,
                error=(
                    "This host does not use apt — install rshim manually "
                    "for your distribution, then rerun Discover."
                ),
            )
        for label, cmd in _APT_INSTALL_STEPS:
            _report(f"{label.capitalize()}…")
            rc, out, err = _exec(client, cmd)
            log.append(_log_entry(f"{label}: {cmd}", rc, out, err))
            if rc != 0:
                return RshimInstallResult(
                    ok=False, log=log,
                    error=(
                        f"'{label}' failed. Most common causes: no "
                        "passwordless sudo for this SSH user, or the "
                        "NVIDIA DOCA apt repo is not configured so "
                        "`rshim` can't be found."
                    ),
                )
    else:
        log.append(_log_entry("rshim already installed — skipping apt", 0, pre.message, ""))

    _report("Enabling rshim service…")
    # Capture clock just before the enable/restart so we can filter the
    # journal to lines produced by THIS attempt only — stale contention
    # markers from earlier runs would otherwise flag a fresh success as
    # a conflict.
    _rc_t, t0, _ = _exec(client, "date +%s", timeout=5)
    since_epoch = (t0 or "").strip() or "1"
    for label, cmd in _ENABLE_STEPS:
        rc, out, err = _exec(client, cmd)
        log.append(_log_entry(f"{label}: {cmd}", rc, out, err))
        if rc != 0 and label == "enable rshim":
            return RshimInstallResult(
                ok=False, log=log,
                error=(
                    "`systemctl enable rshim` failed — check passwordless "
                    "sudo for this SSH user."
                ),
            )
        if rc != 0 and label == "restart rshim":
            return RshimInstallResult(
                ok=False, log=log,
                error=(
                    "`systemctl restart rshim` failed — `systemctl status "
                    "rshim` on the host should show why (masked unit, bad "
                    f"config, etc.). stderr: {err.strip() or '(empty)'}"
                ),
            )
        if rc != 0 and label == "wait for /dev/rshim0/misc":
            # Pull the host rshim journal (this-attempt only) and
            # distinguish "the BMC owns rshim" (contention markers
            # present) from a genuine kernel/PCIe issue (markers absent).
            rc_j, journal, _ = _exec(
                client,
                f"sudo -n journalctl -u rshim --since @{since_epoch} "
                "--no-pager 2>/dev/null | tail -n 30; true",
                timeout=15,
            )
            log.append(_log_entry(
                f"journalctl -u rshim --since @{since_epoch}", rc_j, journal, "",
            ))
            if _rshim_ownership_conflict(journal):
                return RshimInstallResult(
                    ok=False, log=log,
                    error=_host_rshim_conflict_message(journal),
                )
            return RshimInstallResult(
                ok=False, log=log,
                error=(
                    "rshim device /dev/rshim0 did not appear on host\n"
                    "\n"
                    "The rshim service started on the host but the device "
                    "node never came up, and the log shows no ownership-"
                    "contention markers — likely a kernel module or PCIe "
                    "issue on the host side. Check `dmesg | tail` there.\n"
                    "\n"
                    f"Recent host rshim log:\n{journal}"
                ),
            )

    # ── MFT install (best-effort) ─────────────────────────────────────
    if _check_apt():
        _report("Installing kernel headers (for DKMS)…")
        rc, out, err = _exec(
            client,
            "sudo -n DEBIAN_FRONTEND=noninteractive apt-get install -y "
            "linux-headers-$(uname -r)",
        )
        log.append(_log_entry(
            "apt install linux-headers: sudo -n apt-get install -y linux-headers-$(uname -r)",
            rc, out, err,
        ))

        _report("Installing MFT + kernel-mft-dkms (compiles kernel module, ~2–5 min)…")
        rc, out, err = _exec(
            client,
            "sudo -n DEBIAN_FRONTEND=noninteractive apt-get install -y "
            "mft kernel-mft-dkms",
        )
        log.append(_log_entry(
            "apt install mft + kernel-mft-dkms: "
            "sudo -n apt-get install -y mft kernel-mft-dkms",
            rc, out, err,
        ))
        if rc != 0:
            log.append(_log_entry(
                "mft install failed — continuing without it",
                rc, "",
                "check the NVIDIA DOCA apt repo and passwordless sudo",
            ))
    else:
        log.append(_log_entry(
            "skipping mft install — host is not apt-based",
            0, "install mft + kernel-mft-dkms manually for FW identity", "",
        ))

    _report("Starting mst (creates /dev/mst/*)…")
    rc, out, err = _exec(client, "sudo -n mst start 2>&1 || true")
    log.append(_log_entry("mst start: sudo -n mst start", rc, out, err))

    _report("Re-probing final state…")
    post = _probe_rshim_state(client)
    return RshimInstallResult(ok=post.device_present, log=log, status=post)


# ─── Test seam ──────────────────────────────────────────────────────────────

def _extract_first(pattern: str, text: str, *, flags: int = 0) -> str | None:
    import re as _re
    m = _re.search(pattern, text, flags)
    if not m:
        return None
    value = m.group(1).strip()
    # rshim prints "N/A" when a field isn't populated in this firmware —
    # treat it as missing so downstream code doesn't store the literal.
    if value in {"N/A", "n/a", ""}:
        return None
    return value


def _parse_inband_inventory(
    misc: str, lspci: str, mlx: str, sysfs: str = "",
) -> InbandInventory:
    """Best-effort extraction from rshim + lspci + mlxconfig + sysfs.

    None of the regexes are load-bearing — if a tool output format
    changes, we just end up with None for that field and the raw blob
    stays in last_discovery_payload for debugging.
    """
    import re as _re

    # ── /dev/rshim0/misc (DISPLAY_LEVEL 2) ─────────────────────────────
    opn = _extract_first(r"(?:OPN_STR|OPN)\s+(\S.*?)\s*$", misc, flags=_re.MULTILINE)
    dev_info = _extract_first(r"DEV_INFO\s+(.+?)\s*$", misc, flags=_re.MULTILINE)
    bf_mode = _extract_first(r"BF_MODE\s+(.+?)\s*$", misc, flags=_re.MULTILINE)
    boot_mode = _extract_first(r"BOOT_MODE\s+(.+?)\s*$", misc, flags=_re.MULTILINE)
    up_time_match = _re.search(r"UP_TIME\s+(\d+)", misc)
    up_time_seconds = int(up_time_match.group(1)) if up_time_match else None
    uuid = _extract_first(r"UUID\s*[:=]?\s*([0-9a-fA-F-]+)", misc)
    mac = _extract_first(r"\bMAC\s*[:=]?\s*([0-9a-fA-F:]{17})", misc)

    # ── lspci -vvv ─────────────────────────────────────────────────────
    pci_description = _extract_first(r"^\S+\s+(.+?)\s*$", lspci, flags=_re.MULTILINE)
    part_number = _extract_first(r"\[PN\]\s*Part number:\s*(.+?)\s*$", lspci, flags=_re.MULTILINE)
    lspci_serial = _extract_first(r"\[SN\]\s*Serial number:\s*(.+?)\s*$", lspci, flags=_re.MULTILINE)
    subsystem_line_id = _extract_first(
        r"Subsystem:.*?Device\s+([0-9a-fA-F]{4})", lspci,
    )

    # ── sysfs (newline-separated key=value from a shell loop) ──────────
    def _sysfs(key: str) -> str | None:
        v = _extract_first(rf"^{key}=(.+?)\s*$", sysfs, flags=_re.MULTILINE)
        if v and v.startswith("0x"):
            return v[2:]  # strip 0x so IDs render compactly
        return v

    sys_vendor = _sysfs("vendor")
    sys_device = _sysfs("device")
    sys_subvendor = _sysfs("subsystem_vendor")
    sys_subdevice = _sysfs("subsystem_device")
    pci_id = f"{sys_vendor}:{sys_device}" if sys_vendor and sys_device else None
    subsystem_id = (
        f"{sys_subvendor}:{sys_subdevice}" if sys_subvendor and sys_subdevice
        else (f"{sys_subvendor}:{subsystem_line_id}"
              if sys_subvendor and subsystem_line_id else None)
    )

    # ── mlxconfig -d <mst> q ──────────────────────────────────────────
    # Output shape (abridged):
    #   Device #1:
    #   ----------
    #
    #   Device type:        BlueField3
    #   Name:               N/A
    #   Description:        BlueField-3 P-Series DPU ...
    #   Device:             /dev/mst/mt41692_pciconf0
    #
    #   Configurations:                         Next Boot
    #           MEMIC_BAR_SIZE                          0
    #           INTERNAL_CPU_MODEL                      EMBEDDED_CPU(1)
    #           LINK_TYPE_P1                            ETH(2)
    #           ...
    mlx_device_type = _extract_first(r"^\s*Device type:\s*(.+?)\s*$", mlx, flags=_re.MULTILINE)
    mlx_description = _extract_first(r"^\s*Description:\s*(.+?)\s*$", mlx, flags=_re.MULTILINE)
    mlx_pci_device = _extract_first(r"^\s*Device:\s*(.+?)\s*$", mlx, flags=_re.MULTILINE)

    # Configuration settings — everything under "Configurations:" that
    # looks like `KEY VALUE`. Preserves full value strings with parens.
    settings: dict[str, str] = {}
    in_config_block = False
    for line in mlx.splitlines():
        if _re.match(r"^\s*Configurations:\s", line):
            in_config_block = True
            continue
        if not in_config_block:
            continue
        m = _re.match(r"^\s+([A-Z][A-Z0-9_]+)\s+(\S.*?)\s*$", line)
        if m:
            settings[m.group(1)] = m.group(2)

    serial = lspci_serial or opn

    return InbandInventory(
        rshim_device_present=True,
        raw_rshim_misc=misc,
        raw_lspci=lspci,
        raw_mlxconfig=mlx,
        raw_sysfs=sysfs,
        opn=opn,
        dev_info=dev_info,
        bf_mode=bf_mode,
        boot_mode=boot_mode,
        up_time_seconds=up_time_seconds,
        uuid=uuid,
        mac=mac,
        serial=serial,
        psid=None,
        fw_version=None,
        part_number=part_number,
        pci_description=pci_description,
        sku=opn or part_number,
        description=mlx_description or None,
        base_mac=None,
        subsystem_id=subsystem_id,
        pci_id=pci_id,
        mlxconfig_device_type=mlx_device_type,
        mlxconfig_pci_device=mlx_pci_device,
        mlxconfig_settings=settings,
    )


def open_inband_host_ssh(db: Session, dpu: Dpu) -> paramiko.SSHClient:
    """Open an SSH client to an in-band DPU's host.

    Public module-level helper so other services (the flash engine, the
    DPU-OS probe, future rshim console) can reuse the same credential
    resolution + jumphost tunneling as RshimService without having to
    instantiate the service or call private methods.

    Raises ``BadRequestError`` when the DPU isn't in-band or has no
    usable credential. Raises ``paramiko.SSHException`` / connection
    errors on unreachable host or auth failure.
    """
    if dpu.access_mode != "in-band":
        raise BadRequestError(
            f"open_inband_host_ssh called on non-in-band DPU {dpu.id} "
            f"(access_mode={dpu.access_mode!r})"
        )
    return RshimService(db)._open_host_ssh(dpu.project_id, dpu)  # noqa: SLF001


def _probe_doca_host_status(client: paramiko.SSHClient) -> dict | None:
    """Probe DOCA host package status on the host carrying an in-band DPU.

    Same checks as bare-metal host_probe._probe_doca_status but using
    paramiko directly (bare-metal uses SSHSession wrapper). Returns None
    on any failure so the probe never blocks rshim status.
    """
    try:
        checks = {
            "repo_configured": "dpkg -s doca-host > /dev/null 2>&1 && echo ok || echo missing",
            "profile_installed": "dpkg -s doca-all > /dev/null 2>&1 && echo ok || echo missing",
            "doca_version": "dpkg-query -W -f='${Version}' doca-all 2>/dev/null || echo ''",
            "mst_present": "command -v mst > /dev/null 2>&1 && echo ok || echo missing",
            "mlxconfig_present": "command -v mlxconfig > /dev/null 2>&1 && echo ok || echo missing",
            "rshim_present": "command -v rshim > /dev/null 2>&1 && echo ok || echo missing",
            "bfb_install_present": "command -v bfb-install > /dev/null 2>&1 && echo ok || echo missing",
            "openibd_loaded": "systemctl is-active openibd > /dev/null 2>&1 && echo active || echo inactive",
            "mst_devices": "ls /dev/mst/mt* 2>/dev/null | head -3 || echo ''",
        }
        results = {}
        for key, cmd in checks.items():
            rc, out, _ = _exec(client, cmd, timeout=10)
            results[key] = out.strip() if rc == 0 else ""

        return {
            "repo_configured": results["repo_configured"] == "ok",
            "profile_installed": results["profile_installed"] == "ok",
            "doca_version": results["doca_version"] or None,
            "mst_present": results["mst_present"] == "ok",
            "mlxconfig_present": results["mlxconfig_present"] == "ok",
            "rshim_present": results["rshim_present"] == "ok",
            "bfb_install_present": results["bfb_install_present"] == "ok",
            "openibd_loaded": results["openibd_loaded"] == "active",
            "mst_devices": [d for d in results["mst_devices"].splitlines() if d],
            "ready": all([
                results["mst_present"] == "ok",
                results["mlxconfig_present"] == "ok",
                results["rshim_present"] == "ok",
                results["bfb_install_present"] == "ok",
            ]),
        }
    except Exception:
        logger.debug("DOCA host status probe failed — continuing without it")
        return None


__all__ = [
    "RshimService",
    "RshimStatus",
    "RshimInstallResult",
    "InbandInventory",
    "open_inband_host_ssh",
]
