"""
Shared helper functions for OpenTofu Celery tasks.

Extracted from opentofu_tasks.py to keep task definitions focused
on orchestration while helpers handle common patterns like:
- Task status updates and WebSocket notifications
- Stack deployment sequencing (deploy and destroy)
- Kubernetes finalizer cleanup
- Deployment record creation
"""

import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime

from celery import Task

from database import get_db_context
from models import Deployment, Project, ProjectModule
from models import Task as TaskModel
from services.dependency_graph_service import DependencyGraphService

logger = logging.getLogger(__name__)


class TofuLogStreamer:
    """Incrementally flush ``task.logs`` to the DB as a tofu step streams output.

    Fixes the first defect in #195: OpenTofu task logs were buffered until the
    task completed, so ``logs_full_size`` sat at 0 for the whole run and only
    jumped to its final value at the end — a running module was opaque. The
    ``run_*`` runtime methods now stream stdout line-by-line through an
    ``on_output`` callback; this helper turns that callback into throttled
    writes of ``task.logs`` so the task-detail endpoint's ``logs_full_size``
    grows *during* the run.

    Usage::

        streamer = TofuLogStreamer(task, db)
        ...
        all_logs += header
        code, logs = engine.run_plan(work_dir, env, on_output=streamer.begin(all_logs))
        all_logs += logs                 # unchanged: final source of truth
        task.logs = all_logs             # written + committed by the task as before

    ``begin(base)`` snapshots the log accumulated before the step (section
    headers + earlier steps) and returns the sink. During the step the sink
    persists ``base + <lines streamed so far>``. The task still writes the
    complete ``all_logs`` (built from each step's returned output) at the end,
    so the final content and size are exactly what they were before — only the
    *timing* of visibility changes. The sink runs on the task's own thread (the
    runtime reads the pipe synchronously), so touching ``task``/``db`` here is
    safe. Both the write and commit are best-effort: a log flush must never
    fail the operation.
    """

    def __init__(self, task: TaskModel, db, *, interval: float = 2.0):
        self._task = task
        self._db = db
        self._interval = interval
        self._base = ""
        self._buf: list[str] = []
        self._last = 0.0

    def begin(self, base: str) -> Callable[[str], None]:
        """Start streaming a step whose output extends ``base``; return the sink."""
        self._base = base
        self._buf = []
        self._last = 0.0  # force the first line to flush immediately
        return self._sink

    def _sink(self, line: str) -> None:
        self._buf.append(line + "\n")
        now = time.monotonic()
        if now - self._last >= self._interval:
            self._last = now
            self._flush()

    def _flush(self) -> None:
        try:
            self._task.logs = self._base + "".join(self._buf)
            self._db.commit()
        except Exception:  # noqa: BLE001 — a log flush must never fail the step
            self._db.rollback()


# ============================================================================
# DRY Helper Functions
# ============================================================================

def _notify_task_started(task: TaskModel):
    """
    Publish WebSocket update when task starts.
    DRY helper to avoid repeated publish_task_update calls.
    """
    from services.websocket_service import publish_task_update
    publish_task_update(
        task_id=task.id,
        status=task.status,
        project_id=task.project_id,
        module_id=task.module_id,
        task_type=task.task_type
    )


def _mark_task_failed(task: TaskModel, error: str, db,
                      module: ProjectModule | None = None,
                      failed_status: str | None = None,
                      update_stack: bool = True) -> None:
    """
    Mark a task as failed and optionally update module status.
    DRY helper to avoid repeated failure handling code.

    Args:
        task: Task to mark as failed
        error: Error message
        db: Database session
        module: Module to update status (optional)
        failed_status: Module status to set (e.g., "apply_failed", "destroy_failed").
            When provided AND module is not None, sets module.status and
            module.deployment_error.
        update_stack: Whether to call _update_stack_status_if_needed (default True).
            Set to False for lock-conflict errors where another worker owns the module.
    """
    if task:
        task.status = "failed"
        task.error = str(error)[:2000]
        task.completed_at = datetime.now(UTC)
        if task.started_at:
            task.duration_seconds = (task.completed_at - task.started_at).total_seconds()

    if module and failed_status:
        module.status = failed_status
        module.deployment_error = str(error)[:2000]

    db.commit()

    if update_stack and module and module.stack_instance_id:
        _update_stack_status_if_needed(module, db)


