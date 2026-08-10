"""Celery tasks for Proxy Migration (D-021 P3).

Each task execution is idempotent: execute_proxy_migration can be called
multiple times (once per state-machine resume); the service skips already-
completed steps.
"""

import logging

from celery_app import celery_app
from database import get_db_context

logger = logging.getLogger(__name__)


@celery_app.task(
    name="tasks.proxy_migration.execute_migration",
    bind=True,
    time_limit=1800,   # 30 min hard limit
    soft_time_limit=1680,
    acks_late=True,
    max_retries=0,
    queue="default",
)
def execute_proxy_migration(self, migration_id: int) -> dict:  # noqa: ARG001
    """Execute or resume a proxy migration run.

    Called:
    - After create_migration() (to start)
    - After confirm_cutover() (to continue past AWAITING_CUTOVER)
    - After confirm_teardown() (to continue past AWAITING_TEARDOWN)

    The service's execute_migration() is idempotent — completed steps are
    skipped automatically.
    """
    logger.info("Starting proxy migration execute: migration_id=%d", migration_id)

    with get_db_context() as db:
        from services.proxy_migration_service import ProxyMigrationService

        svc = ProxyMigrationService(db)

        # Link Celery task ID
        migration = svc.get_migration(migration_id)
        migration.celery_task_id = self.request.id
        db.commit()

        return svc.execute_migration(migration_id)
