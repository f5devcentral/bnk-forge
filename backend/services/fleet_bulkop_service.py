"""Fleet bulk-operation gated executor — D-022 Phase 2.

Wave-loop pattern copied from services/bare_metal/orchestrator.py::execute_deployment:
  - Single Celery-task in-process sequential loop.
  - Commit after each wave for progress persistence.
  - Resume from current_wave_index (fail-closed resume pattern).
  - Parent (FleetBulkRun) + child (FleetBulkRunResult) records.

CRITICAL safety invariants:
  1. Stale-Decision refusal: refuse if decision.status != "resolved", age > TTL,
     or any resolved_member_id is missing from DB.
  2. Action allowlist: SAFE_ACTIONS only — refuse anything else.
  3. Thread-pool session safety: each per-member job opens its OWN get_db_context().
  4. project_id is NEVER a selector key (validated upstream in fleet_selector).
"""

import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from database import get_db_context
from models.fleet import FleetMember
from models.fleet_targeting import (
    BULK_RUN_STATUS_CANCELLED,
    BULK_RUN_STATUS_COMPLETED,
    BULK_RUN_STATUS_FAILED,
    BULK_RUN_STATUS_PAUSED_GATE,
    BULK_RUN_STATUS_RUNNING,
    BULK_RUN_TERMINAL_STATUSES,
    DECISION_STATUS_CONSUMED,
    DECISION_STATUS_RESOLVED,
    DECISION_TTL_SECONDS,
    GATE_KIND_APPROVAL,
    GATE_KIND_HEALTH_STABLE,
    GATE_KIND_TIMED_WAIT,
    GATE_MODE_HEALTH_STABLE,
    GATE_MODE_MANUAL,
    RESULT_STATUS_FAILED,
    RESULT_STATUS_PENDING,
    RESULT_STATUS_RUNNING,
    RESULT_STATUS_SKIPPED,
    RESULT_STATUS_SUCCEEDED,
    SAFE_ACTIONS,
    STABLE_HEALTH_STATUSES,
    FleetBulkRun,
    FleetBulkRunResult,
    FleetDecision,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stale-decision validation
# ---------------------------------------------------------------------------

class StaleDecisionError(Exception):
    """Raised when a Decision fails the freshness/completeness check."""


def _assert_decision_fresh(db: Session, decision: FleetDecision) -> list[FleetMember]:
    """Validate decision is fresh and all resolved members still exist.

    Raises StaleDecisionError with a descriptive message on any violation:
      - status != "resolved"
      - age > DECISION_TTL_SECONDS
      - any resolved_member_id missing from fleet_members

    Returns the list of FleetMember objects in resolved_member_ids order.
    """
    if decision.status != DECISION_STATUS_RESOLVED:
        raise StaleDecisionError(
            f"Decision {decision.id} has status '{decision.status}' (expected 'resolved'). "
            "Re-resolve the target to get a fresh decision."
        )

    # resolved_at may be offset-naive when read from SQLite in tests.
    resolved_at = decision.resolved_at
    if resolved_at.tzinfo is None:
        resolved_at = resolved_at.replace(tzinfo=UTC)
    age_seconds = (datetime.now(UTC) - resolved_at).total_seconds()
    if age_seconds > DECISION_TTL_SECONDS:
        raise StaleDecisionError(
            f"Decision {decision.id} is stale (age={age_seconds:.0f}s > TTL={DECISION_TTL_SECONDS}s). "
            "Re-resolve the target to get a fresh decision."
        )

    member_ids: list[int] = decision.resolved_member_ids or []
    if not member_ids:
        return []

    members_by_id: dict[int, FleetMember] = {
        m.id: m
        for m in db.query(FleetMember).filter(FleetMember.id.in_(member_ids)).all()
    }

    missing = [mid for mid in member_ids if mid not in members_by_id]
    if missing:
        raise StaleDecisionError(
            f"Decision {decision.id}: {len(missing)} resolved member(s) no longer exist in fleet_members: "
            f"{missing[:5]}{'...' if len(missing) > 5 else ''}. Re-resolve the target."
        )

    # Preserve the ordering from resolved_member_ids.
    return [members_by_id[mid] for mid in member_ids]


# ---------------------------------------------------------------------------
# Wave ordering
# ---------------------------------------------------------------------------

def _group_members_into_waves(
    members: list[FleetMember],
    wave_by: str = "fault_domain",
) -> list[tuple[str, list[FleetMember]]]:
    """Group members into waves by fault_domain (default) or label:<key>.

    wave_by="fault_domain": bucket by member.fault_domain (existing behaviour).
    wave_by="label:<key>": bucket by combined_labels[key] where
        combined_labels = {**discovered_labels, **assigned_labels} (assigned wins
        on key conflicts — same merge used in matches_selector).

    Ordering: non-empty bucket keys sorted lexicographically, then the
    "(none)" bucket for members whose key value is absent/empty.
    This ensures deterministic wave ordering.

    Returns list of (bucket_label, members_in_wave).
    """
    buckets: dict[str, list[FleetMember]] = {}
    none_bucket: list[FleetMember] = []

    if wave_by.startswith("label:"):
        label_key = wave_by[len("label:"):]
        for m in members:
            combined = {**(m.discovered_labels or {}), **(m.assigned_labels or {})}
            val = combined.get(label_key, "").strip()
            if val:
                buckets.setdefault(val, []).append(m)
            else:
                none_bucket.append(m)
    else:
        # Default: fault_domain (original behaviour — no breaking change).
        for m in members:
            fd = (m.fault_domain or "").strip()
            if fd:
                buckets.setdefault(fd, []).append(m)
            else:
                none_bucket.append(m)

    sorted_buckets = sorted(buckets.items())
    if none_bucket:
        sorted_buckets.append(("(none)", none_bucket))

    return sorted_buckets


def _resolve_wave_concurrency(wave_members: list[FleetMember], max_concurrency_pct: int | None, fallback: int) -> int:
    """Resolve the per-wave thread pool size from an optional percentage.

    When max_concurrency_pct is set (1-100), use max(1, ceil(n * pct / 100)).
    Otherwise fall back to `fallback` (the run's literal concurrency field).

    Invariant: result is always >= 1.
    """
    if max_concurrency_pct is not None and 1 <= max_concurrency_pct <= 100:
        return max(1, math.ceil(len(wave_members) * max_concurrency_pct / 100))
    return max(1, fallback)


# ---------------------------------------------------------------------------
# Per-member action dispatch
# ---------------------------------------------------------------------------

def _dispatch_action_for_member(
    member_id: int,
    action: str,
    action_params: dict | None,
) -> tuple[str, str]:
    """Execute the action for a single member in its OWN DB session.

    Returns (status, detail) where status is RESULT_STATUS_SUCCEEDED or
    RESULT_STATUS_FAILED.

    CRITICAL: opens its own get_db_context() — never shares the parent session.
    """
    with get_db_context() as db:
        member = db.query(FleetMember).filter(FleetMember.id == member_id).first()
        if member is None:
            return RESULT_STATUS_SKIPPED, f"Member {member_id} not found — skipped."

        if action == "re-reconcile":
            from services.fleet_reconcile_service import reconcile_fleet_member
            try:
                result = reconcile_fleet_member(db, member.member_type, member.member_id)
                db.commit()
                if result is None:
                    return RESULT_STATUS_SKIPPED, "Target row missing — member skipped."
                return RESULT_STATUS_SUCCEEDED, "Reconcile completed."
            except Exception as exc:
                db.rollback()
                return RESULT_STATUS_FAILED, f"Reconcile failed: {exc}"

        if action == "set-labels":
            # Replace-all semantics (intentional): the supplied labels dict
            # becomes the member's complete assigned_labels — prior keys are
            # dropped.  This matches policy-enforce behaviour where the bulk
            # run is the authoritative source of truth for a label set.
            # Pass an empty dict to clear all assigned labels.
            from services.fleet_vocabulary import validate_assigned_labels
            labels: dict[str, str] = (action_params or {}).get("labels") or {}
            try:
                validate_assigned_labels(labels)
                member.assigned_labels = labels
                db.commit()
                return RESULT_STATUS_SUCCEEDED, f"Assigned labels set: {labels}"
            except Exception as exc:
                db.rollback()
                return RESULT_STATUS_FAILED, f"set-labels failed: {exc}"

        # Should never reach here — allowlist is checked before the wave loop.
        return RESULT_STATUS_FAILED, f"Unknown action '{action}'."


# ---------------------------------------------------------------------------
# Between-wave gate check
# ---------------------------------------------------------------------------

def _wave_gate_passes(
    db: Session,
    wave_member_ids: list[int],
    gate_mode: str,
) -> bool:
    """Return True if the gate allows proceeding to the next wave.

    health_stable / GATE_KIND_HEALTH_STABLE:
        Re-query acted members; all must have health_status in STABLE_HEALTH_STATUSES
        (or None — unknown tolerated).
    manual / GATE_KIND_APPROVAL:
        Always False — operator must resume explicitly.
    GATE_KIND_TIMED_WAIT:
        Always False here — the executor handles the Celery countdown re-dispatch
        path before calling this function; by the time we reach _wave_gate_passes
        for timed_wait, we still block (the resume task will re-enter the executor
        with resuming=True, skipping this gate entirely).
    """
    if gate_mode in (GATE_MODE_MANUAL, GATE_KIND_APPROVAL):
        return False

    if gate_mode in (GATE_MODE_HEALTH_STABLE, GATE_KIND_HEALTH_STABLE):
        members = db.query(FleetMember).filter(FleetMember.id.in_(wave_member_ids)).all()
        for m in members:
            hs = (m.health_status or "").strip().lower()
            # None/empty = unknown — we tolerate it rather than blocking.
            if hs and hs not in STABLE_HEALTH_STATUSES:
                logger.info(
                    "health_stable gate: member %d has health_status='%s' — gate blocks",
                    m.id, hs,
                )
                return False
        return True

    if gate_mode == GATE_KIND_TIMED_WAIT:
        # timed_wait is handled by the wave loop before this function is called:
        # the executor sets gate_resumes_at and dispatches a Celery countdown task,
        # then returns paused_gate. This path should not normally be reached, but
        # we treat it as blocking (fail-closed) in case it is.
        return False

    # Unknown gate_mode — fail-closed.
    logger.warning("Unknown gate_mode '%s' — blocking wave progression.", gate_mode)
    return False


# ---------------------------------------------------------------------------
# Bulk-run persistence helpers
# ---------------------------------------------------------------------------

def _update_result_row(
    db: Session,
    result_row: FleetBulkRunResult,
    status: str,
    detail: str | None = None,
) -> None:
    result_row.status = status
    result_row.completed_at = datetime.now(UTC)
    if detail is not None:
        result_row.detail = detail


# ---------------------------------------------------------------------------
# Gate helpers — strategy-aware
# ---------------------------------------------------------------------------

_DEFAULT_TIMED_WAIT_SECONDS = 300  # 5 min fallback if strategy is missing


def _get_gate_wait_seconds(db: Session, run: FleetBulkRun) -> int:
    """Resolve the timed_wait gate duration for a run.

    Priority: strategy.gate_wait_seconds → _DEFAULT_TIMED_WAIT_SECONDS.
    Returns an int >= 1.
    """
    if run.strategy_id is not None:
        from models.fleet_targeting import FleetOperationStrategy as _StrategyModel
        strat = db.query(_StrategyModel).filter(_StrategyModel.id == run.strategy_id).first()
        if strat is not None and strat.gate_wait_seconds and strat.gate_wait_seconds > 0:
            return strat.gate_wait_seconds
    return _DEFAULT_TIMED_WAIT_SECONDS


# ---------------------------------------------------------------------------
# Main executor — called by Celery task
# ---------------------------------------------------------------------------

def execute_fleet_bulk_run(run_id: int, *, resuming: bool = False, _db: Session | None = None) -> dict:
    """Execute (or resume) a fleet bulk run — called by Celery task.

    Wave-loop pattern from bare_metal/orchestrator.py::execute_deployment:
      - Sequential waves in-process.
      - Commit after each wave.
      - Resume from current_wave_index on re-dispatch.
      - Fail-closed on exception.

    resuming: when True, skip freshness check and decision status flip (already
              consumed on initial run). Used by resume_fleet_bulk_op.

    _db: optional session for testing. In production, omit and a fresh
    get_db_context() session is used.

    Returns summary dict.
    """
    if _db is not None:
        return _execute_bulk_run_with_db(run_id, db=_db, resuming=resuming)

    with get_db_context() as db:
        return _execute_bulk_run_with_db(run_id, db=db, resuming=resuming)


def _execute_bulk_run_with_db(run_id: int, db: Session, *, resuming: bool = False) -> dict:  # noqa: C901
    """Inner executor — called with an already-open session.

    Separated from execute_fleet_bulk_run so tests can inject the test DB
    session directly.

    resuming: when True, skip the stale-decision check and decision consumed
              flip — the decision is already consumed from the initial run.
    """
    run = db.query(FleetBulkRun).filter(FleetBulkRun.id == run_id).first()
    if run is None:
        raise ValueError(f"FleetBulkRun {run_id} not found.")

    # Idempotency guard: refuse if already terminal or actively running.
    if run.status in BULK_RUN_TERMINAL_STATUSES or run.status == BULK_RUN_STATUS_RUNNING:
        raise ValueError(f"Run {run_id} already in status '{run.status}'")

    decision = db.query(FleetDecision).filter(FleetDecision.id == run.decision_id).first()
    if decision is None:
        run.status = BULK_RUN_STATUS_FAILED
        run.error_message = f"FleetDecision {run.decision_id} not found."
        db.commit()
        raise ValueError(run.error_message)

    # --- Run-start gates ---

    # 1. Action allowlist check.
    if run.action not in SAFE_ACTIONS:
        run.status = BULK_RUN_STATUS_FAILED
        run.error_message = (
            f"Action '{run.action}' is not in the safe-actions allowlist "
            f"{sorted(SAFE_ACTIONS)}. Refusing to execute."
        )
        db.commit()
        raise ValueError(run.error_message)

    # 2. Stale-decision refusal (THE safety invariant).
    #    Skipped on resume: decision was already validated and consumed on
    #    the initial run.
    if not resuming:
        try:
            members = _assert_decision_fresh(db, decision)
        except StaleDecisionError as exc:
            run.status = BULK_RUN_STATUS_FAILED
            run.error_message = str(exc)
            db.commit()
            raise

        # 3. Mark decision consumed + run running (single-use).
        decision.status = DECISION_STATUS_CONSUMED
    else:
        # On resume we still need to fetch the member list from the decision.
        member_ids: list[int] = decision.resolved_member_ids or []
        members_by_id: dict[int, FleetMember] = {
            m.id: m
            for m in db.query(FleetMember).filter(FleetMember.id.in_(member_ids)).all()
        }
        members = [members_by_id[mid] for mid in member_ids if mid in members_by_id]

    run.status = BULK_RUN_STATUS_RUNNING
    if not resuming:
        run.started_at = datetime.now(UTC)

    # 4. Build wave plan — respect wave_by from the run (set from strategy snapshot or default).
    waves = _group_members_into_waves(members, wave_by=run.wave_by or "fault_domain")
    run.total_waves = len(waves)
    db.commit()

    if not waves:
        run.status = BULK_RUN_STATUS_COMPLETED
        run.completed_at = datetime.now(UTC)
        db.commit()
        return {
            "status": "completed",
            "run_id": run_id,
            "waves": 0,
            "total_members": 0,
        }

    resume_from = run.current_wave_index
    total_succeeded = 0
    total_failed = 0
    total_skipped = 0

    try:
        for wave_idx, (fault_domain_label, wave_members) in enumerate(waves):
            if wave_idx < resume_from:
                # Skip already-completed waves on resume.
                continue

            # --- Cooperative cancel check (top of every wave boundary) ---
            # Re-read the run's status from the current session. If the run was
            # cancelled via POST /bulk-ops/{id}/cancel while this executor was
            # between waves (or before the first wave), stop cleanly here.
            # The in-flight wave (if any) already completed its ThreadPoolExecutor
            # before we reach this point — no member writes are orphaned.
            # NEVER use celery-revoke: it would SIGKILL mid-wave.
            db.refresh(run)
            if run.status == BULK_RUN_STATUS_CANCELLED:
                logger.info(
                    "FleetBulkRun %d: cooperative cancel detected at wave %d boundary — stopping.",
                    run_id, wave_idx,
                )
                if run.completed_at is None:
                    run.completed_at = datetime.now(UTC)
                    db.commit()
                return {
                    "status": "cancelled",
                    "run_id": run_id,
                    "cancelled_at_wave": wave_idx,
                }

            run.current_wave_index = wave_idx
            db.commit()

            logger.info(
                "FleetBulkRun %d wave %d/%d: fault_domain='%s', %d members",
                run_id, wave_idx + 1, len(waves), fault_domain_label, len(wave_members),
            )

            # Create result rows for this wave (pending).
            wave_member_ids = [m.id for m in wave_members]
            for m in wave_members:
                existing = (
                    db.query(FleetBulkRunResult)
                    .filter(
                        FleetBulkRunResult.run_id == run_id,
                        FleetBulkRunResult.fleet_member_id == m.id,
                    )
                    .first()
                )
                if existing is None:
                    result_row = FleetBulkRunResult(
                        run_id=run_id,
                        fleet_member_id=m.id,
                        wave_index=wave_idx,
                        fault_domain=m.fault_domain,
                        status=RESULT_STATUS_PENDING,
                    )
                    db.add(result_row)
            db.commit()

            # Fan-out with concurrency cap — each job opens its own session.
            # If the run has a strategy with max_concurrency_pct, compute the
            # per-wave worker count from the wave size; else use the literal concurrency field.
            _strategy_pct: int | None = None
            if run.strategy_id is not None:
                from models.fleet_targeting import FleetOperationStrategy as _StrategyModel
                _strat = db.query(_StrategyModel).filter(_StrategyModel.id == run.strategy_id).first()
                if _strat is not None:
                    _strategy_pct = _strat.max_concurrency_pct
            concurrency = _resolve_wave_concurrency(wave_members, _strategy_pct, run.concurrency or 5)
            futures_to_member: dict = {}

            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                for m in wave_members:
                    fut = pool.submit(
                        _dispatch_action_for_member,
                        m.id,
                        run.action,
                        run.action_params,
                    )
                    futures_to_member[fut] = m.id

                for fut in as_completed(futures_to_member):
                    member_id = futures_to_member[fut]
                    # Mark result row as started before collecting result.
                    result_row = (
                        db.query(FleetBulkRunResult)
                        .filter(
                            FleetBulkRunResult.run_id == run_id,
                            FleetBulkRunResult.fleet_member_id == member_id,
                        )
                        .first()
                    )
                    if result_row:
                        result_row.started_at = datetime.now(UTC)
                        result_row.status = RESULT_STATUS_RUNNING

                    try:
                        status, detail = fut.result()
                    except Exception as exc:
                        status = RESULT_STATUS_FAILED
                        detail = f"Unexpected error: {exc}"
                        logger.exception(
                            "FleetBulkRun %d member %d raised unexpected error",
                            run_id, member_id,
                        )

                    if result_row:
                        _update_result_row(db, result_row, status, detail)
                    db.commit()

                    if status == RESULT_STATUS_SUCCEEDED:
                        total_succeeded += 1
                    elif status == RESULT_STATUS_FAILED:
                        total_failed += 1
                    else:
                        total_skipped += 1

            # Between-wave gate (skip after last wave).
            is_last_wave = (wave_idx == len(waves) - 1)
            if not is_last_wave:
                # timed_wait gate: set gate_resumes_at + schedule Celery countdown resume.
                if run.gate_mode == GATE_KIND_TIMED_WAIT:
                    wait_seconds = _get_gate_wait_seconds(db, run)
                    run.status = BULK_RUN_STATUS_PAUSED_GATE
                    run.current_wave_index = wave_idx + 1  # resume from next wave
                    run.gate_resumes_at = datetime.now(UTC) + timedelta(seconds=wait_seconds)
                    db.commit()
                    # Schedule Celery countdown re-dispatch via the existing resume path.
                    from tasks.fleet_tasks import resume_fleet_bulk_op
                    resume_fleet_bulk_op.apply_async(
                        args=[run_id],
                        countdown=wait_seconds,
                    )
                    logger.info(
                        "FleetBulkRun %d timed_wait gate after wave %d — resumes in %ds at %s.",
                        run_id, wave_idx, wait_seconds, run.gate_resumes_at,
                    )
                    return {
                        "status": "paused_gate",
                        "run_id": run_id,
                        "paused_after_wave": wave_idx,
                        "total_waves": len(waves),
                        "gate_resumes_at": run.gate_resumes_at.isoformat(),
                        "succeeded": total_succeeded,
                        "failed": total_failed,
                        "skipped": total_skipped,
                    }

                gate_passes = _wave_gate_passes(db, wave_member_ids, run.gate_mode)
                if not gate_passes:
                    run.status = BULK_RUN_STATUS_PAUSED_GATE
                    run.current_wave_index = wave_idx + 1  # resume from next wave
                    db.commit()
                    logger.info(
                        "FleetBulkRun %d paused at gate after wave %d (mode=%s).",
                        run_id, wave_idx, run.gate_mode,
                    )
                    return {
                        "status": "paused_gate",
                        "run_id": run_id,
                        "paused_after_wave": wave_idx,
                        "total_waves": len(waves),
                        "succeeded": total_succeeded,
                        "failed": total_failed,
                        "skipped": total_skipped,
                    }

        # All waves completed.
        run.status = BULK_RUN_STATUS_COMPLETED
        run.completed_at = datetime.now(UTC)
        db.commit()

        logger.info(
            "FleetBulkRun %d completed: %d succeeded, %d failed, %d skipped",
            run_id, total_succeeded, total_failed, total_skipped,
        )
        return {
            "status": "completed",
            "run_id": run_id,
            "waves": len(waves),
            "succeeded": total_succeeded,
            "failed": total_failed,
            "skipped": total_skipped,
        }

    except Exception as exc:
        run.status = BULK_RUN_STATUS_FAILED
        run.error_message = str(exc)
        run.completed_at = datetime.now(UTC)
        db.commit()
        logger.error("FleetBulkRun %d failed: %s", run_id, exc)
        raise
