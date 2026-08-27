"""
SQLAlchemy models for BNK-Forge database.

Split into domain-specific files for maintainability.
This barrel re-exports all models for backward compatibility --
existing `from models import X` statements continue to work unchanged.
"""

# Re-export Base for Alembic and any direct users
from database import Base

# --- Alerts ---
from models.alert import (
    AlertChannel,
    AlertHistory,
)

# --- Association tables (IMP-020) ---
from models.associations import (
    module_dependencies,
)

# --- Bare Metal (DPU Deployment) ---
from models.bare_metal import BareMetalDeployment, BareMetalHost, DeploymentStep

# --- Benchmarks (Phase 2: AI Performance Dashboard, Phase 4b: Targets) ---
from models.benchmark import (
    BenchmarkAgent,
    BenchmarkConfig,
    BenchmarkRun,
    BenchmarkRunGroup,
    BenchmarkTarget,
    ProxyDeployment,
)

# --- Blueprint source/release catalog (ARCH-EXT-002B) ---
from models.blueprint_catalog import (
    BlueprintRelease,
    BlueprintSource,
)

# --- BNK Deployable Release catalog (ADR-478) ---
from models.bnk_deployable_release import BnkDeployableRelease

# --- BNK Release Registry (issue #217) ---
from models.bnk_release import (
    BnkRelease,
)

# --- BNK Upgrade ---
from models.bnk_upgrade import (
    BnkUpgrade,
)

# --- Config Promotion (UX-013) ---
from models.config_promotion import (
    ConfigPromotion,
)

# --- Config Snapshots (UX-011) ---
from models.config_snapshot import (
    ConfigSnapshot,
)

# --- Container Registries (first-class OCI registry access) ---
from models.container_registry import ContainerRegistry

# --- Discovery (bare-metal node discovery) ---
from models.discovery import (
    DiscoveredNode,
    DiscoveryJob,
)

# --- DPU Provisioning (Blueprint 1) ---
from models.dpu import (
    BfConfTemplate,
    BluefieldSoftwareImage,
    Dpu,
    ProjectDpuSettings,
)

# --- Drift ---
from models.drift import (
    DriftCheck,
    DriftSettings,
)

# --- Status enums (IMP-010) ---
from models.enums import (
    AlertStatus,
    BareMetalDeploymentStatus,
    BareMetalTopology,
    BenchmarkAgentStatus,
    BenchmarkRunStatus,
    BenchmarkTargetStatus,
    BlueprintReleaseState,
    BlueprintValidationState,
    BnkUpgradeStatus,
    ClusterStatus,
    DeploymentPhase,
    DeploymentStatus,
    DeploymentStepStatus,
    DiscoveredNodeStatus,
    DiscoveryJobStatus,
    DriftCheckStatus,
    HostAccessTier,
    K8sResourceStatus,
    MigrationStepStatus,
    ModuleStatus,
    ModuleSyncStatus,
    ModuleTestStatus,
    OperatorCommandStatus,
    OperatorStatus,
    ParallelExecutionStatus,
    ProxyDeploymentStatus,
    ProxyMigrationStatus,
    ReleaseSourceKind,
    ReleaseSourceType,
    StackInstanceStatus,
    SyncJobStatus,
    TaskStatus,
)

# --- F5 BIG-IP (classic TMOS discovery, D-023 Phase 1) ---
from models.f5_credential import F5Credential
from models.f5_device import F5BIGIPDevice

# --- Fleet membership + label layer (D-022) ---
from models.fleet import FleetMember

# --- Fleet policy + compliance (D-022 Phase 3) ---
from models.fleet_policy import FleetPolicy, PolicyEvaluation

# --- Fleet targeting + bulk-ops (D-022 Phase 2 + P6 Slice D) ---
from models.fleet_targeting import (
    FleetBulkRun,
    FleetBulkRunResult,
    FleetDecision,
    FleetOperationStrategy,
    FleetTarget,
)

# --- Kubernetes and F5 BNK networking ---
from models.kubernetes import (
    BnkClusterConfig,
    EgressConfiguration,
    FirewallPolicy,
    K8sGateway,
    KubernetesCluster,
    SnatPool,
)

# --- Module catalog and sources ---
from models.module import (
    ModuleLibrary,
    ModuleSnapshot,
    ModuleSource,
)

# --- Operator management ---
from models.operator import (
    ConnectedOperator,
    OperatorCommandQueue,
    OperatorRegistrationToken,
)

# --- Project, modules, secrets, deployments ---
from models.project import (
    Deployment,
    DeploymentLog,
    Environment,
    ModuleStateTransition,
    Project,
    ProjectModule,
    ProjectSecret,
)
from models.project_backend import ProjectBackendConfig
from models.project_cloud import ProjectCloudConfig

# --- Project Config Extractions (IMP-014/015/016/019) ---
from models.project_encryption import ProjectEncryptionConfig
from models.project_infra import ProjectInfraConfig

# --- Proxy Migration (D-021 P3) ---
from models.proxy_migration import ProxyMigration, ProxyMigrationStep

