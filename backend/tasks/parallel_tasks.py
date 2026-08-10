"""
Event-Chain Destroy Orchestration for BNK-Forge v2

Implements D-001 Phase 3: event-driven self-triggering reverse-DAG destroy,
mirroring the proven deploy path (_dispatch_first_wave / _trigger_next_stack_module).

Replaces the legacy poll-loop (execute_layer_parallel + orchestrate_parallel_deployment
+ orchestrate_stack_destroy). Previously a Canvas chord/chain implementation was
attempted (v1) but replaced by this mechanism on reliability grounds (Redis
chord-unlock fails on worker death; event-chain fails closed + needs no liveness belts).
See .agent/DECISIONS.md D-001 Addendum 2026-05-27.

The deploy path (_dispatch_first_wave / _trigger_next_stack_module) is UNCHANGED.
D-001 Phase 3 S3b: ParallelExecution table dropped (v2_119). PE record creation
and PE record updates removed from _mark_entity_failed and _finalize_destroy.

Destroy orchestration lives in:
  - tasks/_tofu_helpers.py: _trigger_next_destroy_module (post-destroy hook)
  - services/parallel_execution_service.py: _dispatch_first_destroy_wave
  - services/stack_deployment_service.py: _execute_stack_destroy first-wave dispatch
"""

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from models import ProjectModule, StackInstance

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module status constants
# ---------------------------------------------------------------------------

# Statuses that indicate no infrastructure exists (safe to count as "already destroyed")
NO_INFRA_STATUSES = frozenset({
    "destroyed",
    "not_initialized",
    "initialized",
    "planned",
    "init_failed",
    "plan_failed",
})

# Terminal statuses for destroy — a module is terminal-destroyed when it's in one of these
TERMINAL_DESTROY_STATUSES = frozenset({
    "destroyed",
    "destroy_failed",
    "not_initialized",
    "initialized",
    "planned",
    "init_failed",
    "plan_failed",
})


# ---------------------------------------------------------------------------
# In-cluster module classification
# ---------------------------------------------------------------------------

#: Execution engines that run workloads inside the target cluster.
#: These modules may fail at destroy time when the cluster is already gone;
#: project-scope fail-soft and force_destroy treat them differently from
#: cloud-infra modules that MUST be destroyed to avoid cost/orphaned resources.
IN_CLUSTER_ENGINES: frozenset[str] = frozenset({
    "kubernetes-direct",
    "operator",
    "kubernetes",
})


def is_in_cluster_module(module) -> bool:
    """Return True if module runs workloads inside the target cluster.

    Classification: execution_engine in IN_CLUSTER_ENGINES OR engine_type == "kubernetes".
    opentofu modules are explicitly NOT in-cluster (they manage cloud infra, not in-cluster
    workloads), even if they happen to provision K8s resources.

    Args:
        module: ProjectModule ORM instance (with library_module eager-loaded or accessible).

    Returns:
        True if the module is an in-cluster workload module.
    """
    lib = getattr(module, "library_module", None)
    if lib is None:
        return False

    execution_engine = getattr(lib, "execution_engine", None)
    if isinstance(execution_engine, str) and execution_engine.strip().lower() in IN_CLUSTER_ENGINES:
        return True

    engine_type = getattr(lib, "engine_type", None)
    if isinstance(engine_type, str) and engine_type.strip().lower() == "kubernetes":
        return True

    return False


# ---------------------------------------------------------------------------
# Shared helpers used by the event-chain trigger hook and entry-point services
# ---------------------------------------------------------------------------

def _get_module_name(module) -> str:
    """Return a human-readable module name for logging."""
    if module and module.library_module:
        return module.library_module.name
    if module:
        return f"Module {module.id}"
    return "unknown"