def _update_stack_status_if_needed(module: ProjectModule, db) -> None:
    """
    Update parent stack instance status if the module belongs to a stack.

    Called after module apply/destroy completes to update overall stack progress.
    Also triggers the next module in the deployment sequence if applicable.
    DRY helper to avoid repeated stack status update code.
    """
    if not module.stack_instance_id:
        # Blueprint-imported projects create loose project modules with no
        # StackInstance, so the stack-based chaining below never fires for them.
        # Without this, the parallel-execution wave stalls after each layer and
        # only a manual deploy-all re-trigger advances it. Re-run the project-level
        # dispatcher, which idempotently queues any sibling whose deps are now met.
        if module.status == "applied":
            _trigger_next_project_module(module, db)
        return

    try:
        from models import StackInstance, StackInstanceStatus
        from services.stack_deployment_service import StackDeploymentService

        stack = db.query(StackInstance).filter(
            StackInstance.id == module.stack_instance_id
        ).first()

        if stack:
            # Capture whether chaining should fire BEFORE update_stack_progress
            # mutates stack.status.  update_stack_progress sets the stack to
            # PENDING when some modules are applied but none are actively
            # running (applying/initialising/etc.), which is correct for display
            # purposes but would break the chaining check below because
            # stack.status would no longer be DEPLOYING by that point.
            # Accept both DEPLOYING and PENDING so that stacks explicitly
            # started from a PENDING state (e.g. "Deploy" button re-run) also
            # chain correctly.
            should_chain = (
                stack.status in (StackInstanceStatus.DEPLOYING, StackInstanceStatus.PENDING, StackInstanceStatus.FAILED)
                and module.status == "applied"
                and stack.id == module.stack_instance_id
            )

            deployment_service = StackDeploymentService(db)
            deployment_service.update_stack_progress(stack)
            logger.info(
                f"Updated stack {stack.id} status to '{stack.status}' "
                f"(step {stack.current_step}/{stack.total_steps})"
            )

            # Queue downstream module(s) when a dependency just completed.
            # Capture should_chain before update_stack_progress mutates stack.status
            # (update_stack_progress may set status to PENDING, breaking the check).
            if should_chain:
                _trigger_next_stack_module(stack, module, db)

    except Exception as e:
        logger.warning(f"Failed to update stack status for module {module.id}: {e}")


def _trigger_next_project_module(completed_module: ProjectModule, db) -> None:
    """Advance the deploy wave for a stackless (blueprint-imported) project.

    Mirrors _trigger_next_stack_module but at project scope: re-runs the proven,
    idempotent ParallelExecutionService._dispatch_first_wave, which queues every
    project module whose dependencies are now satisfied and skips ones already
    applied or with an in-flight task. The completed module's run_handle is
    propagated so the whole run shares one handle.
    """
    try:
        predecessor_task = (
            db.query(TaskModel)
            .filter(
                TaskModel.module_id == completed_module.id,
                TaskModel.task_type.in_(["init", "apply"]),
            )
            .order_by(TaskModel.id.desc())
            .first()
        )
        run_handle = predecessor_task.run_handle if predecessor_task else None

        from services.parallel_execution_service import ParallelExecutionService

        ParallelExecutionService(db)._dispatch_first_wave(
            completed_module.project_id, run_handle=run_handle
        )
    except Exception as e:
        logger.warning(
            f"Failed to trigger next project module after {completed_module.id}: {e}"
        )


def _trigger_next_stack_module(stack, completed_module: ProjectModule, db) -> None:
    """
    Trigger the next module in the stack deployment sequence.

    Finds modules that depend on the completed module and kicks off their deployment
    if all their dependencies are now satisfied.
    """
    if not stack.deployed_modules:
        return

    try:
        # S14-023: Expire stale session cache before querying dependency status
        db.expire_all()

        # Get all modules in the stack
        stack_modules = db.query(ProjectModule).filter(
            ProjectModule.id.in_(stack.deployed_modules)
        ).order_by(ProjectModule.deployment_order).all()

        # PERF-023: Prefetch all modules by ID to avoid N+1 queries in dependency check
        # Build a lookup dict of module_id -> module for O(1) dependency status checks
        all_module_ids = set(stack.deployed_modules)
        for module in stack_modules:
            all_module_ids.update(module.dependencies or [])

        all_modules_map = {
            m.id: m for m in db.query(ProjectModule).filter(
                ProjectModule.id.in_(all_module_ids)
            ).all()
        } if all_module_ids else {}

        # Find modules that can now be deployed
        for module in stack_modules:
            # A disabled module is not runnable — the dependency chain must not
            # dispatch it just because its predecessor finished (issue #527).
            # Blueprint manifests rely on this: `optional: true` creates a module
            # DISABLED, and that guarantee is only as good as this check.
            if not module.enabled:
                logger.info(
                    "Skipping trigger for module %s (%s) — module is disabled",
                    module.id, module.path_in_project,
                )
                continue

            # Skip if already applied, applying, or failed
            if module.status in ["applied", "applying", "apply_failed", "plan_failed"]:
                continue

            # Check if this module depends on the completed module
            if completed_module.id not in (module.dependencies or []):
                continue

            # Check if ALL dependencies are now satisfied (using prefetched map)
            all_deps_ready = True
            for dep_id in (module.dependencies or []):
                dep = all_modules_map.get(dep_id)
                if not dep or dep.status != "applied":
                    all_deps_ready = False
                    break

            if all_deps_ready:
                # S15-008: Check if a task is already queued/in-progress for this module
                # Prevents double-queueing when two predecessor modules complete simultaneously
                existing_task = db.query(TaskModel).filter(
                    TaskModel.module_id == module.id,
                    TaskModel.status.in_(["pending", "queued", "in_progress"])
                ).first()

                if existing_task:
                    logger.info(
                        f"Skipping trigger for module {module.id} ({module.path_in_project}) "
                        f"- task {existing_task.id} already {existing_task.status}"
                    )
                    continue

                logger.info(
                    f"Triggering deployment of module {module.id} ({module.path_in_project}) "
                    f"- all dependencies satisfied"
                )

                # S2: propagate run_handle from the completed module's most-recent
                # init/apply Task so all Tasks in a deploy run share one handle.
                predecessor_run_handle: str | None = None
                predecessor_task = (
                    db.query(TaskModel)
                    .filter(
                        TaskModel.module_id == completed_module.id,
                        TaskModel.task_type.in_(["init", "apply"]),
                    )
                    .order_by(TaskModel.id.desc())
                    .first()
                )
                if predecessor_task:
                    predecessor_run_handle = predecessor_task.run_handle

                # Check if module needs init first
                if module.status == "not_initialized":
                    # Queue init with auto_apply
                    task = TaskModel(
                        task_type="init",
                        status="pending",
                        project_id=module.project_id,
                        module_id=module.id,
                        run_handle=predecessor_run_handle,  # S2: inherit run_handle
                    )
                    db.add(task)
                    # S14-042: Combine task creation + module status in single commit
                    module.status = "initializing"
                    db.commit()

                    # S14-014: Store celery_task_id so CallbackTask and cancellation work
                    from services.execution.task_dispatch import dispatch_init
                    celery_result = dispatch_init(task.id, module, auto_apply=True)
                    task.celery_task_id = celery_result.id
                    db.commit()
                else:
                    # Module already initialized, queue apply directly
                    task = TaskModel(
                        task_type="apply",
                        status="pending",
                        project_id=module.project_id,
                        module_id=module.id,
                        run_handle=predecessor_run_handle,  # S2: inherit run_handle
                    )
                    db.add(task)
                    # S14-042: Combine task creation + module status in single commit
                    module.status = "applying"
                    db.commit()

                    # S14-014: Store celery_task_id so CallbackTask and cancellation work
                    from services.execution.task_dispatch import dispatch_apply
                    celery_result = dispatch_apply(task.id, module)
                    task.celery_task_id = celery_result.id
                    db.commit()

    except Exception as e:
        logger.warning(f"Failed to trigger next stack module: {e}")


