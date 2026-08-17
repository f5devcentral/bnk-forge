"""Janitor for stale tasks and stuck-destroying entities.

Background — when a worker dies mid-orchestration, when the API container
restarts during a deploy, or when a Celery task is lost from the broker, the
DB row remains stuck in `pending` or `in_progress` indefinitely. The next
deploy attempt then trips on a 409 Conflict because the orchestrator sees an
"in-flight" execution that nothing is actually working on.

Strategy — derive the set of live Celery task IDs from the existing Redis
worker-heartbeat (avoids slow `inspect()` broadcasts), then mark any non-
terminal row whose task_id isn't in that set as failed. Safe to call on boot
and periodically.

Empty/unreachable Redis is treated as "nothing alive" — strict improvement
over the prior boot-time behaviour which always reset every in_progress row.

D-001 Phase 3 S3b: ParallelExecution table dropped (migration v2_119).
`reset_stale_parallel_executions` removed. `reset_stale_destroys` reworked
to detect stuck entities from StackInstance.status + Task rows only (no PE).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from models import StackInstance
from models import Task as TaskModel
from models.enums import StackInstanceStatus, TaskStatus

logger = logging.getLogger(__name__)

NON_TERMINAL_TASK_STATUSES = (
    TaskStatus.QUEUED.value,
    TaskStatus.PENDING.value,
    TaskStatus.IN_PROGRESS.value,
)


def get_live_task_ids() -> set[str]:
    """Return Celery task IDs currently active across all workers (via Redis heartbeat)."""
    try:
        from core.worker_heartbeat import KEY_ACTIVE_TASKS, WorkerHeartbeat
    except Exception as e:
        logger.warning(f"Live-task lookup unavailable, treating as empty: {e}")
        return set()

    heartbeat = WorkerHeartbeat()
    redis = heartbeat.redis
    if not redis:
        return set()

    live_ids: set[str] = set()
    try:
        for key in redis.scan_iter(match=f"{KEY_ACTIVE_TASKS}*"):
            for task_json in redis.smembers(key):
                try:
                    data = json.loads(task_json)
                except (json.JSONDecodeError, TypeError):
                    continue
                task_id = data.get("task_id") if isinstance(data, dict) else None
                if isinstance(task_id, str) and task_id:
                    live_ids.add(task_id)
    except Exception as e:
        logger.warning(f"Live-task scan failed, treating as empty: {e}")
        return set()

    return live_ids


def reset_stale_tasks(
    db: Session,
    live_task_ids: set[str],
    *,
    now: datetime | None = None,
) -> list[int]:
    """Reset non-terminal tasks whose celery_task_id isn't live.

    C-1/H-1 backstop: a destroy task whose worker died (e.g. OOM kill before any
    Python handler ran) leaves its module stuck in ``destroying`` forever. Marking
    only the Task row ``failed`` is not enough — the module never transitions and
    the reverse-DAG destroy chain never advances to its dependencies, orphaning
    cloud resources. For each reset DESTROY task we therefore replicate what the
    engine failure handlers do on a *handled* failure: drive the module to
    ``destroy_failed`` and re-run ``_trigger_next_destroy_module`` so terminal
    detection re-fires and the chain either finalizes fail-closed or advances.
    """
    completed_at = now or datetime.now(UTC)
    rows = (
        db.query(TaskModel)
        .filter(TaskModel.status.in_(NON_TERMINAL_TASK_STATUSES))
        .all()
    )
    reset_ids: list[int] = []
    # Modules to re-drive after the Task rows are flipped. We collect first and
    # trigger after, so a single module with several stale destroy tasks (one per
    # retry) is re-driven once, not per row.
    destroy_modules_to_redrive: dict[int, object] = {}
    for row in rows:
        if row.celery_task_id and row.celery_task_id in live_task_ids:
            continue
        row.status = TaskStatus.FAILED.value
        row.error = row.error or "Worker no longer alive — reset by stale-execution janitor"
        row.completed_at = completed_at
        reset_ids.append(row.id)

        # Only destroy tasks need the module/chain recovery below; deploy tasks
        # keep their existing reset behaviour (Task row flipped, nothing else).
        if row.task_type == "destroy" and row.module is not None:
            destroy_modules_to_redrive[row.module.id] = row.module

    if destroy_modules_to_redrive:
        # Lazy import: _tofu_helpers pulls in services.* which would create an
        # import cycle at module load time. Mirrors how task modules import it.
        from models.enums import ModuleStatus
        from tasks._tofu_helpers import _trigger_next_destroy_module

        for module in destroy_modules_to_redrive.values():
            # Only modules still stuck mid-destroy need the transition. A module
            # already in a terminal destroy state was handled by its engine path.
            if module.status == ModuleStatus.DESTROYING.value:
                module.status = ModuleStatus.DESTROY_FAILED.value
                module.deployment_error = (
                    module.deployment_error
                    or "Worker no longer alive — reset by stale-execution janitor"
                )
            # Re-drive even if already destroy_failed: terminal detection must
            # re-run so the chain finalizes / advances after the worker death.
            # Commit per module so the trigger (which dispatches + commits its
            # own work) sees a consistent state — matches the steady-state
            # per-iteration commit in _trigger_next_destroy_module.
            db.commit()
            # No task_id on purpose: the janitor re-drives after a WORKER DEATH,
            # so there is no executing task — "the module's newest destroy Task"
            # is exactly the interrupted run we are resuming. Every other caller
            # passes its own task id, because scope belongs to the run.
            _trigger_next_destroy_module(module, db)

    return reset_ids


def reset_stale_destroys(
    db: Session,
    live_task_ids: set[str],
    *,
    now: datetime | None = None,
) -> dict[str, list[int]]:
    """C2 backstop: recover stack entities stuck in 'destroying' with no live destroy tasks.

    Architect Q6 terminal safety net. Complements reset_stale_tasks (which handles Task rows)
    by also driving the owning StackInstance to a terminal failed state when:
      - The StackInstance is in DESTROYING status, AND
      - There are no live destroy tasks (all celery_task_ids are dead or absent), AND
      - There are no non-terminal destroy Task rows remaining for this stack's scope.

    This handles the case where exception-path fixes (C1) were not reached (e.g., worker OOM
    kill before any Python handler ran), leaving entity stuck in 'destroying' indefinitely.

    D-001 Phase 3 S3b: ParallelExecution table dropped. PE record sweep removed.
    Project-scoped destroy recovery is handled entirely by reset_stale_tasks (Task rows)
    + the fail-closed barrier in _trigger_next_destroy_module. Stack entities still need
    an explicit DESTROYING→failed transition because StackInstance has its own status field.

    Returns dict with lists of IDs that were transitioned.
    """
    completed_at = now or datetime.now(UTC)
    error_msg = "Destroy orchestrator terminated unexpectedly — recovered by stale-destroy janitor"

    stale_stack_ids: list[int] = []

    # ---- Stack destroy sweep ----
    stuck_stacks = (
        db.query(StackInstance)
        .filter(StackInstance.status == StackInstanceStatus.DESTROYING.value)
        .all()
    )
    for stack in stuck_stacks:
        project_id = stack.project_id

        # Check for any non-terminal destroy task for modules in this stack scope
        scope_ids = set(stack.deployed_modules or [])
        non_terminal_tasks = (
            db.query(TaskModel)
            .filter(
                TaskModel.project_id == project_id,
                TaskModel.task_type == "destroy",
                TaskModel.status.in_(NON_TERMINAL_TASK_STATUSES),
            )
            .all()
        )
        # Restrict to tasks that belong to this stack's scope
        stack_non_terminal = [t for t in non_terminal_tasks if t.module_id in scope_ids]

        if stack_non_terminal:
            # Some tasks are still pending/queued — check if they are actually live
            all_dead = all(
                not (t.celery_task_id and t.celery_task_id in live_task_ids)
                for t in stack_non_terminal
            )
            if not all_dead:
                continue  # Live worker running — leave it alone

        # No live tasks and no non-terminal task rows — entity is stuck; recover it
        stack.status = StackInstanceStatus.FAILED.value
        stack.error_message = stack.error_message or error_msg
        stack.completed_at = completed_at
        stale_stack_ids.append(stack.id)
        logger.info(
            "reset_stale_destroys: stack %s transitioned from DESTROYING to failed (no live tasks)",
            stack.id,
        )

    return {"stale_stack_ids": stale_stack_ids, "stale_pe_ids": []}


def reset_stale_executions(db: Session) -> dict:
    """Single-pass janitor entry point — used by boot cleanup AND periodic job.

    Module locks are NOT swept here. The new heartbeat-based ModuleLockService
    self-heals via the reclaim window: any orphaned holding_task_id whose
    heartbeat_at is older than RECLAIM_AFTER_SECONDS will be auto-reclaimed by
    the next acquire. The legacy Redis lock sweep is gone (PR #96 scaffolding).

    D-001 Phase 3 S3b: reset_stale_parallel_executions removed (table dropped).
    reset_stale_destroys now only sweeps StackInstance entities (no PE records).
    """
    live_ids = get_live_task_ids()

    tasks_reset = reset_stale_tasks(db, live_ids)
    destroy_reset = reset_stale_destroys(db, live_ids)

    if tasks_reset or destroy_reset["stale_stack_ids"]:
        db.commit()
        logger.info(
            "Stale-execution janitor reset %d tasks, "
            "%d stuck-destroying stacks (live ids: %d)",
            len(tasks_reset),
            len(destroy_reset["stale_stack_ids"]),
            len(live_ids),
        )

    return {
        "parallel_executions_reset": 0,  # table dropped in v2_119
        "tasks_reset": len(tasks_reset),
        "stale_destroy_stacks_reset": len(destroy_reset["stale_stack_ids"]),
        "stale_destroy_pes_reset": 0,  # table dropped in v2_119
        "live_task_ids": len(live_ids),
    }
