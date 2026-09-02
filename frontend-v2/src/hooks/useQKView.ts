/**
 * React Query hooks for QKView diagnostic tarball operations.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { qkviewApi } from '@/lib/api/qkview';

import type { QKViewCreateRequest, CWCAPISetupStatusResponse } from '@/types';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

export const QKVIEW_KEYS = {
  all: ['qkview'] as const,
  check: (clusterId: number) => [...QKVIEW_KEYS.all, 'check', clusterId] as const,
  cwcApiSetupStatus: (clusterId: number) => [...QKVIEW_KEYS.all, 'cwc-api-setup-status', clusterId] as const,
  list: (clusterId: number) => [...QKVIEW_KEYS.all, 'list', clusterId] as const,
  status: (clusterId: number, qkviewId: string) =>
    [...QKVIEW_KEYS.all, 'status', clusterId, qkviewId] as const,
};

/** Check if CWC QKView API is available on the cluster */
export function useQKViewCheck(clusterId: number) {
  return useQuery({
    queryKey: QKVIEW_KEYS.check(clusterId),
    queryFn: () => qkviewApi.checkAvailability(clusterId),
    enabled: clusterId > 0,
    staleTime: 60_000, // 1 minute — CWC availability doesn't change often
    placeholderData: (previousData) => previousData,
    retry: 1,
  });
}

/** List all QKView jobs on the cluster */
export function useQKViewList(clusterId: number, enabled = true) {
  return useQuery({
    queryKey: QKVIEW_KEYS.list(clusterId),
    queryFn: () => qkviewApi.listQKViews(clusterId),
    enabled: enabled && clusterId > 0,
    staleTime: 30_000,
    refetchInterval: 30_000, // Poll every 30s — each poll execs into a K8s pod
    placeholderData: (previousData) => previousData,
  });
}

/** Get status of a specific QKView */
export function useQKViewStatus(clusterId: number, qkviewId: string, enabled = true) {
  return useQuery({
    queryKey: QKVIEW_KEYS.status(clusterId, qkviewId),
    queryFn: () => qkviewApi.getQKViewStatus(qkviewId, clusterId),
    enabled: enabled && clusterId > 0 && !!qkviewId,
    staleTime: 15_000,
    refetchInterval: 15_000, // Poll every 15s while active — execs into K8s pod
    placeholderData: (previousData) => previousData,
  });
}

/** Create a new QKView diagnostic collection */
export function useCreateQKView() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (data: QKViewCreateRequest) => qkviewApi.createQKView(data),
    onSuccess: (_data, variables) => {
      // Refetch list to show the new QKView
      queryClient.invalidateQueries({ queryKey: QKVIEW_KEYS.list(variables.cluster_id) });
    },
  });
}

/** Delete a QKView */
export function useDeleteQKView() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ qkviewId, clusterId }: { qkviewId: string; clusterId: number }) =>
      qkviewApi.deleteQKView(qkviewId, clusterId),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: QKVIEW_KEYS.list(variables.clusterId) });
    },
  });
}

/** Cancel a running QKView */
export function useCancelQKView() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ qkviewId, clusterId }: { qkviewId: string; clusterId: number }) =>
      qkviewApi.cancelQKView(qkviewId, clusterId),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: QKVIEW_KEYS.list(variables.clusterId) });
    },
  });
}

/** Download a QKView tarball — triggers browser download */
export function useDownloadQKView() {
  return useAppMutation({
    mutationFn: async ({ qkviewId, clusterId, filename }: {
      qkviewId: string;
      clusterId: number;
      filename?: string;
    }) => {
      const blob = await qkviewApi.downloadQKView(qkviewId, clusterId);
      // Trigger browser download
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename || `qkview-${qkviewId}.tar.gz`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      return { success: true };
    },
  });
}

/** Check if shared CWC API mTLS setup has been completed */
export function useCWCAPISetupStatus(clusterId: number, enabled = true) {
  return useQuery<CWCAPISetupStatusResponse>({
    queryKey: QKVIEW_KEYS.cwcApiSetupStatus(clusterId),
    queryFn: () => qkviewApi.getCWCAPISetupStatus(clusterId),
    enabled: enabled && clusterId > 0,
    staleTime: 60_000,
    placeholderData: (previousData) => previousData,
    retry: 1,
  });
}

/** Run one-time cert-manager setup for shared CWC API mTLS */
export function useCWCAPISetup() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (clusterId: number) => qkviewApi.setupCWCAPI(clusterId),
    onSuccess: (_data, clusterId) => {
      // Invalidate setup status + check — both will now show "complete"
      queryClient.invalidateQueries({ queryKey: QKVIEW_KEYS.cwcApiSetupStatus(clusterId) });
      queryClient.invalidateQueries({ queryKey: QKVIEW_KEYS.check(clusterId) });
    },
  });
}

// Backward-compatible aliases during rename transition.
export const useQKViewSetupStatus = useCWCAPISetupStatus;
export const useQKViewSetup = useCWCAPISetup;
