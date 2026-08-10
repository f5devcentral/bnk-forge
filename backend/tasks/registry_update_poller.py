"""Periodically check registry-imported modules for newer upstream versions.

Runs in the worker container every 6h (see beat_schedule). For each module whose
ModuleSource.source_type is one of the registry types, this task asks the
upstream registry for the latest version and updates `latest_version` +
`update_available` columns. It does NOT auto-import — that requires explicit
user action via `POST /api/registry/modules/{id}/upgrade`.

Rate-limited to ~1 request/second to stay well under public registry quotas.
"""

from __future__ import annotations

import logging
import time

from celery_app import celery_app
from database import get_db_context
from models import ModuleLibrary, ModuleSource
from services.registry_service import RegistryService

logger = logging.getLogger(__name__)

REGISTRY_SOURCE_TYPES = {"terraform_registry", "opentofu_registry", "tfc_private", "registry"}
RATE_LIMIT_SECONDS = 1.1


@celery_app.task(name="tasks.registry.poll_updates")
def poll_updates() -> dict:
    """Iterate registry modules, refresh latest_version + update_available."""
    checked = 0
    bumped = 0
    errors = 0

    with get_db_context() as db:
        rows = (
            db.query(ModuleLibrary)
            .join(ModuleSource, ModuleLibrary.module_source_id == ModuleSource.id)
            .filter(ModuleSource.source_type.in_(REGISTRY_SOURCE_TYPES))
            .filter(ModuleLibrary.is_active.is_(True))
            .all()
        )
        module_ids = [row.id for row in rows]

    for module_id in module_ids:
        try:
            with get_db_context() as db:
                service = RegistryService(db)
                result = service.check_for_updates(module_id)
                db.commit()
            checked += 1
            if result.get("update_available"):
                bumped += 1
        except Exception:  # noqa: BLE001 — keep poller resilient
            logger.exception("registry poll: check_for_updates(%s) failed", module_id)
            errors += 1
        time.sleep(RATE_LIMIT_SECONDS)

    logger.info(
        "registry poll complete: checked=%s update_available=%s errors=%s",
        checked, bumped, errors,
    )
    return {"checked": checked, "update_available": bumped, "errors": errors}
