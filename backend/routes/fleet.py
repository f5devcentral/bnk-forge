"""Fleet members + targeting + bulk-ops API — D-022.

Endpoints (Phase 1 — inventory):
  GET  /api/fleet/members              — List fleet members with label filter + group-by
  PUT  /api/fleet/members/{id}/labels  — Set/unset assigned labels (controlled vocabulary)
  GET  /api/fleet/vocabulary           — Return the controlled assigned-label vocabulary

Endpoints (Phase 2 — targeting + bulk-ops):
  POST /api/fleet/targets              — Create a named label-selector target
  GET  /api/fleet/targets              — List targets
  GET  /api/fleet/targets/{id}         — Get target
  POST /api/fleet/targets/{id}/resolve — Resolve to a Decision (blast-radius preview)
  GET  /api/fleet/decisions/{id}       — Get decision (blast-radius view)
  POST /api/fleet/bulk-ops                         — Start a bulk operation against a decision
  GET  /api/fleet/bulk-ops/{id}                    — Get bulk run status
  POST /api/fleet/bulk-ops/{id}/resume             — Resume a paused bulk run
  POST /api/fleet/targets/{id}/run-selection       — Ad-hoc selection → run (Slice C, D-022 P6)
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.auth_context import effective_role
from core.errors import BadRequestError, ConflictError, NotFoundError, handle_route_errors
from database import get_db
from models import User
from models.fleet import FleetMember
from models.fleet_targeting import (
    BULK_RUN_STATUS_CANCELLED,
    BULK_RUN_STATUS_PAUSED_GATE,
    BULK_RUN_STATUS_PENDING,
    BULK_RUN_STATUS_RUNNING,
    BULK_RUN_TERMINAL_STATUSES,
    DECISION_STATUS_RESOLVED,
    GATE_KIND_TIMED_WAIT,
    GATE_MODES,
    SAFE_ACTIONS,
    STRATEGY_GATE_KINDS,
    FleetBulkRun,
    FleetBulkRunResult,
    FleetDecision,
    FleetOperationStrategy,
    FleetTarget,
)
from models.project import Project
from routes.auth import require_operator, require_viewer
from services.fleet_rbac import require_fleet_mutation, resolve_rollout_defaults
from services.fleet_vocabulary import ASSIGNED_LABEL_KEYS, validate_assigned_labels

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fleet", tags=["fleet"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class FleetMemberOut(BaseModel):
    id: int
    project_id: int | None
    member_type: str
    member_id: int
    name: str
    discovered_labels: dict[str, str]
    assigned_labels: dict[str, str]
    fault_domain: str | None
    health_status: str | None
    # lifecycle_state: derived unified state (provisioning|active|draining|decommissioned|unknown).
    # NULL until the first reconcile pass — consumers should treat NULL as "unknown".
    lifecycle_state: str | None
    last_reconciled_at: str | None

    class Config:
        from_attributes = True


class FleetMembersResponse(BaseModel):
    members: list[FleetMemberOut]
    total: int


class FleetMembersGroupedResponse(BaseModel):
    groups: dict[str, list[FleetMemberOut]]
    total: int
    group_by: str


class AssignedLabelsRequest(BaseModel):
    labels: dict[str, str]


class VocabularyResponse(BaseModel):
    assigned_label_keys: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _member_to_out(m: FleetMember) -> FleetMemberOut:
    return FleetMemberOut(
        id=m.id,
        project_id=m.project_id,
        member_type=m.member_type,
        member_id=m.member_id,
        name=m.name,
        discovered_labels=m.discovered_labels or {},
        assigned_labels=m.assigned_labels or {},
        fault_domain=m.fault_domain,
        health_status=m.health_status,
        lifecycle_state=m.lifecycle_state,
        last_reconciled_at=(
            m.last_reconciled_at.isoformat() if m.last_reconciled_at else None
        ),
    )


def _label_filter_matches(member: FleetMember, label_filters: list[str]) -> bool:
    """Return True if the member has all requested label key=value pairs.

    Delegates to services/fleet_selector.py::matches_selector — single
    canonical implementation shared by route + resolver (DECOMPOSITION §1).
    Kept here for backward-compatibility with existing test imports.
    """
    from services.fleet_selector import matches_selector
    return matches_selector(member, label_filters)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get(
    "/members",
    response_model=FleetMembersResponse | FleetMembersGroupedResponse,
)
@handle_route_errors("list fleet members")
def list_fleet_members(
    label: list[str] = Query(default=[]),
    member_type: str | None = Query(default=None),
    group_by: str | None = Query(default=None),
    current_user: User = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> Any:
    """List fleet members scoped to the caller's visible projects with optional label filter + group-by.

    Admins see all members. Non-admins see only members whose project_id belongs to a
    project they own (Project.user_id == current_user.id).

    label query param format: label=key=value (repeat for AND filter).
    group_by: a label key — members are grouped by their value for that key.

    Note: label filtering runs in Python after a DB query (P1 — unindexed JSON).
    P2 selectors should add a Postgres GIN index before making this the hot path.
    """
    query = db.query(FleetMember)

    if effective_role(current_user) != "admin":
        owned_project_ids = [
            pid for (pid,) in db.query(Project.id).filter(Project.user_id == current_user.id).all()
        ]
        query = query.filter(FleetMember.project_id.in_(owned_project_ids))

    if member_type:
        query = query.filter(FleetMember.member_type == member_type)

    members = query.order_by(FleetMember.member_type, FleetMember.name).all()

    # Filter in Python (see note above).
    if label:
        members = [m for m in members if _label_filter_matches(m, label)]

    members_out = [_member_to_out(m) for m in members]

    if group_by:
        groups: dict[str, list[FleetMemberOut]] = {}
        for m in members_out:
            combined = {**m.discovered_labels, **m.assigned_labels}
            group_value = combined.get(group_by, "(none)")
            groups.setdefault(group_value, []).append(m)
        return FleetMembersGroupedResponse(
            groups=groups,
            total=len(members_out),
            group_by=group_by,
        )

    return FleetMembersResponse(members=members_out, total=len(members_out))


@router.put(
    "/members/{fleet_member_id}/labels",
    response_model=FleetMemberOut,
)
@handle_route_errors("update assigned fleet labels")
def update_assigned_labels(
    fleet_member_id: int,
    body: AssignedLabelsRequest,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> FleetMemberOut:
    """Set/unset assigned labels for a fleet member.

    Only keys in the controlled vocabulary (ASSIGNED_LABEL_KEYS) are allowed.
    Discovered-namespace keys are rejected. Replaces the entire assigned_labels dict.

    RBAC gate: caller must be the project owner (or admin) of the member's project.
    """
    validate_assigned_labels(body.labels)

    member = db.query(FleetMember).filter(FleetMember.id == fleet_member_id).first()
    if not member:
        raise NotFoundError("FleetMember", str(fleet_member_id))

    # RBAC gate — fail-closed before mutation.
    require_fleet_mutation([member.project_id], current_user, db)

    member.assigned_labels = body.labels
    db.commit()
    db.refresh(member)
    return _member_to_out(member)


@router.get(
    "/vocabulary",
    response_model=VocabularyResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get fleet label vocabulary")
def get_vocabulary() -> VocabularyResponse:
    """Return the controlled vocabulary for assigned fleet labels."""
    return VocabularyResponse(assigned_label_keys=sorted(ASSIGNED_LABEL_KEYS))


# ---------------------------------------------------------------------------
# Label vocabulary endpoint — D-022 P6 follow-up (selector builder)
# ---------------------------------------------------------------------------


class LabelKeyEntry(BaseModel):
    key: str
    values: list[str]


class LabelVocabularyResponse(BaseModel):
    keys: list[LabelKeyEntry]


@router.get(
    "/label-vocabulary",
    response_model=LabelVocabularyResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get fleet label vocabulary")
def get_label_vocabulary(
    current_user: User = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> LabelVocabularyResponse:
    """Aggregate DISTINCT combined-label (discovered ∪ assigned) key→values across all
    FleetMembers visible to the caller (RBAC-scoped, same scoping as /members list).

    Returns ``{ keys: [ {key, values: [...]}, ... ] }`` sorted by key, with values
    sorted within each key. This powers the selector builder dropdowns so users
    can pick real labels rather than guess.
    """
    query = db.query(FleetMember)
    if effective_role(current_user) != "admin":
        owned_project_ids = [
            pid for (pid,) in db.query(Project.id).filter(Project.user_id == current_user.id).all()
        ]
        query = query.filter(FleetMember.project_id.in_(owned_project_ids))

    members = query.all()

    # Aggregate key → set[values] across all visible members.
    vocab: dict[str, set[str]] = {}
    for m in members:
        combined = {**(m.discovered_labels or {}), **(m.assigned_labels or {})}
        for k, v in combined.items():
            if k and v:
                vocab.setdefault(k, set()).add(v)

    keys = [
        LabelKeyEntry(key=k, values=sorted(vocab[k]))
        for k in sorted(vocab)
    ]
    return LabelVocabularyResponse(keys=keys)


# ---------------------------------------------------------------------------
# Selector preview endpoint — D-022 P6 follow-up (live match preview)
# ---------------------------------------------------------------------------


class PreviewSelectorRequest(BaseModel):
    selector: dict
    pinned_member_ids: list[int] | None = None


class PreviewMemberOut(BaseModel):
    id: int
    name: str
    member_type: str


class PreviewSelectorResponse(BaseModel):
    matched_count: int
    sample: list[PreviewMemberOut]


_PREVIEW_SAMPLE_LIMIT = 25


@router.post(
    "/preview-selector",
    response_model=PreviewSelectorResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("preview fleet selector")
def preview_selector(
    body: PreviewSelectorRequest,
    current_user: User = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> PreviewSelectorResponse:
    """Read-only dry-run: resolve `selector` + `pinned_member_ids` without writing
    any FleetDecision row.

    Uses the same matching logic as ``get_fleet_members_by_fleet`` and
    ``resolve_target`` (empty selector + no pins = zero members, NOT all).
    RBAC-scoped: non-admins see only their own projects.

    Returns ``{ matched_count, sample }`` (sample capped at 25 members).
    """
    from services.fleet_selector import matches_selector, validate_selector

    validate_selector(body.selector)
    label_filters: list[str] = (body.selector or {}).get("labels") or []
    pinned: list[int] = body.pinned_member_ids or []

    # Empty selector + no pinned members → zero (empty ≠ all).
    if not label_filters and not pinned:
        return PreviewSelectorResponse(matched_count=0, sample=[])

    query = db.query(FleetMember)
    if effective_role(current_user) != "admin":
        owned_project_ids = [
            pid for (pid,) in db.query(Project.id).filter(Project.user_id == current_user.id).all()
        ]
        query = query.filter(FleetMember.project_id.in_(owned_project_ids))

    if label_filters:
        all_members = query.order_by(FleetMember.member_type, FleetMember.name).all()
        matched = [m for m in all_members if matches_selector(m, label_filters)]
    else:
        matched = []

    # Merge pinned members (may not appear in the label-filtered set).
    if pinned:
        matched_ids = {m.id for m in matched}
        extra_ids = [pid for pid in pinned if pid not in matched_ids]
        if extra_ids:
            extra = db.query(FleetMember).filter(FleetMember.id.in_(extra_ids)).all()
            matched = matched + extra

    sample_out = [
        PreviewMemberOut(id=m.id, name=m.name, member_type=m.member_type)
        for m in matched[:_PREVIEW_SAMPLE_LIMIT]
    ]
    return PreviewSelectorResponse(matched_count=len(matched), sample=sample_out)


@router.get(
    "/fleet-members",
    response_model=FleetMembersResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("resolve fleet members by fleet id")
def get_fleet_members_by_fleet(
    fleet_id: int = Query(...),
    current_user: User = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> FleetMembersResponse:
    """Resolve the members of a Fleet (FleetTarget) server-side by its selector.

    Convenience endpoint: client passes only ?fleet_id= and receives the resolved
    member set without needing to know the target's label selectors.
    Only active (non-soft-deleted) fleets are resolvable via this endpoint.
    """
    from services.fleet_selector import matches_selector

    target = (
        db.query(FleetTarget)
        .filter(FleetTarget.id == fleet_id, FleetTarget.deleted_at.is_(None))
        .first()
    )
    if not target:
        raise NotFoundError("FleetTarget", str(fleet_id))

    label_filters: list[str] = (target.selector or {}).get("labels") or []
    pinned: list[int] = target.pinned_member_ids or []

    # Empty selector + no pinned members → zero members (empty ≠ all).
    if not label_filters and not pinned:
        return FleetMembersResponse(members=[], total=0)

    query = db.query(FleetMember)
    if effective_role(current_user) != "admin":
        owned_ids = [
            pid for (pid,) in db.query(Project.id).filter(Project.user_id == current_user.id).all()
        ]
        query = query.filter(FleetMember.project_id.in_(owned_ids))

    if label_filters:
        members = query.order_by(FleetMember.member_type, FleetMember.name).all()
        members = [m for m in members if matches_selector(m, label_filters)]
    else:
        members = []

    # Also include explicitly pinned members not already in selector result.
    if pinned:
        existing_ids = {m.id for m in members}
        extra_ids = [pid for pid in pinned if pid not in existing_ids]
        if extra_ids:
            extra = db.query(FleetMember).filter(FleetMember.id.in_(extra_ids)).all()
            members = members + extra

    members_out = [_member_to_out(m) for m in members]
    return FleetMembersResponse(members=members_out, total=len(members_out))


# ===========================================================================
# Phase 2: Targeting + Bulk Operations
# ===========================================================================

# ---------------------------------------------------------------------------
# Phase 2 response schemas
# ---------------------------------------------------------------------------


class TargetOut(BaseModel):
    id: int
    project_id: int | None
    name: str
    description: str | None = None
    selector: dict
    pinned_member_ids: list[int] | None = None
    created_by: str | None
    created_at: str | None

    class Config:
        from_attributes = True


class DecisionOut(BaseModel):
    id: int
    target_id: int
    project_id: int | None
    selector_snapshot: dict
    resolved_member_ids: list[int]
    member_count: int
    resolved_at: str
    resolved_by: str | None
    status: str
    # Blast-radius preview: enriched member list
    members: list[FleetMemberOut] | None = None

    class Config:
        from_attributes = True


class BulkRunResultOut(BaseModel):
    id: int
    run_id: int
    fleet_member_id: int
    wave_index: int
    fault_domain: str | None
    status: str
    detail: str | None
    started_at: str | None
    completed_at: str | None

    class Config:
        from_attributes = True


class BulkRunOut(BaseModel):
    id: int
    decision_id: int
    project_id: int | None
    action: str
    action_params: dict | None
    wave_by: str
    concurrency: int
    gate_mode: str
    status: str
    current_wave_index: int
    total_waves: int | None
    celery_task_id: str | None
    error_message: str | None
    # D-022 P6 Slice D
    strategy_id: int | None = None
    gate_resumes_at: str | None = None
    created_by: str | None
    created_at: str | None
    started_at: str | None
    completed_at: str | None
    results: list[BulkRunResultOut] | None = None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Strategy schemas — D-022 P6 Slice D
# ---------------------------------------------------------------------------


class StrategyOut(BaseModel):
    id: int
    project_id: int | None
    name: str
    description: str | None = None
    target_id: int | None = None
    wave_by: str
    max_concurrency_pct: int
    gate_kind: str
    gate_wait_seconds: int | None = None
    created_by: str | None
    created_at: str | None
    updated_at: str | None

    class Config:
        from_attributes = True


class CreateStrategyRequest(BaseModel):
    name: str
    description: str | None = None
    target_id: int | None = None
    project_id: int | None = None
    wave_by: str = "fault_domain"
    max_concurrency_pct: int = 20
    gate_kind: str = "approval"
    gate_wait_seconds: int | None = None


class UpdateStrategyRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    target_id: int | None = None
    wave_by: str | None = None
    max_concurrency_pct: int | None = None
    gate_kind: str | None = None
    gate_wait_seconds: int | None = None


class CreateTargetRequest(BaseModel):
    name: str
    selector: dict
    description: str | None = None
    pinned_member_ids: list[int] | None = None
    project_id: int | None = None


class UpdateTargetRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    selector: dict | None = None
    pinned_member_ids: list[int] | None = None


class CreateBulkRunRequest(BaseModel):
    decision_id: int
    action: str
    action_params: dict | None = None
    # concurrency / gate_mode are Optional — None means "inherit from Project defaults
    # (or system literal if no Project default or conflicting defaults).
    concurrency: int | None = None
    gate_mode: str | None = None
    # D-022 P6 Slice D: optional strategy — snapshotted onto the run at start.
    strategy_id: int | None = None


class RunSelectionRequest(BaseModel):
    """Body for POST /targets/{id}/run-selection (Slice C — ad-hoc member selection)."""

    member_ids: list[int]
    action: str
    action_params: dict | None = None
    concurrency: int | None = None
    gate_mode: str | None = None
    # D-022 P6 Slice D: optional strategy.
    strategy_id: int | None = None


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _target_to_out(t: FleetTarget) -> TargetOut:
    return TargetOut(
        id=t.id,
        project_id=t.project_id,
        name=t.name,
        description=t.description,
        selector=t.selector or {},
        pinned_member_ids=t.pinned_member_ids,
        created_by=t.created_by,
        created_at=t.created_at.isoformat() if t.created_at else None,
    )


def _decision_to_out(d: FleetDecision, db: Session | None = None) -> DecisionOut:
    members_out: list[FleetMemberOut] | None = None
    if db is not None:
        member_ids: list[int] = d.resolved_member_ids or []
        if member_ids:
            members = db.query(FleetMember).filter(FleetMember.id.in_(member_ids)).all()
            by_id = {m.id: m for m in members}
            members_out = [_member_to_out(by_id[mid]) for mid in member_ids if mid in by_id]

    return DecisionOut(
        id=d.id,
        target_id=d.target_id,
        project_id=d.project_id,
        selector_snapshot=d.selector_snapshot or {},
        resolved_member_ids=d.resolved_member_ids or [],
        member_count=d.member_count,
        resolved_at=d.resolved_at.isoformat(),
        resolved_by=d.resolved_by,
        status=d.status,
        members=members_out,
    )


def _result_to_out(r: FleetBulkRunResult) -> BulkRunResultOut:
    return BulkRunResultOut(
        id=r.id,
        run_id=r.run_id,
        fleet_member_id=r.fleet_member_id,
        wave_index=r.wave_index,
        fault_domain=r.fault_domain,
        status=r.status,
        detail=r.detail,
        started_at=r.started_at.isoformat() if r.started_at else None,
        completed_at=r.completed_at.isoformat() if r.completed_at else None,
    )


def _run_to_out(run: FleetBulkRun, db: Session | None = None) -> BulkRunOut:
    results_out: list[BulkRunResultOut] | None = None
    if db is not None:
        results = (
            db.query(FleetBulkRunResult)
            .filter(FleetBulkRunResult.run_id == run.id)
            .order_by(FleetBulkRunResult.wave_index, FleetBulkRunResult.id)
            .all()
        )
        results_out = [_result_to_out(r) for r in results]

    return BulkRunOut(
        id=run.id,
        decision_id=run.decision_id,
        project_id=run.project_id,
        action=run.action,
        action_params=run.action_params,
        wave_by=run.wave_by,
        concurrency=run.concurrency,
        gate_mode=run.gate_mode,
        status=run.status,
        current_wave_index=run.current_wave_index,
        total_waves=run.total_waves,
        celery_task_id=run.celery_task_id,
        error_message=run.error_message,
        strategy_id=run.strategy_id,
        gate_resumes_at=run.gate_resumes_at.isoformat() if run.gate_resumes_at else None,
        created_by=run.created_by,
        created_at=run.created_at.isoformat() if run.created_at else None,
        started_at=run.started_at.isoformat() if run.started_at else None,
        completed_at=run.completed_at.isoformat() if run.completed_at else None,
        results=results_out,
    )


# ---------------------------------------------------------------------------
# Strategy serialization helper
# ---------------------------------------------------------------------------


def _strategy_to_out(s: FleetOperationStrategy) -> StrategyOut:
    return StrategyOut(
        id=s.id,
        project_id=s.project_id,
        name=s.name,
        description=s.description,
        target_id=s.target_id,
        wave_by=s.wave_by,
        max_concurrency_pct=s.max_concurrency_pct,
        gate_kind=s.gate_kind,
        gate_wait_seconds=s.gate_wait_seconds,
        created_by=s.created_by,
        created_at=s.created_at.isoformat() if s.created_at else None,
        updated_at=s.updated_at.isoformat() if s.updated_at else None,
    )


def _validate_strategy_fields(
    wave_by: str,
    max_concurrency_pct: int,
    gate_kind: str,
    gate_wait_seconds: int | None,
) -> None:
    """Validate strategy field values — raises BadRequestError on violation."""
    # wave_by: "fault_domain" or "label:<non-empty-key>"
    if wave_by != "fault_domain":
        if not wave_by.startswith("label:") or len(wave_by) <= len("label:"):
            raise BadRequestError(
                "wave_by must be 'fault_domain' or 'label:<key>' (e.g. 'label:env')."
            )
    # max_concurrency_pct 1-100
    if not (1 <= max_concurrency_pct <= 100):
        raise BadRequestError("max_concurrency_pct must be between 1 and 100.")
    # gate_kind
    if gate_kind not in STRATEGY_GATE_KINDS:
        raise BadRequestError(
            f"gate_kind '{gate_kind}' is not valid. Allowed: {sorted(STRATEGY_GATE_KINDS)}"
        )
    # gate_wait_seconds required for timed_wait
    if gate_kind == GATE_KIND_TIMED_WAIT and (gate_wait_seconds is None or gate_wait_seconds < 1):
        raise BadRequestError(
            "gate_wait_seconds is required and must be >= 1 when gate_kind='timed_wait'."
        )


def _apply_strategy_snapshot(run: FleetBulkRun, strategy: FleetOperationStrategy) -> None:
    """Snapshot strategy parameters onto a run (immutable after this point).

    Copies wave_by, gate_mode (mapped from gate_kind), and strategy_id.
    concurrency is NOT overridden here — the executor resolves it per-wave
    from max_concurrency_pct at execution time (keeps run.concurrency as a fallback).
    """
    run.strategy_id = strategy.id
    run.wave_by = strategy.wave_by
    # Map gate_kind → gate_mode (legacy field kept for executor compatibility)
    # approval → manual (both block until explicit resume)
    # timed_wait / health_stable → literal string (handled by extended _wave_gate_passes)
    if strategy.gate_kind == "approval":
        run.gate_mode = "manual"
    else:
        run.gate_mode = strategy.gate_kind


# ===========================================================================
# Phase 6 Slice B: Per-fleet conformance rollup — D-022 P6
# (schemas + helper placed here so the batch route can reference RollupOut
#  and _compute_fleet_rollup before @router.get("/targets/{target_id}"))
# ===========================================================================

# ---------------------------------------------------------------------------
# Rollup response schema
# ---------------------------------------------------------------------------

WORST_STATE_RANK: dict[str, int] = {
    "drifted": 0,
    "provisioning": 1,
    "unreachable": 2,
    "ready": 3,
    "unknown": 4,
}

# Lifecycle states that indicate a member is definitively gone / unreachable.
# active = healthy and reachable; provisioning/draining = transitional (not unreachable).
_UNREACHABLE_LIFECYCLE_STATES: frozenset[str] = frozenset({"unknown", "decommissioned"})


class LifecycleBreakdownOut(BaseModel):
    active: int = 0
    provisioning: int = 0
    draining: int = 0
    decommissioned: int = 0
    unknown: int = 0


class RollupOut(BaseModel):
    fleet_id: int
    member_count: int
    lifecycle: LifecycleBreakdownOut
    unreachable_count: int
    compliant_count: int
    total_evaluated: int
    drift_count: int
    policy_count: int
    worst_state: str  # drifted | provisioning | unreachable | ready | unknown
    last_evaluated_at: str | None
    # Operations status — D-022 P6 traffic-lights
    active_run_count: int = 0
    failed_run_count: int = 0
    last_run_status: str | None = None
    ops_state: str = "grey"  # green | amber | red | grey


def _compute_fleet_rollup(
    fleet_id: int,
    current_user: "User",
    db: "Session",
) -> RollupOut:
    """Shared computation for single + batch rollup endpoints."""
    from services.fleet_selector import matches_selector

    target = (
        db.query(FleetTarget)
        .filter(FleetTarget.id == fleet_id, FleetTarget.deleted_at.is_(None))
        .first()
    )
    if not target:
        raise NotFoundError("FleetTarget", str(fleet_id))

    # --- 1. Resolve members (mirror get_fleet_members_by_fleet logic) ---
    label_filters: list[str] = (target.selector or {}).get("labels") or []
    pinned: list[int] = target.pinned_member_ids or []

    # Empty selector + no pinned members → zero members.
    # A fleet must explicitly target something (D-022 P6 fix: empty≠all).
    if not label_filters and not pinned:
        members = []
    else:
        member_query = db.query(FleetMember)
        if effective_role(current_user) != "admin":
            from models.project import Project as _Project
            owned_ids = [
                pid for (pid,) in db.query(_Project.id).filter(_Project.user_id == current_user.id).all()
            ]
            member_query = member_query.filter(FleetMember.project_id.in_(owned_ids))

        if label_filters:
            members = [m for m in member_query.order_by(FleetMember.id).all()
                       if matches_selector(m, label_filters)]
        else:
            members = []

        if pinned:
            existing_ids = {m.id for m in members}
            extra_ids = [pid for pid in pinned if pid not in existing_ids]
            if extra_ids:
                extra = db.query(FleetMember).filter(FleetMember.id.in_(extra_ids)).all()
                members = members + extra

    member_count = len(members)

    # --- 2. Lifecycle breakdown ---
    lifecycle = LifecycleBreakdownOut()
    unreachable_count = 0
    has_provisioning = False

    for m in members:
        lc = m.lifecycle_state or "unknown"
        if lc == "active":
            lifecycle.active += 1
        elif lc == "provisioning":
            lifecycle.provisioning += 1
            has_provisioning = True
        elif lc == "draining":
            lifecycle.draining += 1
        elif lc == "decommissioned":
            lifecycle.decommissioned += 1
            unreachable_count += 1
        else:
            # unknown or any unrecognised state
            lifecycle.unknown += 1
            unreachable_count += 1

    # --- 3. Policy rollup — latest evaluation per policy for this fleet ---
    from models.fleet_policy import FleetPolicy, PolicyEvaluation

    policies = (
        db.query(FleetPolicy)
        .filter(FleetPolicy.target_id == fleet_id, FleetPolicy.deleted_at.is_(None))
        .all()
    )
    policy_count = len(policies)

    total_compliant = 0
    total_evaluated = 0
    total_drift = 0
    last_eval_dt = None
    has_drift = False

    for policy in policies:
        latest = (
            db.query(PolicyEvaluation)
            .filter(PolicyEvaluation.policy_id == policy.id)
            .order_by(PolicyEvaluation.evaluated_at.desc())
            .first()
        )
        if latest is None:
            continue
        total_compliant += latest.compliant_count
        total_evaluated += latest.total_count
        total_drift += latest.drifted_count
        if latest.drifted_count > 0:
            has_drift = True
        if last_eval_dt is None or latest.evaluated_at > last_eval_dt:
            last_eval_dt = latest.evaluated_at

    # --- 4. Worst-state ranking ---
    # drifted > provisioning > unreachable > ready > unknown
    # (unreachable now covers both unknown-lifecycle and decommissioned members —
    #  the old "error" bucket was health_status-based which was wrong vocab;
    #  lifecycle unknown/decommissioned → unreachable is the correct signal)
    if member_count == 0:
        worst_state = "unknown"
    elif has_drift:
        worst_state = "drifted"
    elif has_provisioning:
        worst_state = "provisioning"
    elif unreachable_count > 0:
        worst_state = "unreachable"
    elif lifecycle.active == member_count and total_drift == 0:
        worst_state = "ready"
    else:
        worst_state = "unknown"

    # --- 5. Operations status — derived from FleetBulkRun rows for this fleet ---
    # 2-hop join: FleetBulkRun.decision_id → FleetDecision.target_id == fleet_id
    # Non-terminal states considered active: pending | running | paused_gate
    _ACTIVE_STATUSES = frozenset({
        BULK_RUN_STATUS_PENDING,
        BULK_RUN_STATUS_RUNNING,
        BULK_RUN_STATUS_PAUSED_GATE,
    })
    _FAILED_STATUS = "failed"

    all_runs_for_fleet = (
        db.query(FleetBulkRun)
        .join(FleetDecision, FleetBulkRun.decision_id == FleetDecision.id)
        .filter(FleetDecision.target_id == fleet_id)
        .order_by(FleetBulkRun.id.desc())
        .all()
    )

    active_run_count = sum(1 for r in all_runs_for_fleet if r.status in _ACTIVE_STATUSES)
    failed_run_count = sum(1 for r in all_runs_for_fleet if r.status == _FAILED_STATUS)
    last_run_status: str | None = all_runs_for_fleet[0].status if all_runs_for_fleet else None

    # ops_state traffic-light derivation:
    #   grey  → no runs ever
    #   red   → most recent run failed (last_run_status == failed)
    #   amber → any active runs (pending/running/paused)
    #   green → runs exist but all terminal and latest is not failed
    if not all_runs_for_fleet:
        ops_state = "grey"
    elif last_run_status == _FAILED_STATUS:
        ops_state = "red"
    elif active_run_count > 0:
        ops_state = "amber"
    else:
        ops_state = "green"

    return RollupOut(
        fleet_id=fleet_id,
        member_count=member_count,
        lifecycle=lifecycle,
        unreachable_count=unreachable_count,
        compliant_count=total_compliant,
        total_evaluated=total_evaluated,
        drift_count=total_drift,
        policy_count=policy_count,
        worst_state=worst_state,
        last_evaluated_at=last_eval_dt.isoformat() if last_eval_dt else None,
        active_run_count=active_run_count,
        failed_run_count=failed_run_count,
        last_run_status=last_run_status,
        ops_state=ops_state,
    )


# ---------------------------------------------------------------------------
# Target endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/targets/rollup",
    response_model=list[RollupOut],
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get fleet targets batch rollup")
def get_fleet_targets_batch_rollup(
    ids: str = Query(..., description="Comma-separated FleetTarget IDs"),
    current_user: User = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> list[RollupOut]:
    """Batch per-fleet rollup — avoids N round-trips from the Fleets list.

    Pass ?ids=1,2,3.  Returns one RollupOut per ID in the same order.
    IDs that 404 (not found or soft-deleted) are silently skipped so the list
    page doesn't blow up on stale data.

    NOTE: this route MUST remain declared before @router.get("/targets/{target_id}")
    so FastAPI matches the static path "/targets/rollup" first and does not attempt
    to coerce "rollup" into an integer target_id (which would return 422).
    """
    try:
        id_list = [int(x.strip()) for x in ids.split(",") if x.strip()]
    except ValueError:
        raise BadRequestError("ids must be a comma-separated list of integers")

    results: list[RollupOut] = []
    for fid in id_list:
        try:
            results.append(_compute_fleet_rollup(fid, current_user, db))
        except NotFoundError:
            continue  # skip unknown / deleted

    return results


@router.post(
    "/targets",
    response_model=TargetOut,
    dependencies=[Depends(require_operator)],
    status_code=201,
)
@handle_route_errors("create fleet target")
def create_fleet_target(
    body: CreateTargetRequest,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> TargetOut:
    """Create a named label-selector target."""
    from services.fleet_selector import validate_selector
    validate_selector(body.selector)

    target = FleetTarget(
        project_id=body.project_id,
        name=body.name,
        selector=body.selector,
        created_by=current_user.username,
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    return _target_to_out(target)


@router.get(
    "/targets",
    response_model=list[TargetOut],
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("list fleet targets")
def list_fleet_targets(
    current_user: User = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> list[TargetOut]:
    """List fleet targets visible to the caller. Soft-deleted targets are excluded."""
    query = db.query(FleetTarget).filter(FleetTarget.deleted_at.is_(None))
    if effective_role(current_user) != "admin":
        from models.project import Project
        owned_ids = [
            pid for (pid,) in db.query(Project.id).filter(Project.user_id == current_user.id).all()
        ]
        query = query.filter(
            (FleetTarget.project_id.is_(None)) | (FleetTarget.project_id.in_(owned_ids))
        )
    targets = query.order_by(FleetTarget.id).all()
    return [_target_to_out(t) for t in targets]


@router.get(
    "/targets/{target_id}",
    response_model=TargetOut,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get fleet target")
def get_fleet_target(
    target_id: int,
    db: Session = Depends(get_db),
) -> TargetOut:
    """Get a fleet target by ID. Returns 404 if soft-deleted."""
    target = (
        db.query(FleetTarget)
        .filter(FleetTarget.id == target_id, FleetTarget.deleted_at.is_(None))
        .first()
    )
    if not target:
        raise NotFoundError("FleetTarget", str(target_id))
    return _target_to_out(target)


@router.put(
    "/targets/{target_id}",
    response_model=TargetOut,
)
@handle_route_errors("update fleet target")
def update_fleet_target(
    target_id: int,
    body: UpdateTargetRequest,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> TargetOut:
    """Update a fleet target's name/description/selector/pinned_member_ids.

    Only the row is mutated — historical FleetDecisions are immutable snapshots
    and are NEVER touched. Re-validates the selector via validate_selector.
    Requires fleet-mutation permission for the target's project.
    """
    from services.fleet_selector import validate_selector

    target = (
        db.query(FleetTarget)
        .filter(FleetTarget.id == target_id, FleetTarget.deleted_at.is_(None))
        .first()
    )
    if not target:
        raise NotFoundError("FleetTarget", str(target_id))

    require_fleet_mutation([target.project_id], current_user, db)

    if body.name is not None:
        target.name = body.name
    if body.description is not None:
        target.description = body.description
    if body.selector is not None:
        validate_selector(body.selector)
        target.selector = body.selector
    if body.pinned_member_ids is not None:
        target.pinned_member_ids = body.pinned_member_ids

    db.commit()
    db.refresh(target)
    return _target_to_out(target)


@router.delete(
    "/targets/{target_id}",
    status_code=204,
)
@handle_route_errors("delete fleet target")
def delete_fleet_target(
    target_id: int,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> None:
    """Soft-delete a fleet target (sets deleted_at).

    Hard-delete is intentionally NOT performed — it would cascade-destroy the
    FleetDecision audit trail or hit the RESTRICT FK on FleetBulkRun.decision_id.
    Soft-deleted targets are excluded from list/get endpoints and will surface
    as '(deleted fleet)' in policy references rather than causing 500s.
    """
    from datetime import UTC, datetime

    target = (
        db.query(FleetTarget)
        .filter(FleetTarget.id == target_id, FleetTarget.deleted_at.is_(None))
        .first()
    )
    if not target:
        raise NotFoundError("FleetTarget", str(target_id))

    require_fleet_mutation([target.project_id], current_user, db)

    target.deleted_at = datetime.now(UTC)
    db.commit()


@router.post(
    "/targets/{target_id}/resolve",
    response_model=DecisionOut,
    dependencies=[Depends(require_operator)],
    status_code=201,
)
@handle_route_errors("resolve fleet target")
def resolve_fleet_target(
    target_id: int,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> DecisionOut:
    """Resolve a target to a Decision — returns the blast-radius preview.

    Creates an immutable snapshot of the member-id set at this point in time.
    The decision is returned with the enriched member list for preview.
    No bulk run is started; this is the show-before-act step.
    """
    from services.fleet_selector import resolve_target

    target = (
        db.query(FleetTarget)
        .filter(FleetTarget.id == target_id, FleetTarget.deleted_at.is_(None))
        .first()
    )
    if not target:
        raise NotFoundError("FleetTarget", str(target_id))

    decision = resolve_target(
        db,
        target,
        resolved_by=current_user.username,
        current_user=current_user,
    )
    db.commit()
    db.refresh(decision)
    # Return with enriched member list (blast-radius preview).
    return _decision_to_out(decision, db=db)


# ---------------------------------------------------------------------------
# Decision endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/decisions/{decision_id}",
    response_model=DecisionOut,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get fleet decision")
def get_fleet_decision(
    decision_id: int,
    db: Session = Depends(get_db),
) -> DecisionOut:
    """Get a decision with its blast-radius member list."""
    decision = db.query(FleetDecision).filter(FleetDecision.id == decision_id).first()
    if not decision:
        raise NotFoundError("FleetDecision", str(decision_id))
    return _decision_to_out(decision, db=db)


# ---------------------------------------------------------------------------
# Bulk-run endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/bulk-ops",
    response_model=BulkRunOut,
    dependencies=[Depends(require_operator)],
    status_code=201,
)
@handle_route_errors("start fleet bulk op")
def start_fleet_bulk_op(
    body: CreateBulkRunRequest,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> BulkRunOut:
    """Start a bulk operation against a Decision.

    Validates:
    - Decision exists and has status='resolved'.
    - Action is in the safe-actions allowlist.
    - Gate mode is valid.
    - Caller is permitted to mutate all projects in the blast radius (RBAC gate).

    concurrency / gate_mode are Optional: when None, inherit from Project defaults
    (or system literals concurrency=5 / gate_mode=manual when not set or conflicting).

    Creates a FleetBulkRun and dispatches the Celery task.
    """
    # Validate action allowlist upfront (before DB write).
    if body.action not in SAFE_ACTIONS:
        raise BadRequestError(
            f"Action '{body.action}' is not allowed. "
            f"Allowed actions: {sorted(SAFE_ACTIONS)}"
        )

    decision = db.query(FleetDecision).filter(FleetDecision.id == body.decision_id).first()
    if not decision:
        raise NotFoundError("FleetDecision", str(body.decision_id))

    if decision.status != DECISION_STATUS_RESOLVED:
        raise BadRequestError(
            f"Decision {body.decision_id} has status '{decision.status}' — "
            "only 'resolved' decisions can be used to start a bulk run. Re-resolve the target."
        )

    # Collect all distinct project_ids in the blast radius (members may span projects).
    member_ids: list[int] = decision.resolved_member_ids or []
    member_project_ids: list[int | None] = [decision.project_id]
    if member_ids:
        rows = (
            db.query(FleetMember.project_id)
            .filter(FleetMember.id.in_(member_ids))
            .distinct()
            .all()
        )
        member_project_ids.extend(pid for (pid,) in rows)

    # RBAC gate — fail-closed; refuse before any run row is written or Celery dispatched.
    require_fleet_mutation(member_project_ids, current_user, db)

    # Resolve concurrency / gate_mode: explicit override wins, else Project defaults.
    if body.concurrency is None or body.gate_mode is None:
        default_concurrency, default_gate_mode = resolve_rollout_defaults(
            db, member_project_ids
        )
        concurrency = body.concurrency if body.concurrency is not None else default_concurrency
        gate_mode = body.gate_mode if body.gate_mode is not None else default_gate_mode
    else:
        concurrency = body.concurrency
        gate_mode = body.gate_mode

    # Validate resolved values.
    if gate_mode not in GATE_MODES:
        raise BadRequestError(
            f"gate_mode '{gate_mode}' is not valid. "
            f"Allowed: {sorted(GATE_MODES)}"
        )
    if concurrency < 1 or concurrency > 50:
        raise BadRequestError("concurrency must be between 1 and 50.")

    run = FleetBulkRun(
        decision_id=decision.id,
        project_id=decision.project_id,
        action=body.action,
        action_params=body.action_params,
        wave_by="fault_domain",
        concurrency=concurrency,
        gate_mode=gate_mode,
        status=BULK_RUN_STATUS_PENDING,
        current_wave_index=0,
        created_by=current_user.username,
    )
    db.add(run)
    db.flush()  # get run.id before strategy snapshot

    # D-022 P6 Slice D: snapshot strategy onto run if provided.
    if body.strategy_id is not None:
        strategy = (
            db.query(FleetOperationStrategy)
            .filter(
                FleetOperationStrategy.id == body.strategy_id,
                FleetOperationStrategy.deleted_at.is_(None),
            )
            .first()
        )
        if not strategy:
            raise NotFoundError("FleetOperationStrategy", str(body.strategy_id))
        _apply_strategy_snapshot(run, strategy)

    db.commit()
    db.refresh(run)

    # Dispatch to Celery.
    from tasks.fleet_tasks import run_fleet_bulk_op

    task = run_fleet_bulk_op.delay(run.id)
    run.celery_task_id = task.id
    db.commit()

    return _run_to_out(run)


@router.post(
    "/targets/{target_id}/run-selection",
    response_model=BulkRunOut,
    dependencies=[Depends(require_operator)],
    status_code=201,
)
@handle_route_errors("run fleet member selection")
def run_fleet_selection(
    target_id: int,
    body: RunSelectionRequest,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> BulkRunOut:
    """Start a bulk operation over a caller-specified subset of a fleet's members.

    Safety contract (fail-closed at every step):
    1. Fleet (FleetTarget) must exist and not be soft-deleted.
    2. `member_ids` is INTERSECTED with the fleet's resolved member set — any id
       not actually in the fleet is silently dropped (prevents out-of-fleet
       smuggling). Empty intersection → 400.
    3. `action` must be in SAFE_ACTIONS.
    4. RBAC gate — reuses require_fleet_mutation over all project_ids in the
       intersected blast radius (same gate as start_fleet_bulk_op).
    5. concurrency / gate_mode: explicit body value wins, else Project defaults.
    6. Creates a FleetDecision via decision_from_member_ids (same helper as
       enforce_policy), then a FleetBulkRun + Celery dispatch.

    NEVER exposes decision_from_member_ids raw — this route is the sole safe
    entry point because it re-validates RBAC before calling the helper.
    """

    from services.fleet_selector import decision_from_member_ids, matches_selector

    # 1. Load fleet.
    target = (
        db.query(FleetTarget)
        .filter(FleetTarget.id == target_id, FleetTarget.deleted_at.is_(None))
        .first()
    )
    if not target:
        raise NotFoundError("FleetTarget", str(target_id))

    # 2. Resolve the full fleet member set (same logic as get_fleet_members_by_fleet).
    label_filters: list[str] = (target.selector or {}).get("labels") or []
    pinned: list[int] = target.pinned_member_ids or []

    # Empty selector + no pins → no members (empty ≠ all).
    if not label_filters and not pinned:
        fleet_members = []
    else:
        fleet_query = db.query(FleetMember)
        if label_filters:
            fleet_members = [m for m in fleet_query.all() if matches_selector(m, label_filters)]
        else:
            fleet_members = []
        if pinned:
            existing_ids = {m.id for m in fleet_members}
            extra_ids = [pid for pid in pinned if pid not in existing_ids]
            if extra_ids:
                extra = db.query(FleetMember).filter(FleetMember.id.in_(extra_ids)).all()
                fleet_members = fleet_members + extra

    fleet_member_ids = {m.id for m in fleet_members}

    # Intersect requested ids with the fleet's actual member set.
    intersected_ids = [mid for mid in body.member_ids if mid in fleet_member_ids]
    if not intersected_ids:
        raise BadRequestError(
            "None of the requested member_ids belong to this fleet, or the fleet is empty."
        )

    # 3. Validate action allowlist.
    if body.action not in SAFE_ACTIONS:
        raise BadRequestError(
            f"Action '{body.action}' is not allowed. "
            f"Allowed actions: {sorted(SAFE_ACTIONS)}"
        )

    # 4. RBAC gate over all project_ids in the intersected blast radius.
    rows = (
        db.query(FleetMember.project_id)
        .filter(FleetMember.id.in_(intersected_ids))
        .distinct()
        .all()
    )
    member_project_ids: list[int | None] = [target.project_id]
    member_project_ids.extend(pid for (pid,) in rows)
    require_fleet_mutation(member_project_ids, current_user, db)

    # 5. Resolve concurrency / gate_mode.
    if body.concurrency is None or body.gate_mode is None:
        default_concurrency, default_gate_mode = resolve_rollout_defaults(
            db, member_project_ids
        )
        concurrency = body.concurrency if body.concurrency is not None else default_concurrency
        gate_mode = body.gate_mode if body.gate_mode is not None else default_gate_mode
    else:
        concurrency = body.concurrency
        gate_mode = body.gate_mode

    if gate_mode not in GATE_MODES:
        raise BadRequestError(
            f"gate_mode '{gate_mode}' is not valid. "
            f"Allowed: {sorted(GATE_MODES)}"
        )
    if concurrency < 1 or concurrency > 50:
        raise BadRequestError("concurrency must be between 1 and 50.")

    # 6. Build Decision + BulkRun + dispatch.
    decision = decision_from_member_ids(
        db,
        target,
        intersected_ids,
        resolved_by=current_user.username,
    )
    db.commit()
    db.refresh(decision)

    run = FleetBulkRun(
        decision_id=decision.id,
        project_id=target.project_id,
        action=body.action,
        action_params=body.action_params,
        wave_by="fault_domain",
        concurrency=concurrency,
        gate_mode=gate_mode,
        status=BULK_RUN_STATUS_PENDING,
        current_wave_index=0,
        created_by=current_user.username,
    )
    db.add(run)
    db.flush()  # get run.id before strategy snapshot

    # D-022 P6 Slice D: snapshot strategy onto run if provided.
    if body.strategy_id is not None:
        strategy = (
            db.query(FleetOperationStrategy)
            .filter(
                FleetOperationStrategy.id == body.strategy_id,
                FleetOperationStrategy.deleted_at.is_(None),
            )
            .first()
        )
        if not strategy:
            raise NotFoundError("FleetOperationStrategy", str(body.strategy_id))
        _apply_strategy_snapshot(run, strategy)

    db.commit()
    db.refresh(run)

    from tasks.fleet_tasks import run_fleet_bulk_op

    task = run_fleet_bulk_op.delay(run.id)
    run.celery_task_id = task.id
    db.commit()

    return _run_to_out(run)


@router.get(
    "/bulk-ops/{run_id}",
    response_model=BulkRunOut,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get fleet bulk op")
def get_fleet_bulk_op(
    run_id: int,
    db: Session = Depends(get_db),
) -> BulkRunOut:
    """Get a bulk run with its per-member results."""
    run = db.query(FleetBulkRun).filter(FleetBulkRun.id == run_id).first()
    if not run:
        raise NotFoundError("FleetBulkRun", str(run_id))
    return _run_to_out(run, db=db)


@router.get(
    "/bulk-ops",
    response_model=list[BulkRunOut],
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("list fleet bulk ops")
def list_fleet_bulk_ops(
    fleet_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    current_user: User = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> list[BulkRunOut]:
    """List fleet bulk runs, newest-first.

    Optional filters:
      - fleet_id: filter runs whose Decision's target_id matches this value
        (2-hop join FleetBulkRun.decision_id → FleetDecision.target_id).
      - status: filter by run status string.

    RBAC: non-admin callers see only runs whose project_id is owned by them.
    """
    query = db.query(FleetBulkRun)

    if effective_role(current_user) != "admin":
        from models.project import Project
        owned_ids = [
            pid for (pid,) in db.query(Project.id).filter(Project.user_id == current_user.id).all()
        ]
        query = query.filter(
            (FleetBulkRun.project_id.is_(None)) | (FleetBulkRun.project_id.in_(owned_ids))
        )

    if fleet_id is not None:
        # 2-hop join: run → decision → target (target_id == fleet_id)
        query = (
            query.join(FleetDecision, FleetBulkRun.decision_id == FleetDecision.id)
            .filter(FleetDecision.target_id == fleet_id)
        )

    if status is not None:
        query = query.filter(FleetBulkRun.status == status)

    runs = query.order_by(FleetBulkRun.id.desc()).all()
    return [_run_to_out(run) for run in runs]


@router.post(
    "/bulk-ops/{run_id}/resume",
    response_model=BulkRunOut,
)
@handle_route_errors("resume fleet bulk op")
def resume_fleet_bulk_op_route(
    run_id: int,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> BulkRunOut:
    """Resume a paused bulk run (must be in 'paused_gate' status).

    RBAC: caller must own all projects in the run's blast radius — the same
    fail-closed ownership gate applied by the start / enforce routes.
    """
    run = db.query(FleetBulkRun).filter(FleetBulkRun.id == run_id).first()
    if not run:
        raise NotFoundError("FleetBulkRun", str(run_id))

    if run.status != BULK_RUN_STATUS_PAUSED_GATE:
        raise BadRequestError(
            f"FleetBulkRun {run_id} cannot be resumed — status is '{run.status}' "
            "(must be 'paused_gate')."
        )

    # RBAC gate — mirror of start_fleet_bulk_op.
    # Resolve the blast-radius project IDs from the decision snapshot so that a
    # non-owning operator cannot resume another project's paused run.
    decision = db.query(FleetDecision).filter(FleetDecision.id == run.decision_id).first()
    member_project_ids: list[int | None] = [run.project_id]
    if decision is not None:
        member_ids: list[int] = decision.resolved_member_ids or []
        if member_ids:
            rows = (
                db.query(FleetMember.project_id)
                .filter(FleetMember.id.in_(member_ids))
                .distinct()
                .all()
            )
            member_project_ids.extend(pid for (pid,) in rows)
    require_fleet_mutation(member_project_ids, current_user, db)

    # Commit RUNNING status BEFORE dispatching the Celery task so that any
    # concurrent resume request sees the updated status and is rejected by the
    # paused_gate guard above — closing the double-submit race window.
    run.status = BULK_RUN_STATUS_RUNNING
    db.commit()

    from tasks.fleet_tasks import resume_fleet_bulk_op

    task = resume_fleet_bulk_op.delay(run.id)
    run.celery_task_id = task.id
    db.commit()

    return _run_to_out(run)


@router.post(
    "/bulk-ops/{run_id}/cancel",
    response_model=BulkRunOut,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("cancel fleet bulk op")
def cancel_fleet_bulk_op(
    run_id: int,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> BulkRunOut:
    """Cancel a non-terminal bulk run.

    Guard: run.status must be one of {pending, running, paused_gate}.
    Returns 409 if the run is already terminal (completed/failed/cancelled).

    The executor will check the status at the top of each wave iteration and
    stop cleanly (cooperative cancel — never celery-revoke).
    """
    from datetime import UTC, datetime



    run = db.query(FleetBulkRun).filter(FleetBulkRun.id == run_id).first()
    if not run:
        raise NotFoundError("FleetBulkRun", str(run_id))

    # RBAC gate — caller must own the run's project.
    require_fleet_mutation([run.project_id], current_user, db)

    if run.status in BULK_RUN_TERMINAL_STATUSES:
        raise ConflictError(
            "FleetBulkRun",
            f"FleetBulkRun {run_id} cannot be cancelled — "
            f"it is already terminal (status='{run.status}').",
        )

    _cancellable = {BULK_RUN_STATUS_PENDING, BULK_RUN_STATUS_RUNNING, BULK_RUN_STATUS_PAUSED_GATE}
    if run.status not in _cancellable:
        raise BadRequestError(
            f"FleetBulkRun {run_id} cannot be cancelled — status is '{run.status}'. "
            f"Cancellable statuses: {sorted(_cancellable)}."
        )

    run.status = BULK_RUN_STATUS_CANCELLED
    run.completed_at = datetime.now(UTC)
    db.commit()

    return _run_to_out(run)


# ===========================================================================
# Phase 6 Slice D: Operation Strategy CRUD + Bulk-Run Rerun — D-022 P6
# ===========================================================================


@router.post(
    "/strategies",
    response_model=StrategyOut,
    dependencies=[Depends(require_operator)],
    status_code=201,
)
@handle_route_errors("create fleet operation strategy")
def create_fleet_strategy(
    body: CreateStrategyRequest,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> StrategyOut:
    """Create a reusable operation strategy (wave/concurrency/gate template)."""
    _validate_strategy_fields(
        body.wave_by, body.max_concurrency_pct, body.gate_kind, body.gate_wait_seconds
    )

    strategy = FleetOperationStrategy(
        project_id=body.project_id,
        name=body.name,
        description=body.description,
        target_id=body.target_id,
        wave_by=body.wave_by,
        max_concurrency_pct=body.max_concurrency_pct,
        gate_kind=body.gate_kind,
        gate_wait_seconds=body.gate_wait_seconds,
        created_by=current_user.username,
    )
    db.add(strategy)
    db.commit()
    db.refresh(strategy)
    return _strategy_to_out(strategy)


@router.get(
    "/strategies",
    response_model=list[StrategyOut],
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("list fleet operation strategies")
def list_fleet_strategies(
    fleet_id: int | None = Query(default=None, description="Filter by associated fleet target_id"),
    current_user: User = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> list[StrategyOut]:
    """List operation strategies (excludes soft-deleted). Optional ?fleet_id= filter."""
    query = db.query(FleetOperationStrategy).filter(FleetOperationStrategy.deleted_at.is_(None))

    if fleet_id is not None:
        query = query.filter(FleetOperationStrategy.target_id == fleet_id)

    if effective_role(current_user) != "admin":
        owned_ids = [
            pid for (pid,) in db.query(Project.id).filter(Project.user_id == current_user.id).all()
        ]
        query = query.filter(
            (FleetOperationStrategy.project_id.is_(None))
            | (FleetOperationStrategy.project_id.in_(owned_ids))
        )

    strategies = query.order_by(FleetOperationStrategy.id).all()
    return [_strategy_to_out(s) for s in strategies]


@router.get(
    "/strategies/{strategy_id}",
    response_model=StrategyOut,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get fleet operation strategy")
def get_fleet_strategy(
    strategy_id: int,
    db: Session = Depends(get_db),
) -> StrategyOut:
    """Get a single operation strategy by ID. Returns 404 if soft-deleted."""
    strategy = (
        db.query(FleetOperationStrategy)
        .filter(FleetOperationStrategy.id == strategy_id, FleetOperationStrategy.deleted_at.is_(None))
        .first()
    )
    if not strategy:
        raise NotFoundError("FleetOperationStrategy", str(strategy_id))
    return _strategy_to_out(strategy)


@router.put(
    "/strategies/{strategy_id}",
    response_model=StrategyOut,
)
@handle_route_errors("update fleet operation strategy")
def update_fleet_strategy(
    strategy_id: int,
    body: UpdateStrategyRequest,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> StrategyOut:
    """Update an operation strategy. Only supplied fields are mutated."""
    from datetime import UTC
    from datetime import datetime as _dt

    strategy = (
        db.query(FleetOperationStrategy)
        .filter(FleetOperationStrategy.id == strategy_id, FleetOperationStrategy.deleted_at.is_(None))
        .first()
    )
    if not strategy:
        raise NotFoundError("FleetOperationStrategy", str(strategy_id))

    require_fleet_mutation([strategy.project_id], current_user, db)

    if body.name is not None:
        strategy.name = body.name
    if body.description is not None:
        strategy.description = body.description
    if body.target_id is not None:
        strategy.target_id = body.target_id

    # Compute effective values for cross-field validation.
    eff_wave_by = body.wave_by if body.wave_by is not None else strategy.wave_by
    eff_pct = body.max_concurrency_pct if body.max_concurrency_pct is not None else strategy.max_concurrency_pct
    eff_gate_kind = body.gate_kind if body.gate_kind is not None else strategy.gate_kind
    eff_wait = body.gate_wait_seconds if body.gate_wait_seconds is not None else strategy.gate_wait_seconds

    _validate_strategy_fields(eff_wave_by, eff_pct, eff_gate_kind, eff_wait)

    if body.wave_by is not None:
        strategy.wave_by = body.wave_by
    if body.max_concurrency_pct is not None:
        strategy.max_concurrency_pct = body.max_concurrency_pct
    if body.gate_kind is not None:
        strategy.gate_kind = body.gate_kind
    if body.gate_wait_seconds is not None:
        strategy.gate_wait_seconds = body.gate_wait_seconds

    strategy.updated_at = _dt.now(UTC)
    db.commit()
    db.refresh(strategy)
    return _strategy_to_out(strategy)


@router.delete(
    "/strategies/{strategy_id}",
    status_code=204,
)
@handle_route_errors("delete fleet operation strategy")
def delete_fleet_strategy(
    strategy_id: int,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> None:
    """Soft-delete a fleet operation strategy."""
    from datetime import UTC
    from datetime import datetime as _dt

    strategy = (
        db.query(FleetOperationStrategy)
        .filter(FleetOperationStrategy.id == strategy_id, FleetOperationStrategy.deleted_at.is_(None))
        .first()
    )
    if not strategy:
        raise NotFoundError("FleetOperationStrategy", str(strategy_id))

    require_fleet_mutation([strategy.project_id], current_user, db)
    strategy.deleted_at = _dt.now(UTC)
    db.commit()


@router.post(
    "/bulk-ops/{run_id}/rerun",
    response_model=BulkRunOut,
    dependencies=[Depends(require_operator)],
    status_code=201,
)
@handle_route_errors("rerun fleet bulk op")
def rerun_fleet_bulk_op(
    run_id: int,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> BulkRunOut:
    """Re-run a terminal bulk-op: re-resolve the target into a FRESH Decision + new run.

    The old run's stale Decision is never reused (the stale-decision guard in the
    executor would refuse it).  Instead, this route:
      1. Loads the original run + its Decision's target.
      2. Calls resolve_target() to get a FRESH Decision.
      3. Clones action / action_params / strategy_id from the original run.
      4. Snaps the strategy (if any) onto the new run.
      5. Dispatches and returns the new BulkRunOut.

    Only terminal runs (completed / failed / cancelled) can be re-run.
    RBAC: same require_fleet_mutation gate as start_fleet_bulk_op.
    """
    original = db.query(FleetBulkRun).filter(FleetBulkRun.id == run_id).first()
    if not original:
        raise NotFoundError("FleetBulkRun", str(run_id))

    if original.status not in BULK_RUN_TERMINAL_STATUSES:
        raise BadRequestError(
            f"FleetBulkRun {run_id} cannot be re-run — status is '{original.status}'. "
            f"Only terminal runs ({sorted(BULK_RUN_TERMINAL_STATUSES)}) can be re-run."
        )

    # Load the original decision to find the target.
    orig_decision = db.query(FleetDecision).filter(FleetDecision.id == original.decision_id).first()
    if not orig_decision:
        raise NotFoundError("FleetDecision", str(original.decision_id))

    target = (
        db.query(FleetTarget)
        .filter(FleetTarget.id == orig_decision.target_id, FleetTarget.deleted_at.is_(None))
        .first()
    )
    if not target:
        raise NotFoundError("FleetTarget", str(orig_decision.target_id))

    # RBAC gate over the blast-radius projects.
    member_ids: list[int] = orig_decision.resolved_member_ids or []
    member_project_ids: list[int | None] = [original.project_id]
    if member_ids:
        rows = (
            db.query(FleetMember.project_id)
            .filter(FleetMember.id.in_(member_ids))
            .distinct()
            .all()
        )
        member_project_ids.extend(pid for (pid,) in rows)
    require_fleet_mutation(member_project_ids, current_user, db)

    # Re-resolve: get a FRESH Decision (old decision is stale).
    from services.fleet_selector import resolve_target
    new_decision = resolve_target(
        db,
        target,
        resolved_by=current_user.username,
        current_user=current_user,
    )
    db.commit()
    db.refresh(new_decision)

    # Clone run configuration from original.
    concurrency, gate_mode = resolve_rollout_defaults(db, member_project_ids)

    new_run = FleetBulkRun(
        decision_id=new_decision.id,
        project_id=original.project_id,
        action=original.action,
        action_params=original.action_params,
        wave_by=original.wave_by or "fault_domain",
        concurrency=original.concurrency or concurrency,
        gate_mode=original.gate_mode or gate_mode,
        status=BULK_RUN_STATUS_PENDING,
        current_wave_index=0,
        created_by=current_user.username,
    )
    db.add(new_run)
    db.flush()

    # Re-apply strategy snapshot if the original run had one.
    if original.strategy_id is not None:
        strategy = (
            db.query(FleetOperationStrategy)
            .filter(
                FleetOperationStrategy.id == original.strategy_id,
                FleetOperationStrategy.deleted_at.is_(None),
            )
            .first()
        )
        if strategy:
            _apply_strategy_snapshot(new_run, strategy)
        else:
            # Strategy was deleted — keep the run's inherited wave_by/gate_mode.
            new_run.strategy_id = original.strategy_id  # preserve FK for audit

    db.commit()
    db.refresh(new_run)

    from tasks.fleet_tasks import run_fleet_bulk_op
    task = run_fleet_bulk_op.delay(new_run.id)
    new_run.celery_task_id = task.id
    db.commit()

    return _run_to_out(new_run)


@router.get(
    "/targets/{target_id}/rollup",
    response_model=RollupOut,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get fleet target rollup")
def get_fleet_target_rollup(
    target_id: int,
    current_user: User = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> RollupOut:
    """Per-fleet conformance rollup — D-022 P6 Slice B.

    Computes on-demand: member count + lifecycle breakdown + unreachable count +
    compliant/total/drift from the latest PolicyEvaluation per policy + worst_state.

    worst_state ranking (worst wins):
      drifted      — any active policy's latest evaluation has drifted_count > 0
      provisioning — any member in provisioning state
      unreachable  — any member whose lifecycle_state is "unknown" or "decommissioned"
      ready        — all members active, zero drift
      unknown      — no members

    Read-only. No migration. RBAC mirrors get_fleet_members_by_fleet.
    """
    return _compute_fleet_rollup(target_id, current_user, db)


# ===========================================================================
# Phase 3: Fleet Policies + Compliance — D-022
# ===========================================================================

# ---------------------------------------------------------------------------
# Phase 3 response schemas
# ---------------------------------------------------------------------------


class PolicyOut(BaseModel):
    id: int
    project_id: int | None
    name: str
    target_id: int
    kind: str
    mode: str
    desired: dict
    created_by: str | None
    created_at: str | None

    class Config:
        from_attributes = True


class MemberComplianceResultOut(BaseModel):
    member_id: int
    status: str  # compliant | drifted | unknown
    detail: str | None = None


class PolicyEvaluationOut(BaseModel):
    id: int
    policy_id: int
    project_id: int | None
    evaluated_at: str
    evaluated_by: str | None
    selector_snapshot: dict | None
    desired_snapshot: dict
    compliant_count: int
    drifted_count: int
    unknown_count: int
    total_count: int
    member_results: list[MemberComplianceResultOut]
    enforced_run_id: int | None
    mode: str
    detail: str | None

    class Config:
        from_attributes = True


class PolicyComplianceRollupOut(BaseModel):
    """Compliance rollup for a single policy — GET {id}/compliance."""
    policy_id: int
    policy_name: str
    kind: str
    mode: str
    supports_enforce: bool
    compliant_count: int
    drifted_count: int
    unknown_count: int
    total_count: int
    drifted_members: list[MemberComplianceResultOut]
    last_evaluated_at: str | None
    last_evaluation_id: int | None


class CreatePolicyRequest(BaseModel):
    name: str
    target_id: int
    kind: str
    mode: str = "inform"
    desired: dict
    project_id: int | None = None


class UpdatePolicyRequest(BaseModel):
    """All fields optional — only supplied fields are mutated."""
    name: str | None = None
    kind: str | None = None
    mode: str | None = None
    desired: dict | None = None


# ---------------------------------------------------------------------------
# Serialisation helpers (Phase 3)
# ---------------------------------------------------------------------------


def _policy_to_out(p) -> PolicyOut:  # type: ignore[no-untyped-def]
    return PolicyOut(
        id=p.id,
        project_id=p.project_id,
        name=p.name,
        target_id=p.target_id,
        kind=p.kind,
        mode=p.mode,
        desired=p.desired or {},
        created_by=p.created_by,
        created_at=p.created_at.isoformat() if p.created_at else None,
    )


def _evaluation_to_out(e) -> PolicyEvaluationOut:  # type: ignore[no-untyped-def]
    member_results = [
        MemberComplianceResultOut(
            member_id=r["member_id"],
            status=r["status"],
            detail=r.get("detail"),
        )
        for r in (e.member_results or [])
    ]
    return PolicyEvaluationOut(
        id=e.id,
        policy_id=e.policy_id,
        project_id=e.project_id,
        evaluated_at=e.evaluated_at.isoformat(),
        evaluated_by=e.evaluated_by,
        selector_snapshot=e.selector_snapshot,
        desired_snapshot=e.desired_snapshot or {},
        compliant_count=e.compliant_count,
        drifted_count=e.drifted_count,
        unknown_count=e.unknown_count,
        total_count=e.total_count,
        member_results=member_results,
        enforced_run_id=e.enforced_run_id,
        mode=e.mode,
        detail=e.detail,
    )


# ---------------------------------------------------------------------------
# Policy endpoints — Phase 3
# ---------------------------------------------------------------------------


@router.post(
    "/policies",
    response_model=PolicyOut,
    dependencies=[Depends(require_operator)],
    status_code=201,
)
@handle_route_errors("create fleet policy")
def create_fleet_policy(
    body: CreatePolicyRequest,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> PolicyOut:
    """Create a fleet policy (label-compliance or os-compliance-seed)."""
    from core.errors import ValidationError as AppValidationError
    from models.fleet_policy import (
        POLICY_KINDS,
        POLICY_MODES,
        FleetPolicy,
    )

    if body.kind not in POLICY_KINDS:
        raise BadRequestError(
            f"Unknown policy kind '{body.kind}'. Allowed: {sorted(POLICY_KINDS)}"
        )
    if body.mode not in POLICY_MODES:
        raise BadRequestError(
            f"Unknown policy mode '{body.mode}'. Allowed: {sorted(POLICY_MODES)}"
        )

    target = db.query(FleetTarget).filter(FleetTarget.id == body.target_id).first()
    if not target:
        raise NotFoundError("FleetTarget", str(body.target_id))

    # Validate desired schema per kind.
    desired = body.desired or {}
    if body.kind == "label-compliance":
        rl = desired.get("required_labels")
        if not isinstance(rl, list) or not rl:
            raise AppValidationError(
                "desired.required_labels",
                "label-compliance policy requires a non-empty 'required_labels' list.",
            )
        # Validate required_labels keys against the assigned-label vocabulary so
        # a discovered-namespace key (e.g. os=ubuntu) is rejected at creation with
        # a clear 4xx, not silently at enforce time.
        from services.fleet_vocabulary import validate_assigned_labels
        rl_dict: dict[str, str] = {}
        for entry in rl:
            if "=" in entry:
                k, _, v = entry.partition("=")
                rl_dict[k] = v
        validate_assigned_labels(rl_dict)
    elif body.kind == "os-compliance-seed":
        if not desired.get("os"):
            raise AppValidationError(
                "desired.os",
                "os-compliance-seed policy requires 'os' in desired.",
            )

    policy = FleetPolicy(
        project_id=body.project_id,
        name=body.name,
        target_id=body.target_id,
        kind=body.kind,
        mode=body.mode,
        desired=desired,
        created_by=current_user.username,
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return _policy_to_out(policy)


@router.get(
    "/policies",
    response_model=list[PolicyOut],
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("list fleet policies")
def list_fleet_policies(
    current_user: User = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> list[PolicyOut]:
    """List fleet policies visible to the caller. Excludes soft-deleted policies."""
    from models.fleet_policy import FleetPolicy

    query = db.query(FleetPolicy).filter(FleetPolicy.deleted_at.is_(None))
    if effective_role(current_user) != "admin":
        from models.project import Project
        owned_ids = [
            pid for (pid,) in db.query(Project.id).filter(
                Project.user_id == current_user.id
            ).all()
        ]
        query = query.filter(
            (FleetPolicy.project_id.is_(None)) | (FleetPolicy.project_id.in_(owned_ids))
        )
    policies = query.order_by(FleetPolicy.id).all()
    return [_policy_to_out(p) for p in policies]


@router.get(
    "/policies/{policy_id}",
    response_model=PolicyOut,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get fleet policy")
def get_fleet_policy(
    policy_id: int,
    db: Session = Depends(get_db),
) -> PolicyOut:
    """Get a fleet policy by ID. Returns 404 if soft-deleted."""
    from models.fleet_policy import FleetPolicy

    policy = (
        db.query(FleetPolicy)
        .filter(FleetPolicy.id == policy_id, FleetPolicy.deleted_at.is_(None))
        .first()
    )
    if not policy:
        raise NotFoundError("FleetPolicy", str(policy_id))
    return _policy_to_out(policy)


@router.post(
    "/policies/{policy_id}/evaluate",
    response_model=PolicyEvaluationOut,
    dependencies=[Depends(require_operator)],
    status_code=201,
)
@handle_route_errors("evaluate fleet policy")
def evaluate_fleet_policy(
    policy_id: int,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> PolicyEvaluationOut:
    """Evaluate a policy (inform mode — reports drift, changes nothing).

    Resolves the policy's target member universe, evaluates compliance per
    member, and persists an immutable PolicyEvaluation snapshot.
    Zero FleetMember mutations.
    """
    from models.fleet_policy import FleetPolicy
    from services.fleet_policy_service import evaluate_policy

    policy = (
        db.query(FleetPolicy)
        .filter(FleetPolicy.id == policy_id, FleetPolicy.deleted_at.is_(None))
        .first()
    )
    if not policy:
        raise NotFoundError("FleetPolicy", str(policy_id))

    _result, evaluation = evaluate_policy(db, policy, current_user=current_user)
    db.commit()
    db.refresh(evaluation)
    return _evaluation_to_out(evaluation)


@router.post(
    "/policies/{policy_id}/enforce",
    response_model=PolicyEvaluationOut,
    dependencies=[Depends(require_operator)],
    status_code=201,
)
@handle_route_errors("enforce fleet policy")
def enforce_fleet_policy(
    policy_id: int,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> PolicyEvaluationOut:
    """Enforce a policy — remediates drifted members via P2's gated bulk-op executor.

    Requires the policy mode to be 'enforce'. OS-compliance-seed policies
    cannot be enforced (returns 400) — they are inform-only.

    RBAC gate: caller must be the project owner (or admin) for all projects in the
    policy's blast radius. Rollout defaults are resolved from those projects.

    Creates a gated FleetBulkRun over the drifted members using the same
    executor path as POST /api/fleet/bulk-ops. Returns the evaluation snapshot.
    """
    from models.fleet_policy import FleetPolicy
    from services.fleet_policy_service import enforce_policy

    policy = (
        db.query(FleetPolicy)
        .filter(FleetPolicy.id == policy_id, FleetPolicy.deleted_at.is_(None))
        .first()
    )
    if not policy:
        raise NotFoundError("FleetPolicy", str(policy_id))

    # Collect all distinct project_ids covered by this policy's target members.
    # Use the most recent resolved decision for the target (avoids a re-resolve).
    # Fall back to [policy.project_id] if no prior resolved decision exists.
    policy_project_ids: list[int | None] = [policy.project_id]
    latest_decision = (
        db.query(FleetDecision)
        .filter(
            FleetDecision.target_id == policy.target_id,
            FleetDecision.status == DECISION_STATUS_RESOLVED,
        )
        .order_by(FleetDecision.id.desc())
        .first()
    )
    if latest_decision is not None:
        member_ids_for_rbac: list[int] = latest_decision.resolved_member_ids or []
        if member_ids_for_rbac:
            rows = (
                db.query(FleetMember.project_id)
                .filter(FleetMember.id.in_(member_ids_for_rbac))
                .distinct()
                .all()
            )
            policy_project_ids.extend(pid for (pid,) in rows)

    # RBAC gate — fail-closed; refuse before any enforcement dispatched.
    require_fleet_mutation(policy_project_ids, current_user, db)

    # Resolve rollout defaults from the blast-radius projects.
    concurrency, gate_mode = resolve_rollout_defaults(db, policy_project_ids)

    _result, evaluation, _run = enforce_policy(
        db,
        policy,
        current_user=current_user,
        concurrency=concurrency,
        gate_mode=gate_mode,
    )
    db.commit()
    db.refresh(evaluation)
    return _evaluation_to_out(evaluation)


@router.get(
    "/policies/{policy_id}/compliance",
    response_model=PolicyComplianceRollupOut,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get fleet policy compliance rollup")
def get_policy_compliance_rollup(
    policy_id: int,
    db: Session = Depends(get_db),
) -> PolicyComplianceRollupOut:
    """Get the latest compliance rollup for a policy.

    Returns per-policy compliant/drifted/unknown counts + the drifted member
    list from the most recent evaluation. If no evaluation has been run yet,
    returns zero counts.
    """
    from models.fleet_policy import (
        KIND_SUPPORTS_ENFORCE,
        FleetPolicy,
        PolicyEvaluation,
    )

    policy = (
        db.query(FleetPolicy)
        .filter(FleetPolicy.id == policy_id, FleetPolicy.deleted_at.is_(None))
        .first()
    )
    if not policy:
        raise NotFoundError("FleetPolicy", str(policy_id))

    # Most recent evaluation.
    latest = (
        db.query(PolicyEvaluation)
        .filter(PolicyEvaluation.policy_id == policy_id)
        .order_by(PolicyEvaluation.evaluated_at.desc())
        .first()
    )

    if latest is None:
        return PolicyComplianceRollupOut(
            policy_id=policy.id,
            policy_name=policy.name,
            kind=policy.kind,
            mode=policy.mode,
            supports_enforce=KIND_SUPPORTS_ENFORCE.get(policy.kind, False),
            compliant_count=0,
            drifted_count=0,
            unknown_count=0,
            total_count=0,
            drifted_members=[],
            last_evaluated_at=None,
            last_evaluation_id=None,
        )

    all_results = latest.member_results or []
    drifted_out = [
        MemberComplianceResultOut(
            member_id=r["member_id"],
            status=r["status"],
            detail=r.get("detail"),
        )
        for r in all_results
        if r.get("status") == "drifted"
    ]

    return PolicyComplianceRollupOut(
        policy_id=policy.id,
        policy_name=policy.name,
        kind=policy.kind,
        mode=policy.mode,
        supports_enforce=KIND_SUPPORTS_ENFORCE.get(policy.kind, False),
        compliant_count=latest.compliant_count,
        drifted_count=latest.drifted_count,
        unknown_count=latest.unknown_count,
        total_count=latest.total_count,
        drifted_members=drifted_out,
        last_evaluated_at=latest.evaluated_at.isoformat(),
        last_evaluation_id=latest.id,
    )


@router.get(
    "/policies/{policy_id}/evaluations",
    response_model=list[PolicyEvaluationOut],
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("list fleet policy evaluations")
def list_policy_evaluations(
    policy_id: int,
    db: Session = Depends(get_db),
) -> list[PolicyEvaluationOut]:
    """List all evaluation snapshots for a policy (newest first).

    Returns 404 if the policy is soft-deleted.  The evaluation rows themselves
    remain in the database (immutable audit trail) but are not queryable via
    the active policy surface.
    """
    from models.fleet_policy import FleetPolicy, PolicyEvaluation

    policy = (
        db.query(FleetPolicy)
        .filter(FleetPolicy.id == policy_id, FleetPolicy.deleted_at.is_(None))
        .first()
    )
    if not policy:
        raise NotFoundError("FleetPolicy", str(policy_id))

    evaluations = (
        db.query(PolicyEvaluation)
        .filter(PolicyEvaluation.policy_id == policy_id)
        .order_by(PolicyEvaluation.evaluated_at.desc())
        .all()
    )
    return [_evaluation_to_out(e) for e in evaluations]


# ---------------------------------------------------------------------------
# Policy PUT / DELETE — D-022 P5 S3
# ---------------------------------------------------------------------------


@router.put(
    "/policies/{policy_id}",
    response_model=PolicyOut,
)
@handle_route_errors("update fleet policy")
def update_fleet_policy(
    policy_id: int,
    body: UpdatePolicyRequest,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> PolicyOut:
    """Update a fleet policy's name/kind/mode/desired.

    Only the policy row is mutated — historical PolicyEvaluation snapshots are
    IMMUTABLE and are never touched.

    Invariant: an 'os-compliance-seed' policy cannot be set to 'enforce' mode
    (KIND_SUPPORTS_ENFORCE[os-compliance-seed] is False).  Attempting to do so
    returns HTTP 400 with a clear message.

    RBAC: caller must own the policy's project (or be admin).
    """
    from core.errors import ValidationError as AppValidationError
    from models.fleet_policy import (
        KIND_SUPPORTS_ENFORCE,
        POLICY_KINDS,
        POLICY_MODES,
        FleetPolicy,
    )

    policy = (
        db.query(FleetPolicy)
        .filter(FleetPolicy.id == policy_id, FleetPolicy.deleted_at.is_(None))
        .first()
    )
    if not policy:
        raise NotFoundError("FleetPolicy", str(policy_id))

    require_fleet_mutation([policy.project_id], current_user, db)

    # Apply updates field-by-field; only mutate what was supplied.
    if body.name is not None:
        policy.name = body.name

    # Determine effective kind/mode after any updates (used for cross-field validation).
    effective_kind = body.kind if body.kind is not None else policy.kind
    effective_mode = body.mode if body.mode is not None else policy.mode

    if body.kind is not None:
        if body.kind not in POLICY_KINDS:
            raise BadRequestError(
                f"Unknown policy kind '{body.kind}'. Allowed: {sorted(POLICY_KINDS)}"
            )
        policy.kind = body.kind

    if body.mode is not None:
        if body.mode not in POLICY_MODES:
            raise BadRequestError(
                f"Unknown policy mode '{body.mode}'. Allowed: {sorted(POLICY_MODES)}"
            )
        policy.mode = body.mode

    # Guard: os-compliance-seed must stay inform-only.
    if effective_mode == "enforce" and not KIND_SUPPORTS_ENFORCE.get(effective_kind, False):
        raise BadRequestError(
            f"Policy kind '{effective_kind}' does not support enforce mode — "
            "it is inform-only.  Remove the mode='enforce' field from the request."
        )

    if body.desired is not None:
        desired = body.desired
        # Re-validate desired schema for the effective kind.
        if effective_kind == "label-compliance":
            rl = desired.get("required_labels")
            if not isinstance(rl, list) or not rl:
                raise AppValidationError(
                    "desired.required_labels",
                    "label-compliance policy requires a non-empty 'required_labels' list.",
                )
            from services.fleet_vocabulary import validate_assigned_labels
            rl_dict: dict[str, str] = {}
            for entry in rl:
                if "=" in entry:
                    k, _, v = entry.partition("=")
                    rl_dict[k] = v
            validate_assigned_labels(rl_dict)
        elif effective_kind == "os-compliance-seed":
            if not desired.get("os"):
                raise AppValidationError(
                    "desired.os",
                    "os-compliance-seed policy requires 'os' in desired.",
                )
        policy.desired = desired

    db.commit()
    db.refresh(policy)
    return _policy_to_out(policy)


@router.delete(
    "/policies/{policy_id}",
    status_code=204,
)
@handle_route_errors("delete fleet policy")
def delete_fleet_policy(
    policy_id: int,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> None:
    """Soft-delete a fleet policy (sets deleted_at).

    Hard-delete is intentionally NOT performed — it would cascade-destroy the
    immutable PolicyEvaluation audit trail.  Soft-deleted policies are excluded
    from list/get/compliance/evaluate/enforce/evaluations endpoints.
    PolicyEvaluation rows are preserved and remain queryable by policy_id
    directly via the database for audit purposes.

    RBAC: caller must own the policy's project (or be admin).
    """
    from datetime import UTC, datetime

    from models.fleet_policy import FleetPolicy

    policy = (
        db.query(FleetPolicy)
        .filter(FleetPolicy.id == policy_id, FleetPolicy.deleted_at.is_(None))
        .first()
    )
    if not policy:
        raise NotFoundError("FleetPolicy", str(policy_id))

    require_fleet_mutation([policy.project_id], current_user, db)

    policy.deleted_at = datetime.now(UTC)
    db.commit()
