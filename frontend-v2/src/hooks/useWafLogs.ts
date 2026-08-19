import { useQuery } from '@tanstack/react-query';
import { wafLogsApi, type WafLogsParams } from '@/lib/api/waf-logs';

const REFETCH_INTERVAL_MS = 30_000;

export function useWafLogs(
  clusterId: number,
  params: WafLogsParams,
  enabled = true,
) {
  return useQuery({
    queryKey: ['waf-security-logs', clusterId, params],
    queryFn: () => wafLogsApi.getSecurityLogs(clusterId, params),
    enabled,
    refetchInterval: REFETCH_INTERVAL_MS,
    staleTime: REFETCH_INTERVAL_MS,
    retry: 1,
  });
}
