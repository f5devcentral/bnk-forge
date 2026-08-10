"""Fleet targeting layer — Target, Decision, BulkRun, BulkRunResult.

D-022 Phase 2: selector-based targeting + gated bulk operations.

Design decisions:
- FleetDecision is IMMUTABLE: resolved_member_ids is a snapshot, never mutated.
  Only status can change (resolved → consumed/superseded).
- String statuses (no DB enum) to allow future extension without migration.
- migration down_revision="v2_118" (P1 branch head).
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
    UniqueConstraint,
)
from sqlalchemy.sql import func

from database import Base

# ---------------------------------------------------------------------------
# Status constants (frozensets — string DB values, not DB enums)
# ---------------------------------------------------------------------------

# FleetDecision statuses
DECISION_STATUS_RESOLVED = "resolved"
DECISION_STATUS_CONSUMED = "consumed"
DECISION_STATUS_SUPERSEDED = "superseded"

DECISION_STATUSES: frozenset[str] = frozenset({
    DECISION_STATUS_RESOLVED,
    DECISION_STATUS_CONSUMED,
    DECISION_STATUS_SUPERSEDED,
})

# FleetBulkRun statuses
BULK_RUN_STATUS_PENDING = "pending"
BULK_RUN_STATUS_RUNNING = "running"
BULK_RUN_STATUS_PAUSED_GATE = "paused_gate"
BULK_RUN_STATUS_COMPLETED = "completed"
BULK_RUN_STATUS_FAILED = "failed"
BULK_RUN_STATUS_CANCELLED = "cancelled"

BULK_RUN_STATUSES: frozenset[str] = frozenset({
    BULK_RUN_STATUS_PENDING,
    BULK_RUN_STATUS_RUNNING,
    BULK_RUN_STATUS_PAUSED_GATE,
    BULK_RUN_STATUS_COMPLETED,
    BULK_RUN_STATUS_FAILED,
    BULK_RUN_STATUS_CANCELLED,
})

BULK_RUN_TERMINAL_STATUSES: frozenset[str] = frozenset({
    BULK_RUN_STATUS_COMPLETED,
    BULK_RUN_STATUS_FAILED,
    BULK_RUN_STATUS_CANCELLED,
})

# FleetBulkRunResult statuses (per-member)
RESULT_STATUS_PENDING = "pending"
RESULT_STATUS_RUNNING = "running"
RESULT_STATUS_SUCCEEDED = "succeeded"
RESULT_STATUS_FAILED = "failed"
RESULT_STATUS_SKIPPED = "skipped"

RESULT_STATUSES: frozenset[str] = frozenset({
    RESULT_STATUS_PENDING,
    RESULT_STATUS_RUNNING,
    RESULT_STATUS_SUCCEEDED,
    RESULT_STATUS_FAILED,
    RESULT_STATUS_SKIPPED,
})

# Gate modes (legacy + strategy gate kinds)
GATE_MODE_HEALTH_STABLE = "health_stable"
GATE_MODE_MANUAL = "manual"

GATE_MODES: frozenset[str] = frozenset({
    GATE_MODE_HEALTH_STABLE,
    GATE_MODE_MANUAL,
})

# Strategy gate kinds (superset of legacy gate modes; timed_wait + approval added in D-022 P6 Slice D)
GATE_KIND_APPROVAL = "approval"
GATE_KIND_TIMED_WAIT = "timed_wait"
GATE_KIND_HEALTH_STABLE = "health_stable"

STRATEGY_GATE_KINDS: frozenset[str] = frozenset({
    GATE_KIND_APPROVAL,
    GATE_KIND_TIMED_WAIT,
    GATE_KIND_HEALTH_STABLE,
})

# Safe actions allowlist — ONLY these may execute; executor refuses any other.
SAFE_ACTIONS: frozenset[str] = frozenset({
    "re-reconcile",
    "set-labels",
})

# Health statuses considered stable for health_stable gate
STABLE_HEALTH_STATUSES: frozenset[str] = frozenset({
    "healthy",
    "active",
    "ready",
    "os_online",
})

# Stale-decision TTL in seconds (aligned to 900s backfill interval)
DECISION_TTL_SECONDS = 900

# Selector keys that are NEVER allowed (D-022 guardrail)
FORBIDDEN_SELECTOR_KEYS: frozenset[str] = frozenset({
    "project_id",
    "member_id",
    "member_type",
})


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class FleetTarget(Base):
    """A named label selector that can be resolved to a Decision.

    selector: JSON {"labels": ["key=value", ...]}

    D-022 P5 S2: description + pinned_member_ids + soft-delete (deleted_at).
    Soft-delete preserves the FK chain: FleetDecision → FleetBulkRun (RESTRICT)
    must never be broken by a hard delete.  Set deleted_at to hide from lists.
    """

    __tablename__ = "fleet_targets"

    id = Column(Integer, primary_key=True, index=True)

    # Optional project scope — targets can be project-scoped or global.
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # Selector JSON: {"labels": ["env=prod", "has-dpu=true"]}
    selector = Column(JSON, nullable=False)

    # Explicit pinned members (override / supplement the selector result).
    pinned_member_ids = Column(JSON, nullable=True)

    created_by = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Soft-delete: set to now() to hide; NULL = active.
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_fleet_target_project", "project_id"),
        Index("idx_fleet_target_deleted", "deleted_at"),
    )


class FleetDecision(Base):
    """Immutable snapshot of a resolved target — the concrete member-id set.

    Produced by resolve_target(). Consumed by run_bulk_op().
    Status flow: resolved → consumed (single-use) or superseded (re-resolved).
    resolved_member_ids is NEVER mutated after creation.
    """

    __tablename__ = "fleet_decisions"

    id = Column(Integer, primary_key=True, index=True)

    target_id = Column(
        Integer,
        ForeignKey("fleet_targets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id = Column(Integer, nullable=True, index=True)

    # Snapshot of the selector at resolve time (denormalized for audit).
    selector_snapshot = Column(JSON, nullable=False)

    # THE immutable member-id snapshot: list of FleetMember.id integers.
    resolved_member_ids = Column(JSON, nullable=False)
    member_count = Column(Integer, nullable=False, default=0)

    resolved_at = Column(DateTime(timezone=True), nullable=False)
    resolved_by = Column(String(255), nullable=True)

    # "resolved" → "consumed" (after run) | "superseded" (re-resolved)
    status = Column(String(50), nullable=False, default=DECISION_STATUS_RESOLVED)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_fleet_decision_target", "target_id"),
        Index("idx_fleet_decision_project", "project_id"),
        Index("idx_fleet_decision_status", "status"),
    )


class FleetOperationStrategy(Base):
    """Reusable operation strategy — ordered waves, per-wave concurrency %, between-wave gate.

    D-022 P6 Slice D: Strategy is a TEMPLATE (authorable, reusable across fleets/runs).
    A live Run snapshots the strategy's parameters at start time (immutable instance).

    wave_by: "fault_domain" (default) | "label:<key>" (bucket by combined_labels[key])
    max_concurrency_pct: 1-100 — resolves to max(1, ceil(len(wave) * pct / 100)) workers.
    gate_kind: "approval" (manual gate, same as legacy manual) |
               "timed_wait" (auto-resume after gate_wait_seconds) |
               "health_stable" (auto-proceed when all members stable)
    gate_wait_seconds: required for timed_wait, ignored for other gate kinds.
    deleted_at: soft-delete — set to now() to hide; NULL = active.
    """

    __tablename__ = "fleet_operation_strategies"

    id = Column(Integer, primary_key=True, index=True)

    # Optional project scope.
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # Optional fleet association — nullable = reusable across multiple fleets.
    target_id = Column(
        Integer,
        ForeignKey("fleet_targets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Wave grouping: "fault_domain" or "label:<key>"
    wave_by = Column(String(100), nullable=False, default="fault_domain")

    # Blast-radius safety dial (1-100 %).
    max_concurrency_pct = Column(Integer, nullable=False, default=20)

    # Between-wave gate.
    gate_kind = Column(String(50), nullable=False, default=GATE_KIND_APPROVAL)
    gate_wait_seconds = Column(Integer, nullable=True)

    created_by = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_fleet_strategy_project", "project_id"),
        Index("idx_fleet_strategy_target", "target_id"),
        Index("idx_fleet_strategy_deleted", "deleted_at"),
    )


class FleetBulkRun(Base):
    """Parent record for a bulk operation across a Decision's member set.

    Models the orchestrator-level record (cf. BareMetalDeployment).
    Waves run sequentially; progress is tracked via current_wave_index.
    """

    __tablename__ = "fleet_bulk_runs"

    id = Column(Integer, primary_key=True, index=True)

    decision_id = Column(
        Integer,
        ForeignKey("fleet_decisions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    project_id = Column(Integer, nullable=True, index=True)

    # Action to execute — must be in SAFE_ACTIONS.
    action = Column(String(100), nullable=False)

    # Optional action parameters (e.g., labels to set for "set-labels").
    action_params = Column(JSON, nullable=True)

    # Wave configuration
    wave_by = Column(String(50), nullable=False, default="fault_domain")
    concurrency = Column(Integer, nullable=False, default=5)
    gate_mode = Column(String(50), nullable=False, default=GATE_MODE_MANUAL)

    # D-022 P6 Slice D: strategy snapshot (FK nullable — run may be created without a strategy).
    strategy_id = Column(
        Integer,
        ForeignKey("fleet_operation_strategies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Execution state
    status = Column(String(50), nullable=False, default=BULK_RUN_STATUS_PENDING)
    current_wave_index = Column(Integer, nullable=False, default=0)
    total_waves = Column(Integer, nullable=True)

    celery_task_id = Column(String(255), nullable=True)
    error_message = Column(Text, nullable=True)

    # D-022 P6 Slice D: timed_wait gate — absolute UTC datetime when the run auto-resumes.
    gate_resumes_at = Column(DateTime(timezone=True), nullable=True)

    created_by = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_fleet_bulk_run_decision", "decision_id"),
        Index("idx_fleet_bulk_run_project", "project_id"),
        Index("idx_fleet_bulk_run_status", "status"),
        Index("idx_fleet_bulk_run_strategy", "strategy_id"),
    )


class FleetBulkRunResult(Base):
    """Per-member result record for a bulk run wave step.

    One row per (run_id, fleet_member_id) pair.
    Models the child step record (cf. DeploymentStep).
    """

    __tablename__ = "fleet_bulk_run_results"

    id = Column(Integer, primary_key=True, index=True)

    run_id = Column(
        Integer,
        ForeignKey("fleet_bulk_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Soft pointer to FleetMember — no FK enforced (member may be deleted).
    fleet_member_id = Column(Integer, nullable=False)

    wave_index = Column(Integer, nullable=False, default=0)
    fault_domain = Column(String(255), nullable=True)

    status = Column(String(50), nullable=False, default=RESULT_STATUS_PENDING)
    detail = Column(Text, nullable=True)

    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "fleet_member_id", name="uq_bulk_run_member"),
        Index("idx_fleet_bulk_run_result_run", "run_id"),
    )
