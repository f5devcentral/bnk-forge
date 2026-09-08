"""
String enums for status values used across BNK-Forge models.

These use the ``(str, Enum)`` mixin pattern so that each member's *value* is a
plain Python string.  This means:

    ModuleStatus.APPLIED == "applied"          # True
    str(ModuleStatus.APPLIED)                   # "ModuleStatus.applied"
    ModuleStatus.APPLIED.value                  # "applied"
    json.dumps({"s": ModuleStatus.APPLIED})     # '{"s": "applied"}'

Because the members serialise to the same bare strings already stored in the
database's ``String(50)`` columns, adopting these enums requires **no database
migration**.  Existing queries, comparisons, and JSON responses continue to
work unchanged -- you simply get the added safety of a closed set of allowed
values.
"""

from enum import StrEnum

# ---------------------------------------------------------------------------
# ModuleStatus — lifecycle of a ProjectModule
# ---------------------------------------------------------------------------

class ModuleStatus(StrEnum):
    """Status values for ``ProjectModule.status``."""

    NOT_INITIALIZED = "not_initialized"
    INITIALIZED = "initialized"
    INITIALIZING = "initializing"
    INIT_FAILED = "init_failed"
    PLANNING = "planning"
    PLANNED = "planned"
    PLAN_FAILED = "plan_failed"
    APPLYING = "applying"
    APPLIED = "applied"
    APPLY_FAILED = "apply_failed"
    DESTROYING = "destroying"
    DESTROYED = "destroyed"
    DESTROY_FAILED = "destroy_failed"
    FAILED = "failed"

    @classmethod
    def terminal_states(cls) -> frozenset["ModuleStatus"]:
        """States from which no further automatic transitions occur."""
        return frozenset({
            cls.APPLIED,
            cls.DESTROYED,
            cls.FAILED,
            cls.APPLY_FAILED,
            cls.DESTROY_FAILED,
            cls.INIT_FAILED,
            cls.PLAN_FAILED,
        })


# ---------------------------------------------------------------------------
# TaskStatus — lifecycle of an async Task (Celery job)
# ---------------------------------------------------------------------------

class TaskStatus(StrEnum):
    """Status values for ``Task.status``."""

    QUEUED = "queued"
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @classmethod
    def terminal_states(cls) -> frozenset["TaskStatus"]:
        """States from which no further automatic transitions occur."""
        return frozenset({
            cls.COMPLETED,
            cls.FAILED,
            cls.CANCELLED,
        })


# ---------------------------------------------------------------------------
# StackStatus — lifecycle of a StackInstance
# ---------------------------------------------------------------------------

class StackStatus(StrEnum):
    """Status values for ``StackInstance.status``."""

    PENDING = "pending"
    DEPLOYING = "deploying"
    DEPLOYED = "deployed"
    DESTROYING = "destroying"
    DESTROYED = "destroyed"
    FAILED = "failed"

    @classmethod
    def terminal_states(cls) -> frozenset["StackStatus"]:
        """States from which no further automatic transitions occur."""
        return frozenset({
            cls.DEPLOYED,
            cls.DESTROYED,
            cls.FAILED,
        })


# ---------------------------------------------------------------------------
# ParallelExecutionStatus — lifecycle of a ParallelExecution session
# ---------------------------------------------------------------------------

class ParallelExecutionStatus(StrEnum):
    """Status values for ``ParallelExecution.status``."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# StackInstanceStatus — lifecycle of a StackInstance
# ---------------------------------------------------------------------------

class StackInstanceStatus(StrEnum):
    """Status values for ``StackInstance.status``."""

    PENDING = "pending"
    DEPLOYING = "deploying"
    DEPLOYED = "deployed"
    DESTROYING = "destroying"
    DESTROYED = "destroyed"
    FAILED = "failed"

    @classmethod
    def terminal_states(cls) -> frozenset["StackInstanceStatus"]:
        """States from which no further automatic transitions occur."""
        return frozenset({
            cls.DEPLOYED,
            cls.DESTROYED,
            cls.FAILED,
        })


# ---------------------------------------------------------------------------
# DriftCheckStatus — lifecycle of a DriftCheck record
# ---------------------------------------------------------------------------

class DriftCheckStatus(StrEnum):
    """Status values for ``DriftCheck.status``."""

    SCHEDULED = "scheduled"
    CHECKING = "checking"
    COMPLETED = "completed"
    FAILED = "failed"



# ---------------------------------------------------------------------------
# BnkUpgradeStatus — lifecycle of a BNK upgrade
# ---------------------------------------------------------------------------

class BnkUpgradeStatus(StrEnum):
    """Status values for ``BnkUpgrade.status``."""

    PLANNING = "planning"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    HEALTH_CHECK = "health_check"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"


# ---------------------------------------------------------------------------
# ClusterStatus — status of a K8s cluster connection
# ---------------------------------------------------------------------------

class ClusterStatus(StrEnum):
    """Status values for ``K8sCluster.status``."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    ERROR = "error"
    CONNECTING = "connecting"


