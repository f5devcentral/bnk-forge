import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { wafPanelsApi, type PanelCreatePayload, type TimeRange } from '@/lib/api/waf-panels';

export function useWafPanelTemplates(clusterId: number | null) {
  return useQuery({
    queryKey: ['waf-panel-templates', clusterId],
    queryFn: () => wafPanelsApi.getTemplates(clusterId!),
    enabled: clusterId !== null,
    staleTime: Infinity, // templates never change at runtime
  });
}

export function useWafPanels(clusterId: number | null, tabId?: number) {
  return useQuery({
    queryKey: ['waf-panels', clusterId, tabId],
    queryFn: () => wafPanelsApi.list(clusterId!, tabId),
    enabled: clusterId !== null,
    staleTime: 30_000,
    placeholderData: (prev) => prev,
  });
}

export function useWafPanelData(clusterId: number | null, panelId: number, timeRange: TimeRange, intervalMs = 0) {
  return useQuery({
    queryKey: ['waf-panel-data', clusterId, panelId, timeRange],
    queryFn: () => wafPanelsApi.getData(clusterId!, panelId, timeRange),
    enabled: clusterId !== null,
    staleTime: 30_000,
    refetchInterval: intervalMs > 0 ? intervalMs : false,
    placeholderData: (prev) => prev,
  });
}

export function useCreateWafPanel(clusterId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: PanelCreatePayload) => wafPanelsApi.create(clusterId, payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['waf-panels', clusterId] }),
  });
}

export function useUpdateWafPanel(clusterId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...payload }: { id: number } & Partial<PanelCreatePayload>) =>
      wafPanelsApi.update(clusterId, id, payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['waf-panels', clusterId] }),
  });
}

export function useDeleteWafPanel(clusterId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (panelId: number) => wafPanelsApi.remove(clusterId, panelId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['waf-panels', clusterId] }),
  });
}
