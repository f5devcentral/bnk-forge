"""Fleet policy service — evaluator (inform) + enforce-via-executor.

D-022 Phase 3.

Safety contract:
  - enforce_policy creates a FleetDecision over the DRIFTED subset via
    decision_from_member_ids, then creates a FleetBulkRun and dispatches
    run_fleet_bulk_op.delay — THE SAME PATH as the P2 route.
  - P3 code NEVER writes to FleetMember directly.
  - _dispatch_action_for_member (in fleet_bulkop_service) remains the sole
    assigned_labels writer.
  - OS-compliance-seed is INFORM-ONLY (KIND_SUPPORTS_ENFORCE[os-seed] = False).
    enforce_policy raises BadRequestError for os-compliance-seed policies.
  - Inform = evaluate + persist PolicyEvaluation; zero FleetMember/Run mutation.

Version comparison: uses tuple-split (no packaging dep required).
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from core.auth_context import effective_role
from models.fleet import FleetMember
from models.fleet_policy import (
    COMPLIANCE_STATUS_COMPLIANT,
    COMPLIANCE_STATUS_DRIFTED,
    COMPLIANCE_STATUS_UNKNOWN,
    KIND_REMEDIATION_ACTION,
    KIND_SUPPORTS_ENFORCE,
    POLICY_KIND_LABEL_COMPLIANCE,
    POLICY_KIND_OS_COMPLIANCE_SEED,
    POLICY_MODE_ENFORCE,
    FleetPolicy,
    PolicyEvaluation,
)
from models.fleet_targeting import (
    BULK_RUN_STATUS_PENDING,
    GATE_MODE_MANUAL,
    SAFE_ACTIONS,
    FleetBulkRun,
    FleetTarget,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class MemberComplianceResult:
    member_id: int
    status: str  # compliant | drifted | unknown
    detail: str | None = None
    # For label-compliance drift: the merged label set needed to make compliant.
    desired_labels: dict[str, str] | None = None


@dataclass
class ComplianceResult:
    policy_id: int
    compliant: list[MemberComplianceResult] = field(default_factory=list)
    drifted: list[MemberComplianceResult] = field(default_factory=list)
    unknown: list[MemberComplianceResult] = field(default_factory=list)

    @property
    def drifted_member_ids(self) -> list[int]:
        return [r.member_id for r in self.drifted]

    @property
    def total_count(self) -> int:
        return len(self.compliant) + len(self.drifted) + len(self.unknown)


# ---------------------------------------------------------------------------
# Read-only member resolver (inform path — no Decision row created)
# ---------------------------------------------------------------------------

def _resolve_member_ids_readonly(
    db: Session,
    target: FleetTarget,
    current_user,
) -> list[FleetMember]:
    """Resolve a target's member universe WITHOUT creating a FleetDecision.

    Used by evaluate_policy (inform path) — changes NOTHING. Reuses the same
    RBAC + matches_selector logic as resolve_target.
    """
    from services.fleet_selector import matches_selector, validate_selector

    selector = target.selector or {}
    validate_selector(selector)
    label_filters: list[str] = selector.get("labels") or []

    query = db.query(FleetMember)
    if effective_role(current_user) != "admin":
        from models.project import Project
        owned_ids = [
            pid for (pid,) in db.query(Project.id).filter(
                Project.user_id == current_user.id
            ).all()
        ]
        query = query.filter(FleetMember.project_id.in_(owned_ids))

    members = query.all()
    return [m for m in members if matches_selector(m, label_filters)]


# ---------------------------------------------------------------------------
# Per-kind evaluators
# ---------------------------------------------------------------------------

def _parse_version(version_str: str) -> tuple[int, ...]:
    """Parse a version string into a comparable tuple of ints.

    '22.04' → (22, 4), '5.15.0' → (5, 15, 0).
    Non-numeric segments are treated as 0.
    """
    parts = []
    for part in version_str.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _evaluate_label_compliance(
    member: FleetMember,
    desired: dict,
) -> MemberComplianceResult:
    """Evaluate label-compliance for a single member.

    D-017: never-reconciled member → unknown.
    required_labels: list of "k=v" strings.
    Check against assigned_labels only (operator-set).
    Drift detail = missing k=v pairs.
    desired_labels = existing_assigned ∪ required (for remediation).
    """
    if member.last_reconciled_at is None:
        return MemberComplianceResult(
            member_id=member.id,
            status=COMPLIANCE_STATUS_UNKNOWN,
            detail="Member has never been reconciled — compliance cannot be determined.",
        )

    required_labels: list[str] = desired.get("required_labels") or []
    assigned = member.assigned_labels or {}

    missing = []
    for req in required_labels:
        if "=" not in req:
            continue
        key, _, value = req.partition("=")
        if assigned.get(key) != value:
            missing.append(req)

    if not missing:
        return MemberComplianceResult(
            member_id=member.id,
            status=COMPLIANCE_STATUS_COMPLIANT,
        )

    # Build desired_labels = existing_assigned ∪ required (so remediation
    # via set-labels replaces the full assigned_labels with everything needed).
    merged = dict(assigned)
    for req in required_labels:
        if "=" not in req:
            continue
        key, _, value = req.partition("=")
        merged[key] = value

    return MemberComplianceResult(
        member_id=member.id,
        status=COMPLIANCE_STATUS_DRIFTED,
        detail=f"Missing required labels: {missing}",
        desired_labels=merged,
    )


def _evaluate_os_compliance_seed(
    member: FleetMember,
    desired: dict,
) -> MemberComplianceResult:
    """Evaluate os-compliance-seed for a single member.

    Reads discovered_labels["os"] and ["os-version"].
    D-017: absent OS facts → unknown.
    Compliant: os matches AND version >= min_version (tuple comparison).
    """
    discovered = member.discovered_labels or {}
    actual_os = discovered.get("os")
    actual_version = discovered.get("os-version")

    if not actual_os or not actual_version:
        return MemberComplianceResult(
            member_id=member.id,
            status=COMPLIANCE_STATUS_UNKNOWN,
            detail=(
                "OS facts missing from discovered_labels — "
                "run reconcile to populate 'os' and 'os-version'."
            ),
        )

    required_os: str = desired.get("os") or ""
    min_version: str = desired.get("min_version") or ""

    if actual_os.lower() != required_os.lower():
        return MemberComplianceResult(
            member_id=member.id,
            status=COMPLIANCE_STATUS_DRIFTED,
            detail=(
                f"OS mismatch: expected '{required_os}', got '{actual_os}'."
            ),
        )

    if min_version:
        actual_ver_tuple = _parse_version(actual_version)
        min_ver_tuple = _parse_version(min_version)
        if actual_ver_tuple < min_ver_tuple:
            return MemberComplianceResult(
                member_id=member.id,
                status=COMPLIANCE_STATUS_DRIFTED,
                detail=(
                    f"OS version '{actual_version}' is below minimum '{min_version}'."
                ),
            )

    return MemberComplianceResult(
        member_id=member.id,
        status=COMPLIANCE_STATUS_COMPLIANT,
    )


def _evaluate_member(
    member: FleetMember,
    kind: str,
    desired: dict,
) -> MemberComplianceResult:
    """Dispatch to the per-kind evaluator."""
    if kind == POLICY_KIND_LABEL_COMPLIANCE:
        return _evaluate_label_compliance(member, desired)
    if kind == POLICY_KIND_OS_COMPLIANCE_SEED:
        return _evaluate_os_compliance_seed(member, desired)
    # Unknown kind — treat as unknown (D-017 spirit).
    return MemberComplianceResult(
        member_id=member.id,
        status=COMPLIANCE_STATUS_UNKNOWN,
        detail=f"Unknown policy kind '{kind}'.",
    )


# ---------------------------------------------------------------------------
# Persist helper
# ---------------------------------------------------------------------------

def _persist_evaluation(
    db: Session,
    policy: FleetPolicy,
    result: ComplianceResult,
    *,
    evaluated_by: str,
    target: FleetTarget,
    mode: str,
    enforced_run_id: int | None = None,
) -> PolicyEvaluation:
    """Write an immutable PolicyEvaluation snapshot."""
    now = datetime.now(UTC)

    all_results = result.compliant + result.drifted + result.unknown
    member_results_json = [
        {
            "member_id": r.member_id,
            "status": r.status,
            "detail": r.detail,
        }
        for r in all_results
    ]

    evaluation = PolicyEvaluation(
        policy_id=policy.id,
        project_id=policy.project_id,
        evaluated_at=now,
        evaluated_by=evaluated_by,
        selector_snapshot=target.selector,
        desired_snapshot=policy.desired,
        compliant_count=len(result.compliant),
        drifted_count=len(result.drifted),
        unknown_count=len(result.unknown),
        total_count=result.total_count,
        member_results=member_results_json,
        enforced_run_id=enforced_run_id,
        mode=mode,
    )
    db.add(evaluation)
    db.flush()
    db.refresh(evaluation)
    return evaluation


# ---------------------------------------------------------------------------
# Public API — evaluate (inform)
# ---------------------------------------------------------------------------

def evaluate_policy(
    db: Session,
    policy: FleetPolicy,
    *,
    current_user,
) -> tuple[ComplianceResult, PolicyEvaluation]:
    """Evaluate a policy in INFORM mode.

    Resolves the member universe via a read-only resolve (no Decision row).
    Evaluates compliance per member.
    Persists an immutable PolicyEvaluation snapshot.
    ZERO FleetMember or FleetBulkRun mutations.

    Returns (ComplianceResult, PolicyEvaluation).
    """
    target = db.query(FleetTarget).filter(FleetTarget.id == policy.target_id).first()
    if target is None:
        from core.errors import NotFoundError
        raise NotFoundError("FleetTarget", str(policy.target_id))

    members = _resolve_member_ids_readonly(db, target, current_user)

    result = ComplianceResult(policy_id=policy.id)
    for member in members:
        member_result = _evaluate_member(member, policy.kind, policy.desired or {})
        if member_result.status == COMPLIANCE_STATUS_COMPLIANT:
            result.compliant.append(member_result)
        elif member_result.status == COMPLIANCE_STATUS_DRIFTED:
            result.drifted.append(member_result)
        else:
            result.unknown.append(member_result)

    evaluated_by = getattr(current_user, "username", str(current_user))
    evaluation = _persist_evaluation(
        db,
        policy,
        result,
        evaluated_by=evaluated_by,
        target=target,
        mode=policy.mode,
        enforced_run_id=None,
    )

    logger.info(
        "evaluate_policy: policy_id=%s kind=%s → %d compliant, %d drifted, %d unknown",
        policy.id, policy.kind,
        len(result.compliant), len(result.drifted), len(result.unknown),
    )
    return result, evaluation


# ---------------------------------------------------------------------------
# Public API — enforce (creates gated bulk-op run)
# ---------------------------------------------------------------------------

def enforce_policy(
    db: Session,
    policy: FleetPolicy,
    *,
    current_user,
    concurrency: int = 5,
    gate_mode: str | None = None,
) -> tuple[ComplianceResult, PolicyEvaluation, FleetBulkRun | None]:
    """Enforce a policy by remediating drifted members via P2's gated executor.

    Safety contract:
      1. Guard: mode==enforce AND KIND_SUPPORTS_ENFORCE[kind] (os-seed → 400).
      2. Re-evaluate fresh (TOCTOU guard).
      3. Empty drift → no-op, returns None for run.
      4. Build Decision over drifted subset via decision_from_member_ids.
      5. action = KIND_REMEDIATION_ACTION[kind] (assert ∈ SAFE_ACTIONS).
      6. Create FleetBulkRun + dispatch run_fleet_bulk_op.delay — SAME path as P2.
      7. Persist PolicyEvaluation with enforced_run_id.

    P3 code NEVER calls _dispatch_action_for_member or writes FleetMember directly.
    Returns (ComplianceResult, PolicyEvaluation, FleetBulkRun | None).
    """
    from core.errors import BadRequestError

    # Guard 1: mode check.
    if policy.mode != POLICY_MODE_ENFORCE:
        raise BadRequestError(
            f"Policy {policy.id} has mode='{policy.mode}' — only 'enforce' mode can enforce."
        )

    # Guard 2: kind supports enforce?
    if not KIND_SUPPORTS_ENFORCE.get(policy.kind, False):
        raise BadRequestError(
            f"Policy kind '{policy.kind}' does not support enforce mode. "
            "OS-compliance-seed is inform-only (OS facts are reconcile-written; "
            "cannot be remediated via set-labels)."
        )

    target = db.query(FleetTarget).filter(FleetTarget.id == policy.target_id).first()
    if target is None:
        from core.errors import NotFoundError
        raise NotFoundError("FleetTarget", str(policy.target_id))

    # Guard 3: Re-evaluate fresh (TOCTOU — use same read-only resolve).
    members = _resolve_member_ids_readonly(db, target, current_user)

    result = ComplianceResult(policy_id=policy.id)
    for member in members:
        member_result = _evaluate_member(member, policy.kind, policy.desired or {})
        if member_result.status == COMPLIANCE_STATUS_COMPLIANT:
            result.compliant.append(member_result)
        elif member_result.status == COMPLIANCE_STATUS_DRIFTED:
            result.drifted.append(member_result)
        else:
            result.unknown.append(member_result)

    evaluated_by = getattr(current_user, "username", str(current_user))

    # Guard 4: empty drift → no-op.
    if not result.drifted:
        evaluation = _persist_evaluation(
            db,
            policy,
            result,
            evaluated_by=evaluated_by,
            target=target,
            mode=policy.mode,
            enforced_run_id=None,
        )
        logger.info(
            "enforce_policy: policy_id=%s — no drift, no-op.", policy.id
        )
        return result, evaluation, None

    # Guard 5: action must be in SAFE_ACTIONS.
    action = KIND_REMEDIATION_ACTION.get(policy.kind)
    if action is None or action not in SAFE_ACTIONS:
        raise BadRequestError(
            f"No safe remediation action for kind '{policy.kind}'. "
            f"SAFE_ACTIONS: {sorted(SAFE_ACTIONS)}"
        )

    # Build action_params.
    # label-compliance: action_params.labels = ONLY the policy's required label keys.
    # set-labels REPLACES assigned_labels on each member, so passing the union of all
    # members' existing labels would mislabel members (e.g. member A gets member B's
    # team label). The label-compliance contract is "required labels must be present"
    # — non-required labels are not preserved by a label-compliance enforce.
    # Users who need to preserve pre-existing labels should use mode=inform + manual merge.
    action_params: dict | None = None
    if policy.kind == POLICY_KIND_LABEL_COMPLIANCE:
        # Build required dict from policy's desired.required_labels.
        required_labels: list[str] = (policy.desired or {}).get("required_labels") or []
        required_dict: dict[str, str] = {}
        for kv in required_labels:
            if "=" not in kv:
                continue
            k, _, v = kv.partition("=")
            required_dict[k] = v
        action_params = {"labels": required_dict}

    # Build Decision over DRIFTED subset only.
    from services.fleet_selector import decision_from_member_ids

    drifted_ids = result.drifted_member_ids
    decision = decision_from_member_ids(
        db,
        target,
        drifted_ids,
        resolved_by=evaluated_by,
    )

    # Create FleetBulkRun — SAME path as the P2 route.
    # concurrency/gate_mode are supplied by the caller (route resolves Project defaults).
    resolved_gate_mode = gate_mode if gate_mode is not None else GATE_MODE_MANUAL
    run = FleetBulkRun(
        decision_id=decision.id,
        project_id=policy.project_id,
        action=action,
        action_params=action_params,
        wave_by="fault_domain",
        concurrency=concurrency,
        gate_mode=resolved_gate_mode,
        status=BULK_RUN_STATUS_PENDING,
        current_wave_index=0,
        created_by=evaluated_by,
    )
    db.add(run)
    db.flush()
    db.refresh(run)

    # Commit before dispatch so the Celery worker's own session can find the run.
    # The worker opens a separate DB session; if it starts before the route commits
    # the transaction it would raise "FleetBulkRun {id} not found" (silent fail).
    # Mirror the P2 route pattern: commit → delay → set celery_task_id.
    db.commit()
    db.refresh(run)

    # Dispatch via Celery — SAME path as P2 route.
    from tasks.fleet_tasks import run_fleet_bulk_op

    task = run_fleet_bulk_op.delay(run.id)
    # Set celery_task_id after dispatch; persisted by the route's trailing commit.
    run.celery_task_id = task.id

    # Persist evaluation with enforced_run_id.
    evaluation = _persist_evaluation(
        db,
        policy,
        result,
        evaluated_by=evaluated_by,
        target=target,
        mode=policy.mode,
        enforced_run_id=run.id,
    )

    logger.info(
        "enforce_policy: policy_id=%s kind=%s → %d drifted → "
        "decision_id=%s bulk_run_id=%s",
        policy.id, policy.kind, len(drifted_ids), decision.id, run.id,
    )
    return result, evaluation, run
