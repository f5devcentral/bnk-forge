"""
Worker Heartbeat System for Celery Workers

PERF-009: Replace slow Celery inspect() with Redis-based heartbeat tracking.

Problem: celery_app.control.inspect() broadcasts to all workers and waits for
responses, taking 6-9 seconds per call - making the System page very slow.

Solution: Workers publish heartbeats to Redis on startup and task events.
API reads heartbeats instantly from Redis instead of broadcasting.

Redis Keys:
- worker:heartbeat:{worker_id} - Worker status hash (TTL: 30s)
- worker:tasks:{worker_id} - Set of active task IDs (no TTL, managed by signals)
"""
import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

from core.cache import cache

logger = logging.getLogger(__name__)

# Heartbeat TTL - if worker doesn't update within this time, considered offline
# Set to 90s to allow for heartbeat task interval (60s / CP-015) plus buffer
HEARTBEAT_TTL_SECONDS = 90

# Redis key prefixes
KEY_HEARTBEAT = "worker:heartbeat:"
KEY_ACTIVE_TASKS = "worker:tasks:"


class WorkerHeartbeat:
    """
    Manages worker heartbeat tracking via Redis.

    Workers call publish_heartbeat() periodically via Celery signals.
    API calls get_worker_status() to read instantly from Redis.

    Reuses the shared Redis connection from CacheService.
    """

    @property
    def redis(self) -> Any:
        """Get Redis client from shared cache instance"""
        return cache.redis

    def publish_heartbeat(
        self,
        worker_id: str,
        status: str = "ready",
        stats: dict[str, Any] | None = None
    ) -> bool:
        """
        Publish worker heartbeat to Redis.

        Called by Celery signals (worker_ready, heartbeat, task events).
        """
        if not self.redis:
            return False

        try:
            key = f"{KEY_HEARTBEAT}{worker_id}"
            data = {
                "worker_id": worker_id,
                "status": status,
                "last_heartbeat": datetime.now(UTC).isoformat(),
                "timestamp": str(time.time()),
            }
            if stats:
                data["stats"] = json.dumps(stats)

            # Set with TTL - worker must refresh or be marked as offline
            self.redis.hset(key, mapping=data)
            self.redis.expire(key, HEARTBEAT_TTL_SECONDS)

            logger.debug(f"Heartbeat published: {worker_id} - {status}")
            return True
        except Exception as e:
            logger.warning(f"Failed to publish heartbeat for {worker_id}: {e}")
            return False

    def record_task_start(self, worker_id: str, task_id: str, task_name: str) -> bool:
        """Record that a task has started on a worker (called by task_prerun signal)."""
        if not self.redis:
            return False

        try:
            key = f"{KEY_ACTIVE_TASKS}{worker_id}"
            task_data = json.dumps({
                "task_id": task_id,
                "task_name": task_name,
                "started_at": datetime.now(UTC).isoformat()
            })
            self.redis.sadd(key, task_data)
            self.publish_heartbeat(worker_id, status="busy")

            logger.debug(f"Task started: {task_id} on {worker_id}")
            return True
        except Exception as e:
            logger.warning(f"Failed to record task start: {e}")
            return False

    def record_task_end(self, worker_id: str, task_id: str) -> bool:
        """Record that a task has completed on a worker (called by task_postrun signal)."""
        if not self.redis:
            return False

        try:
            key = f"{KEY_ACTIVE_TASKS}{worker_id}"

            # Find and remove the task entry (scan set for matching task_id)
            for task_json in self.redis.smembers(key):
                try:
                    if json.loads(task_json).get("task_id") == task_id:
                        self.redis.srem(key, task_json)
                        break
                except json.JSONDecodeError:
                    continue

            # Update status based on remaining tasks
            remaining = self.redis.scard(key)
            self.publish_heartbeat(worker_id, status="busy" if remaining > 0 else "ready")

            logger.debug(f"Task ended: {task_id} on {worker_id}, remaining: {remaining}")
            return True
        except Exception as e:
            logger.warning(f"Failed to record task end: {e}")
            return False

    def mark_worker_offline(self, worker_id: str) -> bool:
        """Mark a worker as offline (called on worker shutdown)."""
        if not self.redis:
            return False

        try:
            self.redis.delete(
                f"{KEY_HEARTBEAT}{worker_id}",
                f"{KEY_ACTIVE_TASKS}{worker_id}"
            )
            logger.info(f"Worker marked offline: {worker_id}")
            return True
        except Exception as e:
            logger.warning(f"Failed to mark worker offline: {e}")
            return False

    def get_worker_status(self) -> dict[str, Any]:
        """
        Get status of all workers from Redis.

        This is the fast replacement for celery_app.control.inspect().
        Returns instantly from Redis instead of broadcasting to workers.
        """
        if not self.redis:
            return self._empty_status("Redis unavailable")

        try:
            workers = []
            total_active_tasks = 0

            # Scan for all heartbeat keys
            cursor = 0
            while True:
                cursor, keys = self.redis.scan(cursor, match=f"{KEY_HEARTBEAT}*", count=100)

                for key in keys:
                    worker_data = self.redis.hgetall(key)
                    if not worker_data:
                        continue

                    worker_id = worker_data.get("worker_id", key.replace(KEY_HEARTBEAT, ""))

                    # Get active tasks for this worker
                    tasks_key = f"{KEY_ACTIVE_TASKS}{worker_id}"
                    active_tasks = self._get_active_tasks(tasks_key)
                    total_active_tasks += len(active_tasks)

                    workers.append({
                        "worker_id": worker_id,
                        "status": worker_data.get("status", "unknown"),
                        "last_heartbeat": worker_data.get("last_heartbeat"),
                        "active_tasks": len(active_tasks),
                        "tasks": active_tasks,
                        "stats": self._parse_json(worker_data.get("stats"))
                    })

                if cursor == 0:
                    break

            active_count = len([w for w in workers if w["status"] in ("ready", "busy")])

            return {
                "workers": workers,
                "total": len(workers),
                "active": active_count,
                "offline": 0,  # Workers that miss heartbeat are auto-removed by TTL
                "active_tasks": total_active_tasks
            }

        except Exception as e:
            logger.error(f"Failed to get worker status: {e}")
            return self._empty_status(str(e))

    def get_queue_stats(self) -> dict[str, dict[str, int]]:
        """Get per-queue statistics from active tasks."""
        default_stats = {"default": {"pending": 0, "active": 0}, "opentofu": {"pending": 0, "active": 0}}

        if not self.redis:
            return default_stats

        try:
            queue_stats = {"default": {"pending": 0, "active": 0}, "opentofu": {"pending": 0, "active": 0}}

            cursor = 0
            while True:
                cursor, keys = self.redis.scan(cursor, match=f"{KEY_ACTIVE_TASKS}*", count=100)

                for key in keys:
                    for task in self._get_active_tasks(key):
                        task_name = task.get("task_name", "")
                        queue = "opentofu" if "opentofu" in task_name.lower() else "default"
                        queue_stats[queue]["active"] += 1

                if cursor == 0:
                    break

            return queue_stats

        except Exception as e:
            logger.warning(f"Failed to get queue stats: {e}")
            return default_stats

    def _get_active_tasks(self, key: str) -> list:
        """Parse active tasks from Redis set."""
        tasks = []
        for task_json in self.redis.smembers(key):
            parsed = self._parse_json(task_json)
            if parsed:
                tasks.append(parsed)
        return tasks

    def _parse_json(self, value: str | None) -> Any | None:
        """Safely parse JSON string."""
        if not value:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    def _empty_status(self, error: str | None = None) -> dict[str, Any]:
        """Return empty worker status dict."""
        result = {"workers": [], "total": 0, "active": 0, "offline": 0, "active_tasks": 0}
        if error:
            result["error"] = error
        return result


# Global heartbeat instance
heartbeat = WorkerHeartbeat()
