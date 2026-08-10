"""DPU Provisioning routes — per-project DPU CRUD + settings.

DPUs belong to a project (1:many). Each project has exactly one DPU
settings row (1:1, lazily created). See docs/TODO.md (DPU Provisioning,
Blueprint 1).
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from routes.auth import require_operator, require_viewer
from schemas.dpu import (
    DpuCreate,
    DpuListResponse,
    DpuResetRequest,
    DpuResetToDefaultsRequest,
    DpuResponse,
    DpuUpdate,
    ProjectDpuSettingsResponse,
    ProjectDpuSettingsUpdate,
)
from services.dpu_discovery_service import DpuDiscoveryService
from services.dpu_service import DpuService, ProjectDpuSettingsService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["dpus"])


# ─── DPU CRUD ───────────────────────────────────────────────────────────────

@router.get(
    "/dpus",
    response_model=DpuListResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("list DPUs")
def list_dpus(project_id: int, db: Session = Depends(get_db)):
    return DpuService(db).list_dpus(project_id)


@router.post(
    "/dpus",
    response_model=DpuResponse,
    status_code=201,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("create DPU")
def create_dpu(project_id: int, data: DpuCreate, db: Session = Depends(get_db)):
    result = DpuService(db).create_dpu(project_id, data)
    db.commit()
    return result


# ─── DPU↔DPU connectivity matrix (Phase 3) ──────────────────────────────────
#
# These routes MUST be declared before the `/dpus/{dpu_id}` parameterized
# route. FastAPI matches routes in order of declaration; if {dpu_id}
# wins first, GET /dpus/connectivity-matrix gets routed to get_dpu and
# fails 422 ("not an int"). The frontend polling silently stalls on
# that error, which is exactly the bug Job-3 testing surfaced.

@router.post(
    "/dpus/connectivity-matrix/run",
    status_code=202,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("run DPU connectivity matrix")
def run_dpu_connectivity_matrix(project_id: int, db: Session = Depends(get_db)):
    """Kick off a pairwise DPU↔DPU ping sweep across all probed DPUs.

    Asynchronous — pairwise SSH + ping over a fleet can take tens of
    seconds. The frontend polls GET /connectivity-matrix to surface the
    result once the task completes.
    """
    from services.dpu_connectivity_service import DpuConnectivityService
    DpuConnectivityService(db).mark_running(project_id)

    from tasks.dpu_tasks import run_dpu_connectivity_matrix_task
    task = run_dpu_connectivity_matrix_task.delay(project_id)
    logger.info(
        "Enqueued DPU connectivity matrix task %s for project_id=%s",
        task.id, project_id,
    )
    return {"project_id": project_id, "task_id": task.id, "status": "running"}


@router.get(
    "/dpus/connectivity-matrix",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get DPU connectivity matrix")
def get_dpu_connectivity_matrix(
    project_id: int, db: Session = Depends(get_db),
):
    """Return the latest persisted DPU↔DPU connectivity matrix + status."""
    from services.dpu_connectivity_service import DpuConnectivityService
    return DpuConnectivityService(db).get_matrix(project_id)


@router.get(
    "/dpus/{dpu_id}",
    response_model=DpuResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get DPU")
def get_dpu(project_id: int, dpu_id: int, db: Session = Depends(get_db)):
    return DpuService(db).get_dpu(project_id, dpu_id)


@router.patch(
    "/dpus/{dpu_id}",
    response_model=DpuResponse,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("update DPU")
def update_dpu(
    project_id: int, dpu_id: int, data: DpuUpdate, db: Session = Depends(get_db)
):
    result = DpuService(db).update_dpu(project_id, dpu_id, data)
    db.commit()
    return result


@router.delete(
    "/dpus/{dpu_id}",
    status_code=204,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("delete DPU")
def delete_dpu(project_id: int, dpu_id: int, db: Session = Depends(get_db)):
    DpuService(db).delete_dpu(project_id, dpu_id)
    db.commit()
    return None


@router.get(
    "/dpus/{dpu_id}/inventory",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get DPU inventory")
def get_dpu_inventory(project_id: int, dpu_id: int, db: Session = Depends(get_db)):
    """Return the full discovered inventory (from last_discovery_payload).

    Returns `null` if the DPU has never been discovered.
    """
    # Use the service's private getter to enforce project-scoped lookup
    dpu = DpuService(db)._require_dpu(project_id, dpu_id)  # noqa: SLF001
    return {
        "dpu_id": dpu.id,
        "last_discovery_at": dpu.last_discovery_at,
        "last_discovery_status": dpu.last_discovery_status,
        "last_discovery_error": dpu.last_discovery_error,
        "inventory": dpu.last_discovery_payload,
    }


@router.post(
    "/dpus/{dpu_id}/discover",
    status_code=202,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("discover DPU")
def discover_dpu(project_id: int, dpu_id: int, db: Session = Depends(get_db)):
    """Enqueue a Celery task to run the BlueField Redfish probe."""
    DpuDiscoveryService(db).mark_running(project_id, dpu_id)
    db.commit()

    # Import the task lazily so the route module is importable without Celery.
    from tasks.dpu_tasks import probe_dpu_task
    task = probe_dpu_task.delay(dpu_id)
    logger.info("Enqueued DPU probe task %s for dpu_id=%s", task.id, dpu_id)

    return {"dpu_id": dpu_id, "task_id": task.id, "status": "running"}


# Parallel bulk discovery — cap concurrency at enqueue, not just via worker
# concurrency. Per TODO: up to 10 at a time across a project.
MAX_PARALLEL_PROBES = 10


def _dispatch_probe_waves(task, dpu_ids: list[int]) -> dict:
    """Enqueue ``task`` for each DPU in waves of at most ``MAX_PARALLEL_PROBES``.

    Each wave is a Celery ``group`` and the waves are chained so wave N+1 only
    starts after wave N completes. This enforces the parallel cap at enqueue time
    regardless of worker concurrency (FEAT-0165/FEAT-0179 / ERR-0019/ERR-0020).
    Immutable signatures (``.si``) keep each probe's single ``dpu_id`` argument
    intact across the chain.
    """
    from celery import chain, group

    waves = [
        dpu_ids[i:i + MAX_PARALLEL_PROBES]
        for i in range(0, len(dpu_ids), MAX_PARALLEL_PROBES)
    ]
    if waves:
        chain(
            *(group(task.si(dpu_id) for dpu_id in wave) for wave in waves)
        ).apply_async()

    return {
        "enqueued": len(dpu_ids),
        "max_parallel": MAX_PARALLEL_PROBES,
        "waves": len(waves),
        "tasks": [{"dpu_id": dpu_id} for dpu_id in dpu_ids],
    }


# ─── In-band (rshim) actions ────────────────────────────────────────────────

class RshimStatusResponse(BaseModel):
    installed: bool
    active: bool
    device_present: bool
    mft_installed: bool = False
    mst_running: bool = False
    message: str


class RshimInstallLogEntry(BaseModel):
    command: str
    exit_code: int
    stdout: str
    stderr: str


class RshimInstallResponse(BaseModel):
    ok: bool
    log: list[RshimInstallLogEntry]
    status: RshimStatusResponse | None = None
    error: str | None = None


class RshimInstallQueuedResponse(BaseModel):
    dpu_id: int
    task_id: str
    status: str  # always "queued"


@router.get(
    "/dpus/{dpu_id}/rshim-status",
    response_model=RshimStatusResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("probe rshim status")
def rshim_status(project_id: int, dpu_id: int, db: Session = Depends(get_db)):
    """Probe whether rshim is installed + active on the DPU's host."""
    from services.rshim_service import RshimService

    status = RshimService(db).probe_status(project_id, dpu_id)
    return RshimStatusResponse(
        installed=status.installed,
        active=status.active,
        device_present=status.device_present,
        mft_installed=status.mft_installed,
        mst_running=status.mst_running,
        message=status.message,
    )