def _destroy_scope_for(
    module: ProjectModule, db, task_id: int | None = None
) -> str | None:
    """Return the destroy scope of the RUN that is executing, not of the module.

    ``task_id`` is the destroy Task actually running. Pass it wherever it is in
    scope — scope is a property of the run, and reading "the newest destroy Task
    for this module id" instead produced a real regression:

      1. user destroys leaf module M     → Task stamped destroy_scope="module"
      2. user clicks Destroy All while it runs → _dispatch_first_destroy_wave
         sees M's non-terminal destroy Task and skips creating a project-scoped
         row for it
      3. M completes → the newest row is still the module-scoped one → the chain
         guard returns before chaining AND before terminal detection
      4. dependencies never queued (cloud infra stranded) and the stack/project
         sits in DESTROYING forever

    Returns "module" / "project" / "stack", or None when the caller should fall
    back to its own heuristic.

    Unknown scope resolves to "module", i.e. DO NOT CHAIN. Every destroy Task
    created before the stamp existed has meta_data = NULL, and the previous
    None-return let those fall through to the stack_instance_id heuristic —
    which cascades. A single-module destroy enqueued by the old code and still
    QUEUED across a deploy would have completed under the heuristic and deleted
    its dependency: the original data-loss bug, live during the rollout window.
    The failure direction of an unknown scope must not be destruction.

    ``run_handle`` is the discriminator that exists on legacy rows: both wave
    dispatchers set it, ``create_task`` never does. So run_handle IS NULL means
    a single-module run regardless of when the row was written.
    """
    query = db.query(TaskModel).filter(TaskModel.task_type == "destroy")
    if task_id is not None:
        row = query.filter(TaskModel.id == task_id).first()
    else:
        row = (
            query.filter(TaskModel.module_id == module.id)
            .order_by(TaskModel.id.desc())
            .first()
        )
    if not row:
        return None

    stamped = (row.meta_data or {}).get("destroy_scope")
    if stamped:
        return stamped
    # Unstamped: infer from run_handle rather than falling through to a
    # heuristic that cascades.
    return "module" if not row.run_handle else None


