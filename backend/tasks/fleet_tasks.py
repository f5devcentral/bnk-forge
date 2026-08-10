"""Fleet inventory + bulk-ops Celery tasks — D-022.

Tasks:
  - backfill_fleet_members: Iterate all cluster/host/DPU rows, upsert fleet
    membership records, and prune memberships whose target row is gone.
  - run_fleet_bulk_op: Execute a fleet bulk run across a Decision's member set.
  - resume_fleet_bulk_op: Resume a paused bulk run from current_wave_index.

Beat entry: backfill ~900s (every 15 minutes). Bulk-op tasks are triggered
on demand.
"""

import logging

from celery_app import celery_app
from database import get_db_context

logger = logging.getLogger(__name__)


@celery_app.task(
    name="tasks.fleet_tasks.backfill_fleet_members",
    bind=False,
    time_limit=600,        # 10-minute hard cap; full backfill of 1000s rows is fast
    soft_time_limit=540,
    acks_late=True,
    max_retries=0,
)
def backfill_fleet_members() -> dict:
    """Reconcile all managed targets and prune stale memberships.

    Reconcile phase: iterate every KubernetesCluster, BareMetalHost, and Dpu
    row and call reconcile_fleet_member for each.

    Prune phase: for each FleetMember whose target row no longer exists in the
    DB, delete the membership record.

    Returns a summary dict for monitoring.
    """
    from models.bare_metal import BareMetalHost
    from models.dpu import Dpu
    from models.fleet import MEMBER_TYPE_CLUSTER, MEMBER_TYPE_DPU, MEMBER_TYPE_HOST, FleetMember
    from models.kubernetes import KubernetesCluster
    from services.fleet_reconcile_service import reconcile_fleet_member

    reconciled = 0
    skipped = 0
    pruned = 0
    errors = 0

    with get_db_context() as db:
        # --- Reconcile phase ---

        # Clusters
        for cluster in db.query(KubernetesCluster).all():
            try:
                result = reconcile_fleet_member(db, MEMBER_TYPE_CLUSTER, cluster.id)
                if result is not None:
                    reconciled += 1
                else:
                    skipped += 1
            except Exception:
                logger.exception(
                    "backfill_fleet_members: reconcile failed for cluster id=%s",
                    cluster.id,
                )
                errors += 1

        # Hosts
        for host in db.query(BareMetalHost).all():
            try:
                result = reconcile_fleet_member(db, MEMBER_TYPE_HOST, host.id)
                if result is not None:
                    reconciled += 1
                else:
                    skipped += 1
            except Exception:
                logger.exception(
                    "backfill_fleet_members: reconcile failed for host id=%s",
                    host.id,
                )
                errors += 1

        # DPUs
        for dpu in db.query(Dpu).all():
            try:
                result = reconcile_fleet_member(db, MEMBER_TYPE_DPU, dpu.id)
                if result is not None:
                    reconciled += 1
                else:
                    skipped += 1
            except Exception:
                logger.exception(
                    "backfill_fleet_members: reconcile failed for dpu id=%s",
                    dpu.id,
                )
                errors += 1

        db.commit()

        # --- Prune phase: remove memberships whose target row is gone ---

        # Build live ID sets for each type
        live_cluster_ids = {row.id for row in db.query(KubernetesCluster.id).all()}
        live_host_ids = {row.id for row in db.query(BareMetalHost.id).all()}
        live_dpu_ids = {row.id for row in db.query(Dpu.id).all()}

        live_by_type = {
            MEMBER_TYPE_CLUSTER: live_cluster_ids,
            MEMBER_TYPE_HOST: live_host_ids,
            MEMBER_TYPE_DPU: live_dpu_ids,
        }

        for member in db.query(FleetMember).all():
            live_ids = live_by_type.get(member.member_type)
            if live_ids is None:
                # Unknown type — leave alone; D-023 may handle it
                continue
            if member.member_id not in live_ids:
                logger.info(
                    "backfill_fleet_members: pruning orphan member id=%s "
                    "(%s/%s — target row gone)",
                    member.id,
                    member.member_type,
                    member.member_id,
                )
                db.delete(member)
                pruned += 1

        db.commit()

    summary = {
        "reconciled": reconciled,
        "skipped": skipped,
        "pruned": pruned,
        "errors": errors,
    }
    logger.info("backfill_fleet_members: %s", summary)
    return summary


# ---------------------------------------------------------------------------
# Fleet Bulk Operations — D-022 Phase 2
# ---------------------------------------------------------------------------


@celery_app.task(
    name="tasks.fleet_tasks.run_fleet_bulk_op",
    bind=True,
    time_limit=3600,       # 1-hour hard cap; bulk reconcile of many members
    soft_time_limit=3300,
    acks_late=True,
    max_retries=0,
)
def run_fleet_bulk_op(self, run_id: int) -> dict:
    """Execute a fleet bulk operation for the given FleetBulkRun ID.

    Delegates to services/fleet_bulkop_service.py::execute_fleet_bulk_run.
    Records celery_task_id on the run before delegating.
    """
    from database import get_db_context
    from models.fleet_targeting import FleetBulkRun

    # Stamp the Celery task ID on the run record so it can be revoked.
    with get_db_context() as db:
        run = db.query(FleetBulkRun).filter(FleetBulkRun.id == run_id).first()
        if run is not None:
            run.celery_task_id = self.request.id
            db.commit()

    from services.fleet_bulkop_service import execute_fleet_bulk_run

    return execute_fleet_bulk_run(run_id)


@celery_app.task(
    name="tasks.fleet_tasks.resume_fleet_bulk_op",
    bind=True,
    time_limit=3600,
    soft_time_limit=3300,
    acks_late=True,
    max_retries=0,
)
def resume_fleet_bulk_op(self, run_id: int) -> dict:
    """Resume a paused fleet bulk run from its current_wave_index.

    The run must be in 'paused_gate' status. The executor resumes from
    the current_wave_index that was persisted when the gate paused it.
    """
    from database import get_db_context
    from models.fleet_targeting import BULK_RUN_STATUS_PAUSED_GATE, FleetBulkRun

    with get_db_context() as db:
        run = db.query(FleetBulkRun).filter(FleetBulkRun.id == run_id).first()
        if run is None:
            raise ValueError(f"FleetBulkRun {run_id} not found.")
        if run.status != BULK_RUN_STATUS_PAUSED_GATE:
            raise ValueError(
                f"FleetBulkRun {run_id} cannot be resumed — status is '{run.status}' "
                f"(must be 'paused_gate')."
            )
        run.celery_task_id = self.request.id
        db.commit()

    from services.fleet_bulkop_service import execute_fleet_bulk_run

    return execute_fleet_bulk_run(run_id, resuming=True)
