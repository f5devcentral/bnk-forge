"""Fleet policy layer — FleetPolicy + PolicyEvaluation.

D-022 Phase 3: inform-then-enforce policy as desired-state over a fleet Target.

Design decisions:
- FleetPolicy references a FleetTarget (NOT NULL FK) — enforcer must own a target.
- PolicyEvaluation is IMMUTABLE after creation (mirror FleetDecision discipline).
- String statuses (no DB enum) to allow future extension without migration.
- OS-compliance-seed is INFORM-ONLY (KIND_SUPPORTS_ENFORCE[os-compliance-seed] = False).
- migration down_revision="v2_119" (P2 branch head).
"""

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.sql import func

from database import Base

# ---------------------------------------------------------------------------
# Policy kinds
# ---------------------------------------------------------------------------

POLICY_KIND_LABEL_COMPLIANCE = "label-compliance"
POLICY_KIND_OS_COMPLIANCE_SEED = "os-compliance-seed"

POLICY_KINDS: frozenset[str] = frozenset({
    POLICY_KIND_LABEL_COMPLIANCE,
    POLICY_KIND_OS_COMPLIANCE_SEED,
})

# ---------------------------------------------------------------------------
# Policy modes
# ---------------------------------------------------------------------------

POLICY_MODE_INFORM = "inform"
POLICY_MODE_ENFORCE = "enforce"

POLICY_MODES: frozenset[str] = frozenset({
    POLICY_MODE_INFORM,
    POLICY_MODE_ENFORCE,
})

# ---------------------------------------------------------------------------
# Per-kind capability maps
# ---------------------------------------------------------------------------

# OS-compliance-seed: OS facts live in discovered_labels (reconcile-written/read-only).
# set-labels only touches assigned_labels → cannot "fix" OS. INFORM-ONLY.
KIND_SUPPORTS_ENFORCE: dict[str, bool] = {
    POLICY_KIND_LABEL_COMPLIANCE: True,
    POLICY_KIND_OS_COMPLIANCE_SEED: False,
}

# Remediation action per kind — must be in SAFE_ACTIONS (fleet_targeting.py).
KIND_REMEDIATION_ACTION: dict[str, str] = {
    POLICY_KIND_LABEL_COMPLIANCE: "set-labels",
    # os-compliance-seed has no remediation action (KIND_SUPPORTS_ENFORCE is False).
}

# ---------------------------------------------------------------------------
# Compliance status constants
# ---------------------------------------------------------------------------

COMPLIANCE_STATUS_COMPLIANT = "compliant"
COMPLIANCE_STATUS_DRIFTED = "drifted"
COMPLIANCE_STATUS_UNKNOWN = "unknown"

COMPLIANCE_STATUSES: frozenset[str] = frozenset({
    COMPLIANCE_STATUS_COMPLIANT,
    COMPLIANCE_STATUS_DRIFTED,
    COMPLIANCE_STATUS_UNKNOWN,
})

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class FleetPolicy(Base):
    """A desired-state policy over a fleet Target.

    kind:   "label-compliance" | "os-compliance-seed"
    mode:   "inform" | "enforce"
    desired: kind-specific JSON —
      label-compliance:    {"required_labels": ["k=v", ...]}
      os-compliance-seed:  {"os": "ubuntu", "min_version": "22.04"}

    target_id is NOT NULL — a policy must own/reference a specific Target.
    """

    __tablename__ = "fleet_policies"

    id = Column(Integer, primary_key=True, index=True)

    # Optional project scope (mirrors FleetTarget).
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    name = Column(String(255), nullable=False)

    # The target this policy applies to (NOT NULL — enforce needs a target).
    target_id = Column(
        Integer,
        ForeignKey("fleet_targets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Open string (validated in service, not DB).
    kind = Column(String(50), nullable=False)

    # "inform" | "enforce"
    mode = Column(String(50), nullable=False, default=POLICY_MODE_INFORM)

    # Kind-specific desired-state specification.
    desired = Column(JSON, nullable=False)

    created_by = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Soft-delete: NULL = active; set = deleted.  PolicyEvaluation history is
    # preserved — hard-delete would violate the immutable-snapshot invariant.
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_fleet_policy_project", "project_id"),
        Index("idx_fleet_policy_target", "target_id"),
        Index("idx_fleet_policy_kind", "kind"),
        Index("idx_fleet_policy_deleted", "deleted_at"),
    )


class PolicyEvaluation(Base):
    """Immutable snapshot of a policy evaluation result.

    One row per evaluation run (inform or enforce).
    member_results JSON: [{"member_id": int, "status": str, "detail": str | null}, ...]
    enforced_run_id: FK to FleetBulkRun if an enforce triggered a run; null for inform.

    IMMUTABLE: never mutated after creation (mirror FleetDecision discipline).
    """

    __tablename__ = "fleet_policy_evaluations"

    id = Column(Integer, primary_key=True, index=True)

    policy_id = Column(
        Integer,
        ForeignKey("fleet_policies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id = Column(Integer, nullable=True, index=True)

    evaluated_at = Column(DateTime(timezone=True), nullable=False)
    evaluated_by = Column(String(255), nullable=True)

    # Snapshots for auditability.
    selector_snapshot = Column(JSON, nullable=True)
    desired_snapshot = Column(JSON, nullable=False)

    # Rollup counts.
    compliant_count = Column(Integer, nullable=False, default=0)
    drifted_count = Column(Integer, nullable=False, default=0)
    unknown_count = Column(Integer, nullable=False, default=0)
    total_count = Column(Integer, nullable=False, default=0)

    # Per-member results: [{"member_id": int, "status": str, "detail": str | null}]
    member_results = Column(JSON, nullable=False)

    # Set when an enforce triggers a FleetBulkRun.
    enforced_run_id = Column(
        Integer,
        ForeignKey("fleet_bulk_runs.id", ondelete="SET NULL"),
        nullable=True,
    )

    # "inform" | "enforce" — snapshot of the policy mode at eval time.
    mode = Column(String(50), nullable=False)

    detail = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_policy_eval_policy", "policy_id"),
        Index("idx_policy_eval_project", "project_id"),
        Index("idx_policy_eval_evaluated_at", "evaluated_at"),
    )
