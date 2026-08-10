import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { QUERY_STALE_TIME, POLL_INTERVALS } from '@/lib/constants';
import { notify, notifyError } from '@/lib/notify';
import { queryKeys } from '@/lib/queryKeys';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

// ========================================================================
// SSH Tunnel Management
// ========================================================================

export function useTunnelStatus(clusterId: number | null) {
  return useQuery({
    queryKey: queryKeys.k8s.clusters.tunnel(clusterId!),
    queryFn: () => api.getTunnelStatus(clusterId!),
    enabled: !!clusterId,
    refetchInterval: POLL_INTERVALS.SLOW,
    staleTime: QUERY_STALE_TIME.MEDIUM,
  });
}

export function useAllTunnels() {
  return useQuery({
    queryKey: queryKeys.k8s.tunnels(),
    queryFn: () => api.listAllTunnels(),
    refetchInterval: POLL_INTERVALS.SLOW,
    staleTime: QUERY_STALE_TIME.MEDIUM,
  });
}

export function useOpenTunnel() {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (clusterId: number) => api.openTunnel(clusterId),
    onSuccess: (_data, clusterId) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.tunnel(clusterId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.tunnels() });
      notify.success('SSH tunnel opened', undefined, { category: 'cluster' });
    },
    onError: (error) => notifyError(error),
  });
}

export function useCloseTunnel() {
  const queryClient = useQueryClient();
  return useAppMutation({
    mutationFn: (clusterId: number) => api.closeTunnel(clusterId),
    onSuccess: (_data, clusterId) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.clusters.tunnel(clusterId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.k8s.tunnels() });
      notify.success('SSH tunnel closed', undefined, { category: 'cluster' });
    },
    onError: (error) => notifyError(error),
  });
}
