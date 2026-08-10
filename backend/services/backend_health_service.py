"""
Backend health — thin dispatcher to the Celery task that runs llmtop.

The heavy work (analyzer walk + llmtop subprocess) lives in
``tasks.backend_health_task.fetch_backend_health`` because the celery worker
container has aws CLI + kubectl baked in, so EKS kubeconfigs with
``exec: aws eks get-token`` auth correctly. This module only:

  1. Returns a fresh-enough cached value from Redis if present (5s TTL).
  2. Otherwise dispatches the task and waits for the result with a timeout.
  3. Caches the result under a per-(cluster, ns, name) key.

Concurrent panel renders for the same analyzer therefore coalesce to one task
fire per ~5s rather than each one forking llmtop.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, cast

from celery.exceptions import TimeoutError as CeleryTimeoutError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 5
TASK_TIMEOUT_SECONDS = 20


def get_backend_health(
    db: Session,  # noqa: ARG001 — kept for route-handler symmetry; task opens its own session
    cluster_id: int,
    namespace: str,
    name: str,
) -> dict[str, Any]:
    """Read-through cached dispatch to the backend-health Celery task.

    Prefers a fresh operator-pushed snapshot (Redis key ``llm_metrics:…``,
    TTL=60s) before falling through to the Celery scrape path.  The operator
    snapshot has a ``source:"operator"`` hint that the frontend ignores.
    """
    from services.llm_metrics_service import get_llm_metrics_snapshot

    operator_snapshot = get_llm_metrics_snapshot(cluster_id, namespace, name)
    if operator_snapshot is not None:
        return operator_snapshot

    cache_key = f"backend_health:{cluster_id}:{namespace}:{name}"
    redis = _get_redis()

    if redis is not None:
        cached = redis.get(cache_key)
        if cached:
            try:
                return cast(dict[str, Any], json.loads(cached))
            except json.JSONDecodeError:
                pass

    # Cache miss — dispatch the task and wait for the result
    from tasks.backend_health_task import fetch_backend_health

    try:
        result = cast(
            dict[str, Any],
            fetch_backend_health.apply_async(
                args=[cluster_id, namespace, name],
            ).get(timeout=TASK_TIMEOUT_SECONDS),
        )
    except CeleryTimeoutError:
        return {
            "available": False,
            "reason": f"backend-health task timed out after {TASK_TIMEOUT_SECONDS}s",
            "backends": [],
            "errors": {},
            "updated_at": datetime.now(UTC).isoformat(),
        }
    except Exception as e:  # noqa: BLE001 — surface task errors to the UI rather than 500
        logger.exception("backend-health dispatch failed")
        return {
            "available": False,
            "reason": f"backend-health dispatch failed: {e}",
            "backends": [],
            "errors": {},
            "updated_at": datetime.now(UTC).isoformat(),
        }

    if redis is not None:
        try:
            redis.setex(cache_key, CACHE_TTL_SECONDS, json.dumps(result))
        except Exception:  # noqa: BLE001 — cache write failures are non-fatal
            pass

    return result


def _get_redis() -> Any:
    try:
        from redis import Redis

        from core.config import settings
        redis_url = getattr(settings, "REDIS_URL", "redis://redis:6379/0")
        return Redis.from_url(redis_url, decode_responses=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("backend-health: Redis unavailable, will dispatch every request: %s", e)
        return None
