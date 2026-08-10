"""
Celery tasks for BNK Upgrade Workflow.

Sprint 8.2 — async execution of upgrade plans with streaming output.

Phase 1.6: Lock acquired in the task entrypoint (matches Phase 1.5 pattern).
The service receives ``lock`` as a keyword arg and routes all status writes
through set_locked_entity_fields (fence-protected). Concurrent
execute_upgrade_task / rollback_upgrade_task invocations on the same
upgrade_id block until the lock is released or the heartbeat goes stale.
"""

import hashlib
import logging
from datetime import UTC, datetime

from celery_app import celery_app
from database import get_db_context
from models import BnkUpgrade
from services.entity_lock import EntityLockError, EntityLockLostError, entity_lock

logger = logging.getLogger(__name__)


def _celery_task_int(celery_task_id: str | None, fallback: int = 0) -> int:
    """Convert a Celery UUID task ID to a stable BIGINT for holding_task_id.

    Returns ``fallback`` when ``celery_task_id`` is None (e.g. direct call in tests).
    """
    if celery_task_id is None:
        return fallback
    digest = hashlib.sha256(celery_task_id.encode()).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF


@celery_app.task(bind=True, name="tasks.bnk_upgrade.execute_upgrade", queue="default")
def execute_upgrade_task(self, upgrade_id: int):
    """
    Execute a BNK upgrade plan asynchronously.

    Acquires an entity lock on the BnkUpgrade row so concurrent executions
    on the same upgrade_id are serialized. Updates BnkUpgrade.celery_task_id
    for status tracking. Streams output via Redis pub/sub for WebSocket delivery.
    """
    with get_db_context() as db:
        upgrade = None
        try:
            upgrade = db.query(BnkUpgrade).filter(BnkUpgrade.id == upgrade_id).first()
            if not upgrade:
                logger.error(f"Upgrade {upgrade_id} not found")
                return {"success": False, "error": "Upgrade not found"}

            # Store celery task ID (not fence-protected — just metadata for UI)
            upgrade.celery_task_id = self.request.id
            db.commit()

            # Set up output streaming via Redis pub/sub
            output_lines = []

            def on_output(line: str):
                output_lines.append(line)
                try:
                    from services.websocket_service import publish_event
                    publish_event(f"upgrade:{upgrade_id}", {
                        "type": "upgrade_output",
                        "upgrade_id": upgrade_id,
                        "line": line,
                        "timestamp": datetime.now(UTC).isoformat(),
                    })
                except Exception as ws_err:
                    logger.debug(f"WebSocket upgrade output delivery failed: {ws_err}")

            # UX-011: Auto-snapshot before upgrade operations
            try:
                if upgrade.project_id:
                    from services.snapshot_service import SnapshotService
                    snap_service = SnapshotService(db)
                    snap_service.create_snapshot(
                        project_id=upgrade.project_id,
                        trigger="auto-before-upgrade",
                        cluster_id=upgrade.cluster_id,
                    )
                    db.commit()
                    logger.info(f"Auto-snapshot created before upgrade {upgrade_id}")
            except Exception as snap_err:
                logger.warning(f"Auto-snapshot failed before upgrade {upgrade_id} (non-blocking): {snap_err}")

            from services.bnk_upgrade_service import BnkUpgradeService
            service = BnkUpgradeService(db)

            with entity_lock(db, table="bnk_upgrades", entity_id=upgrade_id, task_id=_celery_task_int(self.request.id, fallback=upgrade_id)) as lock:
                result = service.execute_upgrade(upgrade_id, on_output=on_output, lock=lock)

            # Fire alert on completion
            try:
                from services.alert_service import fire_alert
                if result.status == "completed":
                    fire_alert(
                        db,
                        event_type="upgrade_success",
                        severity="info",
                        title=f"BNK upgrade completed: {result.from_version} → {result.to_version}",
                        message=f"Cluster upgrade completed in {result.duration_seconds:.0f}s",
                        cluster_id=result.cluster_id,
                        project_id=result.project_id,
                    )
                    db.commit()
                elif result.status == "failed":
                    fire_alert(
                        db,
                        event_type="upgrade_failed",
                        severity="critical",
                        title=f"BNK upgrade failed at step {result.error_step}",
                        message=result.error_message or "Unknown error",
                        cluster_id=result.cluster_id,
                        project_id=result.project_id,
                    )
                    db.commit()
            except Exception as e:
                logger.warning(f"Failed to fire upgrade alert: {e}")

            return {
                "success": result.status == "completed",
                "status": result.status,
                "upgrade_id": upgrade_id,
                "from_version": result.from_version,
                "to_version": result.to_version,
                "duration_seconds": result.duration_seconds,
                "error": result.error_message,
            }

        except EntityLockError as e:
            logger.error("execute_upgrade_task failed - entity locked: upgrade_id=%d err=%s", upgrade_id, e)
            if upgrade:
                try:
                    upgrade.status = "failed"
                    upgrade.error_message = f"Could not acquire lock: {e}"
                    upgrade.completed_at = datetime.now(UTC)
                    if upgrade.started_at:
                        upgrade.duration_seconds = (upgrade.completed_at - upgrade.started_at).total_seconds()
                    db.commit()
                except Exception:
                    pass
            raise

        except EntityLockLostError as e:
            logger.warning("execute_upgrade_task aborted - lock lost: upgrade_id=%d err=%s", upgrade_id, e)
            if upgrade:
                try:
                    upgrade.status = "failed"
                    upgrade.error_message = f"Lock lost mid-upgrade: {e}"
                    upgrade.completed_at = datetime.now(UTC)
                    if upgrade.started_at:
                        upgrade.duration_seconds = (upgrade.completed_at - upgrade.started_at).total_seconds()
                    db.commit()
                except Exception:
                    pass
            raise

        except Exception as e:
            logger.error(f"Upgrade task failed: {e}", exc_info=True)

            # Mark upgrade as failed
            try:
                upgrade = db.query(BnkUpgrade).filter(BnkUpgrade.id == upgrade_id).first()
                if upgrade and upgrade.status == "in_progress":
                    upgrade.status = "failed"
                    upgrade.error_message = f"Task error: {e}"
                    upgrade.completed_at = datetime.now(UTC)
                    if upgrade.started_at:
                        upgrade.duration_seconds = (upgrade.completed_at - upgrade.started_at).total_seconds()
                    db.commit()
            except Exception as cleanup_err:
                logger.warning(f"Failed to mark upgrade as failed: {cleanup_err}")

            return {"success": False, "error": str(e), "upgrade_id": upgrade_id}


