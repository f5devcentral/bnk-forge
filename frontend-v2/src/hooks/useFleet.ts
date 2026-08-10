/**
 * React Query hooks for Fleet Dashboard (UX-012).
 *
 * Provides fleet health polling (30s) and config comparison mutation.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import { fleetApi } from '@/lib/api/fleet';
import { QUERY_STALE_TIME } from '@/lib/constants';

import type {
  CreateBulkRunRequest,
  CreatePolicyRequest,
  CreateStrategyRequest,
  CreateTargetRequest,
  FleetBulkRun,
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
import { queryKeys } from '@/lib/queryKeys';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

// ---------------------------------------------------------------------------
// Query Keys — re-export alias for backward compatibility (used by tests)
// ---------------------------------------------------------------------------

export const fleetKeys = queryKeys.fleet;

// Mirror of useSystem.ts's useDocumentVisibility — kept local to match the
// existing per-file pattern. Stops polling when the tab is hidden so
// background tabs don't hammer /fleet-health.
function useDocumentVisibility(): boolean {
  const [isVisible, setIsVisible] = useState(() =>
    typeof document !== 'undefined' ? !document.hidden : true
  );

  useEffect(() => {
    const handleVisibilityChange = () => {
      setIsVisible(!document.hidden);
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange);
  }, []);

  return isVisible;
}

// ---------------------------------------------------------------------------
// Fleet Health (auto-refresh 30s, paused when tab hidden)
// ---------------------------------------------------------------------------

export function useFleetHealth() {
  const isVisible = useDocumentVisibility();

  return useQuery<FleetHealthResponse>({
    queryKey: queryKeys.fleet.health(),
    queryFn: fleetApi.getFleetHealth,
    staleTime: QUERY_STALE_TIME.DEFAULT,
    refetchInterval: isVisible ? 30_000 : false,
  });
}

// ---------------------------------------------------------------------------
// Fleet Members — D-022 host-inclusive inventory
// ---------------------------------------------------------------------------

export interface UseFleetMembersParams {
  label?: string[];
  member_type?: string;
  group_by?: string;
}

export function useFleetMembers(params?: UseFleetMembersParams) {
  return useQuery<FleetMembersResult>({
    queryKey: queryKeys.fleet.members(params),
    queryFn: () => fleetApi.getFleetMembers(params),
    staleTime: QUERY_STALE_TIME.DEFAULT,
    refetchInterval: 60_000,
  });
}

// ---------------------------------------------------------------------------
// Fleet Config Comparison
// ---------------------------------------------------------------------------

export function useFleetCompare() {
  return useAppMutation<
    FleetCompareResult,
    Error,
    { operatorAId: number; operatorBId: number }
  >({
    mutationFn: ({ operatorAId, operatorBId }) =>
      fleetApi.compareFleetConfigs(operatorAId, operatorBId),
  });
}

// ---------------------------------------------------------------------------
// Fleet Targeting + Bulk-Ops — D-022 Phase 2
// ---------------------------------------------------------------------------

/** List all targets. */
export function useFleetTargets() {
  return useQuery<FleetTarget[]>({
    queryKey: queryKeys.fleet.targets(),
    queryFn: fleetApi.listFleetTargets,
    staleTime: QUERY_STALE_TIME.DEFAULT,
  });
}

/** Create a target mutation. */
export function useCreateFleetTarget() {
  return useAppMutation<FleetTarget, Error, CreateTargetRequest>({
    mutationFn: fleetApi.createFleetTarget,
  });
}

/** Update a fleet target mutation. */
export function useUpdateFleetTarget() {
  return useAppMutation<FleetTarget, Error, { targetId: number; body: UpdateTargetRequest }>({
    mutationFn: ({ targetId, body }) => fleetApi.updateFleetTarget(targetId, body),
  });
}

/** Soft-delete a fleet target mutation. */
export function useDeleteFleetTarget() {
  return useAppMutation<void, Error, number>({
    mutationFn: (targetId) => fleetApi.deleteFleetTarget(targetId),
  });
}