# ---------------------------------------------------------------------------
# K8sResourceStatus — status of a K8s resource
# ---------------------------------------------------------------------------

class K8sResourceStatus(StrEnum):
    """Status values for Kubernetes resources."""

    RUNNING = "running"
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# OperatorStatus — status of a BNK operator
# ---------------------------------------------------------------------------

class OperatorStatus(StrEnum):
    """Status values for ``Operator.status``."""

    REGISTERED = "registered"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"


# ---------------------------------------------------------------------------
# OperatorCommandStatus — lifecycle of an operator command
# ---------------------------------------------------------------------------

class OperatorCommandStatus(StrEnum):
    """Status values for ``OperatorCommandQueue.status``."""

    QUEUED = "queued"
    PICKED_UP = "picked_up"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


# ---------------------------------------------------------------------------
# AlertStatus — status of an alert notification
# ---------------------------------------------------------------------------

class AlertStatus(StrEnum):
    """Status values for ``AlertHistory.status``."""

    SENT = "sent"
    FAILED = "failed"
    RATE_LIMITED = "rate_limited"


# ---------------------------------------------------------------------------
# SyncJobStatus — status of a module sync job
# ---------------------------------------------------------------------------

class SyncJobStatus(StrEnum):
    """Status values for module sync jobs."""

    PENDING = "pending"
    SYNCING = "syncing"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# ModuleSyncStatus — sync status of a module source
# ---------------------------------------------------------------------------

class ModuleSyncStatus(StrEnum):
    """Status values for ``ModuleSource.sync_status``."""

    PENDING = "pending"
    SYNCING = "syncing"
    SYNCED = "synced"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# ModuleTestStatus — test status of a module
# ---------------------------------------------------------------------------

class ModuleTestStatus(StrEnum):
    """Status values for ``Module.test_status``."""

    NOT_TESTED = "not_tested"
    TESTING = "testing"
    PASSED = "passed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# BlueprintReleaseState — lifecycle/promotion state of imported blueprint releases
# ---------------------------------------------------------------------------

class BlueprintReleaseState(StrEnum):
    """State values for ``BlueprintRelease.release_state``."""

    DISCOVERED = "discovered"
    IMPORTED = "imported"
    APPROVED = "approved"
    DEPRECATED = "deprecated"
    INACTIVE = "inactive"


# ---------------------------------------------------------------------------
# BlueprintValidationState — validation truth for imported blueprint releases
# ---------------------------------------------------------------------------

class BlueprintValidationState(StrEnum):
    """Validation state values for ``BlueprintRelease.validation_state``."""

    VALID = "valid"
    INVALID = "invalid"
    WARNING = "warning"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# DeploymentStatus — status of a deployment
# ---------------------------------------------------------------------------

class DeploymentStatus(StrEnum):
    """Status values for deployments."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


# ---------------------------------------------------------------------------
# BenchmarkTargetStatus — lifecycle of a benchmark target cluster
# ---------------------------------------------------------------------------

class BenchmarkTargetStatus(StrEnum):
    """Status values for ``BenchmarkTarget.status``."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    VALIDATING = "validating"
    ERROR = "error"

    @classmethod
    def terminal_states(cls) -> frozenset["BenchmarkTargetStatus"]:
        """Targets don't really have terminal states — they can be reactivated."""
        return frozenset()


# ---------------------------------------------------------------------------
# ProxyDeploymentStatus — lifecycle of a proxy deployment on a target
# ---------------------------------------------------------------------------

