"""WebSocket endpoints for DPU interactive terminals.

Three modes, all sharing the same client protocol:

  • /ws/projects/{id}/dpus/{id}/serial-console
      SSH to BMC → start rshim if needed → `screen /dev/rshim0/console`
      over a PTY. Used to talk to the DPU OS boot-loader / u-boot / getty
      before the host-side interface is reachable.

  • /ws/projects/{id}/dpus/{id}/ssh-bmc
      Plain SSH shell into the DPU BMC (OpenBMC on the ARMv7 secondary core).
      Reuses per-DPU BMC creds or project defaults, with Nvidia `0penBmc`
      fallback — same lookup as the serial console.

  • /ws/projects/{id}/dpus/{id}/ssh-os
      Plain SSH shell into the DPU OS (the Ubuntu Linux on the main ARM
      cores). Requires the DPU OS IP to be known (populated by the probe
      task) and the project-level default_os_* credentials to be set.

Protocol (matches routes/k8s_websocket.py):
  client→server: {"type":"stdin","data":"base64"} | {"type":"resize","rows":N,"cols":M}
  server→client: {"type":"connected","message":"...","hints":"..."}
               | {"type":"stdout"|"stderr","data":"base64"}
               | {"type":"error","message":"..."}
               | {"type":"exit","code":N}
"""

import asyncio
import base64
import json
import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from database import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["dpu-websocket"])

# Nvidia default password fallback — matches probe + reset behaviour.
_NVIDIA_DEFAULT_BMC_PASSWORD = "0penBmc"


async def _validate_ws_token(websocket: WebSocket, token: str | None) -> bool:
    """Auth guard matching routes/k8s_websocket.py."""
    from core.config import settings
    if not settings.REQUIRE_AUTH:
        return True
    if not token:
        await websocket.close(code=4401, reason="Unauthorized — token required")
        return False
    try:
        from services.auth_service import decode_token
        payload = decode_token(token)
        role = payload.get("role", "")
        if role not in ("admin", "operator"):
            await websocket.close(code=4401, reason="Unauthorized — operator role required")
            return False
        # #184: fail closed on the actual User row (see k8s_websocket) -- refuse
        # if it can't be resolved or still owes a password change, so a
        # seed-credential admin never reaches the DPU console / BMC SSH.
        from services.auth_service import token_user_state
        ws_user = token_user_state(token)
        if ws_user is None or ws_user.must_change_password:
            await websocket.close(code=4401, reason="Unauthorized")
            return False
        return True
    except Exception:
        await websocket.close(code=4401, reason="Unauthorized — invalid token")
        return False


# ───────────────────────── BMC serial console (rshim) ─────────────────────────

