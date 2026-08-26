/**
 * Fleet Dashboard types (UX-012).
 *
 * Mirrors backend Pydantic models from routes/operators.py fleet endpoints.
 */

export type FleetBnkSeverity =
  | 'healthy'
  | 'degraded'
  | 'unhealthy'
  | 'unknown'
  // Legacy aliases accepted during transition
  | 'warning'
  | 'critical';

export type FleetOperatorStatus = FleetBnkSeverity | 'offline';

export interface FleetOperatorHealthSummary {
  healthy: number;
  warning: number;
  critical: number;
}

export interface FleetHealthIssue {
  component: string;
  severity: FleetBnkSeverity;
  message: string;
}

export interface FleetOperatorHealth {
  operator_id: number;
  operator_uuid: string;
  cluster_id: number;
  cluster_name: string;
  status: FleetOperatorStatus;
  bnk_severity?: FleetBnkSeverity;
  effective_connectivity_status?: 'connected' | 'reachable' | 'partial' | 'unreachable' | 'unknown';
  last_seen: string | null;
  bnk_version: string | null;
  route_count: number;
  tmm_count: number;
  gateway_count: number;
  uptime: string;
  health_summary: FleetOperatorHealthSummary;
  health_issues?: FleetHealthIssue[];
  kubernetes_version: string | null;
  node_count: number | null;
  operator_version: string | null;
  connectivity_mode: string;
  // DPF (NVIDIA DPU) fields — P5 fleet integration
  dpf_detected?: boolean;
  dpf_version?: string | null;
  dpf_status?: string | null;  // "ready" | "partial" | "not_installed"
  dpu_count?: number;
  dpu_cluster_count?: number;
  detected_platform_profile?: import('./platform').PlatformProfile;
  detected_platform_provider?: import('./platform').PlatformProvider | null;
  cloud_provider?: string | null;
  region?: string | null;
  account_id?: string | null;
  discovery_status?: string | null;
}

export interface FleetComparePlatformContext {
  cluster_a: {
    name: string;
    detected_platform_profile: import('./platform').PlatformProfile;
    detected_platform_provider?: import('./platform').PlatformProvider | null;
    platform_capabilities?: import('./platform').PlatformCapabilities;
    platform_constraints?: import('./platform').PlatformConstraints;
  };
  cluster_b: {
    name: string;
    detected_platform_profile: import('./platform').PlatformProfile;
    detected_platform_provider?: import('./platform').PlatformProvider | null;
    platform_capabilities?: import('./platform').PlatformCapabilities;
    platform_constraints?: import('./platform').PlatformConstraints;
  };
  mixed_platform_profiles: boolean;
  comparison_caveats: string[];
  support_semantics: string[];
}

export interface FleetHealthResponse {
  total_clusters: number;
  healthy: number;
  warning: number;
  critical: number;
  offline: number;
  unknown?: number;
  operators: FleetOperatorHealth[];
  platform_context?: {
    mixed_platform_profiles: boolean;
    detected_profiles: import('./platform').PlatformProfile[];
    clusters: Array<{
      cluster_id: number;
      cluster_name: string;
      detected_platform_profile: import('./platform').PlatformProfile;
      detected_platform_provider?: import('./platform').PlatformProvider | null;
    }>;
    comparison_caveats: string[];
    support_semantics: string[];
  };
}

export interface FleetCompareRequest {
  operator_a_id: number;
  operator_b_id: number;
}

/** When cluster-based diff is available */
export interface FleetCompareClusterResult {
  operator_a: string;
  operator_b: string;
  comparison_mode: 'cluster_config';
  summary: string;
  total_diffs: number;
  cluster_a: string;
  cluster_b: string;
  resources: {
    only_in_a: Array<{ kind: string; name: string; namespace?: string }>;
    only_in_b: Array<{ kind: string; name: string; namespace?: string }>;
    changed: Array<{
      resource: string;
      kind: string;
      spec_a: Record<string, unknown>;
      spec_b: Record<string, unknown>;
    }>;
  };
  modules: {
    only_in_a: string[];
    only_in_b: string[];
    changed: Array<{
      module_path: string;
      variables_a: Record<string, unknown>;
      variables_b: Record<string, unknown>;
    }>;
  };
  platform_context?: FleetComparePlatformContext | null;
}

/** When falling back to health report comparison */
export interface FleetCompareHealthResult {
  operator_a: string;
  operator_b: string;
  comparison_mode: 'health_report';
  total_diffs: number;
  summary: string;
  differences: Array<{
    field: string;
    key: string;
    value_a: unknown;
    value_b: unknown;
  }>;
  platform_context?: FleetComparePlatformContext | null;
}

export type FleetCompareResult = FleetCompareClusterResult | FleetCompareHealthResult;

// ============================================================================
// Fleet Members — D-022 host-inclusive inventory
// ============================================================================

/** Unified lifecycle state derived from each target's existing status (D-022 P4).
 * NULL until first reconcile pass — treat NULL as 'unknown'.
 * 'draining' is in vocab for forward-compat; no source emits it in P4. */