/** Update assigned labels for a single fleet member. */
export function useUpdateFleetMemberLabels() {
  const queryClient = useQueryClient();
  return useAppMutation<void, Error, { memberId: number; labels: Record<string, string> }>({
    mutationFn: ({ memberId, labels }) => fleetApi.updateMemberLabels(memberId, labels),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['fleet', 'members'] });
      void queryClient.invalidateQueries({ queryKey: ['fleet', 'fleet-members'] });
    },
  });
}

/** Resolve fleet members by fleet_id (server-side selector resolution). */
export function useFleetMembersByFleet(fleetId: number | null) {
  return useQuery<FleetMembersResponse>({
    queryKey: queryKeys.fleet.fleetMembers(fleetId ?? 0),
    queryFn: () => fleetApi.getFleetMembersByFleet(fleetId!),
    enabled: fleetId != null,
    staleTime: QUERY_STALE_TIME.DEFAULT,
  });
}

/** Resolve a target to a Decision (blast-radius preview). */
export function useResolveFleetTarget() {
  return useAppMutation<FleetDecision, Error, number>({
    mutationFn: (targetId) => fleetApi.resolveFleetTarget(targetId),
  });
}

/** Get a Decision (blast-radius view). */
export function useFleetDecision(decisionId: number | null) {
  return useQuery<FleetDecision>({
    queryKey: queryKeys.fleet.decision(decisionId ?? 0),
    queryFn: () => fleetApi.getFleetDecision(decisionId!),
    enabled: decisionId != null,
    staleTime: QUERY_STALE_TIME.DEFAULT,
  });
}

/** Start a bulk operation. */
export function useStartBulkOp() {
  return useAppMutation<FleetBulkRun, Error, CreateBulkRunRequest>({
    mutationFn: fleetApi.startFleetBulkOp,
  });
}

/** Get a bulk run (polls every 3s while running). */
export function useFleetBulkRun(runId: number | null) {
  const data = useQuery<FleetBulkRun>({
    queryKey: queryKeys.fleet.bulkRun(runId ?? 0),
    queryFn: () => fleetApi.getFleetBulkRun(runId!),
    enabled: runId != null,
    staleTime: 0,
    refetchInterval: (query) => {
      const run = query.state.data;
      if (!run) return false;
      const active = ['pending', 'running'].includes(run.status);
      return active ? 3_000 : false;
    },
  });
  return data;
}

/** Resume a paused bulk run. */
export function useResumeBulkRun() {
  return useAppMutation<FleetBulkRun, Error, number>({
    mutationFn: (runId) => fleetApi.resumeFleetBulkRun(runId),
  });
}

/** List bulk runs (newest-first), with optional fleet_id / status filters. */
export function useFleetBulkRuns(params?: { fleet_id?: number; status?: string }) {
  return useQuery<FleetBulkRun[]>({
    queryKey: queryKeys.fleet.bulkRuns(params),
    queryFn: () => fleetApi.listBulkRuns(params),
    staleTime: QUERY_STALE_TIME.DEFAULT,
    refetchInterval: 10_000,
  });
}

/** Cancel a non-terminal bulk run (invalidates list + single-run queries on success). */
export function useCancelBulkRun() {
  const queryClient = useQueryClient();
  return useAppMutation<FleetBulkRun, Error, number>({
    mutationFn: (runId) => fleetApi.cancelBulkRun(runId),
    onSuccess: (run) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.fleet.bulkRuns() });
      queryClient.invalidateQueries({ queryKey: queryKeys.fleet.bulkRun(run.id) });
    },
  });
}

// ---------------------------------------------------------------------------
// Fleet Policies + Compliance — D-022 Phase 3
// ---------------------------------------------------------------------------

/** List all fleet policies visible to the caller. */
export function useFleetPolicies() {
  return useQuery<FleetPolicy[]>({
    queryKey: queryKeys.fleet.policies(),
    queryFn: fleetApi.listFleetPolicies,
    staleTime: QUERY_STALE_TIME.DEFAULT,
  });
}

