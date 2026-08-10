"""DPU factory reset — BIOS + BMC + NIC mlxconfig.

A "Reset to Defaults" for BlueField-3 DPUs that have drifted into unknown
state (wrong NicMode, stale BMC users, mlxconfig tweaks the operator no
longer remembers). Three independent layers, each addressable from
Redfish or from the DPU OS:

  1. BIOS attributes (HostPrivilegeLevel, NicMode, InternalCPUModel,
     EnableSMMU, FieldMode, SPCR_UART, …) — `Bios.ResetBios` via
     Redfish. Takes effect on the next power cycle.
  2. BMC runtime-writable filesystem (users, network config, SSH host
     keys, certs) — `Manager.ResetToDefaults` with ResetType=ResetAll.
     BMC reboots into factory state; `root` reverts to `0penBmc` (the
     Phase 3 credential helper then auto-re-PATCHes the configured
     password on the next Forge access).
  3. NIC firmware settings (mlxconfig NVRAM: SRIOV_EN, NUM_OF_VFS,
     INTERNAL_CPU_MODEL, LAG_RESOURCE_ALLOCATION, PER_PF_NUM_SF, …).
     These are NOT wiped by the BMC reset — they live on the NIC
     silicon. Requires `mst` + `mlxconfig` from the DPU OS (or host), so
     we can only run this when `dpu_os_status == 'reachable'`. If the
     DPU OS is unreachable the caller should instead run Flash BFB,
     which includes an `mlxconfig reset` in bfb_post_install.

Execution order (when all three are requested):

  a. NIC mlxconfig via SSH to DPU OS — do this FIRST, while the OS is
     still responsive.
  b. BIOS reset via Redfish (queues the reset for next boot).
  c. PowerCycle via Redfish — applies BIOS reset + kicks the NIC so the
     new mlxconfig values take effect.
  d. BMC reset via Redfish — independent of the DPU boot; BMC reboots
     itself right after.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from core.encryption import decrypt_value_or_none
from core.errors import BadRequestError, NotFoundError
from models.dpu import Dpu, ProjectDpuSettings
from services.dpu_credentials import (
    RedfishError,
    redfish_post_with_bmc_fallback,
)
from services.dpu_tunnel import resolve_project_jumphost_cred
from services.rshim_service import _short_bdf_for_mst

logger = logging.getLogger(__name__)


_BIOS_RESET_URL = "https://{bmc}/redfish/v1/Systems/Bluefield/Bios/Actions/Bios.ResetBios"
# Nvidia BlueField-3 advertises its BMC as `Bluefield_BMC`, not the Redfish
# default `Bmc`. Hitting the wrong Manager ID returns HTTP 404 with
# "resource of type Manager named 'Bmc' was not found".
_BMC_RESET_URL = "https://{bmc}/redfish/v1/Managers/Bluefield_BMC/Actions/Manager.ResetToDefaults"
# Nvidia OEM "deep" variant — same endpoint family, but the OEM action
# clears more state (OEM settings, some persistent caches) than the
# Redfish-standard ResetToDefaults does. No ResetType parameter.
_BMC_RESET_URL_OEM = (
    "https://{bmc}/redfish/v1/Managers/Bluefield_BMC/Actions/Oem/NvidiaManager.ResetToDefaults"
)
_SYSTEM_RESET_URL = "https://{bmc}/redfish/v1/Systems/Bluefield/Actions/ComputerSystem.Reset"


@dataclass
class FactoryResetOptions:
    bios: bool = True
    bmc: bool = True
    nic: bool = True
    # When True AND bmc=True, use the Nvidia OEM "deep" reset variant
    # (clears more state than the Redfish-standard action). Ignored when
    # bmc=False.
    bmc_deep: bool = False


@dataclass
class StepResult:
    name: str
    ran: bool
    ok: bool
    message: str = ""


@dataclass
class FactoryResetSummary:
    dpu_id: int
    bmc_ip: str | None
    steps: list[StepResult]

    def to_dict(self) -> dict:
        return {
            "dpu_id": self.dpu_id,
            "bmc_ip": self.bmc_ip,
            "steps": [vars(s) for s in self.steps],
            "ok": all(s.ok for s in self.steps if s.ran),
        }


class DpuFactoryResetService:
    """Orchestrates a layered factory reset for a single DPU."""

    def __init__(self, db: Session, *, ssh_runner=None):
        self.db = db
        # Injectable SSH runner for tests. Signature:
        #   (host, user, password, command) -> str stdout.
        self._ssh_runner = ssh_runner
        self._jumphost_cred: dict | None = None

    def run(self, *, project_id: int, dpu_id: int, opts: FactoryResetOptions) -> FactoryResetSummary:
        dpu = self._get_dpu(project_id, dpu_id)
        if dpu.access_mode == "in-band":
            return self._run_inband(dpu, opts)
        user, pw = self._resolve_bmc_credentials(dpu)
        self._jumphost_cred = resolve_project_jumphost_cred(self.db, project_id)

        summary = FactoryResetSummary(dpu_id=dpu.id, bmc_ip=dpu.bmc_ip, steps=[])

        # Track whether a power cycle is warranted. BIOS reset only applies
        # on next boot; mlxconfig changes only take effect after a NIC reset
        # (which a power cycle guarantees). Skipping or failing steps don't
        # warrant a disruptive power cycle.
        needs_power_cycle = False

        # 1. NIC mlxconfig via SSH — requires DPU OS reachable.
        if opts.nic:
            nic_step = self._reset_nic(dpu)
            summary.steps.append(nic_step)
            if nic_step.ran and nic_step.ok:
                needs_power_cycle = True

        # 2. BIOS reset via Redfish (takes effect on power cycle).
        if opts.bios:
            bios_step = self._redfish_step(
                name="bios_reset",
                url=_BIOS_RESET_URL.format(bmc=dpu.bmc_ip),
                body={},
                user=user, pw=pw,
            )
            summary.steps.append(bios_step)
            # PowerCycle even on BIOS failure would be wasted — skip it.
            if bios_step.ok:
                needs_power_cycle = True

        # 3. PowerCycle — applies BIOS reset + kicks NIC into re-reading
        #    mlxconfig. Only fire when a preceding step actually succeeded.
        if needs_power_cycle:
            summary.steps.append(
                self._redfish_step(
                    name="power_cycle",
                    url=_SYSTEM_RESET_URL.format(bmc=dpu.bmc_ip),
                    body={"ResetType": "PowerCycle"},
                    user=user, pw=pw,
                )
            )

        # 4. BMC reset — independent of DPU boot. Issued last so it doesn't
        #    cut off the earlier Redfish calls mid-flight.
        if opts.bmc:
            if opts.bmc_deep:
                summary.steps.append(
                    self._redfish_step(
                        name="bmc_reset_to_defaults_oem",
                        url=_BMC_RESET_URL_OEM.format(bmc=dpu.bmc_ip),
                        body={},
                        user=user, pw=pw,
                    )
                )
            else:
                summary.steps.append(
                    self._redfish_step(
                        name="bmc_reset_to_defaults",
                        url=_BMC_RESET_URL.format(bmc=dpu.bmc_ip),
                        body={"ResetType": "ResetAll"},
                        user=user, pw=pw,
                    )
                )

        return summary

    # ── Individual steps ───────────────────────────────────────────────

    def _redfish_step(
        self, *, name: str, url: str, body: dict, user: str, pw: str,
    ) -> StepResult:
        try:
            resp = redfish_post_with_bmc_fallback(
                _host_from_url(url), user, pw, url=url, json_body=body,
                jumphost_cred=self._jumphost_cred,
            )
        except RedfishError as exc:
            logger.warning(
                "Factory reset step %s failed (transport): %s", name, exc,
            )
            return StepResult(name=name, ran=True, ok=False, message=f"Redfish transport: {exc}")

        if 200 <= resp.status_code < 400:
            return StepResult(name=name, ran=True, ok=True, message=f"HTTP {resp.status_code}")
        # Log the full Redfish body — the UI truncates to 200 chars for brevity.
        logger.warning(
            "Factory reset step %s rejected by BMC: HTTP %s — url=%s body=%s response=%s",
            name, resp.status_code, url, body, resp.text[:1000],
        )
        return StepResult(
            name=name, ran=True, ok=False,
            message=f"HTTP {resp.status_code}: {resp.text[:200]}",
        )

    def _reset_nic(self, dpu: Dpu) -> StepResult:
        if dpu.dpu_os_status != "reachable" or not dpu.dpu_os_ip:
            return StepResult(
                name="nic_mlxconfig_reset",
                ran=False,
                ok=False,
                message=(
                    "Skipped — DPU OS is not reachable. Run Flash BFB+FW+CFG "
                    "to reset NIC firmware via bfb_post_install."
                ),
            )

        settings = (
            self.db.query(ProjectDpuSettings)
            .filter(ProjectDpuSettings.project_id == dpu.project_id)
            .first()
        )
        if settings is None or not settings.default_os_username or not settings.default_os_password_encrypted:
            return StepResult(
                name="nic_mlxconfig_reset",
                ran=False,
                ok=False,
                message="Skipped — project DPU settings missing default OS credentials.",
            )
        os_pw = decrypt_value_or_none(settings.default_os_password_encrypted)
        if not os_pw:
            return StepResult(
                name="nic_mlxconfig_reset", ran=False, ok=False,
                message="Skipped — could not decrypt default OS password.",
            )

        # Use sudo so the mst + mlxconfig + mlxprivhost work as non-root.
        # `mlxconfig -y -d <dev> reset` restores NVRAM defaults.
        # `mlxprivhost -d <dev> p` re-enables privileged host (exits Zero-Trust).
        cmd = (
            "set -e; "
            "sudo mst start >/dev/null 2>&1; "
            "for dev in /dev/mst/mt*pciconf*; do "
            "  echo Resetting $dev; "
            "  sudo mlxconfig -y -d $dev reset; "
            "  sudo mlxprivhost -d $dev p; "
            "done"
        )
        try:
            out = self._run_ssh(
                host=dpu.dpu_os_ip, user=settings.default_os_username,
                password=os_pw, command=cmd,
            )
        except Exception as exc:  # noqa: BLE001
            return StepResult(
                name="nic_mlxconfig_reset", ran=True, ok=False,
                message=f"SSH/mlxconfig failed: {type(exc).__name__}: {exc}",
            )
        return StepResult(
            name="nic_mlxconfig_reset", ran=True, ok=True,
            message=(out[:200] + "…") if len(out) > 200 else out,
        )

    # ── In-band reset ──────────────────────────────────────────────────

    def _run_inband(self, dpu: Dpu, opts: FactoryResetOptions) -> FactoryResetSummary:
        """In-band equivalent of "Reset to Defaults".

        BMC and BIOS reset options are skipped (there's no BMC; BIOS is
        reachable only via Redfish). The NIC option runs mlxconfig reset
        on the host using the DPU's MST device, followed by an rshim
        SW_RESET to apply. Power-cycle-equivalent is likewise rshim
        SW_RESET (a PCI-level reset would require mlxfwreset and is
        covered by the regular Reset → Power Cycle action).
        """
        from services.rshim_service import open_inband_host_ssh

        summary = FactoryResetSummary(dpu_id=dpu.id, bmc_ip=None, steps=[])

        if opts.bmc:
            summary.steps.append(StepResult(
                name="bmc_reset_to_defaults",
                ran=False, ok=True,
                message="skipped — in-band DPUs have no BMC",
            ))
        if opts.bios:
            summary.steps.append(StepResult(
                name="bios_reset",
                ran=False, ok=True,
                message="skipped — BIOS reset requires Redfish, not available in-band",
            ))

        if not opts.nic:
            return summary

        try:
            host_client = open_inband_host_ssh(self.db, dpu)
        except Exception as exc:  # noqa: BLE001
            summary.steps.append(StepResult(
                name="nic_mlxconfig_reset", ran=True, ok=False,
                message=f"SSH to host failed: {exc}",
            ))
            return summary

        try:
            pci = dpu.pci_address or ""
            if not pci:
                summary.steps.append(StepResult(
                    name="nic_mlxconfig_reset", ran=True, ok=False,
                    message="DPU has no pci_address — cannot resolve MST device",
                ))
                return summary

            cmd = (
                "set -e; "
                f"MSTDEV=$(sudo -n mst status -v 2>/dev/null | "
                f"awk -v bdf='{_short_bdf_for_mst(pci)}' "
                "'$0 ~ bdf {for (i=1;i<=NF;i++) "
                "if ($i ~ /^\\/dev\\/mst\\//) print $i}' | head -1); "
                "if [ -z \"$MSTDEV\" ]; then "
                "  echo 'MST device not resolved — run Install rshim + MFT first' >&2; "
                "  exit 2; "
                "fi; "
                "sudo -n mlxconfig -d \"$MSTDEV\" -y reset"
            )
            _stdin, stdout, stderr = host_client.exec_command(cmd, timeout=90)
            rc = stdout.channel.recv_exit_status()
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            if rc != 0:
                summary.steps.append(StepResult(
                    name="nic_mlxconfig_reset", ran=True, ok=False,
                    message=(err or out).strip()[:400] or f"exit={rc}",
                ))
                return summary
            summary.steps.append(StepResult(
                name="nic_mlxconfig_reset", ran=True, ok=True,
                message=(out[:200] + "…") if len(out) > 200 else out.strip(),
            ))

            # Apply by rebooting the DPU via rshim SW_RESET on this DPU's
            # rshim device. Multi-DPU hosts expose rshim0/rshim1/...; using
            # rshim0 unconditionally would reset the wrong DPU.
            rshim = (getattr(dpu, "rshim_device", None) or "rshim0")
            _stdin, stdout, stderr = host_client.exec_command(
                f"sudo -n sh -c 'echo SW_RESET 1 > /dev/{rshim}/misc'",
                timeout=20,
            )
            rc = stdout.channel.recv_exit_status()
            err = stderr.read().decode("utf-8", errors="replace")
            summary.steps.append(StepResult(
                name="power_cycle",
                ran=True,
                ok=(rc == 0),
                message=(
                    "rshim SW_RESET issued — DPU rebooting with factory mlxconfig"
                    if rc == 0 else (err.strip() or f"exit={rc}")
                ),
            ))
        finally:
            try:
                host_client.close()
            except Exception:
                pass

        return summary

    # ── Helpers ────────────────────────────────────────────────────────

    def _get_dpu(self, project_id: int, dpu_id: int) -> Dpu:
        dpu = (
            self.db.query(Dpu)
            .filter(Dpu.id == dpu_id, Dpu.project_id == project_id)
            .first()
        )
        if dpu is None:
            raise NotFoundError("Dpu", dpu_id)
        return dpu

    def _resolve_bmc_credentials(self, dpu: Dpu) -> tuple[str, str]:
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
                self.db.query(ProjectDpuSettings)
                .filter(ProjectDpuSettings.project_id == dpu.project_id)
                .first()
            )
            if settings:
                user = user or settings.default_oob_username
                if not pw and settings.default_oob_password_encrypted:
                    pw = decrypt_value_or_none(settings.default_oob_password_encrypted)
        if not user or not pw:
            raise BadRequestError(
                "No BMC credentials — set per-DPU creds or project defaults."
            )
        return user, pw

    def _run_ssh(self, *, host: str, user: str, password: str, command: str) -> str:
        if self._ssh_runner is not None:
            return self._ssh_runner(host, user, password, command)
        return _paramiko_exec(host, user, password, command)


# ── Module-scoped helpers ──────────────────────────────────────────────────

def _host_from_url(url: str) -> str:
    # Strip `https://` and anything after the first `/`.
    return url.split("/", 3)[2]


def _paramiko_exec(host: str, user: str, password: str, command: str) -> str:
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            host, username=user, password=password, timeout=20,
            allow_agent=False, look_for_keys=False,
        )
        _stdin, stdout, stderr = client.exec_command(command, timeout=120)
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        rc = stdout.channel.recv_exit_status()
        if rc != 0:
            raise RuntimeError(f"exit={rc} stderr={err.strip()[:200]}")
        return out
    finally:
        client.close()
