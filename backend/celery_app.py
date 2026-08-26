"""
Celery application configuration for BNK-Forge task queue.

This module configures Celery with Redis as the message broker and result backend.
Handles long-running OpenTofu operations asynchronously.
"""
import os
import sys

# Ensure /app is in the path for module imports
if '/app' not in sys.path:
    sys.path.insert(0, '/app')

# DEVOPS-004: Configure structured logging for Celery workers
from core.logging_config import configure_logging

configure_logging(
    environment=os.getenv("ENVIRONMENT", "development"),
    log_level=os.getenv("LOG_LEVEL", "INFO"),
    service_name="bnk-forge-worker"
)

from celery import Celery
from celery.signals import (
    task_postrun,
    task_prerun,
    worker_process_init,
    worker_ready,
    worker_shutdown,
)
from kombu import Exchange, Queue

# Get Redis URL from environment
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)

# Create Celery app
celery_app = Celery(
    "bnk_forge",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=["tasks.opentofu_tasks", "tasks.kubernetes_tasks", "tasks.ansible_tasks", "tasks.ssh_tasks", "tasks.tmos_tasks", "tasks.cli_tasks", "tasks.bnk_upgrade_tasks", "tasks.drift_tasks", "tasks.stack_tasks", "tasks.parallel_tasks", "tasks.heartbeat_task", "tasks.health_monitor_task", "tasks.operator_cleanup_task", "tasks.proxy_deploy_tasks", "tasks.proxy_migration_tasks", "tasks.bare_metal_tasks", "tasks.dpu_tasks", "tasks.backend_health_task", "tasks.registry_smoke_test", "tasks.registry_update_poller", "tasks.cluster_scan_task", "tasks.helm_tasks", "tasks.fleet_tasks", "tasks.notification_retention_task", "tasks.benchmark_agent_tasks", "tasks.container_tasks"]
)

# Celery configuration
celery_app.conf.update(
    # Task settings
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,

    # Task execution settings
    task_track_started=True,
    # S14-035: Align hard limit with workspace lock timeout (7200s for apply/destroy)
    # Previous 3600s hard limit killed tasks while lock persisted for 7200s
    task_time_limit=7500,  # 2 hours 5 min hard limit (slightly above lock timeout)
    task_soft_time_limit=7200,  # 2 hours soft limit (matches lock timeout)
    task_acks_late=True,  # Acknowledge after task completion
    task_reject_on_worker_lost=True,  # Reject if worker dies

    # Result backend settings
    result_expires=86400,  # Results expire after 24 hours
    result_extended=True,  # Store more task metadata

    # Worker settings
    worker_prefetch_multiplier=1,  # Process one task at a time per worker
    worker_max_tasks_per_child=100,  # Restart worker after 100 tasks (prevent memory leaks)
    worker_disable_rate_limits=True,

    # Queue configuration
    task_default_queue="default",
    task_default_exchange="default",
    task_default_exchange_type="direct",
    task_default_routing_key="default",

    # Define queues
    # S15-025: Separate orchestrator queue to prevent deadlock when all workers
    # are occupied by orchestrator tasks blocking on group_result.get()
    task_queues=(
        Queue("default", Exchange("default"), routing_key="default"),
        Queue("opentofu", Exchange("opentofu"), routing_key="opentofu"),
        Queue("orchestrator", Exchange("orchestrator"), routing_key="orchestrator"),
    ),

    # Route tasks to specific queues
    # S15-025: Orchestrator tasks go to dedicated queue so they don't compete
    # with child tasks for workers, preventing deadlock under concurrent deployments
    # D-001 Phase 3: event-chain destroy uses the same engine queues as deploy —
    # no orchestrator tasks needed (trigger hook runs inline in the worker).
    task_routes={
        "tasks.opentofu_tasks.*": {"queue": "opentofu"},
        "tasks.kubernetes_tasks.*": {"queue": "opentofu"},
        "tasks.container_tasks.*": {"queue": "opentofu"},
        "tasks.ansible_tasks.*": {"queue": "opentofu"},
        "tasks.drift_tasks.*": {"queue": "default"},
        "tasks.stack_tasks.*": {"queue": "default"},
        "tasks.parallel_tasks.*": {"queue": "opentofu"},
    },

    # Retry settings
    task_autoretry_for=(Exception,),
    task_retry_kwargs={"max_retries": 0},  # No automatic retries (manual control)

    # BUG-009: Prevent task re-delivery during long-running operations
    # With acks_late=True, Redis must wait longer than task_time_limit before redelivery
    # Default visibility_timeout is 1 hour, but orchestrator tasks can run 2+ hours
    # during EKS cluster creation/deletion with retries
    broker_transport_options={
        'visibility_timeout': 10800,  # 3 hours - longer than task_time_limit (7500s)
    },

    # Monitoring
    worker_send_task_events=True,
    task_send_sent_event=True,

    # Celery Beat schedule (for periodic tasks)
    beat_schedule={
        'schedule-drift-checks': {
            'task': 'tasks.drift_tasks.schedule_drift_checks',
            'schedule': 900.0,  # Every 15 minutes (900 seconds)
        },
        'worker-heartbeat-keepalive': {
            'task': 'tasks.heartbeat_task.worker_heartbeat_keepalive',
            'schedule': 60.0,  # CP-015: Every 60 seconds (reduced from 15s to cut log noise 4x)
        },
        'health-monitor': {
            'task': 'tasks.health_monitor_task.check_cluster_health',
            'schedule': 60.0,  # Every 60 seconds — fires alerts on severity change
        },
        'operator-cleanup': {
            'task': 'tasks.operator_cleanup_task.cleanup_stale_operators_and_commands',
            'schedule': 120.0,  # Every 2 minutes — mark stale operators + expire old commands
        },
        'registry-update-poll': {
            'task': 'tasks.registry.poll_updates',
            'schedule': 6 * 60 * 60,  # Every 6 hours
        },
        'fleet-member-backfill': {
            'task': 'tasks.fleet_tasks.backfill_fleet_members',
            'schedule': 900.0,  # Every 15 minutes — reconcile all targets + prune orphans
        },
        'notification-retention': {
            'task': 'tasks.notification_retention_task.purge_old_notifications',
            'schedule': 24 * 60 * 60,  # Every 24 hours
        },
        # waf-event-ingest removed — OTEL Collector now handles WAF syslog ingest directly into ClickHouse
    },
)