@router.post(
    "/dpus/{dpu_id}/install-rshim",
    response_model=RshimInstallQueuedResponse,
    status_code=202,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("install rshim")
def install_rshim(project_id: int, dpu_id: int, db: Session = Depends(get_db)):
    """Enqueue a Celery task that installs rshim on the DPU's host.

    Returns 202 immediately so the UI isn't blocked. The task marks the
    DPU's `last_discovery_status` as running while working, then chains
    the normal Discover probe on success (or writes a failure message
    into `last_discovery_error`).
    """
    # Mark the DPU as in-progress synchronously — the Discover action does
    # the same pattern so the UI sees the state flip without a race.
    DpuDiscoveryService(db).mark_running(project_id, dpu_id)
    db.commit()

    from tasks.dpu_tasks import install_rshim_task
    task = install_rshim_task.delay(project_id, dpu_id)
    logger.info("Enqueued install_rshim task %s for dpu_id=%s", task.id, dpu_id)
    return RshimInstallQueuedResponse(
        dpu_id=dpu_id, task_id=task.id, status="queued",
    )


@router.post(
    "/dpus/{dpu_id}/disable-rshim",
    response_model=RshimInstallQueuedResponse,
    status_code=202,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("disable rshim")
def disable_rshim(project_id: int, dpu_id: int, db: Session = Depends(get_db)):
    """Stop + disable rshim on whichever side owns this DPU.

    Disruptive: breaks any active Serial Console / Flash session on this
    DPU. Callers should confirm with the operator before hitting this.
    """
    DpuDiscoveryService(db).mark_running(project_id, dpu_id)
    db.commit()

    from tasks.dpu_tasks import disable_rshim_task
    task = disable_rshim_task.delay(project_id, dpu_id)
    logger.info("Enqueued disable_rshim task %s for dpu_id=%s", task.id, dpu_id)
    return RshimInstallQueuedResponse(
        dpu_id=dpu_id, task_id=task.id, status="queued",
    )


@router.post(
    "/dpus/{dpu_id}/enable-rshim-on-bmc",
    response_model=RshimInstallQueuedResponse,
    status_code=202,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("enable rshim on BMC")
def enable_rshim_on_bmc(project_id: int, dpu_id: int, db: Session = Depends(get_db)):
    """Persistently enable + start rshim on a BMC-mode DPU's BMC.

    OpenBMC ships the rshim unit already installed but disabled; this
    action SSHes in and runs `systemctl enable rshim` + `restart rshim`
    so the device node appears now and survives BMC reboot. The UI
    polls `last_discovery_status` / `rshim_state` to reflect progress
    and the final outcome.
    """
    DpuDiscoveryService(db).mark_running(project_id, dpu_id)
    db.commit()

    from tasks.dpu_tasks import enable_rshim_on_bmc_task
    task = enable_rshim_on_bmc_task.delay(project_id, dpu_id)
    logger.info("Enqueued enable_rshim_on_bmc task %s for dpu_id=%s", task.id, dpu_id)
    return RshimInstallQueuedResponse(
        dpu_id=dpu_id, task_id=task.id, status="queued",
    )


@router.post(
    "/dpus/{dpu_id}/convert-to-ethernet",
    response_model=RshimInstallQueuedResponse,
    status_code=202,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("convert DPU to Ethernet")
def convert_dpu_to_ethernet(project_id: int, dpu_id: int, db: Session = Depends(get_db)):
    """Flip both DPU ports to LINK_TYPE=ETH and reboot the DPU.

    Synchronous preflight (so the operator gets an immediate error if
    the DPU isn't actually in IB mode or — for BMC-mode rows — the
    DPU OS hasn't been probed yet) then the task runs the mlxconfig +
    reset asynchronously. The UI polls `last_discovery_*` for progress.
    """
    # Run the preflight on the request thread so BadRequestError surfaces
    # to the operator immediately rather than landing in a Celery log.
    from services.dpu_link_mode_service import DpuLinkModeService
    from services.dpu_service import _derive_link_mode

    dpu = DpuLinkModeService(db)._require_dpu(project_id, dpu_id)  # noqa: SLF001
    current_mode = _derive_link_mode(dpu.last_discovery_payload)
    if current_mode not in ("ib", "mixed"):
        from core.errors import BadRequestError
        raise BadRequestError(
            f"DPU {dpu.id} is not in InfiniBand mode (current link_mode="
            f"{current_mode!r}). Run Discover to refresh, or pick a DPU "
            "whose link mode badge says InfiniBand."
        )
    if dpu.access_mode == "bmc" and (
        dpu.dpu_os_status != "reachable" or not dpu.dpu_os_ip
    ):
        from core.errors import BadRequestError
        raise BadRequestError(
            "Can't convert this BMC-mode DPU yet: the DPU OS isn't "
            "reachable. Run *Probe DPU OS* first — Convert needs an "
            "OS-side SSH session to call mlxconfig."
        )

    DpuDiscoveryService(db).mark_running(project_id, dpu_id)
    db.commit()

    from tasks.dpu_tasks import convert_to_ethernet_task
    task = convert_to_ethernet_task.delay(project_id, dpu_id)
    logger.info("Enqueued convert_to_ethernet task %s for dpu_id=%s", task.id, dpu_id)
    return RshimInstallQueuedResponse(
        dpu_id=dpu_id, task_id=task.id, status="queued",
    )


class RegisterDpuHostResponse(BaseModel):
    """Result of registering an in-band DPU's host as a bare-metal host.

    Mirrors RegisterAsHostResponse from the Discovery flow so the UI
    can use the same toast logic.
    """
    host_id: int
    status: str  # "created" | "exists"
    name: str
    host_ip: str


@router.post(
    "/dpus/{dpu_id}/register-host",
    response_model=RegisterDpuHostResponse,
    status_code=201,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("register DPU host as bare-metal host")
def register_dpu_host(
    project_id: int,
    dpu_id: int,
    db: Session = Depends(get_db),
) -> RegisterDpuHostResponse:
    """Register the in-band DPU's host into the Bare Metal tab.

    Same idempotent semantics as the Discovery → Register Host action:
    if the host is already registered (matched on project + host_ip),
    returns the existing row with status='exists'. The DPU tab path is
    useful when the discovery job has been deleted but the DPU rows
    persist, or when registering all in-band DPUs' hosts in one place.
    """
    result = DpuService(db).register_host_from_dpu(project_id, dpu_id)
    db.commit()
    return RegisterDpuHostResponse(**result)


@router.post(
    "/dpus/auto-assign-vlan-ips",
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("auto-assign VLAN IPs")
def auto_assign_vlan_ips(project_id: int, db: Session = Depends(get_db)):
    """Assign per-DPU IPs from project subnets + start_host, sequentially by DPU id."""
    response, assignments = DpuService(db).auto_assign_vlan_ips(project_id)
    db.commit()
    return {"dpus": response.dpus, "assignments": assignments}


@router.post(
    "/dpus/discover-all",
    status_code=202,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("discover all DPUs")
def discover_all_dpus(project_id: int, db: Session = Depends(get_db)):
    """Mark all DPUs in the project as running and enqueue Redfish probes."""
    svc = DpuService(db)
    dpus = svc.list_dpus(project_id).dpus

    from tasks.dpu_tasks import probe_dpu_task

    discovery_svc = DpuDiscoveryService(db)
    for dpu in dpus:
        discovery_svc.mark_running(project_id, dpu.id)
    db.commit()

    return _dispatch_probe_waves(probe_dpu_task, [dpu.id for dpu in dpus])


@router.post(
    "/dpus/{dpu_id}/reset",
    status_code=202,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("reset DPU ARM CPU")
def reset_dpu(
    project_id: int,
    dpu_id: int,
    data: DpuResetRequest,
    db: Session = Depends(get_db),
):
    """Trigger a Redfish ComputerSystem.Reset on the DPU ARM CPU.

    reset_type: GracefulRestart | ForceRestart | PowerCycle
    """
    return DpuService(db).reset_dpu(project_id, dpu_id, data.reset_type)


@router.post(
    "/dpus/{dpu_id}/reset-nic",
    status_code=202,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("reset DPU NIC silicon")
def reset_dpu_nic(
    project_id: int,
    dpu_id: int,
    db: Session = Depends(get_db),
):
    """Run ``mlxfwreset -l 4 -t 0`` to recover a stuck NIC silicon.

    Use this when the BF3 NIC firmware is wedged in pre-initializing
    state (``mlxfwmanager``: *Failed to open device*; ``flint``:
    *MFE_NO_FLASH_DETECTED*). Different from PowerCycle (-l 3 PCI
    reset): level 4 is a full-chip warm reboot.

    In-band DPUs run the command on the worker host. BMC-mode DPUs
    run it on the DPU OS itself — that path requires the DPU OS to
    be reachable.
    """
    return DpuService(db).reset_dpu_nic(project_id, dpu_id)


@router.post(
    "/dpus/{dpu_id}/reset-to-defaults",
    status_code=202,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("reset DPU to defaults")
def reset_dpu_to_defaults(
    project_id: int,
    dpu_id: int,
    data: DpuResetToDefaultsRequest,
    db: Session = Depends(get_db),
):
    """Layered factory reset — BIOS + BMC + NIC mlxconfig.

    See services.dpu_factory_reset_service for the execution order and
    the NIC-requires-DPU-OS-reachable caveat. Synchronous (the Redfish
    calls are quick; BMC reboot happens asynchronously on the BMC side).
    """
    from services.dpu_factory_reset_service import (
        DpuFactoryResetService,
        FactoryResetOptions,
    )
    opts = FactoryResetOptions(
        bios=data.bios, bmc=data.bmc, nic=data.nic, bmc_deep=data.bmc_deep,
    )
    summary = DpuFactoryResetService(db).run(
        project_id=project_id, dpu_id=dpu_id, opts=opts,
    )
    return summary.to_dict()


@router.post(
    "/dpus/{dpu_id}/probe-dpu-os",
    status_code=202,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("probe DPU OS")
def probe_dpu_os(project_id: int, dpu_id: int, db: Session = Depends(get_db)):
    """Enqueue a Celery task to BMC-probe + SSH-verify the DPU OS reachable."""
    from services.dpu_os_probe_service import DpuOsProbeService
    DpuOsProbeService(db).mark_probing(project_id, dpu_id)
    db.commit()

    from tasks.dpu_tasks import probe_dpu_os_task
    task = probe_dpu_os_task.delay(dpu_id)
    logger.info("Enqueued DPU OS probe task %s for dpu_id=%s", task.id, dpu_id)

    return {"dpu_id": dpu_id, "task_id": task.id, "status": "running"}


@router.post(
    "/dpus/probe-dpu-os-all",
    status_code=202,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("probe DPU OS all DPUs")
def probe_dpu_os_all(project_id: int, db: Session = Depends(get_db)):
    """Enqueue DPU OS probes for every DPU in the project."""
    from services.dpu_os_probe_service import DpuOsProbeService
    probe_svc = DpuOsProbeService(db)
    dpus = DpuService(db).list_dpus(project_id).dpus
    for dpu in dpus:
        probe_svc.mark_probing(project_id, dpu.id)
    db.commit()

    from tasks.dpu_tasks import probe_dpu_os_task
    return _dispatch_probe_waves(probe_dpu_os_task, [dpu.id for dpu in dpus])


# ─── Flash BFB, Firmware & config ────────────────────────────────────────────

@router.post(
    "/dpus/{dpu_id}/flash",
    status_code=202,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("flash DPU")
def flash_dpu(project_id: int, dpu_id: int, db: Session = Depends(get_db)):
    """Enqueue a full DPU flash (render bf.conf → cache BFB → SFTP to rshim).

    The UI polls `dpu.flash_status` and `flash_stage_detail` for live
    progress. Cancel via DELETE /dpus/{id}/flash.
    """
    # Preflight: render the bf.conf now so a config problem (missing
    # VLAN IPs, bad template, unset OS creds) comes back as a 400 here
    # instead of as a delayed "failed" via Celery.
    from core.errors import BadRequestError
    from services.dpu_flash_service import (
        DpuFlashError,
        DpuFlashService,
        mark_flash_enqueued,
    )
    try:
        DpuFlashService(db).render_only(project_id=project_id, dpu_id=dpu_id)
    except DpuFlashError as exc:
        raise BadRequestError(
            f"bf.conf preview failed — Flash refused so the Celery task "
            f"doesn't immediately fail on the same issue: {exc}"
        ) from exc

    mark_flash_enqueued(db, project_id=project_id, dpu_id=dpu_id)
    db.commit()

    from tasks.dpu_tasks import flash_dpu_task
    task = flash_dpu_task.delay(project_id, dpu_id)
    logger.info("Enqueued flash task %s for project=%s dpu_id=%s", task.id, project_id, dpu_id)

    return {"dpu_id": dpu_id, "task_id": task.id, "status": "uploading"}


@router.post(
    "/dpus/flash-all",
    status_code=202,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("flash all DPUs")
def flash_all_dpus(project_id: int, db: Session = Depends(get_db)):
    """Enqueue a flash for every DPU in the project — parallelism is bounded
    by Celery worker concurrency (~10 across two worker containers)."""
    from services.dpu_flash_service import (
        DpuFlashError,
        DpuFlashService,
        mark_flash_enqueued,
    )
    from tasks.dpu_tasks import flash_dpu_task

    dpus = DpuService(db).list_dpus(project_id).dpus
    # Preflight each DPU's render so a broken config on row N doesn't
    # half-start a batch. Collect failures and report them instead of
    # enqueueing Celery tasks that will immediately fail.
    flash_svc = DpuFlashService(db)
    render_failures: list[dict[str, object]] = []
    renderable: list = []
    for dpu in dpus:
        try:
            flash_svc.render_only(project_id=project_id, dpu_id=dpu.id)
            renderable.append(dpu)
        except DpuFlashError as exc:
            render_failures.append({"dpu_id": dpu.id, "error": str(exc)})

    # Pre-mark the in-flight state in one commit so the UI's next poll
    # sees all rows flip together.
    for dpu in renderable:
        try:
            mark_flash_enqueued(db, project_id=project_id, dpu_id=dpu.id)
        except Exception as exc:  # noqa: BLE001 — skip already-flashing rows gracefully
            logger.info("Skipping flash for dpu_id=%s: %s", dpu.id, exc)
    db.commit()

    enqueued: list[dict[str, object]] = []
    for dpu in renderable:
        task = flash_dpu_task.delay(project_id, dpu.id)
        enqueued.append({"dpu_id": dpu.id, "task_id": task.id})

    return {
        "enqueued": len(enqueued),
        "skipped_render_failures": render_failures,
        "max_parallel": MAX_PARALLEL_PROBES,
        "tasks": enqueued,
    }


@router.post(
    "/dpus/{dpu_id}/render-bf-conf",
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("render DPU bf.conf")
def render_dpu_bf_conf(project_id: int, dpu_id: int, db: Session = Depends(get_db)):
    """Render + lint the DPU's bf.conf without flashing. Persists the
    result on dpu.last_rendered_bf_conf so GET /rendered-bf-conf returns
    the same content until the next render or flash."""
    from services.dpu_flash_service import DpuFlashError, DpuFlashService
    try:
        content = DpuFlashService(db).render_only(project_id=project_id, dpu_id=dpu_id)
    except DpuFlashError as exc:
        from core.errors import BadRequestError
        raise BadRequestError(str(exc)) from exc
    from models.dpu import Dpu
    dpu = db.query(Dpu).filter(Dpu.id == dpu_id, Dpu.project_id == project_id).first()
    return {
        "dpu_id": dpu_id,
        "content": content,
        "rendered_at": dpu.last_rendered_bf_conf_at.isoformat() if dpu and dpu.last_rendered_bf_conf_at else None,
    }


@router.get(
    "/dpus/{dpu_id}/rendered-bf-conf",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get rendered DPU bf.conf")
def get_rendered_dpu_bf_conf(project_id: int, dpu_id: int, db: Session = Depends(get_db)):
    """Return the most recently rendered bf.conf for the DPU, or 404 if
    the DPU has never been rendered/flashed."""
    from core.errors import NotFoundError
    from models.dpu import Dpu
    dpu = db.query(Dpu).filter(Dpu.id == dpu_id, Dpu.project_id == project_id).first()
    if dpu is None:
        raise NotFoundError("Dpu", dpu_id)
    if not dpu.last_rendered_bf_conf:
        raise NotFoundError("rendered bf.conf", dpu_id)
    return {
        "dpu_id": dpu_id,
        "content": dpu.last_rendered_bf_conf,
        "rendered_at": dpu.last_rendered_bf_conf_at.isoformat() if dpu.last_rendered_bf_conf_at else None,
    }


@router.delete(
    "/dpus/{dpu_id}/flash",
    status_code=200,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("cancel DPU flash")
def cancel_flash(project_id: int, dpu_id: int, db: Session = Depends(get_db)):
    """Revoke an in-flight flash task. No-op if the task has already finished."""
    from celery_app import celery_app
    from models.dpu import Dpu

    dpu = (
        db.query(Dpu)
        .filter(Dpu.id == dpu_id, Dpu.project_id == project_id)
        .first()
    )
    if dpu is None:
        from core.errors import NotFoundError
        raise NotFoundError("Dpu", dpu_id)

    task_id = dpu.flash_celery_task_id
    if dpu.flash_status == "uploading" and task_id:
        # terminate=True → SIGTERM to the worker process mid-SFTP. The BFB
        # upload is not idempotent so we prefer a hard abort over waiting
        # for a safe checkpoint.
        celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
        dpu.flash_status = "cancelled"
        dpu.flash_stage_detail = None
        dpu.flash_error = "Flash cancelled by operator"
        from datetime import UTC, datetime
        dpu.flash_completed_at = datetime.now(UTC)
        db.commit()

    return {"dpu_id": dpu_id, "status": dpu.flash_status or "idle"}


# ─── Per-project DPU settings ───────────────────────────────────────────────

@router.get(
    "/dpu-settings",
    response_model=ProjectDpuSettingsResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get DPU settings")
def get_dpu_settings(project_id: int, db: Session = Depends(get_db)):
    """Get (lazily creates) the per-project DPU settings row."""
    result = ProjectDpuSettingsService(db).get_or_create(project_id)
    db.commit()
    return result


@router.patch(
    "/dpu-settings",
    response_model=ProjectDpuSettingsResponse,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("update DPU settings")
def update_dpu_settings(
    project_id: int,
    data: ProjectDpuSettingsUpdate,
    db: Session = Depends(get_db),
):
    result = ProjectDpuSettingsService(db).update(project_id, data)
    db.commit()
    return result


# ─── Reveal-plaintext-password routes (operator-only) ──────────────────────

@router.get(
    "/dpu-settings/reveal-oob-password",
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("reveal default BMC password")
def reveal_default_oob_password(project_id: int, db: Session = Depends(get_db)):
    return {"password": ProjectDpuSettingsService(db).reveal_default_oob_password(project_id)}


@router.get(
    "/dpu-settings/reveal-os-password",
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("reveal default OS password")
def reveal_default_os_password(project_id: int, db: Session = Depends(get_db)):
    return {"password": ProjectDpuSettingsService(db).reveal_default_os_password(project_id)}


@router.get(
    "/dpus/{dpu_id}/reveal-bmc-password",
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("reveal DPU BMC password")
def reveal_dpu_bmc_password(project_id: int, dpu_id: int, db: Session = Depends(get_db)):
    return {"password": DpuService(db).reveal_bmc_password(project_id, dpu_id)}
