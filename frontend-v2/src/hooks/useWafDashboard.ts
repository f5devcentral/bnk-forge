import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { wafDashboardApi, type TimeRange } from '@/lib/api/waf-dashboard';

const STALE    = 30_000;  // data treated as fresh for 30s — no refetch within this window
const INTERVAL = 60_000;  // background auto-refresh every 60s

// Shared options: keep previous data visible while background-refetching so charts never flash empty
const shared = {
  staleTime: STALE,
  refetchInterval: INTERVAL,
  placeholderData: keepPreviousData, // old data shown during refetch — eliminates the 0/empty flash
  retry: 1,
};

export function useWafDashboardStatus(clusterId: number | null) {
  return useQuery({
    ...shared,
    queryKey: ['waf-dashboard-status', clusterId],
    queryFn: () => wafDashboardApi.getStatus(clusterId!),
    enabled: clusterId !== null,
  });
}

export function useWafDashboardSummary(clusterId: number | null, timeRange: TimeRange) {
  return useQuery({
    ...shared,
    queryKey: ['waf-dashboard-summary', clusterId, timeRange],
    queryFn: () => wafDashboardApi.getSummary(clusterId!, timeRange),
    enabled: clusterId !== null,
  });
}

export function useWafDashboardTrend(clusterId: number | null, timeRange: TimeRange) {
  return useQuery({
    ...shared,
    queryKey: ['waf-dashboard-trend', clusterId, timeRange],
    queryFn: () => wafDashboardApi.getTrend(clusterId!, timeRange),
    enabled: clusterId !== null,
  });
}

export function useWafDashboardTopAttacks(clusterId: number | null, timeRange: TimeRange) {
  return useQuery({
    ...shared,
    queryKey: ['waf-dashboard-top-attacks', clusterId, timeRange],
    queryFn: () => wafDashboardApi.getTopAttacks(clusterId!, timeRange),
    enabled: clusterId !== null,
  });
}

export function useWafDashboardTopIps(clusterId: number | null, timeRange: TimeRange) {
  return useQuery({
    ...shared,
    queryKey: ['waf-dashboard-top-ips', clusterId, timeRange],
    queryFn: () => wafDashboardApi.getTopIps(clusterId!, timeRange),
    enabled: clusterId !== null,
  });
}

export function useWafDashboardTopUris(clusterId: number | null, timeRange: TimeRange) {
  return useQuery({
    ...shared,
    queryKey: ['waf-dashboard-top-uris', clusterId, timeRange],
    queryFn: () => wafDashboardApi.getTopUris(clusterId!, timeRange),
    enabled: clusterId !== null,
  });
}
