"""Fleet selector logic — shared label-filter + target resolver.

D-022 Phase 2: extracted from routes/fleet.py so route + resolver share ONE
implementation of the label-filter predicate (DECOMPOSITION §1 requirement).

D-022 Phase 3 addition:
  decision_from_member_ids(db, target, member_ids, resolved_by) → FleetDecision
    Builds a Decision over a pre-computed member-id subset (used by enforce_policy
    to create a Decision over the drifted subset only). Reuses the supersede +
    immutable-snapshot pattern from resolve_target.

Public API:
  matches_selector(member, label_filters) → bool
  validate_selector(selector) → None  (raises on forbidden keys)
  resolve_target(db, target, resolved_by, current_user) → FleetDecision
  decision_from_member_ids(db, target, member_ids, *, resolved_by) → FleetDecision
"""

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from core.auth_context import effective_role
from models.fleet import FleetMember
from models.fleet_targeting import (
    DECISION_STATUS_RESOLVED,
    DECISION_STATUS_SUPERSEDED,
    FORBIDDEN_SELECTOR_KEYS,
    FleetDecision,
    FleetTarget,
)

logger = logging.getLogger(__name__)


def matches_selector(member: FleetMember, label_filters: list[str]) -> bool:
    """Return True if the member has all requested label key=value pairs.

    Labels are checked against the union of discovered + assigned labels.
    Filter format: "key=value". Filters without "=" are ignored.

    This is the single canonical implementation shared by the list route and
    the resolver — extracted from routes/fleet.py::_label_filter_matches.
    """
    if not label_filters:
        return True
    combined = {**(member.discovered_labels or {}), **(member.assigned_labels or {})}
    for f in label_filters:
        if "=" not in f:
            continue
        key, _, value = f.partition("=")
        if combined.get(key) != value:
            return False
    return True


def validate_selector(selector: dict) -> None:
    """Validate a selector dict — raise ValueError on forbidden keys.

    selector format: {"labels": ["key=value", ...]}
    Forbidden keys (D-022 guardrail): project_id, member_id, member_type.
    """
    from core.errors import ValidationError

    if not isinstance(selector, dict):
        raise ValidationError("selector", "Selector must be a JSON object with a 'labels' key.")

    label_filters: list[str] = selector.get("labels") or []
    if not isinstance(label_filters, list):
        raise ValidationError("selector.labels", "selector.labels must be a list of 'key=value' strings.")

    for f in label_filters:
        if "=" not in f:
            continue
        key, _, _ = f.partition("=")
        if key in FORBIDDEN_SELECTOR_KEYS:
            raise ValidationError(
                "selector",
                f"Forbidden selector key '{key}'. "
                f"Keys {sorted(FORBIDDEN_SELECTOR_KEYS)} may not be used as label selectors.",
            )


def _get_owned_project_ids(db: Session, current_user) -> list[int] | None:
    """Return owned project IDs for non-admin users, or None for admins.

    Mirrors the RBAC block from routes/fleet.py::list_fleet_members.
    """
    from models.project import Project

    if effective_role(current_user) == "admin":
        return None  # admins see all

    return [
        pid for (pid,) in db.query(Project.id).filter(Project.user_id == current_user.id).all()
    ]