# --- BNK Release Source (ADR-494) ---
from models.release_source import ReleaseSource

# --- SSH Credentials (first-class on-prem/bastion access) ---
from models.ssh_credential import SSHCredential

# --- Stacks ---
from models.stack import (
    StackInstance,
    StackTemplate,
)

# --- System: settings, credentials, users, audit, notifications, helm ---
from models.system import (
    ApiToken,
    ApplicationSetting,
    AuditLog,
    CloudCredentialTemplate,
    HelmChart,
    Notification,
    SyncJob,
    User,
)

# --- Tasks ---
# Note: ParallelExecution was removed in D-001 Phase 3 S3b (migration v2_121).
from models.task import (
    Task,
)

# --- Use-Case Artifacts (D-034 Phase 0 tracer) ---
from models.usecase_artifact import (
    UseCaseApplication,
    UseCaseArtifact,
    UseCaseArtifactVersion,
)

# --- Variable mappings ---
from models.variable import (
    VariableMapping,
    VariableMappingTemplate,
)

__all__ = [
    "Base",
    # enums
    "TaskStatus", "ModuleStatus", "ParallelExecutionStatus", "StackInstanceStatus",
    "DiscoveryJobStatus", "DiscoveredNodeStatus",
    "DriftCheckStatus", "BnkUpgradeStatus", "ReleaseSourceKind", "ReleaseSourceType", "ClusterStatus",
    "K8sResourceStatus", "OperatorStatus", "OperatorCommandStatus", "AlertStatus",
    "SyncJobStatus", "ModuleSyncStatus", "ModuleTestStatus", "DeploymentStatus",
    "BlueprintReleaseState", "BlueprintValidationState",
    "BareMetalDeploymentStatus", "BareMetalTopology", "DeploymentPhase", "DeploymentStepStatus", "HostAccessTier",
    "ProxyMigrationStatus", "MigrationStepStatus",
    # kubernetes
    "KubernetesCluster", "BnkClusterConfig", "K8sGateway", "FirewallPolicy", "EgressConfiguration", "SnatPool",
    # project
    "Project", "ProjectModule", "ProjectSecret", "Environment", "Deployment", "DeploymentLog",
    "ModuleStateTransition",
    # project config extractions
    "ProjectEncryptionConfig", "ProjectBackendConfig", "ProjectInfraConfig", "ProjectCloudConfig",
    # module
    "ModuleLibrary", "ModuleSource", "ModuleSnapshot",
    # variable
    "VariableMapping", "VariableMappingTemplate",
    # stack
    "StackTemplate", "StackInstance",
    # task
    "Task",
    # associations (IMP-020)
    "module_dependencies",
    # drift
    "DriftCheck", "DriftSettings",
    # F5 BIG-IP (D-023 P1)
    "F5Credential",
    "F5BIGIPDevice",
    # ssh credentials
    "SSHCredential",
    # container registries
    "ContainerRegistry",
    # system
    "ApplicationSetting", "SyncJob", "CloudCredentialTemplate", "User", "ApiToken", "AuditLog", "Notification", "HelmChart",
    # operator
    "OperatorRegistrationToken", "ConnectedOperator", "OperatorCommandQueue",
    # alert
    "AlertChannel", "AlertHistory",
    # benchmark (Phase 2 + Phase 4b + Phase 6 scenarios)
    "BenchmarkRun", "BenchmarkRunGroup", "BenchmarkConfig", "BenchmarkAgent",
    "BenchmarkTarget", "ProxyDeployment",
    "BenchmarkRunStatus", "BenchmarkAgentStatus",
    "BenchmarkTargetStatus", "ProxyDeploymentStatus",
    # bnk_release
    "BnkRelease",
    # bnk_upgrade
    "BnkUpgrade",
    # blueprint catalog
    "BlueprintSource", "BlueprintRelease",
    # config_snapshot
    "ConfigSnapshot",
    # config_promotion
    "ConfigPromotion",
    # discovery
    "DiscoveryJob", "DiscoveredNode",
    # bare metal (DPU deployment)
    "BareMetalHost", "BareMetalDeployment", "DeploymentStep", "BnkDeployableRelease",
    # release source (ADR-494)
    "ReleaseSource",
    # proxy migration (D-021 P3)
    "ProxyMigration", "ProxyMigrationStep",
    # DPU provisioning (Blueprint 1)
    "BfConfTemplate", "BluefieldSoftwareImage", "Dpu", "ProjectDpuSettings",
    # fleet membership + label layer (D-022)
    "FleetMember",
    # fleet targeting + bulk-ops (D-022 Phase 2 + P6 Slice D)
    "FleetTarget",
    "FleetDecision",
    "FleetBulkRun",
    "FleetBulkRunResult",
    "FleetOperationStrategy",
    # fleet policy + compliance (D-022 Phase 3)
    "FleetPolicy",
    "PolicyEvaluation",
    # use-case artifacts (D-034 Phase 0 tracer)
    "UseCaseArtifact", "UseCaseArtifactVersion", "UseCaseApplication",
]