def _trigger_next_destroy_module(
    module: ProjectModule, db, task_id: int | None = None
) -> None:
    """
    Post-destroy trigger hook — invoked when destroy worker completes for module M.

    Mirrors _trigger_next_stack_module but for the reverse-DAG destroy event chain.

    When module M reaches 'destroyed' (or any terminal-destroy status):
      For each dependency D of M (i.e. D in M.dependencies):
        - Queue D's destroy iff:
          a) D still has infra (status in {applied, apply_failed, destroy_failed})
          b) ALL dependents of D are now terminal-destroyed
          c) No non-terminal destroy Task already exists for D (double-dispatch guard)

    Fail-closed barrier: if any dependent of D is NOT terminal (e.g. destroy_failed with
    another dependent still destroying), D is never queued → no orphaned resources.

    After triggering, performs terminal detection: if EVERY module in the destroy scope
    is terminal, calls finalize or marks entity failed.

    D in M.dependencies = modules that M depends on = lower layer (root direction).
    Dependents of D = modules X where D in X.dependencies = destroyed before D.

    Works for project-scope and stack-scope destroys:
    - Stack modules: scoped to stack.deployed_modules list
    - Project modules: scoped to project_id

    Module-scope destroys (a single POST /project-modules/{id}/destroy) do NOT
    chain at all — see the destroy_scope == "module" guard below.
    """
    if _destroy_scope_for(module, db, task_id) == "module":
        # Single-module destroy: the dependencies are precisely what the caller
        # asked to keep, and there is no parent stack/project teardown in flight
        # to finalize. Stop here (issue #525).
        logger.info(
            "_trigger_next_destroy_module: module %s was destroyed at module scope — "
            "not chaining into its %d dependenc(ies)",
            module.id,
            len(module.dependencies or []),
        )
        return

    if not module.dependencies:
        # No dependencies to check — still run terminal detection
        _run_terminal_detection(module, db, task_id)
        return

    try:
        from models import Task as TaskModel
        from models.enums import ModuleStatus, TaskStatus
        from services.execution.task_dispatch import dispatch_destroy_signature
        from tasks.parallel_tasks import NO_INFRA_STATUSES, is_in_cluster_module  # hoisted: used in loop + barrier

        # Expire stale cache before querying (mirrors _trigger_next_stack_module pattern)
        db.expire_all()

        project_id = module.project_id
        graph_service = DependencyGraphService(db)

        # Determine destroy scope from predecessor task metadata (S2: Issue #2 fix).
        # Project-scope /destroy-all stamps meta_data={"destroy_scope":"project"} on every
        # first-wave Task.  Blueprint modules have stack_instance_id but MUST be treated
        # as project-scope in a project destroy.  Relying on stack_instance_id alone would
        # misclassify them as stack-scope and break the fail-soft barrier logic.
        scope_from_meta = _destroy_scope_for(module, db, task_id)
        # is_stack_scope: True only when there is no explicit meta_data override saying
        # "project" AND the module actually belongs to a stack.
        is_stack_scope = (scope_from_meta != "project") and bool(module.stack_instance_id)

        destroy_scope_ids: set[int] | None = None
        if is_stack_scope:
            from models import StackInstance
            stack = db.query(StackInstance).filter(
                StackInstance.id == module.stack_instance_id
            ).first()
            if stack and stack.deployed_modules:
                destroy_scope_ids = set(stack.deployed_modules)

        # For each dependency D (M depends on D, so D is deeper/root direction)
        for dep_id in (module.dependencies or []):
            # Defensive: filter by project_id to prevent cross-project dispatch if data is corrupted
            dep = db.query(ProjectModule).filter(
                ProjectModule.id == dep_id,
                ProjectModule.project_id == project_id,
            ).first()
            if not dep:
                continue

            # (a) Skip if D has no infra to destroy
            if dep.status in NO_INFRA_STATUSES:
                logger.debug(
                    "_trigger_next_destroy_module: dep %s status=%s — no infra, skip",
                    dep_id, dep.status,
                )
                continue

            # (b) Barrier: ALL dependents of D must be terminal before D is dispatched.
            #
            # Stack-scope: fail-closed — destroy_failed always blocks (preserves resource safety).
            # Project-scope: fail-soft for in-cluster dependents — when a dependent is an
            #   in-cluster module (kubernetes-direct / operator / kubernetes engine) and has
            #   reached destroy_failed, the cluster is likely already gone and the failure is
            #   unrecoverable. Allow the dependency to proceed so cloud-infra teardown is not
            #   blocked by an unreachable in-cluster module. Non-in-cluster destroy_failed
            #   still blocks (fail-closed) to prevent orphaned cloud resources.
            #
            # Dependents of D = modules X where dep_id in X.dependencies
            dependents_of_dep = graph_service.get_reverse_dependencies(dep_id, project_id)

            # Filter to destroy scope if applicable
            if destroy_scope_ids is not None:
                dependents_of_dep = [d for d in dependents_of_dep if d.id in destroy_scope_ids]

            uncleared_dependents = []
            for d in dependents_of_dep:
                if d.status == "destroyed" or d.status in NO_INFRA_STATUSES:
                    continue  # terminal-clean — clears barrier
                if d.status == "destroy_failed":
                    if not is_stack_scope and is_in_cluster_module(d):
                        # Project-scope fail-soft: in-cluster destroy_failed is treated as terminal.
                        logger.debug(
                            "_trigger_next_destroy_module: dep %s dependent %s is in-cluster "
                            "destroy_failed (project-scope fail-soft) — clears barrier",
                            dep_id, d.id,
                        )
                        continue
                    # Otherwise block (stack-scope or non-in-cluster module)
                uncleared_dependents.append(d)

            if uncleared_dependents:
                logger.debug(
                    "_trigger_next_destroy_module: dep %s has %d uncleared dependents "
                    "(%s) — skipping (fail-closed)",
                    dep_id,
                    len(uncleared_dependents),
                    [(d.id, d.status) for d in uncleared_dependents],
                )
                continue

            # (c) Double-dispatch guard (S15-008 pattern)
            existing_task = db.query(TaskModel).filter(
                TaskModel.module_id == dep_id,
                TaskModel.task_type == "destroy",
                TaskModel.status.in_([
                    TaskStatus.PENDING.value,
                    TaskStatus.QUEUED.value,
                    TaskStatus.IN_PROGRESS.value,
                ]),
            ).first()
            if existing_task:
                logger.info(
                    "_trigger_next_destroy_module: dep %s already has non-terminal destroy "
                    "task %s (%s) — skipping",
                    dep_id, existing_task.id, existing_task.status,
                )
                continue

            # All checks passed — queue the destroy
            logger.info(
                "_trigger_next_destroy_module: queuing destroy for dep %s (%s) "
                "— all dependents terminal",
                dep_id, dep.status,
            )

            # S2: propagate run_handle from the completed module's most-recent
            # destroy task so all tasks in a run share one handle.
            predecessor_run_handle: str | None = None
            predecessor_task = (
                db.query(TaskModel)
                .filter(
                    TaskModel.module_id == module.id,
                    TaskModel.task_type == "destroy",
                )
                .order_by(TaskModel.id.desc())
                .first()
            )
            if predecessor_task:
                predecessor_run_handle = predecessor_task.run_handle

            # Propagate destroy_scope so the next trigger in the chain also uses
            # the correct scope (project vs stack) without re-inspecting stack_instance_id.
            downstream_meta: dict = {}
            if scope_from_meta:
                downstream_meta["destroy_scope"] = scope_from_meta

            task = TaskModel(
                task_type="destroy",
                status=TaskStatus.QUEUED.value,
                project_id=dep.project_id,
                module_id=dep_id,
                created_at=datetime.now(UTC),
                run_handle=predecessor_run_handle,  # S2: inherit run_handle
                meta_data=downstream_meta or None,
            )
            db.add(task)
            dep.status = ModuleStatus.DESTROYING.value
            db.flush()
            db.refresh(task)

            sig = dispatch_destroy_signature(task.id, dep)
            async_result = sig.apply_async()
            task.celery_task_id = async_result.id
            db.commit()

            logger.info(
                "_trigger_next_destroy_module: dispatched destroy task %s (celery %s) "
                "for module %s (run_handle: %s)",
                task.id, async_result.id, dep_id, predecessor_run_handle,
            )

    except Exception as e:
        logger.warning(
            "_trigger_next_destroy_module: error triggering deps of module %s: %s",
            module.id, e,
        )

    # Always run terminal detection after attempting triggers
    _run_terminal_detection(module, db, task_id)


