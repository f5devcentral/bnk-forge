"""
Best-effort background cluster scan triggered after register/update.

Enqueued at the commit boundary (route handler or task layer) after a new
KubernetesCluster row is committed. The task acquires its own DB session so
it never races with the API commit that created the row.

Fire-and-forget: scanner failures are swallowed and logged at WARNING.
The existing UI Rescan button is the user's escape hatch for persistent failures.
"""

import logging
from typing import Any

from celery_app import celery_app
from database import get_db_context
from services.cluster_scanner import ClusterScanner

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.cluster_scan.scan_cluster_async", bind=True)
def scan_cluster_async(self: Any, cluster_id: int) -> None:
    """Best-effort background scan after cluster register/update.

    Acquires its own DB session; never receives a Session as an argument
    (Celery serializes via JSON — Session objects don't serialize).
    No autoretry: scanner failures (bad kubeconfig, unreachable tunnel,
    missing creds) are not transient. Task always returns normally.
    """
    try:
        with get_db_context() as db:
            ClusterScanner(db).scan(cluster_id)
            db.commit()
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("scan_cluster_async failed for cluster_id=%s: %s", cluster_id, exc)


def enqueue_cluster_scan(cluster_id: int) -> None:
    """Fire-and-forget enqueue of a background scan after register/update.

    Swallows broker exceptions so the calling API request still succeeds
    when the broker is unreachable.
    """
    try:
        scan_cluster_async.delay(cluster_id)
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning(
            "Failed to enqueue scan_cluster_async for cluster_id=%s: %s", cluster_id, exc
        )
