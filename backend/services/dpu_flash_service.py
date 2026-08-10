"""DPU Flash engine — end-to-end orchestration of "Flash BFB, Firmware & config".

Flow per DPU:

  1. Resolve dependencies: DPU row, ProjectDpuSettings, BluefieldSoftwareImage,
     BfConfTemplate, SSHCredential (public key for authorized_keys), DPU-OS
     password plaintext (for the Jinja render).
  2. Render bf.conf via the Jinja renderer.
  3. Lint the rendered bf.conf (bash -n + YAML heredoc validation). Abort
     on any issue — we never SFTP broken config to a DPU.
  4. Ensure the BFB bundle is in the local cache (download on miss).
  5. SSH into the BMC (with 0penBmc fallback + auto-PATCH of configured
     password), open an SFTP session.
  6. Stream: concatenated `<BFB>` + `<bf.cfg>` bytes to
     `/dev/rshim0/boot`. The rshim driver consumes the stream and reboots
     the DPU into the BFB installer.
  7. Mark `flash_status='waiting_reboot'`. The DPU-side installer takes
     5-10 minutes; Phase 7 adds the "wait until SSH on DPU OS is back"
     poller that promotes to `os_online`.

State machine (column: `dpu.flash_status`):

  null → uploading → waiting_reboot
                   ↘ failed
                   ↘ cancelled (Celery revoke)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from core.encryption import decrypt_value, decrypt_value_or_none
from core.errors import BadRequestError, NotFoundError
from models.dpu import BfConfTemplate, BluefieldSoftwareImage, Dpu, ProjectDpuSettings
from models.ssh_credential import SSHCredential
from services.bf_conf_renderer import (
    BfConfRenderError,
    bfb_with_config_stream,
    build_render_context,
    lint_bf_conf,
    render_bf_conf,
)
from services.bfb_cache_service import BfbCacheError, BfbCacheService
from services.dpu_credentials import ssh_connect_bmc_with_fallback
from services.dpu_tunnel import resolve_project_jumphost_cred

logger = logging.getLogger(__name__)


# The BlueField BMC's rshim device. Writing to this path hands the bytes to
# the rshim kernel driver, which in turn boots the DPU into the installer.
# BMC-mode DPUs always see their own DPU as `/dev/rshim0` (a BMC sees only
# its own SoC); only the in-band path needs per-DPU `/dev/rshimN` selection.
RSHIM_BOOT_PATH = "/dev/rshim0/boot"


def _inband_rshim_device(dpu: Dpu) -> str:
    """Return the rshim device name for an in-band DPU, defaulting safely."""
    return (getattr(dpu, "rshim_device", None) or "rshim0")


def _refuse_if_ib_mode(dpu: Dpu) -> None:
    """Raise DpuFlashError if the DPU's ports are in InfiniBand mode.

    The seeded bf.conf templates render Ethernet-only netplan, so flashing
    an IB DPU produces unusable network config (`p0`/`p1` won't come up
    as Ethernet interfaces). This is a defence-in-depth check — the UI
    already gates flash on IB rows, but alternate paths (MCP, curl,
    operator just flipped link mode and the badge hasn't refreshed yet)
    can still reach this code.
    """
    # Lazy import to avoid a circular reference between dpu_flash_service
    # and dpu_service (the renderer + flash module each pull from the
    # other for unrelated helpers).
    from services.dpu_service import _derive_link_mode

    mode = _derive_link_mode(getattr(dpu, "last_discovery_payload", None))
    if mode in ("ib", "mixed"):
        raise DpuFlashError(
            f"DPU {dpu.id} is in {'InfiniBand' if mode == 'ib' else 'mixed'} "
            "link mode. The seeded bf.conf templates assume Ethernet "
            "uplinks (p0/p1) and will produce broken networking on this "
            "DPU. Use Actions → Convert to Ethernet… first, then re-run "
            "Discover to confirm the link mode flipped, then flash."
        )

# A 4 MiB SFTP window — matches the renderer's stream chunk and keeps
# ~2 GiB uploads within a reasonable number of round-trips.
_SFTP_CHUNK_BYTES = 4 * 1024 * 1024

# Soft stages written to `flash_stage_detail` so the UI can show meaningful
# progress. Exposed as module constants so tests can match exactly.
STAGE_PREPARING = "preparing"
STAGE_RENDERING = "rendering bf.conf"
STAGE_CACHING_BFB = "caching BFB"
STAGE_RESETTING = "resetting DPU into BFB-boot mode"
STAGE_UPLOADING = "uploading BFB+bf.conf to rshim"
STAGE_WAITING_REBOOT = "waiting for DPU to reboot"

# Seconds to wait after SW_RESET before streaming the BFB. The DPU needs
# time to enter rshim-boot mode; push too early and the stream is dropped.
_RSHIM_RESET_SETTLE_S = 8


class DpuFlashError(Exception):
    """Raised by the service when a flash cannot proceed / was aborted."""


@dataclass
class FlashResources:
    dpu: Dpu
    settings: ProjectDpuSettings
    # None when used by the render-only path, which doesn't touch the cache.
    bfb_image: BluefieldSoftwareImage | None
    bf_template: BfConfTemplate
    ssh_cred: SSHCredential | None
    bmc_user: str
    bmc_password: str
    os_password_plaintext: str


class DpuFlashService:
    """One-shot flash orchestrator. Instantiate per flash attempt."""

    def __init__(self, db: Session, *, bfb_cache: BfbCacheService | None = None):
        self.db = db
        self._bfb_cache = bfb_cache or BfbCacheService()
        # Throttle progress DB commits to at most once every N seconds so the
        # UI can show smooth progress without hammering Postgres during a
        # multi-minute rshim SFTP.
        self._last_progress_commit: float = 0.0

    # ── Public API ─────────────────────────────────────────────────────

    def render_only(self, *, project_id: int, dpu_id: int) -> str:
        """Resolve → render → lint and persist the rendered bf.conf without
        flashing. Returns the content. Raises DpuFlashError on render/lint
        failure so the UI can surface the exact issue.

        Uses the lenient resolver — rendering only needs the template,
        OS credentials, and the DPU row, not the BFB image or BMC creds.
        That means operators can preview bf.conf before they've chosen a
        BFB or filled in per-DPU BMC creds.
        """
        dpu = self._get_dpu(project_id, dpu_id)
        resources = self._resolve_for_render(dpu)
        return self._render_and_lint(resources)

    def flash(self, *, project_id: int, dpu_id: int) -> dict:
        """Run the whole flash synchronously. Called by the Celery task.

        Never raises on user-correctable failures — every failure mode
        lands on the DPU row so the UI can surface it and the operator
        can retry. Raises only on truly unexpected programmer errors.
        """
        dpu = self._get_dpu(project_id, dpu_id)
        self._mark_stage(dpu, "uploading", STAGE_PREPARING, error=None)
        dpu.flash_started_at = datetime.now(UTC)
        dpu.flash_completed_at = None
        self.db.commit()

        try:
            resources = self._resolve(dpu)
            self._mark_stage(dpu, "uploading", STAGE_RENDERING)
            bf_cfg = self._render_and_lint(resources)
            self._mark_stage(dpu, "uploading", STAGE_CACHING_BFB)
            cached = self._ensure_bfb_cached(resources.bfb_image)
            self._mark_stage(dpu, "uploading", STAGE_UPLOADING)
            self._stream_to_rshim(resources, cached.path, bf_cfg)
            self._mark_stage(dpu, "waiting_reboot", STAGE_WAITING_REBOOT, completed=True)
            return {"dpu_id": dpu.id, "status": "waiting_reboot"}
        except DpuFlashError as exc:
            self._record_failure(dpu, str(exc))
            return {"dpu_id": dpu.id, "status": "failed", "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 — never let a bug leave the row in `uploading`
            logger.exception("Unexpected DPU flash error for dpu=%s", dpu_id)
            self._record_failure(dpu, f"Unexpected: {type(exc).__name__}: {exc}")
            return {"dpu_id": dpu.id, "status": "failed", "error": str(exc)}

    # ── Resource resolution ────────────────────────────────────────────

    def _get_dpu(self, project_id: int, dpu_id: int) -> Dpu:
        dpu = (
            self.db.query(Dpu)
            .filter(Dpu.id == dpu_id, Dpu.project_id == project_id)
            .first()
        )
        if dpu is None:
            raise NotFoundError("Dpu", dpu_id)
        return dpu

    def _count_inband_peers(self, dpu: Dpu) -> int:
        """Number of in-band DPUs registered on this DPU's host.

        Used by the renderer to decide whether to append a numeric
        suffix to the DPU hostname. Returns 1 for BMC DPUs and for
        in-band DPUs without a host_node_ip recorded — both cases
        render with the legacy single-DPU hostname.
        """
        if dpu.access_mode != "in-band" or not dpu.host_node_ip:
            return 1
        return (
            self.db.query(Dpu)
            .filter(
                Dpu.project_id == dpu.project_id,
                Dpu.access_mode == "in-band",
                Dpu.host_node_ip == dpu.host_node_ip,
            )
            .count()
        )

    def _resolve(self, dpu: Dpu) -> FlashResources:
        # Refuse to flash a DPU whose ports are in InfiniBand mode.
        # The seeded bf.conf templates render Ethernet-only netplan
        # (`p0` / `p1` ethernets, OVS bridges, LAG over Ethernet) — pushing
        # them to an IB-mode DPU produces broken networking that won't
        # come up after reboot. The DPU tab gates flash in the UI, but
        # this guards alternate paths (curl, MCP, racy state when the
        # operator just flipped the link mode and the badge hasn't
        # refreshed yet). Convert to Ethernet first.
        _refuse_if_ib_mode(dpu)

        settings = (
            self.db.query(ProjectDpuSettings)
            .filter(ProjectDpuSettings.project_id == dpu.project_id)
            .first()
        )
        if settings is None:
            raise DpuFlashError(
                "Project DPU settings missing — set BFB image, bf.conf template, "
                "and DPU OS credentials before flashing."
            )
        if settings.bluefield_image_id is None:
            raise DpuFlashError("No BFB image selected in project DPU settings.")
        if settings.bf_template_id is None:
            raise DpuFlashError("No bf.conf template selected in project DPU settings.")
        if not settings.default_os_username or not settings.default_os_password_encrypted:
            raise DpuFlashError(
                "Project DPU settings must include a default DPU OS username and password."
            )

        bfb_image = self.db.get(BluefieldSoftwareImage, settings.bluefield_image_id)
        if bfb_image is None:
            raise DpuFlashError("Selected BFB image no longer exists.")

        bf_template = self.db.get(BfConfTemplate, settings.bf_template_id)
        if bf_template is None:
            raise DpuFlashError("Selected bf.conf template no longer exists.")

        ssh_cred: SSHCredential | None = None
        if settings.default_os_ssh_credential_id is not None:
            ssh_cred = self.db.get(SSHCredential, settings.default_os_ssh_credential_id)

        os_password = decrypt_value_or_none(settings.default_os_password_encrypted)
        if not os_password:
            raise DpuFlashError("Could not decrypt DPU OS password.")

        # Access-mode branch: BMC DPUs resolve Redfish/SSH-to-BMC creds;
        # in-band DPUs skip BMC entirely and the uploader SSHes to the
        # host instead (see _stream_to_rshim).
        if dpu.access_mode == "in-band":
            # Validate host creds exist up front so we fail fast with a
            # clear message instead of hitting a SSH error deep in the
            # upload flow.
            from core.errors import BadRequestError
            from services.rshim_service import _resolve_host_credential
            try:
                _resolve_host_credential(self.db, dpu)
            except BadRequestError as exc:
                raise DpuFlashError(str(exc)) from exc
            return FlashResources(
                dpu=dpu, settings=settings,
                bfb_image=bfb_image, bf_template=bf_template,
                ssh_cred=ssh_cred,
                bmc_user="", bmc_password="",
                os_password_plaintext=os_password,
            )

        bmc_user, bmc_password = self._resolve_bmc_credentials(dpu, settings)
        return FlashResources(
            dpu=dpu, settings=settings,
            bfb_image=bfb_image, bf_template=bf_template,
            ssh_cred=ssh_cred,
            bmc_user=bmc_user, bmc_password=bmc_password,
            os_password_plaintext=os_password,
        )

    def _resolve_for_render(self, dpu: Dpu) -> FlashResources:
        """Subset of `_resolve` — only what the Jinja render actually needs.

        Skips the BFB-image + BMC-creds checks so the Preview bf.conf
        action works on DPUs that haven't yet got their flash prerequisites
        filled in. Still requires the bf.conf template + DPU OS defaults
        (both are referenced by the template).
        """
        # Same IB guard as the flash path — Preview is the same template
        # render that flash uses, and rendering would produce misleading
        # output for an IB DPU (Ethernet uplinks the operator can't use).
        _refuse_if_ib_mode(dpu)

        settings = (
            self.db.query(ProjectDpuSettings)
            .filter(ProjectDpuSettings.project_id == dpu.project_id)
            .first()
        )
        if settings is None:
            raise DpuFlashError(
                "Render failed: this project has no DPU settings yet. "
                "Open the DPU tab → DPU Project Settings and click "
                "'Save defaults' to initialize them."
            )
        missing: list[str] = []
        if settings.bf_template_id is None:
            missing.append("bf.conf template")
        if not settings.default_os_username:
            missing.append("DPU OS username")
        if not settings.default_os_password_encrypted:
            missing.append("DPU OS password")
        if missing:
            raise DpuFlashError(
                f"Render failed: DPU Project Settings is missing "
                f"{', '.join(missing)}. Open DPU Project Settings, fill the "
                f"highlighted field(s), and click 'Save defaults'."
            )
        bf_template = self.db.get(BfConfTemplate, settings.bf_template_id)
        if bf_template is None:
            raise DpuFlashError(
                "Render failed: the selected bf.conf template no longer exists. "
                "Pick a different template in DPU Project Settings and save."
            )
        ssh_cred: SSHCredential | None = None
        if settings.default_os_ssh_credential_id is not None:
            ssh_cred = self.db.get(SSHCredential, settings.default_os_ssh_credential_id)
        os_password = decrypt_value_or_none(settings.default_os_password_encrypted)
        if not os_password:
            raise DpuFlashError(
                "Render failed: DPU OS password could not be decrypted "
                "(encryption key mismatch). Re-enter the password in DPU "
                "Project Settings and save."
            )
        # At least one configured VLAN row must have an uplink IP assigned —
        # otherwise the rendered bf.conf would have zero network interfaces,
        # which is never what the operator wants.
        has_any_ip = any(
            getattr(dpu, f"{iface}_vlan_ipv4", None)
            or getattr(dpu, f"{iface}_vlan_ipv6", None)
            for iface in ("external", "internal", "storage")
        )
        if not has_any_ip:
            raise DpuFlashError(
                "Render failed: this DPU has no uplink IP addresses yet. "
                "Open the DPU Uplink Interfaces table and click "
                "'Auto-assign' (or fill in the IP columns manually), "
                "then try Preview again."
            )
        return FlashResources(
            dpu=dpu, settings=settings,
            bfb_image=None, bf_template=bf_template,
            ssh_cred=ssh_cred,
            bmc_user="", bmc_password="",
            os_password_plaintext=os_password,
        )

    def _resolve_bmc_credentials(
        self, dpu: Dpu, settings: ProjectDpuSettings,
    ) -> tuple[str, str]:
        user = (
            decrypt_value_or_none(dpu.bmc_username_encrypted)
            if dpu.bmc_username_encrypted else None
        )
        pw = (
            decrypt_value_or_none(dpu.bmc_password_encrypted)
            if dpu.bmc_password_encrypted else None
        )
        if not user:
            user = settings.default_oob_username
        if not pw and settings.default_oob_password_encrypted:
            pw = decrypt_value_or_none(settings.default_oob_password_encrypted)
        if not user or not pw:
            raise DpuFlashError(
                "No BMC credentials — set per-DPU creds or project defaults."
            )
        return user, pw

    # ── Phase steps ────────────────────────────────────────────────────

    def _render_and_lint(self, res: FlashResources) -> str:
        try:
            ctx = build_render_context(
                dpu=res.dpu,
                settings=res.settings,
                ssh_credential=res.ssh_cred,
                ubuntu_password_plaintext=res.os_password_plaintext,
                decrypt=decrypt_value,
                peer_dpu_count=self._count_inband_peers(res.dpu),
            )
            rendered = render_bf_conf(res.bf_template, ctx)
        except BfConfRenderError as exc:
            raise DpuFlashError(f"bf.conf render failed: {exc}") from exc

        # Persist the rendered copy BEFORE linting so the operator can see
        # what was generated even when a lint failure aborts the flash.
        res.dpu.last_rendered_bf_conf = rendered
        res.dpu.last_rendered_bf_conf_at = datetime.now(UTC)
        self.db.commit()

        issues = lint_bf_conf(rendered)
        if issues:
            # Keep the message compact — the full list goes to the task log
            # so we don't blow past the 255-char stage_detail column.
            logger.error("bf.conf lint failed for dpu=%s: %s", res.dpu.id, issues)
            raise DpuFlashError(
                f"bf.conf lint failed ({len(issues)} issue(s)): {issues[0]}"
            )
        return rendered

    def _ensure_bfb_cached(self, bfb: BluefieldSoftwareImage):
        try:
            return self._bfb_cache.get(
                filename=bfb.image_filename,
                base_url=bfb.base_url,
            )
        except BfbCacheError as exc:
            raise DpuFlashError(f"BFB cache/download failed: {exc}") from exc

    def _stream_to_rshim(self, res: FlashResources, bfb_path, bf_cfg: str) -> None:
        """Push BFB + bf.cfg into /dev/rshim0/boot after putting the DPU
        into rshim-boot mode.

        Without the SW_RESET step, a running DPU OS only consumes the
        firmware portions of the BFB — the OS partition never gets
        rewritten and our bf.cfg is ignored. See Nvidia's bfb-install
        source for the canonical sequence.

        For BMC DPUs we SSH to the BMC, whose OpenBMC exposes
        /dev/rshim0/boot locally. For in-band DPUs we SSH to the host
        the DPU is plugged into — same /dev/rshim0/boot path, different
        transport — via jumphost when configured.
        """
        import time

        import paramiko

        is_inband = res.dpu.access_mode == "in-band"
        ssh_target = res.dpu.host_node_ip if is_inband else res.dpu.bmc_ip

        try:
            if is_inband:
                from services.rshim_service import open_inband_host_ssh
                client = open_inband_host_ssh(self.db, res.dpu)
            else:
                jumphost_cred = resolve_project_jumphost_cred(self.db, res.dpu.project_id)
                client = ssh_connect_bmc_with_fallback(
                    res.dpu.bmc_ip, res.bmc_user, res.bmc_password, timeout=20,
                    jumphost_cred=jumphost_cred,
                )
        except paramiko.ssh_exception.AuthenticationException as exc:
            raise DpuFlashError(
                f"SSH auth failed for {ssh_target}: {exc}"
            ) from exc
        except Exception as exc:
            raise DpuFlashError(f"SSH connect failed for {ssh_target}: {exc}") from exc

        try:
            # Make sure rshim is running and the device nodes are present
            # BEFORE issuing SW_RESET + SFTP — gives the operator a precise
            # remediation instead of an opaque "No such file or directory".
            from services.rshim_service import (
                RshimNotReadyError,
                ensure_rshim_active_on_bmc,
                ensure_rshim_active_on_host,
            )
            rshim_device = _inband_rshim_device(res.dpu) if is_inband else "rshim0"
            try:
                if is_inband:
                    ensure_rshim_active_on_host(
                        client, device="misc", rshim_device=rshim_device,
                    )
                else:
                    ensure_rshim_active_on_bmc(client, device="misc")
            except RshimNotReadyError as exc:
                raise DpuFlashError(str(exc)) from exc
            self._mark_stage(res.dpu, "uploading", STAGE_RESETTING)
            self._reset_dpu_into_bfb_mode(
                client, ssh_target or "?",
                use_sudo=is_inband, rshim_device=rshim_device,
            )
            time.sleep(_RSHIM_RESET_SETTLE_S)
            self._mark_stage(res.dpu, "uploading", STAGE_UPLOADING)

            total = self._bfb_cache.get(
                filename=res.bfb_image.image_filename,
                base_url=res.bfb_image.base_url,
            ).size_bytes + len(bf_cfg.encode())

            if is_inband:
                # /dev/rshimN/boot is root-only on Ubuntu hosts, so SFTP
                # (which runs as the logged-in user) would get EACCES.
                # Stream the BFB through `sudo -n dd` instead — dd runs
                # as root, reads our bytes from stdin, and writes them
                # raw to the per-DPU rshim device.
                self._stream_via_sudo_dd(
                    client, res.dpu, bfb_path, bf_cfg, total,
                    rshim_device=rshim_device,
                )
            else:
                sftp = client.open_sftp()
                try:
                    with sftp.open(RSHIM_BOOT_PATH, "wb") as remote:
                        # rshim accepts raw bytes in arbitrarily-sized
                        # writes; a 4 MiB window keeps throughput high
                        # without exhausting BMC RAM.
                        remote.set_pipelined(True)
                        sent = 0
                        last_pct = -1
                        for chunk in bfb_with_config_stream(bfb_path, bf_cfg, _SFTP_CHUNK_BYTES):
                            remote.write(chunk)
                            sent += len(chunk)
                            pct = int(sent * 100 / total) if total else None
                            if pct is not None and pct != last_pct:
                                last_pct = pct
                                self._progress(res.dpu, pct)
                finally:
                    sftp.close()
        except Exception as exc:  # noqa: BLE001
            raise DpuFlashError(f"SFTP upload to rshim failed: {exc}") from exc
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _stream_via_sudo_dd(
        self, client, dpu: Dpu, bfb_path, bf_cfg: str, total: int,
        *, rshim_device: str = "rshim0",
    ) -> None:
        """In-band BFB upload: `sudo -n dd of=/dev/{rshim_device}/boot bs=1M`,
        with our byte stream written to dd's stdin.

        Avoids SFTP (which would write as the logged-in non-root user
        and hit EACCES on the root-only rshim device). ``Channel.sendall``
        handles backpressure so throughput stays close to SFTP.
        """
        transport = client.get_transport()
        if transport is None:
            raise DpuFlashError("host SSH transport unavailable")
        chan = transport.open_session()
        # Merge stderr so any dd complaint shows up in recv() for the
        # error path without needing a second pump.
        chan.exec_command(f"sudo -n dd of=/dev/{rshim_device}/boot bs=1M 2>&1")

        sent = 0
        last_pct = -1
        try:
            for data_chunk in bfb_with_config_stream(bfb_path, bf_cfg, _SFTP_CHUNK_BYTES):
                chan.sendall(data_chunk)
                sent += len(data_chunk)
                pct = int(sent * 100 / total) if total else None
                if pct is not None and pct != last_pct:
                    last_pct = pct
                    self._progress(dpu, pct)
        finally:
            # EOF to dd — it flushes + exits cleanly after the last block.
            try:
                chan.shutdown_write()
            except Exception:
                pass

        rc = chan.recv_exit_status()
        # Drain any remaining output for the error message.
        tail = b""
        try:
            while chan.recv_ready():
                tail += chan.recv(4096)
        except Exception:
            pass
        if rc != 0:
            raise DpuFlashError(
                f"sudo dd to /dev/{rshim_device}/boot failed (exit={rc}): "
                f"{tail.decode(errors='replace').strip() or '<no output>'}"
            )

    def _reset_dpu_into_bfb_mode(
        self, client, ssh_target: str, *,
        use_sudo: bool, rshim_device: str = "rshim0",
    ) -> None:
        """SSH-exec the rshim SW_RESET sequence on whichever host owns
        /dev/{rshim_device} (BMC for BMC DPUs, host for in-band DPUs).

        `BOOT_MODE 1` forces the DPU to boot from rshim on next reset,
        regardless of onboard state. `SW_RESET 1` then triggers the SoC
        reset. After this the DPU is in BFB-receive mode and will
        consume whatever we stream to /dev/{rshim_device}/boot as an install.

        In-band hosts run Ubuntu and need passwordless ``sudo`` for the
        root-only rshim device. BMC hosts run OpenBMC (BusyBox) as root
        and have no ``sudo`` binary at all, so the sudo prefix would hit
        exit 127 ("command not found") — the caller passes
        ``use_sudo=False`` in that case.
        """
        misc = f"/dev/{rshim_device}/misc"
        inner = (
            f"echo \"BOOT_MODE 1\" > {misc} && "
            f"echo \"SW_RESET 1\" > {misc}"
        )
        cmd = f"sudo -n sh -c '{inner}'" if use_sudo else f"sh -c '{inner}'"
        _stdin, stdout, stderr = client.exec_command(cmd, timeout=20)
        rc = stdout.channel.recv_exit_status()
        err = stderr.read().decode(errors="replace").strip()
        if rc != 0:
            raise DpuFlashError(
                f"SW_RESET via rshim failed on {ssh_target} (exit={rc}): {err or '<no stderr>'}"
            )
        logger.info("%s: DPU SW_RESET issued, waiting %ss for rshim-boot mode",
                    ssh_target, _RSHIM_RESET_SETTLE_S)

    # ── State helpers ──────────────────────────────────────────────────

    def _mark_stage(
        self,
        dpu: Dpu,
        status: str,
        detail: str,
        *,
        error: str | None = ...,  # ... = "don't touch"
        completed: bool = False,
    ) -> None:
        dpu.flash_status = status
        dpu.flash_stage_detail = detail
        if error is not ...:
            dpu.flash_error = error
        if completed:
            dpu.flash_completed_at = datetime.now(UTC)
        self.db.commit()

    # Minimum seconds between progress commits. Low enough that a slow rshim
    # SFTP shows smooth UI progress (3s poll + up to 2s throttle → max ~5s
    # perceived lag), high enough to keep DB writes modest over a 2 GiB blob
    # (~150 commits max at 2s throttle over a 5-min upload).
    _PROGRESS_COMMIT_INTERVAL_S = 2.0

    def _progress(self, dpu: Dpu, pct: int) -> None:
        import time
        dpu.flash_stage_detail = f"{STAGE_UPLOADING} — {pct}%"
        # Always commit at 100% so the final stage_detail lands in DB even
        # if it falls within the debounce window.
        now = time.monotonic()
        if pct >= 100 or now - self._last_progress_commit >= self._PROGRESS_COMMIT_INTERVAL_S:
            self._last_progress_commit = now
            self.db.commit()

    def _record_failure(self, dpu: Dpu, message: str) -> None:
        dpu.flash_status = "failed"
        dpu.flash_error = message
        dpu.flash_stage_detail = None
        dpu.flash_completed_at = datetime.now(UTC)
        self.db.commit()


# ── Prepare / enqueue helpers ──────────────────────────────────────────────
#
# Kept here (not in the Celery task module) so callers that don't want the
# task-queue dependency can still run a flash synchronously (tests, CLI).

def mark_flash_enqueued(db: Session, *, project_id: int, dpu_id: int, celery_task_id: str | None = None) -> None:
    """Set flash_status='uploading' before the Celery worker picks the job up.

    Matches the `mark_probing` pattern on the DPU OS probe: the UI needs to
    see the in-flight state immediately, not wait for the worker to schedule.
    """
    dpu = (
        db.query(Dpu)
        .filter(Dpu.id == dpu_id, Dpu.project_id == project_id)
        .first()
    )
    if dpu is None:
        raise NotFoundError("Dpu", dpu_id)
    if dpu.flash_status == "uploading":
        raise BadRequestError(
            "Flash already in progress for this DPU — cancel it first or wait for completion."
        )
    dpu.flash_status = "uploading"
    dpu.flash_stage_detail = STAGE_PREPARING
    dpu.flash_error = None
    dpu.flash_started_at = datetime.now(UTC)
    dpu.flash_completed_at = None
    dpu.flash_celery_task_id = celery_task_id
    db.flush()
