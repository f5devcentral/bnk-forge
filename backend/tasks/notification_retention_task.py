"""
Notification Retention Task — periodic cleanup of old/excess notifications.

Runs daily via Celery Beat.

Responsibilities:
  1. Delete read notifications older than 30 days
  2. Cap each user to the 500 most-recent notifications (delete older overflow)
"""

import logging
from datetime import UTC, datetime, timedelta

from celery_app import celery_app
from database import get_db_context

logger = logging.getLogger(__name__)

RETENTION_DAYS = 30
MAX_PER_USER = 500


@celery_app.task(name="tasks.notification_retention_task.purge_old_notifications")
def purge_old_notifications():
    """
    Periodic cleanup of notifications.

    Called by Celery Beat once per day.
    - Deletes read notifications older than RETENTION_DAYS.
    - Caps each user to MAX_PER_USER total notifications (oldest deleted first).
    """
    with get_db_context() as db:
        try:
            from sqlalchemy import func

            from models import Notification

            cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)

            # 1. Delete read notifications older than 30 days
            old_read = (
                db.query(Notification)
                .filter(Notification.is_read == True, Notification.created_at < cutoff)  # noqa: E712
                .all()
            )
            deleted_old = len(old_read)
            for n in old_read:
                db.delete(n)

            # 2. Cap each user to MAX_PER_USER (skip "all" broadcast user)
            users = (
                db.query(Notification.user)
                .filter(Notification.user != "all")
                .group_by(Notification.user)
                .having(func.count(Notification.id) > MAX_PER_USER)
                .all()
            )

            deleted_overflow = 0
            for (username,) in users:
                # Get IDs of the MAX_PER_USER newest for this user
                keep_ids = (
                    db.query(Notification.id)
                    .filter(Notification.user == username)
                    .order_by(Notification.created_at.desc())
                    .limit(MAX_PER_USER)
                    .subquery()
                )
                overflow = (
                    db.query(Notification)
                    .filter(Notification.user == username, ~Notification.id.in_(keep_ids))
                    .all()
                )
                deleted_overflow += len(overflow)
                for n in overflow:
                    db.delete(n)

            db.commit()

            if deleted_old > 0 or deleted_overflow > 0:
                logger.info(
                    f"Notification retention: deleted {deleted_old} old read, "
                    f"{deleted_overflow} overflow"
                )

        except Exception as e:
            logger.error(f"Notification retention task error: {e}", exc_info=True)
