"""
Operator Cleanup Task — periodic stale connection detection + command expiration.

Runs every 2 minutes via Celery Beat.

Responsibilities:
  1. Mark polling-mode operators as disconnected if their heartbeat is too old
  2. Expire queued commands that were never picked up
"""

import logging

from celery_app import celery_app
from database import get_db_context

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.operator_cleanup_task.cleanup_stale_operators_and_commands")
def cleanup_stale_operators_and_commands():
    """
    Periodic cleanup of stale operator connections and expired commands.

    Called by Celery Beat every 2 minutes.
    """
    with get_db_context() as db:
        try:
            from services.operator_registry import cleanup_expired_commands, cleanup_stale_operators

            stale_count = cleanup_stale_operators(db)
            expired_count = cleanup_expired_commands(db)
            db.commit()

            if stale_count > 0 or expired_count > 0:
                logger.info(
                    f"Operator cleanup: {stale_count} stale operator(s), "
                    f"{expired_count} expired command(s)"
                )

        except Exception as e:
            logger.error(f"Operator cleanup task error: {e}", exc_info=True)
