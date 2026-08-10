"""Module status state machine + audit log helper.

Defines the legal `ModuleStatus -> ModuleStatus` transition graph and a
single helper (`transition_module_status`) that every status writer must
go through. The helper:

  1. Validates the transition is in `ALLOWED_TRANSITIONS`. Self-loops are
     allowed as no-op idempotency. Illegal transitions raise
     `IllegalTransitionError` (strict mode — see RFC).
  2. Inserts a row into `module_state_transitions` recording the
     from/to/task_id/fence_token/reason/timestamp.
  3. Applies the status change. If a `ModuleLock` is provided the write
     goes through the fence-protected `set_locked_module_fields` helper.
     Otherwise (HTTP-time pre-writes, admin reset paths) it goes through
     a plain ORM update — these writes happen before the celery task
     acquires the lock and have no fence to enforce.

Phase 2 of the locking RFC. See memory: rfc_robust_module_locking.md.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from models import ModuleStateTransition, ProjectModule
from models.enums import ModuleStatus
from services.module_lock import ModuleLock, set_locked_module_fields

logger = logging.getLogger(__name__)


class IllegalTransitionError(Exception):
    """Raised when a status transition is rejected by the state machine.

    Carries the from/to statuses and module_id so callers can log a
    useful error message.
    """

    def __init__(self, module_id: int, from_status: str, to_status: str) -> None:
        self.module_id = module_id
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f"Illegal module status transition for module {module_id}: "
            f"{from_status!r} -> {to_status!r}"
        )


# ---------------------------------------------------------------------------
# Transition graph
# ---------------------------------------------------------------------------
# Built by auditing every site that writes module.status (see Phase 2 audit
# task in handoff_phase1_5_engine_locking.md / Phase 2 worktree).
#
# Self-loops are NOT listed here — `transition_module_status` short-circuits
# them as no-ops (idempotent re-set of the same status).
#
# When in doubt, prefer adding an edge over rejecting a transition that
# happens in production. A spurious reject is a deploy outage; a spurious
# allow is at worst a noisy audit log.
# ---------------------------------------------------------------------------

_INIT_OUTCOMES = frozenset({
    ModuleStatus.INITIALIZING, ModuleStatus.INITIALIZED, ModuleStatus.INIT_FAILED,
})
_PLAN_OUTCOMES = frozenset({
    ModuleStatus.PLANNING, ModuleStatus.PLANNED, ModuleStatus.PLAN_FAILED,
})
_APPLY_OUTCOMES = frozenset({
    ModuleStatus.APPLYING, ModuleStatus.APPLIED, ModuleStatus.APPLY_FAILED,
})
_DESTROY_OUTCOMES = frozenset({
    ModuleStatus.DESTROYING, ModuleStatus.DESTROYED, ModuleStatus.DESTROY_FAILED,
})
_ADMIN_RESET = frozenset({ModuleStatus.NOT_INITIALIZED})
_NO_OP_DESTROY = frozenset({ModuleStatus.DESTROYED})

ALLOWED_TRANSITIONS: dict[ModuleStatus, frozenset[ModuleStatus]] = {
    # NOT_INITIALIZED can begin init (queue OR direct outcome — the HTTP-time
    # "initializing" pre-write is optional UX decoration, the celery task can
    # write the outcome directly when invoked from a service layer or test).
    # NOT permitted: any apply/plan/destroy outcomes — those would skip init.
    ModuleStatus.NOT_INITIALIZED: _INIT_OUTCOMES | _NO_OP_DESTROY,

    # Transitional states resolve to their own outcomes or get cancelled back.
    ModuleStatus.INITIALIZING: frozenset({
        ModuleStatus.INITIALIZED, ModuleStatus.INIT_FAILED,
        ModuleStatus.NOT_INITIALIZED,  # cancel/reset
    }),
    ModuleStatus.PLANNING: frozenset({
        ModuleStatus.PLANNED, ModuleStatus.PLAN_FAILED,
        ModuleStatus.INIT_FAILED,      # plan task ran init internally and it failed
        ModuleStatus.INITIALIZED,      # cancel-back
    }),
    ModuleStatus.APPLYING: frozenset({
        ModuleStatus.APPLIED, ModuleStatus.APPLY_FAILED,
        ModuleStatus.INIT_FAILED,      # apply task ran init internally and it failed
        ModuleStatus.PLAN_FAILED,      # apply task ran plan internally and it failed
        ModuleStatus.PLANNED,          # cancel-back (status_map: applying -> planned)
    }),
    ModuleStatus.DESTROYING: frozenset({
        ModuleStatus.DESTROYED, ModuleStatus.DESTROY_FAILED,
        ModuleStatus.APPLIED,          # cancel-back (status_map: destroying -> applied)
    }),

    # Idle non-failed states allow any reasonable next operation.
    ModuleStatus.INITIALIZED:
        _INIT_OUTCOMES | _PLAN_OUTCOMES | _APPLY_OUTCOMES | _DESTROY_OUTCOMES |
        _NO_OP_DESTROY | _ADMIN_RESET,
    ModuleStatus.PLANNED:
        _PLAN_OUTCOMES | _APPLY_OUTCOMES | _DESTROY_OUTCOMES | _NO_OP_DESTROY |
        _ADMIN_RESET,
    ModuleStatus.APPLIED:
        _PLAN_OUTCOMES | _APPLY_OUTCOMES | _DESTROY_OUTCOMES | _ADMIN_RESET,
    ModuleStatus.DESTROYED:
        _INIT_OUTCOMES | _ADMIN_RESET,

    # Failed states allow any retry path. The celery task that retries may
    # be init, plan, apply, or destroy depending on the user's intent (per
    # project_module_service.retry_deployment which switches strategy).
    ModuleStatus.INIT_FAILED:
        _INIT_OUTCOMES | _PLAN_OUTCOMES | _APPLY_OUTCOMES | _NO_OP_DESTROY |
        _ADMIN_RESET,
    ModuleStatus.PLAN_FAILED:
        _INIT_OUTCOMES | _PLAN_OUTCOMES | _APPLY_OUTCOMES | _NO_OP_DESTROY |
        _ADMIN_RESET,
    ModuleStatus.APPLY_FAILED:
        _INIT_OUTCOMES | _PLAN_OUTCOMES | _APPLY_OUTCOMES | _DESTROY_OUTCOMES |
        _ADMIN_RESET,
    ModuleStatus.DESTROY_FAILED:
        _DESTROY_OUTCOMES | _ADMIN_RESET,

    # FAILED is a generic catch-all rarely used in current code.
    ModuleStatus.FAILED: _INIT_OUTCOMES | _ADMIN_RESET,
}


def is_legal_transition(from_status: str, to_status: str) -> bool:
    """True if `from_status -> to_status` is in the allowed graph.

    Self-loops are always considered legal (idempotent re-set).
    Unknown statuses (not in ModuleStatus) reject.
    """
    if from_status == to_status:
        return True
    try:
        from_enum = ModuleStatus(from_status)
        to_enum = ModuleStatus(to_status)
    except ValueError:
        return False
    return to_enum in ALLOWED_TRANSITIONS.get(from_enum, frozenset())


def transition_module_status(
    db: Session,
    module: ProjectModule,
    *,
    to_status: str,
    lock: ModuleLock | None = None,
    task_id: int | None = None,
    reason: str = "",
    extra_fields: dict | None = None,
) -> None:
    """Atomically transition `module.status` to `to_status` with an audit row.

    Args:
        db: Session for both the audit insert and the status UPDATE.
        module: ProjectModule whose status is changing.
        to_status: target status (string, must match a ModuleStatus value).
        lock: if provided, the status UPDATE goes through the fence-protected
            `set_locked_module_fields` helper. If None, plain ORM update —
            used for HTTP-time pre-writes (e.g. submit_init marking a module
            "initializing" before the celery task picks up).
        task_id: integer Task.id (NOT the celery UUID) responsible for the
            transition; recorded in the audit row.
        reason: short human-readable why ("tofu apply succeeded", "init exit
            code 1", "user clicked retry"). Truncated to 500 chars.
        extra_fields: additional module fields to write atomically with the
            status change (e.g. `outputs={...}`, `last_deployed_at=now()`).
            Only honored when `lock` is provided — those columns are part
            of the same fence-protected UPDATE.

    Raises:
        IllegalTransitionError: if the transition is not in ALLOWED_TRANSITIONS.
        ModuleLockLostError: if `lock` is provided and the fence no longer
            matches (paused worker resumed after reclaim).
    """
    from_status = module.status

    if not is_legal_transition(from_status, to_status):
        raise IllegalTransitionError(module.id, from_status, to_status)

    # Self-loop short-circuit: still log the audit row (caller asked for it
    # explicitly with a reason; useful for observability even if no-op).
    if from_status == to_status and not extra_fields:
        _log_transition(db, module, from_status, to_status, lock, task_id, reason)
        return

    if lock is not None:
        # Sentinel breaks the auto-route loop: set_locked_module_fields
        # auto-routes status writes through transition_module_status; we set
        # _from_state_machine so it knows to apply the UPDATE directly.
        fields = {"status": to_status, "_from_state_machine": True, **(extra_fields or {})}
        set_locked_module_fields(db, module, lock, **fields)
    else:
        # No lock: plain ORM update. extra_fields ignored to keep the helper's
        # contract simple — callers without a lock are HTTP-time pre-writes
        # that only set status, not outputs/etc.
        if extra_fields:
            logger.warning(
                "transition_module_status: extra_fields=%s ignored on unlocked "
                "transition for module %s — pass a ModuleLock to write them.",
                list(extra_fields.keys()),
                module.id,
            )
        module.status = to_status
        db.commit()
        db.expire(module, ["status"])

    _log_transition(db, module, from_status, to_status, lock, task_id, reason)


def _log_transition(
    db: Session,
    module: ProjectModule,
    from_status: str,
    to_status: str,
    lock: ModuleLock | None,
    task_id: int | None,
    reason: str,
) -> None:
    row = ModuleStateTransition(
        module_id=module.id,
        from_status=from_status,
        to_status=to_status,
        task_id=task_id,
        fence_token=lock.fence_token if lock is not None else None,
        reason=(reason or "")[:500],
    )
    db.add(row)
    db.commit()
    logger.info(
        "module_state_transition module_id=%s %s->%s task_id=%s fence=%s reason=%r",
        module.id, from_status, to_status, task_id,
        lock.fence_token if lock else None, reason,
    )