class ProxyDeploymentStatus(StrEnum):
    """Status values for ``ProxyDeployment.status``.

    Discovery-first lifecycle:
      discovered → ready (already running on cluster)
      pending → deploying → ready | failed (Helm install)
      ready → uninstalling → uninstalled
    """

    DISCOVERED = "discovered"  # Found on cluster via proxy discovery scan
    PENDING = "pending"
    DEPLOYING = "deploying"
    READY = "ready"
    FAILED = "failed"
    UNINSTALLING = "uninstalling"
    UNINSTALLED = "uninstalled"

    @classmethod
    def terminal_states(cls) -> frozenset["ProxyDeploymentStatus"]:
        """States from which no further automatic transitions occur."""
        return frozenset({
            cls.DISCOVERED,
            cls.READY,
            cls.FAILED,
            cls.UNINSTALLED,
        })


# ---------------------------------------------------------------------------
# BenchmarkRunStatus — lifecycle of a load test run
# ---------------------------------------------------------------------------

class BenchmarkRunStatus(StrEnum):
    """Status values for ``BenchmarkRun.status``."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @classmethod
    def terminal_states(cls) -> frozenset["BenchmarkRunStatus"]:
        """States from which no further automatic transitions occur."""
        return frozenset({
            cls.COMPLETED,
            cls.FAILED,
            cls.CANCELLED,
        })


# ---------------------------------------------------------------------------
# BenchmarkAgentStatus — connection status of a test client machine
# ---------------------------------------------------------------------------

class BenchmarkAgentStatus(StrEnum):
    """Status values for ``BenchmarkAgent.status`` (test client machine, NOT an AI model)."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    RUNNING = "running"

    @classmethod
    def terminal_states(cls) -> frozenset["BenchmarkAgentStatus"]:
        """Agents don't really have terminal states — they reconnect."""
        return frozenset()


# ---------------------------------------------------------------------------
# HealthSeverity — canonical operational health (PLAT-REL-001)
# ---------------------------------------------------------------------------

class HealthSeverity(StrEnum):
    """Canonical health severity for any operational entity.

    Answers: "How broken is this thing right now?"

    Ordering (worst → best): unhealthy < degraded < unknown < healthy

    Replaces ad-hoc strings in:
    - BNK health (was: critical/warning/healthy/unknown)
    - Fleet status (was: critical/warning/healthy/offline)
    - System health (was: healthy/degraded/offline)
    - DPF health (was: healthy/partial/degraded/no_devices/not_installed)
    """

    HEALTHY = "healthy"      # All components functional
    DEGRADED = "degraded"    # Partially functional, some impaired
    UNHEALTHY = "unhealthy"  # Non-functional or critically impaired
    UNKNOWN = "unknown"      # Cannot determine (no data, not configured)

    @classmethod
    def ordering(cls) -> dict["HealthSeverity", int]:
        """Return severity ordering map (lower = worse)."""
        return {
            cls.UNHEALTHY: 0,
            cls.DEGRADED: 1,
            cls.UNKNOWN: 2,
            cls.HEALTHY: 3,
        }

    @classmethod
    def worst(cls, severities: list["HealthSeverity | str"]) -> "HealthSeverity":
        """Return the worst severity from a list (like rollup_severity)."""
        if not severities:
            return cls.UNKNOWN
        order = cls.ordering()
        return min(
            (cls(s) for s in severities),
            key=lambda s: order.get(s, 2),
        )

    @classmethod
    def from_counts(cls, healthy: int, total: int) -> "HealthSeverity":
        """Derive severity from healthy/total counts (like calc_severity)."""
        if total == 0:
            return cls.UNKNOWN
        if healthy == total:
            return cls.HEALTHY
        if healthy == 0:
            return cls.UNHEALTHY
        return cls.DEGRADED


# ---------------------------------------------------------------------------
# ConnectivityStatus — canonical network reachability (PLAT-REL-001)
# ---------------------------------------------------------------------------

