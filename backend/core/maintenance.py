"""
Maintenance mode management for backup/restore operations.

Uses Redis for multi-worker safe coordination. The maintenance flag has a
10-minute TTL as a safety net — if a restore crashes, the flag auto-clears.
"""
import json
import logging
from datetime import UTC, datetime

import redis

from core.config import settings

logger = logging.getLogger(__name__)

# Redis key for maintenance mode
MAINTENANCE_KEY = "bnkforge:maintenance_mode"
MAINTENANCE_TTL_SECONDS = 600  # 10 minutes safety timeout


def _get_redis() -> redis.Redis:
    """Get Redis connection from settings."""
    if not settings.REDIS_URL:
        raise RuntimeError("REDIS_URL not configured — maintenance mode requires Redis")
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


def set_maintenance_mode(message: str = "System maintenance in progress") -> None:
    """
    Enable maintenance mode.

    Args:
        message: Human-readable message to display to users
    """
    r = _get_redis()
    data = {
        "message": message,
        "started_at": datetime.now(UTC).isoformat(),
    }
    r.setex(MAINTENANCE_KEY, MAINTENANCE_TTL_SECONDS, json.dumps(data))
    logger.warning(f"Maintenance mode ENABLED: {message}")


def clear_maintenance_mode() -> None:
    """Disable maintenance mode."""
    r = _get_redis()
    r.delete(MAINTENANCE_KEY)
    logger.info("Maintenance mode CLEARED")


def get_maintenance_status() -> dict[str, str] | None:
    """
    Get current maintenance mode status.

    Returns:
        dict with {message, started_at} if in maintenance mode, None otherwise.
        Returns None if Redis is not configured (graceful degradation).
    """
    try:
        r = _get_redis()
        raw = r.get(MAINTENANCE_KEY)
        if raw:
            result: dict[str, str] = json.loads(raw)
            return result
    except (RuntimeError, redis.ConnectionError, redis.RedisError):
        # Redis not configured or unreachable — treat as not in maintenance
        return None
    except (TypeError, ValueError):
        # The key held something json.loads() could not read. This function runs
        # from maintenance_middleware on EVERY request, so letting that escape
        # turns one bad value into a 500 for the entire API. Degrade to "not in
        # maintenance", which is what this function already promises to do when
        # it cannot get an answer from Redis.
        logger.warning("Ignoring unparseable value under %s", MAINTENANCE_KEY)
        return None
    return None


def is_maintenance_mode() -> bool:
    """Check if system is in maintenance mode."""
    return get_maintenance_status() is not None