/** Get a single fleet policy. */
export function useFleetPolicy(policyId: number | null) {
  return useQuery<FleetPolicy>({
    queryKey: queryKeys.fleet.policy(policyId ?? 0),
    queryFn: () => fleetApi.getFleetPolicy(policyId!),
    enabled: policyId != null,
    staleTime: QUERY_STALE_TIME.DEFAULT,
  });
}

/** Create a fleet policy mutation. */
export function useCreateFleetPolicy() {
  return useAppMutation<FleetPolicy, Error, CreatePolicyRequest>({
    mutationFn: fleetApi.createFleetPolicy,
  });
}

/** Evaluate a policy (inform — reports drift, changes nothing). */
export function useEvaluateFleetPolicy() {
  return useAppMutation<PolicyEvaluation, Error, number>({
    mutationFn: (policyId) => fleetApi.evaluateFleetPolicy(policyId),
  });
}

/** Enforce a policy (remediates drifted members via gated bulk-op executor). */
export function useEnforceFleetPolicy() {
  return useAppMutation<PolicyEvaluation, Error, number>({
    mutationFn: (policyId) => fleetApi.enforceFleetPolicy(policyId),
  });
}

/** Get the latest compliance rollup for a policy. */
export function usePolicyCompliance(policyId: number | null) {
  return useQuery<PolicyComplianceRollup>({
    queryKey: queryKeys.fleet.policyCompliance(policyId ?? 0),
    queryFn: () => fleetApi.getPolicyCompliance(policyId!),
    enabled: policyId != null,
    staleTime: QUERY_STALE_TIME.DEFAULT,
  });
}

/** List all evaluation snapshots for a policy. */
export function usePolicyEvaluations(policyId: number | null) {
  return useQuery<PolicyEvaluation[]>({
    queryKey: queryKeys.fleet.policyEvaluations(policyId ?? 0),
    queryFn: () => fleetApi.listPolicyEvaluations(policyId!),
    enabled: policyId != null,
    staleTime: QUERY_STALE_TIME.DEFAULT,
  });
}

/** Update a fleet policy (PUT). Invalidates policy list + compliance. */
export function useUpdateFleetPolicy() {
  const queryClient = useQueryClient();
  return useAppMutation<FleetPolicy, Error, { policyId: number; body: UpdatePolicyRequest }>({
    mutationFn: ({ policyId, body }) => fleetApi.updateFleetPolicy(policyId, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.fleet.policies() });
    },
  });
}

/** Soft-delete a fleet policy. Invalidates policy list. */
export function useDeleteFleetPolicy() {
  const queryClient = useQueryClient();
  return useAppMutation<void, Error, number>({
    mutationFn: (policyId) => fleetApi.deleteFleetPolicy(policyId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.fleet.policies() });
    },
  });
}

// ---------------------------------------------------------------------------
// Per-fleet conformance rollup — D-022 P6 Slice B
// ---------------------------------------------------------------------------

/** Get the conformance rollup for a single fleet. */
export function useFleetRollup(fleetId: number | null) {
  return useQuery<FleetRollup>({
    queryKey: queryKeys.fleet.rollup(fleetId ?? 0),
    queryFn: () => fleetApi.getFleetRollup(fleetId!),
    enabled: fleetId != null,
    staleTime: QUERY_STALE_TIME.DEFAULT,
    refetchInterval: 60_000,
  });
}

/** Batch rollup for multiple fleets — avoids N round-trips from the Fleets list. */
export function useFleetRollups(ids: number[]) {
  return useQuery<FleetRollup[]>({
    queryKey: queryKeys.fleet.rollups(ids),
    queryFn: () => fleetApi.getFleetRollups(ids),
    enabled: ids.length > 0,
    staleTime: QUERY_STALE_TIME.DEFAULT,
    refetchInterval: 60_000,
  });
}

// ---------------------------------------------------------------------------
// D-022 P6 Slice C — ad-hoc selection run
// ---------------------------------------------------------------------------

/** Start a bulk run over a caller-specified member subset. Invalidates bulk-run list on success. */
export function useRunSelection() {
  const queryClient = useQueryClient();
  return useAppMutation<FleetBulkRun, Error, { fleetId: number; body: RunSelectionRequest }>({
    mutationFn: ({ fleetId, body }) => fleetApi.runSelection(fleetId, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.fleet.bulkRuns() });
    },
  });
}