class ConnectivityStatus(StrEnum):
    """Canonical connectivity status for network-reachable targets.

    Answers: "Can we reach this target over the network?"

    Ordering (worst → best): unreachable < partial < reachable < unknown < connected

    Replaces ad-hoc strings in:
    - Connectivity probe (was: healthy/reachable/port_blocked/unreachable/unknown/error)
    """

    CONNECTED = "connected"      # Network open, API responsive
    REACHABLE = "reachable"      # Network open, API not responding
    PARTIAL = "partial"          # Some paths work, others blocked
    UNREACHABLE = "unreachable"  # No network path
    UNKNOWN = "unknown"          # Not checked or unable to determine

    @classmethod
    def ordering(cls) -> dict["ConnectivityStatus", int]:
        """Return connectivity ordering map (lower = worse)."""
        return {
            cls.UNREACHABLE: 0,
            cls.PARTIAL: 1,
            cls.REACHABLE: 2,
            cls.UNKNOWN: 3,
            cls.CONNECTED: 4,
        }

    @classmethod
    def worst(cls, statuses: list["ConnectivityStatus | str"]) -> "ConnectivityStatus":
        """Return the worst connectivity status from a list."""
        if not statuses:
            return cls.UNKNOWN
        order = cls.ordering()
        return min(
            (cls(s) for s in statuses),
            key=lambda s: order.get(s, 3),
        )


# ---------------------------------------------------------------------------
# DiscoveryJobStatus — lifecycle of a bare-metal discovery job
# ---------------------------------------------------------------------------

class DiscoveryJobStatus(StrEnum):
    """Status values for ``DiscoveryJob.status``."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @classmethod
    def terminal_states(cls) -> frozenset["DiscoveryJobStatus"]:
        """States from which no further automatic transitions occur."""
        return frozenset({cls.COMPLETED, cls.FAILED, cls.CANCELLED})


# ---------------------------------------------------------------------------
# DiscoveredNodeStatus — per-node discovery lifecycle
# ---------------------------------------------------------------------------

class DiscoveredNodeStatus(StrEnum):
    """Status values for ``DiscoveredNode.status``."""

    PENDING = "pending"
    CONNECTING = "connecting"
    DISCOVERING = "discovering"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# BareMetalTopology — deployment topology classification
# ---------------------------------------------------------------------------

class BareMetalTopology(StrEnum):
    """Deployment topology for bare-metal DPU hosts."""

    REGULAR = "regular"          # Host has independent NIC, DPU is worker only
    BF3 = "bf3"                  # Host network through BF3 PF, mode change via BFB
    BF3_IPMI = "bf3_ipmi"        # BF3 + mlxconfig mode change + IPMI power cycle
    BMC = "bmc"                  # BF3 + Redfish BIOS mode change + Redfish power cycle
    DUAL_DPU_OBMC = "dual_dpu_obmc"  # Two DPUs, rshim via DPU OpenBMC, deployment on non-mgmt DPU


# ---------------------------------------------------------------------------
# BareMetalDeploymentStatus — lifecycle of a bare-metal deployment
# ---------------------------------------------------------------------------

class BareMetalDeploymentStatus(StrEnum):
    """Status values for ``BareMetalDeployment.status``."""

    PENDING = "pending"
    DISCOVERING = "discovering"
    PLANNING = "planning"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"            # User-initiated pause between phases
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @classmethod
    def terminal_states(cls) -> frozenset["BareMetalDeploymentStatus"]:
        return frozenset({cls.COMPLETED, cls.FAILED, cls.CANCELLED})


# ---------------------------------------------------------------------------
# DeploymentStepStatus — lifecycle of a single deployment step
# ---------------------------------------------------------------------------

class DeploymentStepStatus(StrEnum):
    """Status values for ``DeploymentStep.status``."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"          # Probe detected already done


# ---------------------------------------------------------------------------
# DeploymentPhase — the four deployment phases
# ---------------------------------------------------------------------------

class DeploymentPhase(StrEnum):
    """Phase identifiers for the four-phase deployment."""

    PHASE_1_DPU = "phase_1_dpu"
    PHASE_2_K8S = "phase_2_k8s"
    PHASE_3_JOIN = "phase_3_join"
    PHASE_4_PLATFORM = "phase_4_platform"


# ---------------------------------------------------------------------------
# HostAccessTier — BMC/IPMI capability tier
# ---------------------------------------------------------------------------

class HostAccessTier(StrEnum):
    """BMC access tier detected during discovery."""

    NONE = "none"          # No BMC access
    IPMI_ONLY = "ipmi"     # IPMI LAN reachable, no Redfish
    REDFISH = "redfish"    # Full Redfish available