@router.websocket("/projects/{project_id}/dpus/{dpu_id}/serial-console")
async def dpu_serial_console(
    websocket: WebSocket,
    project_id: int,
    dpu_id: int,
    token: str | None = Query(None),
):
    await websocket.accept()

    if not await _validate_ws_token(websocket, token):
        return

    # Split by access mode: BMC DPUs SSH to the BMC and cat rshim's console
    # device there; in-band DPUs SSH to the host the DPU sits in (same
    # /dev/rshim0/console, different transport + needs sudo).
    access_mode, lookup_err = _get_dpu_access_mode(project_id, dpu_id)
    if access_mode is None:
        await websocket.send_json({"type": "error", "message": lookup_err or f"DPU {dpu_id} not found"})
        await websocket.close()
        return

    try:
        if access_mode == "in-band":
            await _run_serial_console_inband(websocket, project_id, dpu_id)
        else:
            creds = _load_bmc_credentials(project_id, dpu_id)
            if creds is None:
                await websocket.send_json({"type": "error", "message": f"DPU {dpu_id} not found"})
                await websocket.close()
                return
            if isinstance(creds, str):
                await websocket.send_json({"type": "error", "message": creds})
                await websocket.close()
                return
            bmc_ip, user, pw, jumphost_cred = creds
            await _run_serial_console(websocket, bmc_ip, user, pw, jumphost_cred)
    except WebSocketDisconnect:
        logger.info("DPU %s serial console: client disconnected", dpu_id)
    except Exception as exc:  # noqa: BLE001 — catch so we can inform the client
        logger.exception("DPU %s serial console error", dpu_id)
        try:
            await websocket.send_json({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass


def _get_dpu_access_mode(project_id: int, dpu_id: int) -> tuple[str | None, str | None]:
    """Return (access_mode, None) or (None, error_message) for the given DPU."""
    db: Session = SessionLocal()
    try:
        from models.dpu import Dpu
        dpu = (
            db.query(Dpu)
            .filter(Dpu.id == dpu_id, Dpu.project_id == project_id)
            .first()
        )
        if dpu is None:
            return None, f"DPU {dpu_id} not found"
        return dpu.access_mode or "bmc", None
    finally:
        db.close()


# ────────────────────────────── SSH to BMC shell ──────────────────────────────

@router.websocket("/projects/{project_id}/dpus/{dpu_id}/ssh-bmc")
async def dpu_ssh_bmc(
    websocket: WebSocket,
    project_id: int,
    dpu_id: int,
    token: str | None = Query(None),
):
    await websocket.accept()

    if not await _validate_ws_token(websocket, token):
        return

    creds = _load_bmc_credentials(project_id, dpu_id)
    if creds is None:
        await websocket.send_json({"type": "error", "message": f"DPU {dpu_id} not found"})
        await websocket.close()
        return
    if isinstance(creds, str):
        await websocket.send_json({"type": "error", "message": creds})
        await websocket.close()
        return
    bmc_ip, user, pw, jumphost_cred = creds

    try:
        await _run_ssh_shell(
            websocket,
            host=bmc_ip,
            user=user,
            password=pw,
            label=f"BMC {bmc_ip}",
            allow_nvidia_default=True,
            jumphost_cred=jumphost_cred,
        )
    except WebSocketDisconnect:
        logger.info("DPU %s ssh-bmc: client disconnected", dpu_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("DPU %s ssh-bmc error", dpu_id)
        try:
            await websocket.send_json({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass


# ─────────────────────────── SSH to DPU OS shell ──────────────────────────────

@router.websocket("/projects/{project_id}/dpus/{dpu_id}/ssh-os")
async def dpu_ssh_os(
    websocket: WebSocket,
    project_id: int,
    dpu_id: int,
    token: str | None = Query(None),
):
    await websocket.accept()

    if not await _validate_ws_token(websocket, token):
        return

    # For in-band DPUs the DPU OS sits on tmfifo (192.168.100.2) which is
    # only reachable from the host. That's 2 hops (forge → host → DPU) or
    # 3 with a jumphost (forge → jumphost → host → DPU). Handled in the
    # in-band branch via open_inband_host_ssh + a second direct-tcpip
    # channel.
    access_mode, lookup_err = _get_dpu_access_mode(project_id, dpu_id)
    if access_mode is None:
        await websocket.send_json({"type": "error", "message": lookup_err or f"DPU {dpu_id} not found"})
        await websocket.close()
        return

    if access_mode == "in-band":
        try:
            await _run_ssh_os_inband(websocket, project_id, dpu_id)
        except WebSocketDisconnect:
            logger.info("DPU %s ssh-os in-band: client disconnected", dpu_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("DPU %s ssh-os in-band error", dpu_id)
            try:
                await websocket.send_json({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            except Exception:
                pass
            try:
                await websocket.close()
            except Exception:
                pass
        return

    creds = _load_dpu_os_credentials(project_id, dpu_id)
    if creds is None:
        await websocket.send_json({"type": "error", "message": f"DPU {dpu_id} not found"})
        await websocket.close()
        return
    if isinstance(creds, str):
        await websocket.send_json({"type": "error", "message": creds})
        await websocket.close()
        return
    os_ip, user, pw, jumphost_cred = creds

    try:
        await _run_ssh_shell(
            websocket,
            host=os_ip,
            user=user,
            password=pw,
            label=f"DPU OS {os_ip}",
            jumphost_cred=jumphost_cred,
            allow_nvidia_default=False,
        )
    except WebSocketDisconnect:
        logger.info("DPU %s ssh-os: client disconnected", dpu_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("DPU %s ssh-os error", dpu_id)
        try:
            await websocket.send_json({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass


# ─────────────────────────── Credential resolution ────────────────────────────

def _load_bmc_credentials(
    project_id: int, dpu_id: int
) -> tuple[str, str, str, dict | None] | str | None:
    """Resolve BMC (host, user, password, jumphost_cred) from DPU row with
    project-default fallback. Returns None if DPU not found, a string
    error message if creds are missing, or a
    (host, user, pw, jumphost_cred) tuple on success."""
    db: Session = SessionLocal()
    try:
        from core.encryption import decrypt_value_or_none
        from models.dpu import Dpu, ProjectDpuSettings

        dpu = (
            db.query(Dpu)
            .filter(Dpu.id == dpu_id, Dpu.project_id == project_id)
            .first()
        )
        if not dpu:
            return None

        user = (
            decrypt_value_or_none(dpu.bmc_username_encrypted)
            if dpu.bmc_username_encrypted else None
        )
        pw = (
            decrypt_value_or_none(dpu.bmc_password_encrypted)
            if dpu.bmc_password_encrypted else None
        )
        if not user or not pw:
            settings_row = (
                db.query(ProjectDpuSettings)
                .filter(ProjectDpuSettings.project_id == project_id)
                .first()
            )
            if settings_row:
                user = user or settings_row.default_oob_username
                if not pw and settings_row.default_oob_password_encrypted:
                    pw = decrypt_value_or_none(settings_row.default_oob_password_encrypted)

        if pw and not user:
            user = "root"
        if not user or not pw:
            return "No BMC password configured — set a per-DPU password or a project default OOB password."

        from services.dpu_tunnel import resolve_project_jumphost_cred
        jumphost_cred = resolve_project_jumphost_cred(db, project_id)
        return dpu.bmc_ip, user, pw, jumphost_cred
    finally:
        db.close()


def _load_dpu_os_credentials(
    project_id: int, dpu_id: int
) -> tuple[str, str, str] | str | None:
    """Resolve DPU-OS (host, user, password) from the project-level defaults.

    Returns None if DPU not found, a string error message if prerequisites
    are missing, or a (host, user, pw) tuple on success.
    """
    db: Session = SessionLocal()
    try:
        from core.encryption import decrypt_value_or_none
        from models.dpu import Dpu, ProjectDpuSettings

        dpu = (
            db.query(Dpu)
            .filter(Dpu.id == dpu_id, Dpu.project_id == project_id)
            .first()
        )
        if not dpu:
            return None

        if not dpu.dpu_os_ip:
            return (
                "DPU OS IP is not known — run 'Probe DPU OS' first so the DPU OS address "
                "can be learned from the BMC."
            )

        settings_row = (
            db.query(ProjectDpuSettings)
            .filter(ProjectDpuSettings.project_id == project_id)
            .first()
        )
        if not settings_row:
            return "Project DPU settings missing — configure default_os_username/password."

        user = settings_row.default_os_username
        pw = (
            decrypt_value_or_none(settings_row.default_os_password_encrypted)
            if settings_row.default_os_password_encrypted else None
        )
        if not user or not pw:
            return (
                "No DPU OS credentials — set 'DPU OS Default Username' and "
                "'DPU OS Default Password' in the project's DPU settings."
            )

        from services.dpu_tunnel import resolve_project_jumphost_cred
        jumphost_cred = resolve_project_jumphost_cred(db, project_id)
        return dpu.dpu_os_ip, user, pw, jumphost_cred
    finally:
        db.close()


# ────────────────────────────── Shell pump helpers ────────────────────────────

async def _run_ssh_shell(
    websocket: WebSocket,
    *,
    host: str,
    user: str,
    password: str,
    label: str,
    allow_nvidia_default: bool,
    jumphost_cred: dict | None = None,
) -> None:
    """Open a paramiko interactive-shell PTY on `host` and bridge bytes to
    the WebSocket. `allow_nvidia_default` adds the `0penBmc` fallback used
    by the BMC path (the DPU OS side has no such default). When the
    project has a jumphost configured the SSH session tunnels through it.
    """
    import paramiko

    from services.dpu_tunnel import ssh_connect_to_bmc

    loop = asyncio.get_running_loop()

    def _connect(pw: str) -> paramiko.SSHClient:
        return ssh_connect_to_bmc(
            host, username=user, password=pw,
            jumphost_cred=jumphost_cred, timeout=15,
        )

    try:
        client: paramiko.SSHClient = await loop.run_in_executor(None, _connect, password)
    except paramiko.ssh_exception.AuthenticationException:
        if not allow_nvidia_default or password == _NVIDIA_DEFAULT_BMC_PASSWORD:
            await websocket.send_json({"type": "error", "message": f"SSH authentication failed for {label}."})
            await websocket.close()
            return
        try:
            client = await loop.run_in_executor(None, _connect, _NVIDIA_DEFAULT_BMC_PASSWORD)
        except Exception as exc:
            await websocket.send_json({
                "type": "error",
                "message": f"SSH auth failed for {label} (and Nvidia default password also rejected): {exc}",
            })
            await websocket.close()
            return
    except Exception as exc:
        await websocket.send_json({
            "type": "error",
            "message": f"Could not connect to {label}: {exc}",
        })
        await websocket.close()
        return

    try:
        await websocket.send_json({
            "type": "connected",
            "message": f"Connected to {label}",
            "hints": "Interactive SSH shell — close this window to disconnect.",
        })

        channel = client.invoke_shell(term="xterm-256color", width=120, height=32)
        channel.settimeout(0.0)

        await _pump_channel(websocket, channel)
    finally:
        try:
            client.close()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass


# Shell pipeline that owns /dev/{rshim_device}/console for the duration
# of the SSH session. No embedded single quotes — so the same string
# works standalone (BMC, runs as root) or wrapped in `sudo -n bash -c '…'`
# (in-band host, non-root user with passwordless sudo). Logic:
#   1. Kick any stale opener off the device.
#   2. Wait for the rshim driver's post-release window to settle (probe).
#   3. Put the allocated PTY in raw mode.
#   4. Open one bidirectional fd on the console; reader + writer share it.
def _console_pipeline(rshim_device: str = "rshim0") -> str:
    console = f"/dev/{rshim_device}/console"
    return (
        f"(fuser -k {console} 2>/dev/null || true); "
        "for i in 1 2 3 4 5 6 7 8 9 10; do "
        f"  fuser {console} >/dev/null 2>&1 || break; "
        "  sleep 0.2; "
        "done; "
        "probe_ok=0; "
        "for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do "
        f"  if (exec 8<>{console}) 2>/dev/null; then "
        "    probe_ok=1; break; "
        "  fi; "
        "  sleep 0.25; "
        "done; "
        "if [ $probe_ok -ne 1 ]; then "
        "  echo rshim console still busy after retries - try again >&2; "
        "  exit 13; "
        "fi; "
        "stty raw -echo -icanon min 1 time 0 2>/dev/null; "
        f"exec 7<>{console}; "
        "cat <&7 & "
        "cat >&7; "
        "kill %1 2>/dev/null"
    )


# Backward-compat alias for the BMC path (always rshim0).
_CONSOLE_PIPELINE = _console_pipeline("rshim0")


async def _run_serial_console(
    websocket: WebSocket, bmc_ip: str, user: str, password: str,
    jumphost_cred: dict | None = None,
) -> None:
    import paramiko

    from services.dpu_tunnel import ssh_connect_to_bmc

    loop = asyncio.get_running_loop()

    def _connect(pw: str) -> paramiko.SSHClient:
        return ssh_connect_to_bmc(
            bmc_ip, username=user, password=pw,
            jumphost_cred=jumphost_cred, timeout=15,
        )

    try:
        client: paramiko.SSHClient = await loop.run_in_executor(None, _connect, password)
    except paramiko.ssh_exception.AuthenticationException:
        if password == _NVIDIA_DEFAULT_BMC_PASSWORD:
            await websocket.send_json({"type": "error", "message": "BMC SSH authentication failed."})
            await websocket.close()
            return
        try:
            client = await loop.run_in_executor(None, _connect, _NVIDIA_DEFAULT_BMC_PASSWORD)
        except Exception as exc:
            await websocket.send_json({
                "type": "error",
                "message": f"BMC SSH auth failed (and Nvidia default password also rejected): {exc}",
            })
            await websocket.close()
            return
    except Exception as exc:
        await websocket.send_json({
            "type": "error",
            "message": f"Could not connect to BMC at {bmc_ip}: {exc}",
        })
        await websocket.close()
        return

    try:
        # Prep rshim.
        await websocket.send_json({"type": "stdout", "data": _b64("\x1b[36m[bnk-forge] Checking rshim on BMC...\x1b[0m\r\n")})
        from services.rshim_service import (
            RshimNotReadyError,
            ensure_rshim_active_on_bmc,
        )
        try:
            await loop.run_in_executor(
                None,
                lambda: ensure_rshim_active_on_bmc(client, device="console"),
            )
        except RshimNotReadyError as exc:
            await websocket.send_json({"type": "error", "message": str(exc)})
            await websocket.close()
            return
        await websocket.send_json({"type": "stdout", "data": _b64("\x1b[32m[bnk-forge] rshim is active; /dev/rshim0/console is available.\x1b[0m\r\n")})
        await websocket.send_json({
            "type": "connected",
            "message": "Attached to /dev/rshim0/console",
            "hints": "Raw tty — all keystrokes go to the DPU. Close this window to disconnect.",
        })

        # Open a PTY channel. We deliberately avoid `screen` (the BusyBox
        # build on many BlueField BMCs lacks /var/run/utmp and bails with
        # `[screen is terminating]`). Instead, put the pty in raw mode and
        # bridge stdin↔console with two cat processes:
        #   stty raw…        — stop the pty from cooking/echoing keystrokes
        #   cat console &    — reader: device → pty stdout → WS
        #   cat > console    — writer: WS → pty stdin → device
        # When the WS closes, paramiko closes the channel, the BMC pty
        # gets SIGHUP, and both cats terminate.
        transport = client.get_transport()
        if transport is None:
            raise RuntimeError("SSH transport unavailable")
        channel = transport.open_session()
        channel.get_pty(term="xterm-256color", width=120, height=32)
        channel.exec_command(_CONSOLE_PIPELINE)
        channel.settimeout(0.0)  # non-blocking reads

        await _pump_channel(websocket, channel)
    finally:
        try:
            client.close()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass


async def _run_serial_console_inband(
    websocket: WebSocket, project_id: int, dpu_id: int,
) -> None:
    """In-band serial console: SSH to the host (via jumphost if any),
    then `sudo -n bash -c <_CONSOLE_PIPELINE>` since /dev/rshim0/* is
    root-only on typical Ubuntu hosts. All credential resolution —
    inline host_ssh_* fields, saved SSHCredential FK, jumphost via
    direct-tcpip — is handled by RshimService's public helper.
    """
    from database import SessionLocal

    loop = asyncio.get_running_loop()

    def _connect():
        from models.dpu import Dpu
        from services.rshim_service import open_inband_host_ssh

        db: Session = SessionLocal()
        try:
            dpu = (
                db.query(Dpu)
                .filter(Dpu.id == dpu_id, Dpu.project_id == project_id)
                .first()
            )
            if dpu is None:
                raise RuntimeError(f"DPU {dpu_id} not found")
            rshim_device = (dpu.rshim_device or "rshim0")
            return open_inband_host_ssh(db, dpu), dpu.host_node_ip, rshim_device
        finally:
            db.close()

    try:
        client, host_ip, rshim_device = await loop.run_in_executor(None, _connect)
    except Exception as exc:  # noqa: BLE001
        await websocket.send_json({
            "type": "error",
            "message": f"Could not SSH to host for in-band console: {exc}",
        })
        await websocket.close()
        return

    try:
        # Prep rshim on the host (sudo-aware).
        await websocket.send_json({
            "type": "stdout",
            "data": _b64(f"\x1b[36m[bnk-forge] Checking rshim on host {host_ip}...\x1b[0m\r\n"),
        })
        ok, message = await loop.run_in_executor(
            None, _prepare_rshim_inband, client, rshim_device,
        )
        if not ok:
            await websocket.send_json({"type": "error", "message": message})
            await websocket.close()
            return
        await websocket.send_json({
            "type": "stdout",
            "data": _b64(f"\x1b[32m[bnk-forge] {message}\x1b[0m\r\n"),
        })
        await websocket.send_json({
            "type": "connected",
            "message": f"Attached to /dev/{rshim_device}/console on {host_ip}",
            "hints": "Raw tty — all keystrokes go to the DPU. Close this window to disconnect.",
        })

        transport = client.get_transport()
        if transport is None:
            raise RuntimeError("SSH transport unavailable")
        channel = transport.open_session()
        channel.get_pty(term="xterm-256color", width=120, height=32)
        # Wrap the per-DPU pipeline in `sudo -n bash -c` — /dev/rshimN/*
        # is root-only on Ubuntu. Single-quote wrapping is safe because
        # _console_pipeline output contains no apostrophes.
        channel.exec_command(f"sudo -n bash -c '{_console_pipeline(rshim_device)}'")
        channel.settimeout(0.0)

        await _pump_channel(websocket, channel)
    finally:
        try:
            client.close()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass


async def _run_ssh_os_inband(
    websocket: WebSocket, project_id: int, dpu_id: int,
) -> None:
    """Open an interactive SSH shell into an in-band DPU's OS.

    The tmfifo address is only reachable from the host the DPU sits in.
    Flow:

        forge → [jumphost →] host → 192.168.{100+rshim_idx}.2

    Hops 1+2 come from RshimService.open_inband_host_ssh (inherits the
    project jumphost when configured). Hop 3 opens a second direct-tcpip
    channel over the host SSH to the per-DPU tmfifo IP:22 and runs SSH
    as the DPU-OS default user through that channel. Paramiko transports
    nest cleanly, so both hop styles work with the same code.
    """
    import paramiko

    from database import SessionLocal

    loop = asyncio.get_running_loop()

    def _connect() -> tuple[paramiko.SSHClient, paramiko.SSHClient, str]:
        """Return (dpu_client, host_client, os_ip). Both clients must be
        closed by the caller when the websocket disconnects."""
        from core.encryption import decrypt_value_or_none
        from models.dpu import Dpu, ProjectDpuSettings
        from services.bf_conf_renderer import derive_tmfifo_dpu_host
        from services.rshim_service import open_inband_host_ssh

        db: Session = SessionLocal()
        try:
            dpu = (
                db.query(Dpu)
                .filter(Dpu.id == dpu_id, Dpu.project_id == project_id)
                .first()
            )
            if dpu is None:
                raise RuntimeError(f"DPU {dpu_id} not found")

            settings_row = (
                db.query(ProjectDpuSettings)
                .filter(ProjectDpuSettings.project_id == project_id)
                .first()
            )
            os_user = settings_row.default_os_username if settings_row else None
            os_pw = (
                decrypt_value_or_none(settings_row.default_os_password_encrypted)
                if settings_row and settings_row.default_os_password_encrypted
                else None
            )
            if not os_user or not os_pw:
                raise RuntimeError(
                    "DPU OS credentials not set — fill in "
                    "'DPU OS Default Username' and 'DPU OS Default Password' "
                    "in the project's DPU settings."
                )

            # Prefer whatever the probe captured; fall back to the per-DPU
            # tmfifo IP derived from the rshim index for multi-DPU hosts.
            os_ip = dpu.dpu_os_ip or derive_tmfifo_dpu_host(dpu.rshim_device, dpu=dpu)

            host_client = open_inband_host_ssh(db, dpu)
            try:
                transport = host_client.get_transport()
                if transport is None:
                    raise RuntimeError("host SSH transport unavailable")
                chan = transport.open_channel(
                    "direct-tcpip", (os_ip, 22), ("127.0.0.1", 0), timeout=15,
                )
            except Exception:
                host_client.close()
                raise

            dpu_client = paramiko.SSHClient()
            dpu_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                dpu_client.connect(
                    os_ip, username=os_user, password=os_pw,
                    sock=chan,
                    look_for_keys=False, allow_agent=False, timeout=15,
                )
            except Exception:
                try:
                    chan.close()
                except Exception:
                    pass
                host_client.close()
                raise
            return dpu_client, host_client, os_ip
        finally:
            db.close()

    try:
        dpu_client, host_client, os_ip = await loop.run_in_executor(None, _connect)
    except Exception as exc:  # noqa: BLE001
        await websocket.send_json({
            "type": "error",
            "message": f"Could not SSH to DPU OS: {exc}",
        })
        await websocket.close()
        return

    try:
        await websocket.send_json({
            "type": "connected",
            "message": f"Connected to DPU OS {os_ip} via host {host_client.get_transport().getpeername()[0] if host_client.get_transport() else '?'}",
            "hints": "Interactive SSH shell — close this window to disconnect.",
        })
        channel = dpu_client.invoke_shell(term="xterm-256color", width=120, height=32)
        channel.settimeout(0.0)
        await _pump_channel(websocket, channel)
    finally:
        try:
            dpu_client.close()
        except Exception:
            pass
        try:
            host_client.close()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass


def _prepare_rshim_inband(client, rshim_device: str = "rshim0") -> tuple[bool, str]:
    """Check rshim on an Ubuntu host (sudo-aware). Returns (ok, message).

    `rshim_device` selects which `/dev/rshimN` to test on multi-DPU hosts.
    On failure, directs the operator to the `Install rshim + MFT` action
    rather than spelling out remediation — that dialog handles the
    per-step install flow.
    """
    def run(cmd: str, timeout: int = 10) -> tuple[int, str, str]:
        stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        rc = stdout.channel.recv_exit_status()
        return rc, out.strip(), err.strip()

    _, state, _ = run("sudo -n systemctl is-active rshim 2>/dev/null; true")
    if state != "active":
        run("sudo -n systemctl start rshim", timeout=15)
        run("sleep 2")
        _, state, _ = run("sudo -n systemctl is-active rshim 2>/dev/null; true")

    console = f"/dev/{rshim_device}/console"
    rc, _, _ = run(f"sudo -n test -e {console}")
    if rc == 0:
        return True, f"rshim is active on host (state={state}); {console} available."

    _, journal, _ = run(
        "sudo -n journalctl -u rshim -n 40 --no-pager 2>/dev/null | tail -n 20; true",
        timeout=15,
    )
    return False, (
        f"rshim state={state!r} but {console} is not present on the host. "
        "Use Actions → Install rshim + MFT to finish setup.\n\nRecent host rshim log:\n"
        + journal
    )


async def _pump_channel(websocket: WebSocket, channel) -> None:
    """Bridge bytes between a paramiko Channel and the WebSocket until
    either side closes."""
    async def pump_out():
        while not channel.closed:
            await asyncio.sleep(0.02)
            try:
                if channel.recv_ready():
                    data = channel.recv(4096)
                    if data:
                        await websocket.send_json({
                            "type": "stdout",
                            "data": base64.b64encode(data).decode("ascii"),
                        })
                if channel.recv_stderr_ready():
                    data = channel.recv_stderr(4096)
                    if data:
                        await websocket.send_json({
                            "type": "stderr",
                            "data": base64.b64encode(data).decode("ascii"),
                        })
                if channel.exit_status_ready() and not channel.recv_ready():
                    break
            except Exception as exc:  # noqa: BLE001
                logger.debug("ssh-pump recv loop error: %s", exc)
                break
        try:
            code = channel.recv_exit_status() if channel.exit_status_ready() else 0
            await websocket.send_json({"type": "exit", "code": code})
        except Exception:
            pass

    async def pump_in():
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            mtype = msg.get("type")
            if mtype == "stdin":
                try:
                    data = base64.b64decode(msg.get("data", ""))
                except Exception:
                    continue
                if data:
                    channel.send(data)
            elif mtype == "resize":
                rows = int(msg.get("rows", 32))
                cols = int(msg.get("cols", 120))
                try:
                    channel.resize_pty(width=cols, height=rows)
                except Exception:
                    pass

    try:
        await asyncio.wait(
            [asyncio.create_task(pump_out()), asyncio.create_task(pump_in())],
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        try:
            channel.close()
        except Exception:
            pass


def _b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")