def resolve_target(
    db: Session,
    target: FleetTarget,
    *,
    resolved_by: str,
    current_user,
) -> FleetDecision:
    """Resolve a FleetTarget → FleetDecision (immutable member-id snapshot).

    Steps:
    1. Validate selector (no forbidden keys).
    2. Query FleetMember rows scoped by RBAC (non-admins: owned projects only).
    3. Filter in Python using matches_selector (mirrors route behaviour).
    4. Supersede any prior resolved decisions for this target.
    5. Create and persist a new FleetDecision with the member-id snapshot.

    The returned Decision has status="resolved" and is ready for blast-radius
    preview or handoff to run_bulk_op.
    """
    selector = target.selector or {}
    validate_selector(selector)
    label_filters: list[str] = selector.get("labels") or []
    pinned: list[int] = target.pinned_member_ids or []

    # Empty selector + no pinned members → zero members.
    # A fleet must explicitly target something (empty ≠ all).
    if not label_filters and not pinned:
        member_ids = []
    else:
        # RBAC-scoped member query (mirrors routes/fleet.py list_fleet_members).
        query = db.query(FleetMember)
        owned_project_ids = _get_owned_project_ids(db, current_user)
        if owned_project_ids is not None:
            query = query.filter(FleetMember.project_id.in_(owned_project_ids))

        if label_filters:
            members = query.all()
            # Python-side label filter (same logic as route).
            matched = [m for m in members if matches_selector(m, label_filters)]
        else:
            matched = []

        # Merge pinned members (may not appear in label-filtered set).
        matched_ids = {m.id for m in matched}
        for pid in pinned:
            if pid not in matched_ids:
                extra = db.query(FleetMember).filter(FleetMember.id == pid).first()
                if extra:
                    matched.append(extra)
                    matched_ids.add(pid)

        member_ids = [m.id for m in matched]

    now = datetime.now(UTC)

    # Supersede any prior resolved decisions for this target so there's always
    # at most one active resolved decision per target.
    prior = (
        db.query(FleetDecision)
        .filter(
            FleetDecision.target_id == target.id,
            FleetDecision.status == DECISION_STATUS_RESOLVED,
        )
        .all()
    )
    for d in prior:
        d.status = DECISION_STATUS_SUPERSEDED

    decision = FleetDecision(
        target_id=target.id,
        project_id=target.project_id,
        selector_snapshot=selector,
        resolved_member_ids=member_ids,
        member_count=len(member_ids),
        resolved_at=now,
        resolved_by=resolved_by,
        status=DECISION_STATUS_RESOLVED,
    )
    db.add(decision)
    db.flush()
    db.refresh(decision)

    logger.info(
        "resolve_target: target_id=%s → decision_id=%s, %d members matched",
        target.id,
        decision.id,
        decision.member_count,
    )
    return decision


def decision_from_member_ids(
    db: Session,
    target: FleetTarget,
    member_ids: list[int],
    *,
    resolved_by: str,
) -> FleetDecision:
    """Build a FleetDecision over a pre-computed member-id subset.

    Used by enforce_policy (D-022 P3) to create a Decision over the DRIFTED
    subset only — not the full target population. Reuses the supersede +
    immutable-snapshot pattern from resolve_target.

    IMPORTANT: This does NOT re-validate RBAC or re-run the selector. The
    caller is responsible for ensuring member_ids came from a fresh evaluation
    that was already RBAC-scoped. The executor's stale-decision check backstops.

    member_ids: list of FleetMember.id integers (must be non-empty for enforce
                to make sense; caller may call with empty list for no-op).
    """
    now = datetime.now(UTC)

    # Supersede any prior resolved decisions for this target (same pattern as
    # resolve_target — at most one active resolved decision per target).
    prior = (
        db.query(FleetDecision)
        .filter(
            FleetDecision.target_id == target.id,
            FleetDecision.status == DECISION_STATUS_RESOLVED,
        )
        .all()
    )
    for d in prior:
        d.status = DECISION_STATUS_SUPERSEDED

    decision = FleetDecision(
        target_id=target.id,
        project_id=target.project_id,
        selector_snapshot=target.selector or {},
        resolved_member_ids=list(member_ids),
        member_count=len(member_ids),
        resolved_at=now,
        resolved_by=resolved_by,
        status=DECISION_STATUS_RESOLVED,
    )
    db.add(decision)
    db.flush()
    db.refresh(decision)

    logger.info(
        "decision_from_member_ids: target_id=%s → decision_id=%s, %d drifted members",
        target.id,
        decision.id,
        decision.member_count,
    )
    return decision