# ---------------------------------------------------------------------------
# ProxyMigrationStatus — lifecycle of a proxy-to-BNK migration run (D-021 P3)
# ---------------------------------------------------------------------------

class ProxyMigrationStatus(StrEnum):
    """Status values for ``ProxyMigration.status``.

    State machine:
      PENDING → IN_PROGRESS → AWAITING_VERIFY (deploy done) →
        VERIFY_FAILED (hard stop, old proxy serving) |
        AWAITING_CUTOVER (verify passed, operator must confirm DNS/VIP) →
          CUTOVER_CONFIRMED → AWAITING_TEARDOWN → COMPLETED
      Any pre-cutover state → ABORTED (operator abort, NEW deleted, OLD serving)
      Post-cutover → ABORTED (manual fallback note, no auto-rollback possible)
      Any step failure → FAILED
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_VERIFY = "awaiting_verify"
    VERIFY_FAILED = "verify_failed"
    AWAITING_CUTOVER = "awaiting_cutover"
    CUTOVER_CONFIRMED = "cutover_confirmed"
    AWAITING_TEARDOWN = "awaiting_teardown"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"
    ROLLED_BACK = "rolled_back"

    @classmethod
    def terminal_states(cls) -> frozenset["ProxyMigrationStatus"]:
        """States from which no further automatic transitions occur.

        VERIFY_FAILED is intentionally excluded: it is pre-cutover, so
        abort_migration() must be able to delete the deployed BNK manifest
        and transition to ROLLED_BACK.  Including it here would make the
        deployed BNK gateway permanently uncleanable via forge.
        """
        return frozenset({
            cls.COMPLETED,
            cls.FAILED,
            cls.ABORTED,
            cls.ROLLED_BACK,
        })


# ---------------------------------------------------------------------------
# MigrationStepStatus — lifecycle of a single migration step (D-021 P3)
# ---------------------------------------------------------------------------

class MigrationStepStatus(StrEnum):
    """Status values for ``ProxyMigrationStep.status``."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"          # Blocked until prerequisite passes (e.g. verify gate)
    AWAITING_CONFIRM = "awaiting_confirm"  # Operator must confirm before proceeding


# ---------------------------------------------------------------------------
# ReleaseSourceType — provenance of a BnkRelease row
# ---------------------------------------------------------------------------

class ReleaseSourceType(StrEnum):
    """How the BnkRelease row was populated."""

    CLOUDDOCS = "clouddocs"  # F5 clouddocs release notes / support matrix
    OCI = "oci"              # Derived from live OCI registry tag observation
    OBSERVED = "observed"    # Observed on a live cluster (flo_version + manifest_version)
    MANUAL = "manual"        # Hand-entered by an admin


# ---------------------------------------------------------------------------
# ReleaseSourceKind — the kind of a first-class ReleaseSource entity (ADR-494)
# DISTINCT from ReleaseSourceType above, which tracks provenance of BnkRelease rows.
# ---------------------------------------------------------------------------

class ReleaseSourceKind(StrEnum):
    """The transport kind of a ReleaseSource — where the Catalog syncs releases from."""

    OCI = "oci"        # OCI registry (repo.f5.com or compatible)
    MIRROR = "mirror"  # Air-gapped mirror / proxy registry
    MANUAL = "manual"  # No sync; releases are hand-entered by an admin


# ---------------------------------------------------------------------------
# Module-level convenience constants
# ---------------------------------------------------------------------------

MODULE_TERMINAL_STATES: frozenset[ModuleStatus] = ModuleStatus.terminal_states()
TASK_TERMINAL_STATES: frozenset[TaskStatus] = TaskStatus.terminal_states()
BENCHMARK_RUN_TERMINAL_STATES: frozenset[BenchmarkRunStatus] = BenchmarkRunStatus.terminal_states()
BENCHMARK_AGENT_TERMINAL_STATES: frozenset[BenchmarkAgentStatus] = BenchmarkAgentStatus.terminal_states()
PROXY_DEPLOYMENT_TERMINAL_STATES: frozenset[ProxyDeploymentStatus] = ProxyDeploymentStatus.terminal_states()
BARE_METAL_DEPLOYMENT_TERMINAL_STATES: frozenset[BareMetalDeploymentStatus] = BareMetalDeploymentStatus.terminal_states()
