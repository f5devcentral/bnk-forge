"""Celery tasks for DPU Provisioning.

Tasks:
  - probe_dpu_task:       Redfish probe (Phase 4).
  - probe_dpu_os_task:    BMC-side ARP sweep + DPU OS SSH probe (Phase 5).
  - flash_dpu_task:       Full DPU flash (render bf.conf + SFTP BFB to rshim).

Parallel dispatch: the route layer enqueues up to 10 sibling tasks at a
time across a project. Celery worker concurrency is the natural limiter
— each worker container processes `CELERY_WORKER_CONCURRENCY` in
parallel, and with 2 worker containers the default per-project cap is 10.
"""

import logging

from celery_app import celery_app
from database import get_db_context

logger = logging.getLogger(__name__)


@celery_app.task(
    name="tasks.dpu.probe_dpu",
    bind=True,
    time_limit=120,         # Redfish probe is fast; hard cap at 2 min
    soft_time_limit=90,
    acks_late=True,
    max_retries=0,
)
def probe_dpu_task(self, dpu_id: int) -> dict:  # noqa: ARG001
    """Run the BlueField Redfish probe for a single DPU and persist the result."""
    logger.info("DPU probe task started for dpu_id=%s", dpu_id)
    with get_db_context() as db:
        from services.dpu_discovery_service import DpuDiscoveryService
        DpuDiscoveryService(db).run_and_persist(dpu_id)
    return {"dpu_id": dpu_id, "status": "completed"}


@celery_app.task(
    name="tasks.dpu.install_rshim",
    bind=True,
    # apt update + install + kernel-mft-dkms build can take 5-8 minutes —
    # cap at 15 min to leave headroom on slow hosts.
    time_limit=15 * 60,
    soft_time_limit=14 * 60,
    acks_late=True,
    max_retries=0,
)
def install_rshim_task(self, project_id: int, dpu_id: int) -> dict:  # noqa: ARG001
    """Install rshim on an in-band DPU's host, then chain Discover.

    Captures the install log + status on the DPU row (reusing
    last_discovery_* for user-visible state) so the UI shows progress and
    final outcome without blocking on the dialog.
    """
    from datetime import datetime

    from models.dpu import Dpu

    logger.info("install_rshim task started for project=%s dpu=%s", project_id, dpu_id)
    with get_db_context() as db:
        dpu = db.query(Dpu).filter(Dpu.id == dpu_id, Dpu.project_id == project_id).first()
        if dpu is None:
            logger.warning("install_rshim: dpu %s vanished", dpu_id)
            return {"dpu_id": dpu_id, "status": "aborted"}

        try:
            from services.rshim_service import RshimService
            result = RshimService(db).install(project_id, dpu_id)
        except Exception as exc:  # noqa: BLE001 — capture everything
            logger.exception("install_rshim: dpu %s raised", dpu_id)
            dpu.last_discovery_status = "failed"
            dpu.last_discovery_error = f"Install rshim failed: {exc}"
            dpu.last_discovery_at = datetime.utcnow()
            db.commit()
            return {"dpu_id": dpu_id, "status": "failed"}

        if result.ok:
            # Hand off to the normal Discover flow — it'll flip
            # last_discovery_status to "running" then "ok".
            from services.dpu_discovery_service import DpuDiscoveryService
            DpuDiscoveryService(db).mark_running(project_id, dpu_id)
            db.commit()
        else:
            dpu.last_discovery_status = "failed"
            dpu.last_discovery_error = result.error or "Install rshim failed"
            dpu.last_discovery_at = datetime.utcnow()
            db.commit()

    # Chain a probe only on success. Enqueue outside the DB context so the
    # transaction is already committed when the follow-up task picks up.
    if result.ok:
        probe_dpu_task.delay(dpu_id)

    return {"dpu_id": dpu_id, "status": "ok" if result.ok else "failed"}


