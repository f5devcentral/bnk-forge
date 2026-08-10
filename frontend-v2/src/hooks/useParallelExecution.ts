/**
 * React Query hooks for P2-3 Parallel Execution
 *
 * Provides hooks for:
 * - Fetching execution plans
 * - Deploying/destroying all modules in parallel
 * - Tracking parallel execution progress (new: useRunProgress / useActiveRunHandle)
 * - Viewing execution history
 */

import { useState, useCallback } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import type {
  ExecutionPlan,
  DeployAllResponse,
  RunProgress,
  ParallelExecutionStatus,
  ParallelExecutionSummary
} from '@/types';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

/**
 * Fetch execution plan showing layer grouping and time estimates
 */
export function useExecutionPlan(projectId: number, enabled: boolean = true) {
  return useQuery<ExecutionPlan>({
    queryKey: ['execution-plan', projectId],
    queryFn: () => api.fetchExecutionPlan(projectId),
    enabled,
    staleTime: 10 * 1000, // 10 seconds
  });
}

/**
 * Deploy all modules in project using parallel execution
 */
export function useDeployAllModules() {
  const queryClient = useQueryClient();

  return useAppMutation<DeployAllResponse, Error, { projectId: number; parallel?: boolean }>({
    mutationFn: ({ projectId, parallel = true }) => api.deployAllModules(projectId, parallel),
    onSuccess: async (_, { projectId }) => {
      // Use refetchQueries for keys that feed polling hooks
      await queryClient.refetchQueries({ queryKey: queryKeys.modules.project.byProject(projectId) });
      // Invalidate non-polling queries
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.detail(projectId) });
      // Note: ['run-progress', ...] is self-driven via refetchInterval in useRunProgress;
      // no explicit invalidation needed here.
    },
  });
}

/**
 * Destroy all modules in project using parallel execution (reverse order)
 */
export function useDestroyAllModules() {
  const queryClient = useQueryClient();

  return useAppMutation<DeployAllResponse, Error, { projectId: number; parallel?: boolean }>({
    mutationFn: ({ projectId, parallel = true }) => api.destroyAllModules(projectId, parallel),
    onSuccess: async (_, { projectId }) => {
      await queryClient.refetchQueries({ queryKey: queryKeys.modules.project.byProject(projectId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.detail(projectId) });
      // Note: ['run-progress', ...] is self-driven via refetchInterval in useRunProgress;
      // no explicit invalidation needed here.
    },
  });
}

/**
 * Poll run progress from the new /orchestration/{run_handle} endpoint (D-001 Phase 3 S2).
 *
 * Replaces useParallelExecutionStatus for the string run_handle path.
 * Polling stops automatically when the run reaches a terminal state.
 */
export function useRunProgress(
  projectId: number,
  runHandle: string | null,
  options?: {
    enabled?: boolean;
    refetchInterval?: number | false;
  }
) {
  return useQuery<RunProgress>({
    queryKey: ['run-progress', projectId, runHandle],
    queryFn: () => api.fetchRunProgress(projectId, runHandle!),
    enabled: options?.enabled !== false && runHandle !== null,
    refetchInterval: (query) => {
      if (query.state.data?.status === 'in_progress') {
        return options?.refetchInterval ?? 2000;
      }
      return false;
    },
    staleTime: 1000, // 1 second
  });
}

/**
 * Track the active run handle for a project.
 *
 * Consumers call setActiveRunHandle(orchestrator_task_id) when a deploy/destroy
 * response arrives. The handle is held in React state (session-scoped) rather than
 * derived from the old parallel-executions list endpoint.
 *
 * Returns:
 *   - activeRunHandle: string | null
 *   - setActiveRunHandle: (handle: string | null) => void
 *   - clearActiveRunHandle: () => void
 */
export function useActiveRunHandle() {
  const [activeRunHandle, setActiveRunHandleState] = useState<string | null>(null);
  const [activeAction, setActiveActionState] = useState<'deploy' | 'destroy' | null>(null);

  const setActiveRunHandle = useCallback((handle: string | null, action?: 'deploy' | 'destroy') => {
    setActiveRunHandleState(handle);
    setActiveActionState(action ?? null);
  }, []);

  const clearActiveRunHandle = useCallback(() => {
    setActiveRunHandleState(null);
    setActiveActionState(null);
  }, []);

  return {
    activeRunHandle,
    activeAction,
    hasActiveRun: activeRunHandle !== null,
    setActiveRunHandle,
    clearActiveRunHandle,
  };
}

/**
 * Fetch real-time status of a parallel execution
 */
export function useParallelExecutionStatus(
  projectId: number,
  executionId: number | null,
  options?: {
    enabled?: boolean;
    refetchInterval?: number | false;
  }
) {
  return useQuery<ParallelExecutionStatus>({
    queryKey: ['parallel-execution-status', projectId, executionId],
    queryFn: () => api.fetchParallelExecutionStatus(projectId, executionId!),
    enabled: options?.enabled !== false && executionId !== null,
    refetchInterval: (query) => {
      // Poll every 2 seconds if execution is in progress
      if (query.state.data?.status === 'in_progress') {
        return options?.refetchInterval ?? 2000;
      }
      // Stop polling if completed/failed
      return false;
    },
    staleTime: 1000, // 1 second
  });
}

/**
 * Fetch recent parallel execution history for a project
 */
export function useParallelExecutions(projectId: number, limit: number = 10) {
  return useQuery<ParallelExecutionSummary[]>({
    queryKey: ['parallel-executions', projectId, limit],
    queryFn: () => api.fetchParallelExecutions(projectId, limit),
    staleTime: 30 * 1000, // 30 seconds
  });
}

/**
 * Hook to track active parallel execution for a project
 *
 * Returns the most recent in-progress execution, or null if none active
 */
export function useActiveParallelExecution(projectId: number) {
  const { data: executions } = useParallelExecutions(projectId, 5);

  // Find most recent in-progress execution
  const activeExecution = executions?.find((ex) => ex.status === 'in_progress');

  return {
    hasActiveExecution: !!activeExecution,
    activeExecutionId: activeExecution?.id ?? null,
    activeExecution,
  };
}