@celery_app.task(bind=True, name="tasks.bnk_upgrade.rollback_upgrade", queue="default")
def rollback_upgrade_task(self, upgrade_id: int):
    """
    Execute a rollback for a failed BNK upgrade.

    Acquires an entity lock on the BnkUpgrade row so a concurrent
    execute_upgrade_task (e.g. a stuck resume) cannot race with the rollback.
    """
    with get_db_context() as db:
        upgrade = None
        try:
            upgrade = db.query(BnkUpgrade).filter(BnkUpgrade.id == upgrade_id).first()
            if not upgrade:
                logger.error(f"Upgrade {upgrade_id} not found for rollback")
                return {"success": False, "error": "Upgrade not found"}

            output_lines = []

            def on_output(line: str):
                output_lines.append(line)
                try:
                    from services.websocket_service import publish_event
                    publish_event(f"upgrade:{upgrade_id}", {
                        "type": "rollback_output",
                        "upgrade_id": upgrade_id,
                        "line": line,
                        "timestamp": datetime.now(UTC).isoformat(),
                    })
                except Exception as ws_err:
                    logger.debug(f"WebSocket rollback output delivery failed: {ws_err}")

            from services.bnk_upgrade_service import BnkUpgradeService
            service = BnkUpgradeService(db)

            with entity_lock(db, table="bnk_upgrades", entity_id=upgrade_id, task_id=_celery_task_int(self.request.id, fallback=upgrade_id)) as lock:
                result = service.rollback(upgrade_id, on_output=on_output, lock=lock)

            # Fire alert
            try:
                from services.alert_service import fire_alert
                fire_alert(
                    db,
                    event_type="upgrade_rollback",
                    severity="warning",
                    title="BNK upgrade rolled back on cluster",
                    message=f"Rolled back from {result.to_version} to {result.from_version}",
                    cluster_id=result.cluster_id,
                    project_id=result.project_id,
                )
                db.commit()
            except Exception as alert_err:
                logger.warning(f"Failed to fire rollback alert: {alert_err}")

            return {
                "success": result.status == "rolled_back",
                "status": result.status,
                "upgrade_id": upgrade_id,
            }

        except EntityLockError as e:
            logger.error("rollback_upgrade_task failed - entity locked: upgrade_id=%d err=%s", upgrade_id, e)
            if upgrade:
                try:
                    upgrade.status = "failed"
                    upgrade.error_message = f"Could not acquire lock for rollback: {e}"
                    upgrade.completed_at = datetime.now(UTC)
                    if upgrade.started_at:
                        upgrade.duration_seconds = (upgrade.completed_at - upgrade.started_at).total_seconds()
                    db.commit()
                except Exception:
                    pass
            raise

        except EntityLockLostError as e:
            logger.warning("rollback_upgrade_task aborted - lock lost: upgrade_id=%d err=%s", upgrade_id, e)
            if upgrade:
                try:
                    upgrade.status = "failed"
                    upgrade.error_message = f"Lock lost mid-rollback: {e}"
                    upgrade.completed_at = datetime.now(UTC)
                    if upgrade.started_at:
                        upgrade.duration_seconds = (upgrade.completed_at - upgrade.started_at).total_seconds()
                    db.commit()
                except Exception:
                    pass
            raise

        except Exception as e:
            logger.error(f"Rollback task failed: {e}", exc_info=True)
            return {"success": False, "error": str(e), "upgrade_id": upgrade_id}