@worker_process_init.connect
def init_worker_process(**kwargs):
    """
    Initialize worker process.

    This signal is called when a worker process is forked.
    Use this to initialize database connections, set up paths, etc.
    """
    import logging
    import sys

    _logger = logging.getLogger(__name__)

    # Ensure /app is in the path for forked worker processes
    if '/app' not in sys.path:
        sys.path.insert(0, '/app')

    # Import here to avoid circular imports
    from sqlalchemy import text

    from database import SessionLocal

    # Test database connection
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        _logger.info("Worker database connection initialized")
    except Exception as e:
        _logger.error(f"Worker database connection failed: {e}")


# ============================================================
# Worker Heartbeat Signals (PERF-009)
# ============================================================
# These signals publish worker status to Redis for fast API reads.
# Replaces slow celery_app.control.inspect() calls.

import logging as _logging

_celery_logger = _logging.getLogger(__name__)


@worker_ready.connect
def on_worker_ready(sender, **kwargs):
    """Publish heartbeat when worker becomes ready."""
    try:
        from core.worker_heartbeat import heartbeat
        heartbeat.publish_heartbeat(sender.hostname, status="ready")
        _celery_logger.info(f"Worker heartbeat registered: {sender.hostname}")
    except Exception as e:
        _celery_logger.error(f"Worker heartbeat failed: {e}")


@worker_shutdown.connect
def on_worker_shutdown(sender, **kwargs):
    """Mark worker offline when shutting down."""
    try:
        from core.worker_heartbeat import heartbeat
        heartbeat.mark_worker_offline(sender.hostname)
        _celery_logger.info(f"Worker heartbeat removed: {sender.hostname}")
    except Exception as e:
        _celery_logger.error(f"Worker shutdown heartbeat failed: {e}")


@task_prerun.connect
def on_task_prerun(sender, task_id, task, args, kwargs, **kw):
    """Record task start for worker status tracking."""
    try:
        from core.worker_heartbeat import heartbeat
        worker_id = task.request.hostname if task.request else "unknown"
        heartbeat.record_task_start(worker_id, task_id, task.name)
    except Exception as e:
        _celery_logger.error(f"Task prerun heartbeat failed: {e}")


@task_postrun.connect
def on_task_postrun(sender, task_id, task, args, kwargs, retval, state, **kw):
    """Record task completion for worker status tracking."""
    try:
        from core.worker_heartbeat import heartbeat
        worker_id = task.request.hostname if task.request else "unknown"
        heartbeat.record_task_end(worker_id, task_id)
    except Exception as e:
        _celery_logger.error(f"Task postrun heartbeat failed: {e}")


if __name__ == "__main__":
    celery_app.start()
