/**
 * Fleet Dashboard API methods (UX-012 + D-022).
 */
import { apiClient } from './client';
import type {
  CreateBulkRunRequest,
  CreatePolicyRequest,
  CreateStrategyRequest,
  CreateTargetRequest,
  FleetBulkRun,
  FleetCompareRequest,
  FleetCompareResult,
  FleetDecision,
  FleetHealthResponse,
  FleetMembersResponse,
  FleetMembersResult,
  FleetOperationStrategy,
  FleetPolicy,
  FleetRollup,
  FleetTarget,
  LabelVocabularyResponse,
  PolicyComplianceRollup,
  PolicyEvaluation,
  PreviewSelectorRequest,
  PreviewSelectorResponse,
  RunSelectionRequest,
  UpdatePolicyRequest,
  UpdateStrategyRequest,
  UpdateTargetRequest,
} from '@/types/fleet';

export const fleetApi = {
  /** Get aggregate fleet health for all connected operators. */
  getFleetHealth: async (): Promise<FleetHealthResponse> => {
    const { data } = await apiClient.get<FleetHealthResponse>('/api/operators/fleet-health');
    return data;
  },

  /** Compare configs between two operators. */
  compareFleetConfigs: async (
    operatorAId: number,
    operatorBId: number
  ): Promise<FleetCompareResult> => {
    const body: FleetCompareRequest = {
      operator_a_id: operatorAId,
      operator_b_id: operatorBId,
    };
    const { data } = await apiClient.post<FleetCompareResult>(
      '/api/operators/fleet/compare',
      body
    );
    return data;
  },

  /** List fleet members (clusters + hosts + DPUs) with optional label filter + group-by. */
  getFleetMembers: async (params?: {
    label?: string[];
    member_type?: string;
    group_by?: string;
  }): Promise<FleetMembersResult> => {
    const searchParams = new URLSearchParams();
    if (params?.label) {
      params.label.forEach((l) => searchParams.append('label', l));
    }
    if (params?.member_type) searchParams.set('member_type', params.member_type);
    if (params?.group_by) searchParams.set('group_by', params.group_by);
    const qs = searchParams.toString();
    const { data } = await apiClient.get<FleetMembersResult>(
      `/api/fleet/members${qs ? `?${qs}` : ''}`
    );
    return data;
  },

  /** Update assigned labels for a fleet member. */
  updateMemberLabels: async (
    memberId: number,
    labels: Record<string, string>
  ): Promise<void> => {
    await apiClient.put(`/api/fleet/members/${memberId}/labels`, { labels });
  },

  // ---------------------------------------------------------------------------
  // Targeting + Bulk-Ops — D-022 Phase 2
  // ---------------------------------------------------------------------------

  /** Create a named label-selector target (fleet targeting, D-022 P2). */
  createFleetTarget: async (body: CreateTargetRequest): Promise<FleetTarget> => {
    const { data } = await apiClient.post<FleetTarget>('/api/fleet/targets', body);
    return data;
  },

  /** List all fleet targets visible to the caller. */
  listFleetTargets: async (): Promise<FleetTarget[]> => {
    const { data } = await apiClient.get<FleetTarget[]>('/api/fleet/targets');
    return data;
  },

  /** Update a fleet target (name/description/selector/pinned_member_ids). */
  updateFleetTarget: async (targetId: number, body: UpdateTargetRequest): Promise<FleetTarget> => {
    const { data } = await apiClient.put<FleetTarget>(`/api/fleet/targets/${targetId}`, body);
    return data;
  },

  /** Soft-delete a fleet target. */
  deleteFleetTarget: async (targetId: number): Promise<void> => {
    await apiClient.delete(`/api/fleet/targets/${targetId}`);
  },

  /** Resolve the members of a fleet server-side by its selector. */
  getFleetMembersByFleet: async (fleetId: number): Promise<FleetMembersResponse> => {
    const { data } = await apiClient.get<FleetMembersResponse>(
      `/api/fleet/fleet-members?fleet_id=${fleetId}`
    );
    return data;
  },

  /** Resolve a fleet target to a Decision (blast-radius preview). */
  resolveFleetTarget: async (targetId: number): Promise<FleetDecision> => {
    const { data } = await apiClient.post<FleetDecision>(`/api/fleet/targets/${targetId}/resolve`);
    return data;
  },

  /** Get a Fleet Decision with its enriched member list. */
  getFleetDecision: async (decisionId: number): Promise<FleetDecision> => {
    const { data } = await apiClient.get<FleetDecision>(`/api/fleet/decisions/${decisionId}`);
    return data;
  },

  /** Start a fleet bulk operation against a Decision. */
  startFleetBulkOp: async (body: CreateBulkRunRequest): Promise<FleetBulkRun> => {
    const { data } = await apiClient.post<FleetBulkRun>('/api/fleet/bulk-ops', body);
    return data;
  },

  /** Get a fleet bulk run with its per-member results. */
  getFleetBulkRun: async (runId: number): Promise<FleetBulkRun> => {
    const { data } = await apiClient.get<FleetBulkRun>(`/api/fleet/bulk-ops/${runId}`);
    return data;
  },

  /** Resume a paused fleet bulk run. */
  resumeFleetBulkRun: async (runId: number): Promise<FleetBulkRun> => {
    const { data } = await apiClient.post<FleetBulkRun>(`/api/fleet/bulk-ops/${runId}/resume`);
    return data;
  },

  /** List fleet bulk runs (newest-first), with optional fleet_id / status filters. */
  listBulkRuns: async (params?: {
    fleet_id?: number;
    status?: string;
  }): Promise<FleetBulkRun[]> => {
    const searchParams = new URLSearchParams();
    if (params?.fleet_id != null) searchParams.set('fleet_id', String(params.fleet_id));
    if (params?.status) searchParams.set('status', params.status);
    const qs = searchParams.toString();
    const { data } = await apiClient.get<FleetBulkRun[]>(
      `/api/fleet/bulk-ops${qs ? `?${qs}` : ''}`
    );
    return data;
  },

  /** Cancel a non-terminal fleet bulk run. */
  cancelBulkRun: async (runId: number): Promise<FleetBulkRun> => {
    const { data } = await apiClient.post<FleetBulkRun>(`/api/fleet/bulk-ops/${runId}/cancel`);
    return data;
  },

  // ---------------------------------------------------------------------------
  // Fleet Policies + Compliance — D-022 Phase 3
  // ---------------------------------------------------------------------------

  /** Create a fleet policy. */
  createFleetPolicy: async (body: CreatePolicyRequest): Promise<FleetPolicy> => {
    const { data } = await apiClient.post<FleetPolicy>('/api/fleet/policies', body);
    return data;
  },

  /** List fleet policies visible to the caller. */
  listFleetPolicies: async (): Promise<FleetPolicy[]> => {
    const { data } = await apiClient.get<FleetPolicy[]>('/api/fleet/policies');
    return data;
  },

  /** Get a fleet policy by ID. */
  getFleetPolicy: async (policyId: number): Promise<FleetPolicy> => {
    const { data } = await apiClient.get<FleetPolicy>(`/api/fleet/policies/${policyId}`);
    return data;
  },

  /** Evaluate a policy (inform — reports drift, changes nothing). */
  evaluateFleetPolicy: async (policyId: number): Promise<PolicyEvaluation> => {
    const { data } = await apiClient.post<PolicyEvaluation>(
      `/api/fleet/policies/${policyId}/evaluate`
    );
    return data;
  },

  /** Enforce a policy (remediates drifted members via gated bulk-op executor). */
  enforceFleetPolicy: async (policyId: number): Promise<PolicyEvaluation> => {
    const { data } = await apiClient.post<PolicyEvaluation>(
      `/api/fleet/policies/${policyId}/enforce`
    );
    return data;
  },

  /** Get the latest compliance rollup for a policy. */
  getPolicyCompliance: async (policyId: number): Promise<PolicyComplianceRollup> => {
    const { data } = await apiClient.get<PolicyComplianceRollup>(
      `/api/fleet/policies/${policyId}/compliance`
    );
    return data;
  },

  /** List all evaluation snapshots for a policy. */
  listPolicyEvaluations: async (policyId: number): Promise<PolicyEvaluation[]> => {
    const { data } = await apiClient.get<PolicyEvaluation[]>(
      `/api/fleet/policies/${policyId}/evaluations`
    );
    return data;
  },

  /** Update a fleet policy (PUT — partial update). */
  updateFleetPolicy: async (policyId: number, body: UpdatePolicyRequest): Promise<FleetPolicy> => {
    const { data } = await apiClient.put<FleetPolicy>(`/api/fleet/policies/${policyId}`, body);
    return data;
  },

  /** Soft-delete a fleet policy. */
  deleteFleetPolicy: async (policyId: number): Promise<void> => {
    await apiClient.delete(`/api/fleet/policies/${policyId}`);
  },

  // ---------------------------------------------------------------------------
  // Per-fleet conformance rollup — D-022 P6 Slice B
  // ---------------------------------------------------------------------------

  /** Get the conformance rollup for a single fleet. */
  getFleetRollup: async (fleetId: number): Promise<FleetRollup> => {
    const { data } = await apiClient.get<FleetRollup>(`/api/fleet/targets/${fleetId}/rollup`);
    return data;
  },

  /** Batch rollup for multiple fleets (avoids N round-trips from the list). */
  getFleetRollups: async (ids: number[]): Promise<FleetRollup[]> => {
    if (ids.length === 0) return [];
    const { data } = await apiClient.get<FleetRollup[]>(
      `/api/fleet/targets/rollup?ids=${ids.join(',')}`
    );
    return data;
  },

  // ---------------------------------------------------------------------------
  // D-022 P6 Slice C — ad-hoc selection run
  // ---------------------------------------------------------------------------

  /** Start a bulk run over a caller-specified subset of fleet members. */
  runSelection: async (fleetId: number, body: RunSelectionRequest): Promise<FleetBulkRun> => {
    const { data } = await apiClient.post<FleetBulkRun>(
      `/api/fleet/targets/${fleetId}/run-selection`,
      body
    );
    return data;
  },

  // ---------------------------------------------------------------------------
  // D-022 P6 Slice D — Operation Strategies
  // ---------------------------------------------------------------------------

  /** Create an operation strategy. */
  createStrategy: async (body: CreateStrategyRequest): Promise<FleetOperationStrategy> => {
    const { data } = await apiClient.post<FleetOperationStrategy>('/api/fleet/strategies', body);
    return data;
  },

  /** List operation strategies. Optional ?fleet_id= filter. */
  listStrategies: async (fleetId?: number): Promise<FleetOperationStrategy[]> => {
    const qs = fleetId != null ? `?fleet_id=${fleetId}` : '';
    const { data } = await apiClient.get<FleetOperationStrategy[]>(`/api/fleet/strategies${qs}`);
    return data;
  },

  /** Get a single strategy by ID. */
  getStrategy: async (strategyId: number): Promise<FleetOperationStrategy> => {
    const { data } = await apiClient.get<FleetOperationStrategy>(`/api/fleet/strategies/${strategyId}`);
    return data;
  },

  /** Update an operation strategy. */
  updateStrategy: async (strategyId: number, body: UpdateStrategyRequest): Promise<FleetOperationStrategy> => {
    const { data } = await apiClient.put<FleetOperationStrategy>(`/api/fleet/strategies/${strategyId}`, body);
    return data;
  },

  /** Soft-delete an operation strategy. */
  deleteStrategy: async (strategyId: number): Promise<void> => {
    await apiClient.delete(`/api/fleet/strategies/${strategyId}`);
  },

  /** Re-run a terminal bulk-op: re-resolves the target, clones action/strategy. */
  rerunBulkRun: async (runId: number): Promise<FleetBulkRun> => {
    const { data } = await apiClient.post<FleetBulkRun>(`/api/fleet/bulk-ops/${runId}/rerun`);
    return data;
  },

  // ---------------------------------------------------------------------------
  // D-022 P6 follow-up — Selector builder (label vocabulary + live preview)
  // ---------------------------------------------------------------------------

  /** Aggregate DISTINCT combined-label key→values across RBAC-visible members.
   *  Powers the selector-builder dropdowns. */
  getLabelVocabulary: async (): Promise<LabelVocabularyResponse> => {
    const { data } = await apiClient.get<LabelVocabularyResponse>('/api/fleet/label-vocabulary');
    return data;
  },

  /** Read-only dry-run: resolve selector+pinned without writing a Decision row.
   *  Returns { matched_count, sample } — use for live match preview. */
  previewSelector: async (body: PreviewSelectorRequest): Promise<PreviewSelectorResponse> => {
    const { data } = await apiClient.post<PreviewSelectorResponse>(
      '/api/fleet/preview-selector',
      body
    );
    return data;
  },
};
