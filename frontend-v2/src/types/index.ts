// =============================================================================
// Types – Re-export shim for backward compatibility
// =============================================================================
// All types have been split into domain-specific files under src/types/.
// This file re-exports everything so that existing `import { Foo } from '@/types'`
// statements continue to work without changes.
//
// Domain files:
//   common.ts        – ApiResponse, ApiError, JsonValue, WSMessage
//   auth.ts          – User, UserRole
//   projects.ts      – Project, CloudCredentialTemplate, SSO*, ProjectType
//   modules.ts       – ModuleLibrary, ProjectModule, PlanStatus, ModuleSyncStats
//   kubernetes.ts    – K8sCluster, K8sResource, K8sCondition, etc.
//   helm.ts          – HelmRelease, HelmChart, InstallChartRequest, etc.
//   f5bnk.ts         – F5FirewallRule, BnkUpgrade, BnkVersionInfo, etc.
//   tasks.ts         – Task, Deployment, TaskListResponse, TaskStatsResponse
//   drift.ts         – DriftSettings, DriftCheck, DriftSummary, ClusterDriftStatus
//   config.ts        – ConfigExport, ConfigImportResult, ConfigDiffResult
//   stacks.ts        – StackTemplate, StackInstance, StackInputDefinition, etc.
//   execution.ts     – ExecutionPlan, RunProgress, LayerProgress, LayerResult, ParallelExecutionStatus, DeployAllResponse
//   secrets.ts       – ProjectSecret, RequiredSecret, RequiredSecretsResponse
//   operators.ts     – ConnectedOperator, ConnectivityModeInfo, etc.
//   alerts.ts        – AlertChannel, AlertChannelCreate, AlertHistoryEntry
//   module-sources.ts – ModuleSource, RegistryModule, VariableMapping, etc.
//   fleet.ts         – FleetHealthResponse, FleetOperatorHealth, FleetCompareResult
//   bare-metal.ts    – BareMetalHost, BareMetalDeployment, DeploymentStep, BnkVersionProfile
// =============================================================================

export * from './benchmarks';
export * from './common';
export * from './auth';
export * from './projects';
export * from './modules';
export * from './kubernetes';
export * from './helm';
export * from './f5bnk';
export * from './tasks';
export * from './platform';
export * from './drift';
export * from './config';
export * from './stacks';
export * from './execution';
export * from './secrets';
export * from './operators';
export * from './alerts';
export * from './module-sources';
export * from './blueprint-catalog';
export * from './fleet';
export * from './runbooks';
export * from './licensing';
export * from './qkview';
export * from './snapshots';
export * from './config-promotion';
export * from './system';
export * from './tmm-debug';
export * from './dpf';
export * from './recovery';
export * from './backup';
export type {
  BareMetalHost,
  BareMetalHostCreate,
  BareMetalHostUpdate,
  BareMetalDeployment,
  BareMetalDeploymentCreate,
  BareMetalDeploymentResumeRequest,
  DeploymentStep,
  BareMetalDiscoveryRequest,
  BareMetalDiscoveryResponse,
  DiscoveryAssessmentCheck,
  VersionDrift,
  BnkVersionProfile,
} from './bare-metal';
export * from './waf';
