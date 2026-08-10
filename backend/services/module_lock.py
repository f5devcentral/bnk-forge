"""Alias shim — ModuleLockService re-exported from entity_lock.py.

DEPRECATED: Import from services.entity_lock directly for new code.
This module is kept for backward compatibility with:
  - Seven non-task production callers (routes/system.py, routes/tasks.py,
    services/project_module_service.py, services/project_service.py,
    services/project_crud_service.py)
  - Test mocks that patch 'services.module_lock.ModuleLockService' by
    string path (tests/integration/test_routes_*.py, tests/component/
    test_project_*.py)

Planned removal: Phase 1.7 or the next locking follow-up once all callers
are migrated.

Phase 2 state-machine routing lives in set_locked_module_fields below.
The generalized set_locked_entity_fields in entity_lock.py is deliberately
dumb — no state-machine calls. This preserves locality: state-machine logic
stays in module_state.py + this alias.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

# Re-export everything from entity_lock so callers get the real implementation.
# noqa: F401 on the aliases — they are intentional backward-compat re-exports
# consumed by tasks/ssh_tasks.py, tasks/kubernetes_tasks.py, tasks/opentofu_tasks.py,
# tasks/ansible_tasks.py, and services/module_state.py.
from services.entity_lock import (
    _ALLOWED_FIELDS_BY_TABLE,
    _ALLOWED_LOCK_TABLES,  # noqa: F401
    DEFAULT_CONTEXT_WAIT_TIMEOUT,
    HEARTBEAT_INTERVAL_SECONDS,
    RECLAIM_AFTER_SECONDS,
    EntityLock,
    EntityLockError,  # noqa: F401
    EntityLockLostError,  # noqa: F401
    EntityLockService,
    HeartbeatRefresher,  # noqa: F401
    ModuleLock,  # noqa: F401
    ModuleLockError,  # noqa: F401
    ModuleLockLostError,  # noqa: F401
    entity_lock,
    set_locked_entity_fields,
    sweep_stale_entity_locks,  # noqa: F401
)

# ---------------------------------------------------------------------------
# ModuleLockService backward-compat wrapper
# ---------------------------------------------------------------------------


class ModuleLockService(EntityLockService):
    """Backward-compat wrapper: hard-codes table='project_modules'.

    DEPRECATED: Use EntityLockService(db, 'project_modules') for new code.

    All seven non-task production callers pass only ``db`` — they get
    project_modules behavior with no signature change.
    """

    def __init__(
        self,
        db: Session,
        *,
        reclaim_after_seconds: int = RECLAIM_AFTER_SECONDS,
        heartbeat_interval_seconds: int = HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        super().__init__(
            db,
            "project_modules",
            reclaim_after_seconds=reclaim_after_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
        )

    # The parent class stores entity_id / task_id generically.  The old API
    # exposed module_id — keep that as a transparent alias via property access
    # on the returned EntityLock (which already has entity_id).

    def get_holder(self, entity_id: int) -> dict | None:
        """Return holder dict with backward-compat 'module_id' key."""
        result = super().get_holder(entity_id)
        if result is not None and "entity_id" in result:
            result["module_id"] = result["entity_id"]
        return result

    def list_held(self) -> list[dict]:
        """Return held-lock list with backward-compat 'module_id' key in each entry."""
        rows = super().list_held()
        for row in rows:
            if "entity_id" in row:
                row["module_id"] = row["entity_id"]
        return rows


# module_lock context manager is re-exported from entity_lock for the
# module-specific convenience alias below.

def module_lock(
    db: Session,
    module_id: int,
    task_id: int,
    *,
    wait_timeout: float = DEFAULT_CONTEXT_WAIT_TIMEOUT,
):
    """module_lock context manager — acquires on project_modules.

    DEPRECATED: Use entity_lock(db, table='project_modules', entity_id=...,
    task_id=...) for new code.
    """
    return entity_lock(
        db,
        table="project_modules",
        entity_id=module_id,
        task_id=task_id,
        wait_timeout=wait_timeout,
    )


# ---------------------------------------------------------------------------
# Phase 2 state-machine-aware field update alias
# ---------------------------------------------------------------------------

# The Phase 2 _ALLOWED_LOCKED_UPDATE_FIELDS set is the same as
# _ALLOWED_FIELDS_BY_TABLE["project_modules"].
_ALLOWED_LOCKED_UPDATE_FIELDS = _ALLOWED_FIELDS_BY_TABLE["project_modules"]


def set_locked_module_fields(
    db: Session,
    module,
    lock: EntityLock,
    **fields,
) -> None:
    """Apply field updates to a project_modules row under fence-token guard.

    DEPRECATED: The Phase 2 state-machine auto-routing is preserved here.
    For non-project_modules tables use set_locked_entity_fields directly.

    Status changes go through the state machine helper so every transition
    is validated against the allowed-transitions graph and recorded in the
    module_state_transitions audit log.  We strip status here, hand it off,
    and rely on transition_module_status to call back in (with the internal
    _from_state_machine marker) for the non-status fields.

    The generalized set_locked_entity_fields is a dumb fence-protected UPDATE
    with no state-machine awareness. State-machine routing belongs here, not
    in the generalized helper — locality discipline.
    """
    if not fields:
        return

    # Status changes go through the state machine helper so every transition
    # is validated against the allowed-transitions graph and recorded in the
    # module_state_transitions audit log. We strip status here, hand it off,
    # and rely on transition_module_status to call back in (with the internal
    # marker — see _from_state_machine sentinel) for the non-status fields.
    # (Phase 2 of the locking RFC.)
    from_state_machine = fields.pop("_from_state_machine", False)
    if "status" in fields and not from_state_machine:
        new_status = fields.pop("status")
        # Lazy import to avoid module-level cycle (module_state imports us).
        from services.module_state import transition_module_status
        transition_module_status(
            db,
            module,
            to_status=new_status,
            lock=lock,
            task_id=lock.task_id,
            extra_fields=fields if fields else None,
        )
        return

    # Delegate the actual fence-protected UPDATE to the generalized helper.
    # _from_state_machine is already stripped above, so fields is clean.
    set_locked_entity_fields(db, module, lock, table="project_modules", **fields)