def _run_terminal_detection(
    module: ProjectModule, db, task_id: int | None = None
) -> None:
    """
    Check if all modules in the destroy scope are terminal and finalize if so.

    Called from _trigger_next_destroy_module after triggering.

    Scope is read from the predecessor task's meta_data["destroy_scope"] field when
    available (set by _dispatch_first_destroy_wave / _trigger_next_destroy_module for
    project-scope destroys).  Falls back to stack_instance_id presence for backwards
    compatibility with stack-scope destroys that don't set meta_data.
    """
    try:
        # Resolve scope from task metadata first (prevents blueprint modules with
        # stack_instance_id from being misclassified as stack-scope on project destroy).
        scope_from_meta = _destroy_scope_for(module, db, task_id)

        if scope_from_meta == "module":
            # Single-module destroy — no parent stack/project teardown is in
            # flight, so there is no entity to finalize or mark failed (#525).
            return

        if scope_from_meta == "project":
            _run_terminal_detection_project(module, db)
        elif scope_from_meta == "stack":
            _run_terminal_detection_stack(module, db)
        elif module.stack_instance_id:
            # No explicit scope in metadata — fall back to stack_instance_id heuristic
            _run_terminal_detection_stack(module, db)
        else:
            _run_terminal_detection_project(module, db)

    except Exception as e:
        logger.warning(
            "_run_terminal_detection: error for module %s: %s",
            module.id, e,
        )


def _run_terminal_detection_stack(module: ProjectModule, db) -> None:
    """Terminal detection for stack-scoped destroy."""
    try:
        from models import StackInstance, StackInstanceStatus
        from tasks.parallel_tasks import (
            TERMINAL_DESTROY_STATUSES,
            _finalize_destroy,
            _mark_entity_failed,
        )

        # with_for_update: makes read-check-finalize atomic; prevents double-finalize
        # race when two modules complete near-simultaneously.
        stack = db.query(StackInstance).filter(
            StackInstance.id == module.stack_instance_id
        ).with_for_update().first()
        if not stack:
            return

        # Only finalize if stack is in DESTROYING state (re-entrancy guard)
        if stack.status != StackInstanceStatus.DESTROYING:
            return

        scope_ids = set(stack.deployed_modules or [])
        if not scope_ids:
            return

        db.expire_all()
        scope_modules = db.query(ProjectModule).filter(
            ProjectModule.id.in_(scope_ids)
        ).all()

        non_terminal = [m for m in scope_modules if m.status not in TERMINAL_DESTROY_STATUSES]
        if non_terminal:
            return  # Still in progress — not yet terminal

        failed_modules = [m for m in scope_modules if m.status == "destroy_failed"]
        if failed_modules:
            error_msg = f"Destroy stopped: {len(failed_modules)} module(s) failed to destroy"
            logger.info(
                "_run_terminal_detection_stack: stack %s → failed (%s)",
                stack.id, error_msg,
            )
            _mark_entity_failed(db, "stack", stack.id, error_msg)
        else:
            logger.info(
                "_run_terminal_detection_stack: stack %s → all terminal, finalizing",
                stack.id,
            )
            _finalize_destroy(db, "stack", stack.id)

    except Exception as e:
        logger.warning(
            "_run_terminal_detection_stack: error for module %s: %s",
            module.id, e,
        )