export type FleetLifecycleState =
  | 'provisioning'
  | 'active'
  | 'draining'
  | 'decommissioned'
  | 'unknown'
  | null;

/** A single fleet membership record (cluster, bare-metal host, or DPU). */
export interface FleetMember {
  id: number;
  project_id: number | null;
  member_type: 'cluster' | 'bare-metal-host' | 'dpu' | string;
  member_id: number;
  name: string;
  /** Auto-detected labels (written by reconcile, never overwritten by operator). */
  discovered_labels: Record<string, string>;
  /** Operator-set labels from the controlled vocabulary. */
  assigned_labels: Record<string, string>;
  fault_domain: string | null;
  health_status: string | null;
  /** Derived unified lifecycle state (provisioning|active|draining|decommissioned|unknown). */
  lifecycle_state: FleetLifecycleState;
  last_reconciled_at: string | null;
}

export interface FleetMembersResponse {
  members: FleetMember[];
  total: number;
}

export interface FleetMembersGroupedResponse {
  groups: Record<string, FleetMember[]>;
  total: number;
  group_by: string;
}

export type FleetMembersResult = FleetMembersResponse | FleetMembersGroupedResponse;

/** Type guard: is this a grouped response? */
export function isGroupedMembersResponse(
  r: FleetMembersResult
): r is FleetMembersGroupedResponse {
  return 'group_by' in r;
}

// ============================================================================
// Fleet Targeting + Bulk-Ops — D-022 Phase 2
// ============================================================================

/** A named label-selector target (UI label: "Fleet"). */
export interface FleetTarget {
  id: number;
  project_id: number | null;
  name: string;
  description: string | null;
  selector: { labels?: string[] };
  pinned_member_ids: number[] | null;
  created_by: string | null;
  created_at: string | null;
}

/** An immutable resolved member-id snapshot (blast-radius preview). */
export interface FleetDecision {
  id: number;
  target_id: number;
  project_id: number | null;
  selector_snapshot: { labels?: string[] };
  resolved_member_ids: number[];
  member_count: number;
  resolved_at: string;
  resolved_by: string | null;
  status: 'resolved' | 'consumed' | 'superseded';
  /** Enriched member list — present in blast-radius preview responses. */
  members: FleetMember[] | null;
}

/** Per-member result for a single bulk-run wave step. */
export interface FleetBulkRunResult {
  id: number;
  run_id: number;
  fleet_member_id: number;
  wave_index: number;
  fault_domain: string | null;
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'skipped';
  detail: string | null;
  started_at: string | null;
  completed_at: string | null;
}

/** Parent bulk-operation run record. */
export interface FleetBulkRun {
  id: number;
  decision_id: number;
  project_id: number | null;
  action: string;
  action_params: Record<string, unknown> | null;
  wave_by: string;
  concurrency: number;
  gate_mode: 'manual' | 'health_stable' | 'approval' | 'timed_wait';
  status: 'pending' | 'running' | 'paused_gate' | 'completed' | 'failed' | 'cancelled';
  current_wave_index: number;
  total_waves: number | null;
  celery_task_id: string | null;
  error_message: string | null;
  /** D-022 P6 Slice D: FK to the strategy used for this run (null if no strategy). */
  strategy_id: number | null;
  /** D-022 P6 Slice D: ISO-8601 datetime when timed_wait gate auto-resumes. */
  gate_resumes_at: string | null;
  created_by: string | null;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  results: FleetBulkRunResult[] | null;
}

export interface CreateTargetRequest {
  name: string;
  selector: { labels: string[] };
  description?: string;
  pinned_member_ids?: number[];
  project_id?: number;
}

export interface UpdateTargetRequest {
  name?: string;
  description?: string;
  selector?: { labels: string[] };
  pinned_member_ids?: number[];
}

export interface CreateBulkRunRequest {
  decision_id: number;
  action: string;
  action_params?: Record<string, unknown>;
  concurrency?: number;
  gate_mode?: 'manual' | 'health_stable';
  /** D-022 P6 Slice D: optional strategy to snapshot onto the run. */
  strategy_id?: number;
}

// ============================================================================
// Phase 3: Fleet Policies + Compliance — D-022
// ============================================================================

export type PolicyKind = 'label-compliance' | 'os-compliance-seed';
export type PolicyMode = 'inform' | 'enforce';
export type ComplianceStatus = 'compliant' | 'drifted' | 'unknown';

/** Fleet policy — desired-state over a fleet Target. */
export interface FleetPolicy {
  id: number;
  project_id: number | null;
  name: string;
  target_id: number;
  kind: PolicyKind;
  mode: PolicyMode;
  desired: Record<string, unknown>;
  created_by: string | null;
  created_at: string | null;
}

/** Per-member compliance result within an evaluation. */
export interface MemberComplianceResult {
  member_id: number;
  status: ComplianceStatus;
  detail: string | null;
}

/** Immutable evaluation snapshot. */
export interface PolicyEvaluation {
  id: number;
  policy_id: number;
  project_id: number | null;
  evaluated_at: string;
  evaluated_by: string | null;
  selector_snapshot: Record<string, unknown> | null;
  desired_snapshot: Record<string, unknown>;
  compliant_count: number;
  drifted_count: number;
  unknown_count: number;
  total_count: number;
  member_results: MemberComplianceResult[];
  enforced_run_id: number | null;
  mode: PolicyMode;
  detail: string | null;
}

