/**
 * React Query hooks for operator management.
 *
 * Handles:
 *   - Connected operators (list, detail, delete, send command)
 *   - Connectivity modes
 *
 * Note: Registration token hooks (useRegistrationTokens, useCreateRegistrationToken,
 * useRevokeRegistrationToken, useGenerateInstallCommand) were removed in D3-CLEANUP.
 * The kubeconfig-first fleet architecture made them obsolete.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import { QUERY_STALE_TIME, POLL_INTERVALS } from '@/lib/constants';
import { notify } from '@/lib/notify';
import type { OperatorConnectivityMode } from '@/types';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

// ---------------------------------------------------------------------------
// Query Keys (re-export from centralized factory for backward compatibility)
// ---------------------------------------------------------------------------

export const operatorKeys = {
  all: queryKeys.operators.all,
  operators: () => queryKeys.operators.list(),
  operator: (id: string) => queryKeys.operators.detail(id),
};

// ---------------------------------------------------------------------------
// Connected Operators
// ---------------------------------------------------------------------------

export function useOperators(connectedOnly: boolean = false) {
  return useQuery({
    queryKey: [...operatorKeys.operators(), connectedOnly],
    queryFn: () => api.listOperators(connectedOnly),
    staleTime: QUERY_STALE_TIME.SHORT,
    refetchInterval: POLL_INTERVALS.MEDIUM,
  });
}

export function useOperator(operatorId: string) {
  return useQuery({
    queryKey: operatorKeys.operator(operatorId),
    queryFn: () => api.getOperator(operatorId),
    enabled: !!operatorId,
    staleTime: QUERY_STALE_TIME.SHORT,
    refetchInterval: POLL_INTERVALS.STANDARD,
  });
}

export function useDeleteOperator() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (operatorId: string) => api.deleteOperator(operatorId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: operatorKeys.operators() });
      notify.success('Operator deregistered', undefined, { category: 'cluster' });
    },
  });
}

export function useSendOperatorCommand() {
  return useAppMutation({
    mutationFn: ({
      operatorId,
      action,
      payload,
      timeout,
    }: {
      operatorId: string;
      action: string;
      payload?: Record<string, unknown>;
      timeout?: number;
    }) => api.sendOperatorCommand(operatorId, action, payload, timeout),
  });
}

export function useLinkOperatorToCluster() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ operatorId, clusterId }: { operatorId: string; clusterId: number }) =>
      api.linkOperatorToCluster(operatorId, clusterId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: operatorKeys.operators() });
    },
  });
}

export function useUnlinkOperatorFromCluster() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (operatorId: string) => api.unlinkOperatorFromCluster(operatorId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: operatorKeys.operators() });
    },
  });
}

// ---------------------------------------------------------------------------
// Connectivity Modes
// ---------------------------------------------------------------------------

export function useConnectivityModes() {
  return useQuery({
    queryKey: queryKeys.operators.connectivityModes(),
    queryFn: api.getConnectivityModes,
    staleTime: QUERY_STALE_TIME.VERY_LONG,
  });
}

export function useSetOperatorConnectivity() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({
      operatorId,
      mode,
      config,
    }: {
      operatorId: string;
      mode: string;
      config?: Record<string, unknown>;
    }) => api.setOperatorConnectivity(operatorId, mode as OperatorConnectivityMode, config),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: operatorKeys.operators() });
    },
  });
}

export function useOpenReverseTunnel() {
  return useAppMutation({
    mutationFn: (operatorId: string) => api.openReverseTunnel(operatorId),
    onSuccess: (data) => {
      notify.success('Reverse tunnel opened', data.message, { category: 'cluster' });
    },
  });
}