def _run_terminal_detection_project(module: ProjectModule, db) -> None:
    """Terminal detection for project-scoped destroy.

    D-001 Phase 3 S3b: scope is now determined by the run_handle shared by the
    completed module's destroy Task, NOT by querying the PE record (table dropped).
    The run_handle identifies the specific run for the /orchestration endpoint
    (progress display), but correctness of the done-check requires examining ALL
    project modules — not just those with Task rows.

    Why Task-row-only scope is insufficient: in a reverse-DAG destroy, root modules
    only get a Task row when _trigger_next_destroy_module dispatches them (after ALL
    their dependents finish). A partially-dispatched run has Task rows only for the
    leaf wave. If A finishes first (A and C are independent leaves over shared root B),
    the Task-row set is {A} → looks terminal → premature finalize while C and B live.

    The fix: after identifying run_handle, query ALL project modules. A module that
    still has infra (status ∉ TERMINAL_DESTROY_STATUSES) — whether dispatched or not —
    means the run is NOT done. Only when every to-destroy module is in a terminal
    destroy state can we finalize.
    """
    try:
        from models import Task as TaskModel
        from tasks.parallel_tasks import (
            NO_INFRA_STATUSES,
            TERMINAL_DESTROY_STATUSES,
            _finalize_destroy,
            _mark_entity_failed,
        )

        project_id = module.project_id

        # Find the run_handle from the completed module's most-recent destroy Task
        predecessor_task = (
            db.query(TaskModel)
            .filter(
                TaskModel.module_id == module.id,
                TaskModel.task_type == "destroy",
            )
            .order_by(TaskModel.id.desc())
            .first()
        )
        if not predecessor_task or not predecessor_task.run_handle:
            # No run_handle — fall back to project-scope heuristic (pre-S2 rows)
            _run_terminal_detection_project_legacy(module, db)
            return

        run_handle = predecessor_task.run_handle

        # Freshly load ALL project modules — correctness requires full scope.
        # Task rows only exist for dispatched modules; undispatched root modules
        # (queued behind still-destroying leaves) have no Task row yet.
        db.expire_all()
        all_project_modules = db.query(ProjectModule).filter(
            ProjectModule.project_id == project_id,
        ).all()

        # Modules that needed destroying = those with infra at run start.
        # Proxy: any module NOT in NO_INFRA_STATUSES has (or had) infra.
        # Modules already in NO_INFRA_STATUSES before the run are inert — skip.
        to_destroy_modules = [
            m for m in all_project_modules
            if m.status not in NO_INFRA_STATUSES
        ]

        # Every to-destroy module must be in a terminal destroy state.
        # This includes: "destroying" (in-flight), "applied"/"apply_failed"
        # (infra still live, not yet dispatched or failed pre-dispatch).
        non_terminal = [
            m for m in to_destroy_modules
            if m.status not in TERMINAL_DESTROY_STATUSES
        ]
        if non_terminal:
            # Some modules still have live infra or are actively destroying —
            # this run is not complete yet. Return without finalizing.
            return

        # All to-destroy modules are now in terminal states.
        # Guard: if to_destroy_modules is empty (nothing needed destroying),
        # still finalize — an empty project destroy is valid completion.
        failed_modules = [m for m in to_destroy_modules if m.status == "destroy_failed"]

        # Fail-soft: in-cluster destroy_failed modules are reconciled to "destroyed"
        # when ALL non-in-cluster (infra) modules are cleanly destroyed.  These failures
        # are unrecoverable once the cluster is gone — recording them as "destroyed"
        # allows the project destroy to finalize cleanly.
        from tasks.parallel_tasks import is_in_cluster_module as _is_in_cluster
        in_cluster_failed = [m for m in failed_modules if _is_in_cluster(m)]
        non_cluster_failed = [m for m in failed_modules if not _is_in_cluster(m)]

        if in_cluster_failed and not non_cluster_failed:
            # All failures are in-cluster — reconcile them to destroyed.
            for m in in_cluster_failed:
                m.deployment_error = (
                    (m.deployment_error or "") +
                    " [reconciled-to-destroyed: cluster already torn down]"
                )[:2000]
                m.status = "destroyed"
            db.commit()
            logger.info(
                "_run_terminal_detection_project: project %s run %s → "
                "%d in-cluster module(s) reconciled to destroyed; finalizing",
                project_id, run_handle, len(in_cluster_failed),
            )
            _finalize_destroy(db, "project", project_id)
            return

        if failed_modules:
            error_msg = f"Destroy stopped: {len(failed_modules)} module(s) failed to destroy"
            logger.info(
                "_run_terminal_detection_project: project %s run %s → failed (%s)",
                project_id, run_handle, error_msg,
            )
            _mark_entity_failed(db, "project", project_id, error_msg)
        else:
            logger.info(
                "_run_terminal_detection_project: project %s run %s → all terminal, finalizing",
                project_id, run_handle,
            )
            _finalize_destroy(db, "project", project_id)

    except Exception as e:
        logger.warning(
            "_run_terminal_detection_project: error for module %s: %s",
            module.id, e,
        )


def _run_terminal_detection_project_legacy(module: ProjectModule, db) -> None:
    """Fallback terminal detection for project destroys without a run_handle (pre-S2 Task rows).

    Uses project-scope heuristic: considers all modules whose status indicates
    they participated in a destroy run. Safe because the conflict guard prevents
    concurrent deploy+destroy on the same project.
    """
    try:
        from tasks.parallel_tasks import (
            TERMINAL_DESTROY_STATUSES,
            _finalize_destroy,
            _mark_entity_failed,
        )

        project_id = module.project_id

        db.expire_all()
        all_modules = db.query(ProjectModule).filter(
            ProjectModule.project_id == project_id,
        ).all()

        active_destroy_modules = [
            m for m in all_modules
            if m.status not in {"not_initialized", "initialized", "planned", "init_failed", "plan_failed"}
        ]

        non_terminal = [
            m for m in active_destroy_modules
            if m.status not in TERMINAL_DESTROY_STATUSES
        ]
        if non_terminal:
            destroying = [m for m in non_terminal if m.status == "destroying"]
            remaining_infra = [m for m in non_terminal if m.status in {"applied", "apply_failed"}]
            if destroying or remaining_infra:
                return

        failed_modules = [m for m in active_destroy_modules if m.status == "destroy_failed"]

        if non_terminal:
            return

        if failed_modules:
            error_msg = f"Destroy stopped: {len(failed_modules)} module(s) failed to destroy"
            logger.info(
                "_run_terminal_detection_project_legacy: project %s → failed (%s)",
                project_id, error_msg,
            )
            _mark_entity_failed(db, "project", project_id, error_msg)
        else:
            logger.info(
                "_run_terminal_detection_project_legacy: project %s → all terminal, finalizing",
                project_id,
            )
            _finalize_destroy(db, "project", project_id)

    except Exception as e:
        logger.warning(
            "_run_terminal_detection_project_legacy: error for module %s: %s",
            module.id, e,
        )