/** Compliance rollup for a policy — latest evaluation summary. */
export interface PolicyComplianceRollup {
  policy_id: number;
  policy_name: string;
  kind: PolicyKind;
  mode: PolicyMode;
  supports_enforce: boolean;
  compliant_count: number;
  drifted_count: number;
  unknown_count: number;
  total_count: number;
  drifted_members: MemberComplianceResult[];
  last_evaluated_at: string | null;
  last_evaluation_id: number | null;
}

export interface CreatePolicyRequest {
  name: string;
  target_id: number;
  kind: PolicyKind;
  mode: PolicyMode;
  desired: Record<string, unknown>;
  project_id?: number;
}

/** All fields optional — only supplied fields are mutated. */
export interface UpdatePolicyRequest {
  name?: string;
  kind?: PolicyKind;
  mode?: PolicyMode;
  desired?: Record<string, unknown>;
}

// ============================================================================
// D-022 P6 Slice C — ad-hoc member selection run
// ============================================================================

/** Body for POST /api/fleet/targets/{id}/run-selection. */
export interface RunSelectionRequest {
  member_ids: number[];
  action: string;
  action_params?: Record<string, unknown>;
  concurrency?: number;
  gate_mode?: 'manual' | 'health_stable';
  /** D-022 P6 Slice D: optional strategy to snapshot onto the run. */
  strategy_id?: number;
}

// ============================================================================
// D-022 P6 Slice D — Operation Strategies
// ============================================================================

export type StrategyGateKind = 'approval' | 'timed_wait' | 'health_stable';

/** Reusable operation strategy — wave/concurrency/gate template. */
export interface FleetOperationStrategy {
  id: number;
  project_id: number | null;
  name: string;
  description: string | null;
  target_id: number | null;
  wave_by: string;
  max_concurrency_pct: number;
  gate_kind: StrategyGateKind;
  gate_wait_seconds: number | null;
  created_by: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface CreateStrategyRequest {
  name: string;
  description?: string;
  target_id?: number;
  project_id?: number;
  wave_by?: string;
  max_concurrency_pct?: number;
  gate_kind?: StrategyGateKind;
  gate_wait_seconds?: number;
}

export interface UpdateStrategyRequest {
  name?: string;
  description?: string;
  target_id?: number;
  wave_by?: string;
  max_concurrency_pct?: number;
  gate_kind?: StrategyGateKind;
  gate_wait_seconds?: number;
}

// ============================================================================
// Per-fleet conformance rollup — D-022 P6 Slice B
// ============================================================================

/** worst_state values — ranking: error > drifted > provisioning > unreachable > ready > unknown */
export type WorstState = 'error' | 'drifted' | 'provisioning' | 'unreachable' | 'ready' | 'unknown';

export interface FleetLifecycleBreakdown {
  active: number;
  provisioning: number;
  draining: number;
  decommissioned: number;
  unknown: number;
}

/** Traffic-light state for a single indicator (ops, health, policy). */
export type TrafficLight = 'green' | 'amber' | 'red' | 'grey';

/** Per-fleet conformance rollup — computed on-demand from GET /api/fleet/targets/{id}/rollup. */
export interface FleetRollup {
  fleet_id: number;
  member_count: number;
  lifecycle: FleetLifecycleBreakdown;
  unreachable_count: number;
  compliant_count: number;
  total_evaluated: number;
  drift_count: number;
  policy_count: number;
  worst_state: WorstState;
  last_evaluated_at: string | null;
  /** Operations status — D-022 P6 traffic-lights */
  active_run_count: number;
  failed_run_count: number;
  last_run_status: string | null;
  ops_state: TrafficLight;
}

// ============================================================================
// D-022 P6 follow-up — Selector builder (label vocabulary + live preview)
// ============================================================================

/** A single label key with its known values across all fleet members. */
export interface LabelKeyEntry {
  key: string;
  values: string[];
}

/** Response from GET /api/fleet/label-vocabulary */
export interface LabelVocabularyResponse {
  keys: LabelKeyEntry[];
}

/** Request body for POST /api/fleet/preview-selector */
export interface PreviewSelectorRequest {
  selector: { labels: string[] };
  pinned_member_ids?: number[];
}

/** A lightweight member summary used in preview results. */
export interface PreviewMember {
  id: number;
  name: string;
  member_type: string;
}

/** Response from POST /api/fleet/preview-selector */
export interface PreviewSelectorResponse {
  matched_count: number;
  sample: PreviewMember[];
}

// ============================================================================

/** Type guard: is this a health-report-based comparison? */
export function isHealthReportComparison(
  result: FleetCompareResult
): result is FleetCompareHealthResult {
  return 'comparison_mode' in result && result.comparison_mode === 'health_report';
}

export function isClusterConfigComparison(
  result: FleetCompareResult
): result is FleetCompareClusterResult {
  return 'comparison_mode' in result && result.comparison_mode === 'cluster_config';
}