def _mark_entity_failed(db, entry_kind: str, entity_id: int, error_message: str) -> None:
    """Set the owning entity to failed state.

    For stack-entry: marks the StackInstance as failed.
    For project-entry: logs the failure. The project record has no top-level
    status field; progress is tracked via Task rows (run_handle).

    D-001 Phase 3 S3b: ParallelExecution table dropped; PE record update removed.
    """
    try:
        if entry_kind == "stack":
            stack = db.query(StackInstance).filter(StackInstance.id == entity_id).first()
            if stack:
                stack.status = "failed"
                stack.error_message = error_message
                stack.completed_at = datetime.now(UTC)
        else:
            # project — no PE record to update (table dropped in v2_119)
            # Progress is derived from Task rows via run_handle.
            logger.info(
                "_mark_entity_failed: project %s destroy failed — %s",
                entity_id, error_message,
            )
        db.commit()
    except Exception as e:
        logger.error(
            "_mark_entity_failed failed (entry_kind=%s entity=%s): %s",
            entry_kind, entity_id, e,
        )
        try:
            db.rollback()
        except Exception:
            pass


def _finalize_destroy(db: Session, entry_kind: str, entity_id: int) -> None:
    """
    Finalize a successful destroy run.

    Stack-entry: deletes destroyed ProjectModule records + clears
    StackInstance.deployed_modules + sets stack 'destroyed'. Mirrors the
    legacy parallel_tasks.py:716-721 behavior.

    Project-entry: does NOT delete module records. Logs completion only.
    Progress is derived from Task rows (run_handle column).

    D-001 Phase 3 S3b: ParallelExecution table dropped; PE record updates removed.
    Called from _trigger_next_destroy_module when terminal detection fires.
    PLAIN HELPER — not a Celery task.
    """
    completed_at = datetime.now(UTC)

    try:
        if entry_kind == "stack":
            stack = db.query(StackInstance).filter(StackInstance.id == entity_id).first()
            if not stack:
                logger.error("_finalize_destroy: stack %s not found", entity_id)
                return

            module_ids_to_clean = list(stack.deployed_modules or [])

            for module_id in module_ids_to_clean:
                module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
                if module:
                    db.delete(module)

            stack.deployed_modules = []
            stack.status = "destroyed"
            stack.completed_at = completed_at

            db.commit()
            logger.info(
                "_finalize_destroy: stack %s destroyed; %d module records cleaned up",
                entity_id, len(module_ids_to_clean),
            )

        else:
            # project-entry: do NOT delete module records
            # No PE record to update (table dropped in v2_119)
            db.commit()
            logger.info("_finalize_destroy: project %s destroy complete", entity_id)

    except Exception as e:
        logger.exception("_finalize_destroy failed (entry_kind=%s entity=%s): %s", entry_kind, entity_id, e)
        try:
            db.rollback()
        except Exception:
            pass
        raise


def _update_stack_status_after_deploy(
    db,
    project_id: int,
    successful_modules: list[int],
    failed_modules: list[int],
):
    """
    Update stack instance status after deployment completes.

    If all modules in a stack are successfully deployed, mark stack as 'deployed'.
    If any module failed, mark stack as 'failed'.
    """
    try:
        stacks = db.query(StackInstance).filter(
            StackInstance.project_id == project_id
        ).all()

        for stack in stacks:
            if not stack.deployed_modules:
                continue

            stack_module_ids = set(stack.deployed_modules)
            successful_set = set(successful_modules)
            failed_set = set(failed_modules)

            stack_succeeded = stack_module_ids & successful_set
            stack_failed = stack_module_ids & failed_set

            if stack_failed:
                stack.status = "failed"
                logger.info(f"Stack {stack.id} marked as failed ({len(stack_failed)} modules failed)")
            elif stack_succeeded == stack_module_ids:
                stack.status = "deployed"
                logger.info(f"Stack {stack.id} marked as deployed (all {len(stack_succeeded)} modules succeeded)")

        db.commit()

    except Exception as e:
        logger.error(f"Failed to update stack status: {e}")