# Credential loading moved to services.credentials_service.get_cloud_credentials_env()


# ============================================================================
# Finalizer Cleanup Helpers
# ============================================================================

def _create_notification(db, notification_type: str, title: str, message: str,
                         resource_type: str = None, resource_id: int = None):
    """
    Create a notification in the database for the bell icon.

    Args:
        db: Database session
        notification_type: 'success', 'error', 'warning', or 'info'
        title: Short title for the notification
        message: Detailed message
        resource_type: Optional resource type (e.g., 'module', 'deployment')
        resource_id: Optional resource ID
    """
    from models import Notification

    notification = Notification(
        user="all",  # Show to all users
        type=notification_type,
        title=title,
        message=message,
        resource_type=resource_type,
        resource_id=resource_id
    )
    db.add(notification)
    db.commit()
    logger.info(f"Created notification: {title}")


def _is_namespace_finalizer_issue(logs: str) -> bool:
    """
    Detect if destroy failed due to namespace finalizer issues.

    Common patterns:
    - "context deadline exceeded" during namespace deletion
    - "namespace is terminating" errors
    - Long "Still destroying..." for kubernetes_namespace resources
    """
    patterns = [
        r"kubernetes_namespace.*Still destroying.*\d+m\d+s elapsed",  # Long namespace deletion
        r"context deadline exceeded",  # Timeout
        r"namespace.*is terminating",
        r"finalizers.*remaining",
        r"NamespaceFinalizersRemaining",
    ]

    return any(re.search(p, logs, re.IGNORECASE) for p in patterns)


def _cleanup_stuck_finalizers(db, project: Project, module: ProjectModule, logs: str) -> dict:
    """
    Attempt to clean up stuck Kubernetes finalizers.

    This is called when a destroy operation fails due to namespace deletion timeout.
    It finds the registered K8s cluster for the project and cleans up F5 BNK
    and other known CRD finalizers.

    Args:
        db: Database session
        project: Project being destroyed
        module: Module that failed to destroy
        logs: Destroy logs (used to detect which namespaces are stuck)

    Returns:
        dict with 'cleaned' (bool), 'message' (str), 'namespaces' (list)
    """
    result = {
        "cleaned": False,
        "message": "",
        "namespaces": []
    }

    try:
        from models import KubernetesCluster
        from services.finalizer_cleanup_service import FinalizerCleanupService

        # Find the K8s cluster for this project
        cluster = db.query(KubernetesCluster).filter(
            KubernetesCluster.project_id == project.id
        ).first()

        if not cluster:
            result["message"] = "No Kubernetes cluster registered for project, cannot cleanup finalizers"
            logger.warning(result["message"])
            return result

        # Extract stuck namespace names from logs
        stuck_namespaces = set()

        # Match "kubernetes_namespace.xxx: Still destroying... [id=namespace-name"
        ns_matches = re.findall(r'kubernetes_namespace\.\w+:.*\[id=([^\],]+)', logs)
        stuck_namespaces.update(ns_matches)

        # Also check for F5 BNK common namespaces
        f5_namespaces = ["f5-bnk", "f5-utils", "bnk-gw", "observability"]
        for ns in f5_namespaces:
            if ns in logs:
                stuck_namespaces.add(ns)

        if not stuck_namespaces:
            result["message"] = "Could not identify stuck namespaces from logs"
            return result

        logger.info(f"Attempting to clean up finalizers in namespaces: {stuck_namespaces}")

        cleanup_service = FinalizerCleanupService(db)
        cleaned_count = 0

        for namespace in stuck_namespaces:
            try:
                cleanup_result = cleanup_service.cleanup_namespace_finalizers(
                    cluster_id=cluster.id,
                    namespace=namespace,
                    force=False,  # Only remove known safe finalizers
                    timeout_seconds=60
                )

                if cleanup_result["resources_cleaned"]:
                    cleaned_count += len(cleanup_result["resources_cleaned"])
                    result["namespaces"].append({
                        "name": namespace,
                        "resources": len(cleanup_result["resources_cleaned"]),
                        "deleted": cleanup_result["namespace_deleted"]
                    })
                    logger.info(
                        f"Cleaned {len(cleanup_result['resources_cleaned'])} resources "
                        f"in namespace {namespace}"
                    )

            except Exception as e:
                logger.warning(f"Failed to cleanup namespace {namespace}: {e}")

        if cleaned_count > 0:
            result["cleaned"] = True
            result["message"] = f"Cleaned finalizers from {cleaned_count} resources in {len(result['namespaces'])} namespaces"

            # Create notification about the cleanup
            ns_names = ", ".join([ns["name"] for ns in result["namespaces"]])
            _create_notification(
                db,
                notification_type="warning",
                title="Auto-Cleanup: K8s Finalizers Removed",
                message=f"Module '{module.library_module.name}' destroy timed out due to stuck finalizers. "
                       f"Automatically cleaned {cleaned_count} CRD finalizers in namespace(s): {ns_names}. "
                       f"Retrying destroy...",
                resource_type="module",
                resource_id=module.id
            )
        else:
            result["message"] = "No finalizers found to clean up"

    except ImportError as e:
        result["message"] = f"Finalizer cleanup service not available: {e}"
        logger.warning(result["message"])
    except Exception as e:
        result["message"] = f"Finalizer cleanup failed: {e}"
        logger.error(result["message"], exc_info=True)

        # Create notification about the failure
        _create_notification(
            db,
            notification_type="error",
            title="Finalizer Cleanup Failed",
            message=f"Module '{module.library_module.name}' destroy failed due to stuck K8s finalizers. "
                   f"Automatic cleanup also failed: {str(e)[:150]}. "
                   f"Manual intervention may be required.",
            resource_type="module",
            resource_id=module.id
        )

    return result


