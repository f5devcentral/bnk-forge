"""Fleet membership layer — thin polymorphic label layer over existing target tables.

D-022 Phase 1: FleetMember carries a key/value label set (discovered + assigned)
for each managed target (cluster, bare-metal host, DPU). The member_type field
is an open String(32) — D-023 will add "f5-bigip" later.

Design decisions:
- NO cross-table FK enforced — member_type/member_id pair is a soft pointer.
- Orphan cleanup lives in the backfill task (not a DB cascade).
- project_id FK is nullable; reconcile always fills it from the target row.
- discovered_labels and assigned_labels are kept in separate JSON columns so
  reconcile can overwrite discovered without touching assigned.

D-022 Phase 4 additions:
- lifecycle_state: derived (not driven) from each target's existing status.
  Populated by the same reconcile pass that writes health_status.
  Vocab: provisioning | active | draining | decommissioned | unknown
  Note: "draining" is kept in LIFECYCLE_STATES for forward-compat but no
  mapper produces it in P4 — drain EXECUTION is out of scope.
"""

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from database import Base

# Open string values for member_type — validated in service, not DB.
MEMBER_TYPE_CLUSTER = "cluster"
MEMBER_TYPE_HOST = "bare-metal-host"
MEMBER_TYPE_DPU = "dpu"

# Allowed member types at P1 — service raises on anything outside this set.
# D-023 will add "f5-bigip" here.
KNOWN_MEMBER_TYPES: frozenset[str] = frozenset({
    MEMBER_TYPE_CLUSTER,
    MEMBER_TYPE_HOST,
    MEMBER_TYPE_DPU,
})

# Unified lifecycle vocabulary — derived by reconcile, not driven by any runtime.
# "draining" is intentionally kept in vocab for forward-compat (D-022 P4 out-of-scope).
# No mapper produces "draining" until drain EXECUTION is implemented.
LIFECYCLE_STATE_PROVISIONING = "provisioning"
LIFECYCLE_STATE_ACTIVE = "active"
LIFECYCLE_STATE_DRAINING = "draining"
LIFECYCLE_STATE_DECOMMISSIONED = "decommissioned"
LIFECYCLE_STATE_UNKNOWN = "unknown"

LIFECYCLE_STATES: frozenset[str] = frozenset({
    LIFECYCLE_STATE_PROVISIONING,
    LIFECYCLE_STATE_ACTIVE,
    LIFECYCLE_STATE_DRAINING,
    LIFECYCLE_STATE_DECOMMISSIONED,
    LIFECYCLE_STATE_UNKNOWN,
})


class FleetMember(Base):
    """A single managed-target's fleet membership + label record.

    Polymorphic pointer: (member_type, member_id) uniquely identifies the
    target row in the per-type table.  No DB-level FK is enforced — the
    pair is a soft reference; the reconcile backfill prunes memberships
    whose target row has been deleted.
    """

    __tablename__ = "fleet_members"

    id = Column(Integer, primary_key=True)

    # Tenancy boundary — FK to projects, nullable for orphan-safe queries.
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # Polymorphic pointer — open string, NOT a DB enum.
    # Allowed values at P1: "cluster" | "bare-metal-host" | "dpu"
    member_type = Column(String(32), nullable=False, index=True)
    member_id = Column(Integer, nullable=False)

    # Human-readable name (denormalized from the target row for fast display).
    name = Column(String(255), nullable=False)

    # Label namespaces kept strictly separate.
    # discovered_labels: written by reconcile (overwritten on each run).
    # assigned_labels:  written by operator via PUT endpoint (NEVER overwritten by reconcile).
    discovered_labels = Column(JSON, nullable=True)
    assigned_labels = Column(JSON, nullable=True)

    # Optional coarse fault-domain hint (rack, zone, region — operator-set).
    fault_domain = Column(String(255), nullable=True)

    # Derived from the most recent reconcile pass.
    health_status = Column(String(50), nullable=True)
    # lifecycle_state: unified per-target state derived from existing target status.
    # Populated on create + every update by reconcile_fleet_member.
    # NULL until the first reconcile pass (treated as "unknown" by consumers).
    lifecycle_state = Column(String(50), nullable=True, index=True)
    last_reconciled_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        # Core constraint: one membership per physical target.
        UniqueConstraint("member_type", "member_id", name="uq_fleet_member_type_id"),
        # Index for project-scoped listing.
        Index("idx_fleet_member_project", "project_id"),
        # Index for type-filtered queries.
        Index("idx_fleet_member_type", "member_type"),
        # Index for lifecycle-state filters (P4).
        Index("idx_fleet_member_lifecycle", "lifecycle_state"),
    )
