"""
Worker Heartbeat Keepalive Task

This task runs every 15 seconds via Celery Beat to refresh the worker heartbeat
in Redis. Without this, idle workers would disappear after the 30s TTL expires.

The task is distributed to all workers, ensuring each worker refreshes its own
heartbeat when it processes the task.
"""
from celery import shared_task


@shared_task(name='tasks.heartbeat_task.worker_heartbeat_keepalive', bind=True, ignore_result=True)
def worker_heartbeat_keepalive(self):
    """
    Refresh worker heartbeat in Redis.

    This task is scheduled by Celery Beat every 60 seconds (CP-015).
    When a worker picks it up, it refreshes that worker's heartbeat.
    ignore_result=True prevents storing result keys in Redis.
    """
    try:
        from core.worker_heartbeat import heartbeat
        worker_id = self.request.hostname or "unknown"
        heartbeat.publish_heartbeat(worker_id, status="ready")
        return {"worker": worker_id, "status": "heartbeat_refreshed"}
    except Exception as e:
        # Don't fail - just log
        return {"error": str(e)}