def create_deployment_record(db, task: TaskModel, module: ProjectModule, action: str, logs: str = "") -> Deployment:
    """
    Create a Deployment record from a Task record for history tracking.
    """
    # Parse resource changes from logs
    resources_to_add = 0
    resources_to_change = 0
    resources_to_destroy = 0

    if logs:
        # Plan summary: "Plan: 5 to add, 2 to change, 1 to destroy"
        plan_match = re.search(r'Plan:\s+(\d+)\s+to\s+add,\s+(\d+)\s+to\s+change,\s+(\d+)\s+to\s+destroy', logs)
        if plan_match:
            resources_to_add = int(plan_match.group(1))
            resources_to_change = int(plan_match.group(2))
            resources_to_destroy = int(plan_match.group(3))

        # Apply summary: "Apply complete! Resources: 5 added, 2 changed, 1 destroyed"
        apply_match = re.search(r'Apply\s+complete!\s+Resources:\s+(\d+)\s+added,\s+(\d+)\s+changed,\s+(\d+)\s+destroyed', logs)
        if apply_match:
            resources_to_add = int(apply_match.group(1))
            resources_to_change = int(apply_match.group(2))
            resources_to_destroy = int(apply_match.group(3))

    # Determine status
    if task.status == "completed" and task.exit_code == 0:
        status = "success"
    elif task.status == "failed" or (task.exit_code and task.exit_code != 0):
        status = "failed"
    elif task.status == "cancelled":
        status = "cancelled"
    else:
        status = "pending"

    deployment = Deployment(
        module_id=module.id,
        action=action,
        status=status,
        triggered_by=task.triggered_by or "user",  # Use username from the Task record
        started_at=task.started_at or datetime.now(UTC),
        completed_at=task.completed_at,
        duration_seconds=task.duration_seconds,
        command=task.command,
        exit_code=task.exit_code,
        stdout=logs,
        stderr="",
        resources_to_add=resources_to_add,
        resources_to_change=resources_to_change,
        resources_to_destroy=resources_to_destroy,
        environment=module.project.environment or "dev",
        # The task is the handle for this run's output (GET /api/tasks/{id}).
        # A deployment row has no task_id column, and that handle was not
        # reachable from any module-facing endpoint (#154) -- so an operator
        # who found a deployment id had a number that looked like the log
        # handle but was not. Record it here; the /deployments route exposes
        # it as task_id.
        meta_data={"task_id": task.id, "celery_task_id": task.celery_task_id},
    )

    db.add(deployment)
    db.commit()

    logger.info(f"Created deployment record {deployment.id} for module {module.id}")
    return deployment


def _publish_task_completion(task: TaskModel) -> None:
    """
    Publish WebSocket update for task completion/failure.
    DRY helper to avoid duplicated publish_task_update calls.
    """
    from services.websocket_service import publish_task_update
    publish_task_update(
        task_id=task.id,
        status=task.status,
        project_id=task.project_id,
        module_id=task.module_id,
        task_type=task.task_type,
        exit_code=task.exit_code,
        error=task.error,
        duration_seconds=task.duration_seconds,
        metadata=task.meta_data
    )


class CallbackTask(Task):
    """Base task that updates database with progress."""

    def on_success(self, retval, task_id, args, kwargs):
        with get_db_context() as db:
            try:
                task = db.query(TaskModel).filter(TaskModel.celery_task_id == task_id).first()
                if not task:
                    return
                # S14-022: Only update if status isn't already terminal — the task
                # body may have set completed/failed/cancelled and committed already.
                if task.status not in ("failed", "completed", "cancelled"):
                    task.status = "completed"
                    task.completed_at = datetime.now(UTC)
                    if task.started_at:
                        task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                    db.commit()
                # Always publish, regardless of who set the terminal state — without
                # this the WebSocket task_update event never fires when the body set
                # status='completed' before returning, leaving the UI stuck on
                # 'applying' until manual refresh.
                _publish_task_completion(task)
            except Exception as e:
                logger.error(f"Error updating task success status: {e}")

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        with get_db_context() as db:
            try:
                task = db.query(TaskModel).filter(TaskModel.celery_task_id == task_id).first()
                if not task:
                    return
                # S15-014: Only update if not already in a terminal state — body
                # may have committed a detailed error already.
                if task.status not in ("failed", "completed", "cancelled"):
                    task.status = "failed"
                    task.completed_at = datetime.now(UTC)
                    task.error = str(exc)
                    if task.started_at:
                        task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                    db.commit()
                # Always publish, same reason as on_success.
                _publish_task_completion(task)
            except Exception as e:
                logger.error(f"Error updating task failure status: {e}")
