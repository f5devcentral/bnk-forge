"""
LLM Metrics ingest service — stores operator-pushed snapshots in Redis.

The bnk-operator scrapes vLLM ``/metrics`` in-cluster and pushes one
``{type:"llm_metrics", ...}`` message per analyzer over the operator WebSocket
(or polling path).  This service persists the latest snapshot under a
per-(cluster_id, namespace, name) key with a short TTL so the
``get_backend_health`` read path can prefer fresh operator data over the
fallback Celery scrape.

Key design decisions:
  - cluster_id is ALWAYS server-resolved from the linked ConnectedOperator;
    the payload cluster_id is NEVER trusted (R2 security).
  - If the operator is unlinked (cluster_id is None), the snapshot is dropped
    and a debug log is emitted — no error, no exception.
  - TTL-as-freshness: if the key is absent, the data is stale; no updated_at
    clock comparison (clock-skew-brittle).
  - Key space is distinct (``llm_metrics:`` prefix) from the existing
    ``backend_health:`` Celery cache so they don't collide.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

LLM_METRICS_TTL_SECONDS = 60
LLM_METRICS_KEY_PREFIX = "llm_metrics"


def store_llm_metrics(
    db: Session,
    operator_id: str,
    analyzer_namespace: str,
    analyzer_name: str,
    data: dict[str, Any],
) -> bool:
    """
    Resolve the operator's linked cluster_id and persist the snapshot in Redis.

    Returns True if stored, False if dropped (unlinked operator or Redis
    unavailable).  Never raises.
    """
    cluster_id = _resolve_cluster_id(db, operator_id)
    if cluster_id is None:
        logger.debug(
            "llm_metrics: operator %s is unlinked — snapshot for %s/%s dropped",
            operator_id,
            analyzer_namespace,
            analyzer_name,
        )
        return False

    redis = _get_redis()
    if redis is None:
        return False

    key = f"{LLM_METRICS_KEY_PREFIX}:{cluster_id}:{analyzer_namespace}:{analyzer_name}"
    try:
        redis.setex(key, LLM_METRICS_TTL_SECONDS, json.dumps(data))
        logger.debug(
            "llm_metrics: stored snapshot for cluster=%s %s/%s (TTL=%ds)",
            cluster_id,
            analyzer_namespace,
            analyzer_name,
            LLM_METRICS_TTL_SECONDS,
        )
        return True
    except Exception:  # noqa: BLE001 — Redis write failure is non-fatal
        logger.warning("llm_metrics: Redis write failed for %s", key, exc_info=True)
        return False


def get_llm_metrics_snapshot(
    cluster_id: int,
    namespace: str,
    name: str,
) -> dict[str, Any] | None:
    """
    Return the operator-pushed snapshot if fresh (key exists in Redis), else None.

    Called at the TOP of ``get_backend_health()`` as an early-return path.
    """
    redis = _get_redis()
    if redis is None:
        return None

    key = f"{LLM_METRICS_KEY_PREFIX}:{cluster_id}:{namespace}:{name}"
    try:
        raw = redis.get(key)
        if not raw:
            return None
        data = json.loads(raw)
        # Attach source hint — ignored by FE (no response_model strips extras)
        data["source"] = "operator"
        return data
    except Exception:  # noqa: BLE001
        return None


def _resolve_cluster_id(db: Session, operator_id: str) -> int | None:
    """Look up ConnectedOperator.cluster_id for the given operator_id."""
    from models import ConnectedOperator

    op = db.query(ConnectedOperator).filter(
        ConnectedOperator.operator_id == operator_id,
    ).first()
    if op is None:
        return None
    return op.cluster_id  # type: ignore[return-value]


def _get_redis() -> Any:
    try:
        from redis import Redis

        from core.config import settings

        redis_url = getattr(settings, "REDIS_URL", "redis://redis:6379/0")
        return Redis.from_url(redis_url, decode_responses=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("llm_metrics: Redis unavailable: %s", e)
        return None
