import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { wafDashboardTabsApi } from '@/lib/api/waf-dashboard-tabs';

export function useWafDashboardTabs(clusterId: number | null) {
  return useQuery({
    queryKey: ['waf-dashboard-tabs', clusterId],
    queryFn: () => wafDashboardTabsApi.list(clusterId!),
    enabled: clusterId !== null,
    staleTime: 30_000,
    placeholderData: (prev) => prev,
  });
}

export function useCreateWafDashboardTab(clusterId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => wafDashboardTabsApi.create(clusterId, name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['waf-dashboard-tabs', clusterId] }),
  });
}

export function useRenameWafDashboardTab(clusterId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, name }: { id: number; name: string }) => wafDashboardTabsApi.rename(clusterId, id, name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['waf-dashboard-tabs', clusterId] }),
  });
}

export function useDeleteWafDashboardTab(clusterId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (tabId: number) => wafDashboardTabsApi.remove(clusterId, tabId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['waf-dashboard-tabs', clusterId] });
      qc.invalidateQueries({ queryKey: ['waf-panels', clusterId] });
    },
  });
}