@celery_app.task(
    name="tasks.dpu.enable_rshim_on_bmc",
    bind=True,
    time_limit=120,
    soft_time_limit=90,
    acks_late=True,
    max_retries=0,
)
def enable_rshim_on_bmc_task(self, project_id: int, dpu_id: int) -> dict:  # noqa: ARG001
    """SSH to the DPU's BMC and persistently enable rshim.

    Captures the outcome on `last_discovery_error` / `last_discovery_status`
    so the UI (which already polls those fields for the Install-rshim path)
    shows the result without any new polling contract. On success, chains
    the standard Discover so the rshim_state pill updates.
    """
    from datetime import datetime

    from models.dpu import Dpu

    logger.info("enable_rshim_on_bmc started for project=%s dpu=%s", project_id, dpu_id)
    with get_db_context() as db:
        dpu = db.query(Dpu).filter(Dpu.id == dpu_id, Dpu.project_id == project_id).first()
        if dpu is None:
            return {"dpu_id": dpu_id, "status": "aborted"}

        try:
            from services.rshim_service import RshimService
            result = RshimService(db).enable_on_bmc(project_id, dpu_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("enable_rshim_on_bmc: dpu %s raised", dpu_id)
            dpu.last_discovery_status = "failed"
            dpu.last_discovery_error = f"Enable rshim on BMC failed: {exc}"
            dpu.last_discovery_at = datetime.utcnow()
            db.commit()
            return {"dpu_id": dpu_id, "status": "failed"}

        if result.ok:
            from services.dpu_discovery_service import DpuDiscoveryService
            DpuDiscoveryService(db).mark_running(project_id, dpu_id)
            db.commit()
        else:
            dpu.last_discovery_status = "failed"
            dpu.last_discovery_error = result.error or "Enable rshim on BMC failed"
            dpu.last_discovery_at = datetime.utcnow()
            db.commit()

    if result.ok:
        probe_dpu_task.delay(dpu_id)
    return {"dpu_id": dpu_id, "status": "ok" if result.ok else "failed"}


@celery_app.task(
    name="tasks.dpu.disable_rshim",
    bind=True,
    time_limit=120,
    soft_time_limit=90,
    acks_late=True,
    max_retries=0,
)
def disable_rshim_task(self, project_id: int, dpu_id: int) -> dict:  # noqa: ARG001
    """Stop + disable rshim on whichever side owns this DPU (host or BMC).

    Persists the final readiness snapshot onto the DPU row, and chains
    Discover so the Discovery/pill column refreshes.
    """
    from datetime import datetime

    from models.dpu import Dpu

    logger.info("disable_rshim started for project=%s dpu=%s", project_id, dpu_id)
    with get_db_context() as db:
        dpu = db.query(Dpu).filter(Dpu.id == dpu_id, Dpu.project_id == project_id).first()
        if dpu is None:
            return {"dpu_id": dpu_id, "status": "aborted"}

        try:
            from services.rshim_service import RshimService
            result = RshimService(db).disable_rshim(project_id, dpu_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("disable_rshim: dpu %s raised", dpu_id)
            dpu.last_discovery_status = "failed"
            dpu.last_discovery_error = f"Disable rshim failed: {exc}"
            dpu.last_discovery_at = datetime.utcnow()
            db.commit()
            return {"dpu_id": dpu_id, "status": "failed"}

        if result.ok:
            from services.dpu_discovery_service import DpuDiscoveryService
            DpuDiscoveryService(db).mark_running(project_id, dpu_id)
            db.commit()
        else:
            dpu.last_discovery_status = "failed"
            dpu.last_discovery_error = result.error or "Disable rshim failed"
            dpu.last_discovery_at = datetime.utcnow()
            db.commit()

    if result.ok:
        probe_dpu_task.delay(dpu_id)
    return {"dpu_id": dpu_id, "status": "ok" if result.ok else "failed"}


@celery_app.task(
    name="tasks.dpu.convert_to_ethernet",
    bind=True,
    # mlxconfig + reset is fast (< 1 min). Cap at 3 min to leave headroom
    # for slow MST resolution on first-touch hosts.
    time_limit=180,
    soft_time_limit=150,
    acks_late=True,
    max_retries=0,
)
def convert_to_ethernet_task(self, project_id: int, dpu_id: int) -> dict:  # noqa: ARG001
    """Set LINK_TYPE_P1/P2=2 + reset on a DPU; surface the outcome on the row.

    Reuses the `last_discovery_*` fields the UI already polls for the
    Install-rshim / Enable-rshim-on-BMC paths, so no new polling
    contract is needed. Does NOT chain a probe — DPU reboots take
    1-2 minutes and the operator runs *Discover* afterwards (the UI
    explicitly tells them so in the success notification).
    """
    from datetime import datetime

    from models.dpu import Dpu

    logger.info("convert_to_ethernet started for project=%s dpu=%s", project_id, dpu_id)
    with get_db_context() as db:
        dpu = db.query(Dpu).filter(Dpu.id == dpu_id, Dpu.project_id == project_id).first()
        if dpu is None:
            return {"dpu_id": dpu_id, "status": "aborted"}

        try:
            from services.dpu_link_mode_service import DpuLinkModeService
            result = DpuLinkModeService(db).convert_to_ethernet(project_id, dpu_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("convert_to_ethernet: dpu %s raised", dpu_id)
            dpu.last_discovery_status = "failed"
            dpu.last_discovery_error = f"Convert to Ethernet failed: {exc}"
            dpu.last_discovery_at = datetime.utcnow()
            db.commit()
            return {"dpu_id": dpu_id, "status": "failed"}

        if result.ok:
            dpu.last_discovery_status = "ok"
            dpu.last_discovery_error = (
                "Convert to Ethernet: mlxconfig applied + DPU reset issued. "
                "The DPU is rebooting (~1-2 min). Click Discover to refresh "
                "the link mode."
            )
            dpu.last_discovery_at = datetime.utcnow()
            db.commit()
        else:
            dpu.last_discovery_status = "failed"
            dpu.last_discovery_error = result.error or "Convert to Ethernet failed"
            dpu.last_discovery_at = datetime.utcnow()
            db.commit()

    return {"dpu_id": dpu_id, "status": "ok" if result.ok else "failed"}


@celery_app.task(
    name="tasks.dpu.probe_dpu_os",
    bind=True,
    time_limit=300,         # ping-sweep + SSH — cap at 5 min
    soft_time_limit=240,
    acks_late=True,
    max_retries=0,
)
def probe_dpu_os_task(self, dpu_id: int) -> dict:  # noqa: ARG001
    """BMC-side ARP sweep + DPU OS SSH probe for a single DPU."""
    logger.info("DPU OS probe task started for dpu_id=%s", dpu_id)
    with get_db_context() as db:
        from services.dpu_os_probe_service import DpuOsProbeService
        DpuOsProbeService(db).run_and_persist(dpu_id)
    return {"dpu_id": dpu_id, "status": "completed"}


@celery_app.task(
    name="tasks.dpu.run_dpu_connectivity_matrix",
    bind=True,
    # N×N pings across DPUs over SSH — generous cap; DpuConnectivity-
    # Service runs sources in parallel so wall-clock stays bounded even
    # for fleets up to ~20 DPUs.
    time_limit=600,
    soft_time_limit=540,
    acks_late=True,
    max_retries=0,
)
def run_dpu_connectivity_matrix_task(self, project_id: int) -> dict:  # noqa: ARG001
    """Pairwise DPU↔DPU ping sweep, persisted on the project's settings row."""
    logger.info(
        "DPU connectivity matrix task started for project_id=%s", project_id,
    )
    with get_db_context() as db:
        from services.dpu_connectivity_service import DpuConnectivityService
        result = DpuConnectivityService(db).run(project_id)
    return {"project_id": project_id, "status": result.get("status")}


@celery_app.task(
    name="tasks.dpu.flash_dpu",
    bind=True,
    # BFB upload over SFTP to a typically-rshim'd BMC runs 3-15 minutes for
    # a ~2 GiB bundle; cap at 30 min to protect against stuck I/O.
    time_limit=30 * 60,
    soft_time_limit=25 * 60,
    # `acks_late=False` so a crashed worker's task is NOT redelivered to
    # another worker — flashing twice is dangerous (the DPU may already be
    # rebooting into the installer).
    acks_late=False,
    max_retries=0,
)
def flash_dpu_task(self, project_id: int, dpu_id: int) -> dict:  # noqa: ARG001
    """Full DPU flash: render bf.conf → lint → cache BFB → SFTP to rshim."""
    logger.info(
        "DPU flash task started for project=%s dpu=%s (celery task id=%s)",
        project_id, dpu_id, self.request.id,
    )
    with get_db_context() as db:
        # Stamp the Celery task id so DELETE /flash can revoke it.
        from models.dpu import Dpu
        dpu = db.query(Dpu).filter(Dpu.id == dpu_id, Dpu.project_id == project_id).first()
        if dpu is not None:
            dpu.flash_celery_task_id = self.request.id
            db.commit()

        from services.dpu_flash_service import DpuFlashService
        result = DpuFlashService(db).flash(project_id=project_id, dpu_id=dpu_id)

    # On successful SFTP, start the post-flash reachability poller. Give
    # the BFB installer ~3 minutes to take the DPU OS offline — without
    # the delay, the first poll would likely connect to the OLD
    # (pre-flash) OS and falsely mark the flash as complete.
    # The poller is now access-mode-aware via DpuOsProbeService (BMC ARP
    # sweep for BMC DPUs; tmfifo direct-tcpip for in-band).
    if result.get("status") == "waiting_reboot":
        poll_flash_online_task.apply_async(
            args=[project_id, dpu_id],
            countdown=180,
        )
    return result


# ── Post-flash reachability poller ────────────────────────────────────────

# Retries: 25 × 30s = 12.5 min after the initial 180s delay → 15.5 min total
# budget before we give up waiting for the DPU to come back.
_POLL_MAX_RETRIES = 25
_POLL_RETRY_COUNTDOWN = 30


@celery_app.task(
    name="tasks.dpu.poll_flash_online",
    bind=True,
    # Each invocation just does one probe — quick. Hard cap at 8 min to
    # accommodate a slow BMC ARP sweep under load.
    time_limit=8 * 60,
    soft_time_limit=7 * 60,
    acks_late=False,
    max_retries=_POLL_MAX_RETRIES,
    default_retry_delay=_POLL_RETRY_COUNTDOWN,
)
def poll_flash_online_task(self, project_id: int, dpu_id: int) -> dict:
    """Poll a recently-flashed DPU until its OS is SSH-reachable.

    Retries itself every ~30s for up to ~15 minutes. On timeout, marks the
    flash as failed so the UI stops showing "waiting for reboot…" forever.
    Cancellation is honoured — if `flash_status` is anything other than
    `waiting_reboot` when we wake up (e.g. the operator hit Cancel), we
    exit quietly.
    """
    from datetime import UTC, datetime

    from models.dpu import Dpu

    with get_db_context() as db:
        dpu = db.query(Dpu).filter(
            Dpu.id == dpu_id, Dpu.project_id == project_id,
        ).first()
        if dpu is None:
            logger.info("Flash poller: dpu=%s vanished; exiting", dpu_id)
            return {"status": "aborted", "reason": "dpu_gone"}
        if dpu.flash_status != "waiting_reboot":
            logger.info(
                "Flash poller: dpu=%s status=%s (not waiting_reboot); exiting",
                dpu_id, dpu.flash_status,
            )
            return {"status": "aborted", "reason": f"flash_status={dpu.flash_status}"}

        # Run the same probe the user triggers manually — full BMC ARP
        # sweep + SSH verification. Writes dpu_os_* columns as a side effect.
        from services.dpu_os_probe_service import DpuOsProbeService
        DpuOsProbeService(db).run_and_persist(dpu_id)
        db.refresh(dpu)

        if dpu.dpu_os_status == "reachable":
            dpu.flash_status = "os_online"
            dpu.flash_stage_detail = "DPU OS reachable after reflash"
            dpu.flash_error = None
            dpu.flash_completed_at = datetime.now(UTC)
            db.commit()
            logger.info("Flash poller: dpu=%s os_online", dpu_id)

            # D-022: re-reconcile fleet membership after flash completion.
            try:
                from services.fleet_reconcile_service import reconcile_fleet_member
                reconcile_fleet_member(db, "dpu", dpu_id)
            except Exception:
                logger.warning("Fleet reconcile failed after DPU flash os_online id=%s", dpu_id)

            return {"status": "os_online"}

        # Still unreachable — retry if we have budget, else time out.
        if self.request.retries >= _POLL_MAX_RETRIES:
            minutes = (180 + _POLL_MAX_RETRIES * _POLL_RETRY_COUNTDOWN) // 60
            dpu.flash_status = "failed"
            dpu.flash_stage_detail = None
            dpu.flash_error = (
                f"DPU OS did not come back online within {minutes} minutes after flash"
            )
            dpu.flash_completed_at = datetime.now(UTC)
            db.commit()
            logger.warning(
                "Flash poller: dpu=%s timed out after %d retries", dpu_id, self.request.retries,
            )
            return {"status": "timeout"}

        # Keep the stage_detail informative so the UI shows something other
        # than a stale "waiting_reboot" label.
        attempt = self.request.retries + 1
        dpu.flash_stage_detail = (
            f"waiting for DPU OS (attempt {attempt}/{_POLL_MAX_RETRIES})"
        )
        db.commit()

    # Re-raises as Retry internally; Celery re-queues with the configured delay.
    raise self.retry(countdown=_POLL_RETRY_COUNTDOWN)