// ---------------------------------------------------------------------------
// D-022 P6 Slice D — Operation Strategies
// ---------------------------------------------------------------------------

/** List operation strategies, optionally filtered by fleet. */
export function useStrategies(fleetId?: number) {
  return useQuery<FleetOperationStrategy[]>({
    queryKey: queryKeys.fleet.strategies(fleetId),
    queryFn: () => fleetApi.listStrategies(fleetId),
    staleTime: QUERY_STALE_TIME.DEFAULT,
  });
}

/** Create a strategy mutation. Invalidates strategy list on success. */
export function useCreateStrategy() {
  const queryClient = useQueryClient();
  return useAppMutation<FleetOperationStrategy, Error, CreateStrategyRequest>({
    mutationFn: fleetApi.createStrategy,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['fleet', 'strategies'] });
    },
  });
}

/** Update a strategy mutation. Invalidates strategy list on success. */
export function useUpdateStrategy() {
  const queryClient = useQueryClient();
  return useAppMutation<FleetOperationStrategy, Error, { strategyId: number; body: UpdateStrategyRequest }>({
    mutationFn: ({ strategyId, body }) => fleetApi.updateStrategy(strategyId, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['fleet', 'strategies'] });
    },
  });
}

/** Delete a strategy mutation. Invalidates strategy list on success. */
export function useDeleteStrategy() {
  const queryClient = useQueryClient();
  return useAppMutation<void, Error, number>({
    mutationFn: (strategyId) => fleetApi.deleteStrategy(strategyId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['fleet', 'strategies'] });
    },
  });
}

/** Re-run a terminal bulk-op. Invalidates bulk-run list on success. */
export function useRerunBulkRun() {
  const queryClient = useQueryClient();
  return useAppMutation<FleetBulkRun, Error, number>({
    mutationFn: (runId) => fleetApi.rerunBulkRun(runId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.fleet.bulkRuns() });
    },
  });
}

// ---------------------------------------------------------------------------
// D-022 P6 follow-up — Selector builder hooks
// ---------------------------------------------------------------------------

/** Fetch the label vocabulary (distinct key→values across all RBAC-visible members).
 *  Used to populate key/value dropdowns in the SelectorBuilder. */
export function useLabelVocabulary() {
  return useQuery<LabelVocabularyResponse>({
    queryKey: queryKeys.fleet.labelVocabulary(),
    queryFn: fleetApi.getLabelVocabulary,
    staleTime: QUERY_STALE_TIME.DEFAULT,
    // Vocabulary is fairly stable — refetch only on window focus, not on interval.
    refetchOnWindowFocus: true,
  });
}

/** Debounced live preview of a selector.
 *
 *  Returns ``{ data, isLoading }`` where ``data`` is the latest
 *  ``PreviewSelectorResponse`` (or null before the first successful call).
 *  Calls are debounced at 400 ms to avoid hammering the API on every keystroke.
 */
export function usePreviewSelector(request: PreviewSelectorRequest | null) {
  const [data, setData] = useState<PreviewSelectorResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const trigger = useCallback(
    (req: PreviewSelectorRequest) => {
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(async () => {
        // Abort any inflight request before starting a new one.
        if (abortRef.current) abortRef.current.abort();
        abortRef.current = new AbortController();
        setIsLoading(true);
        setError(null);
        try {
          const result = await fleetApi.previewSelector(req);
          setData(result);
        } catch (e) {
          if (e instanceof Error && e.name !== 'AbortError') {
            setError(e);
          }
        } finally {
          setIsLoading(false);
        }
      }, 400);
    },
    []
  );

  useEffect(() => {
    if (!request) {
      setData(null);
      setIsLoading(false);
      return;
    }
    trigger(request);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [
    // Depend on the serialised selector so changes in label filters re-trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    JSON.stringify(request),
    trigger,
  ]);

  return { data, isLoading, error };
}
