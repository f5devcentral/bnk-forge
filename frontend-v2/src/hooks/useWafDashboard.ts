import { useQuery } from '@tanstack/react-query';
import { wafDashboardApi, type TimeRange, type DashboardGlobalFilter } from '@/lib/api/waf-dashboard';

export const REFRESH_OPTIONS = [
  { label: 'Off',   ms: 0         },
  { label: '10s',   ms: 10_000    },
  { label: '30s',   ms: 30_000    },
  { label: '1 min', ms: 60_000    },
  { label: '5 min', ms: 300_000   },
  { label: '15 min',ms: 900_000   },
] as const;

export type RefreshIntervalMs = typeof REFRESH_OPTIONS[number]['ms'];

const STALE = 30_000; // data treated as fresh for 30s

function sharedOpts(intervalMs: number) {
  return {
    staleTime: STALE,
    refetchInterval: intervalMs > 0 ? (intervalMs as number | false) : (false as number | false),
    retry: 1,
  } as const;
}

export function useWafDashboardStatus(clusterId: number | null, intervalMs = 60_000) {
  return useQuery({
    ...sharedOpts(intervalMs),
    queryKey: ['waf-dashboard-status', clusterId],
    queryFn: () => wafDashboardApi.getStatus(clusterId!),
    enabled: clusterId !== null,
  });
}

export function useWafDashboardSummary(clusterId: number | null, timeRange: TimeRange, intervalMs = 60_000, gf?: DashboardGlobalFilter) {
  return useQuery({
    ...sharedOpts(intervalMs),
    queryKey: ['waf-dashboard-summary', clusterId, timeRange].concat([JSON.stringify(gf ?? {})] as const),
    queryFn: () => wafDashboardApi.getSummary(clusterId!, timeRange, gf),
    enabled: clusterId !== null,
  });
}

export function useWafDashboardTrend(clusterId: number | null, timeRange: TimeRange, intervalMs = 60_000, gf?: DashboardGlobalFilter) {
  return useQuery({
    ...sharedOpts(intervalMs),
    queryKey: ['waf-dashboard-trend', clusterId, timeRange].concat([JSON.stringify(gf ?? {})] as const),
    queryFn: () => wafDashboardApi.getTrend(clusterId!, timeRange, gf),
    enabled: clusterId !== null,
  });
}

export function useWafDashboardTopAttacks(clusterId: number | null, timeRange: TimeRange, intervalMs = 60_000, gf?: DashboardGlobalFilter) {
  return useQuery({
    ...sharedOpts(intervalMs),
    queryKey: ['waf-dashboard-top-attacks', clusterId, timeRange].concat([JSON.stringify(gf ?? {})] as const),
    queryFn: () => wafDashboardApi.getTopAttacks(clusterId!, timeRange, 10, gf),
    enabled: clusterId !== null,
  });
}

export function useWafDashboardTopIps(clusterId: number | null, timeRange: TimeRange, intervalMs = 60_000, gf?: DashboardGlobalFilter) {
  return useQuery({
    ...sharedOpts(intervalMs),
    queryKey: ['waf-dashboard-top-ips', clusterId, timeRange].concat([JSON.stringify(gf ?? {})] as const),
    queryFn: () => wafDashboardApi.getTopIps(clusterId!, timeRange, 10, gf),
    enabled: clusterId !== null,
  });
}

export function useWafDashboardTopUris(clusterId: number | null, timeRange: TimeRange, intervalMs = 60_000, gf?: DashboardGlobalFilter) {
  return useQuery({
    ...sharedOpts(intervalMs),
    queryKey: ['waf-dashboard-top-uris', clusterId, timeRange].concat([JSON.stringify(gf ?? {})] as const),
    queryFn: () => wafDashboardApi.getTopUris(clusterId!, timeRange, 10, gf),
    enabled: clusterId !== null,
  });
}

export function useWafDashboardTopPolicies(clusterId: number | null, timeRange: TimeRange, intervalMs = 60_000, gf?: DashboardGlobalFilter) {
  return useQuery({
    ...sharedOpts(intervalMs),
    queryKey: ['waf-dashboard-top-policies', clusterId, timeRange].concat([JSON.stringify(gf ?? {})] as const),
    queryFn: () => wafDashboardApi.getTopPolicies(clusterId!, timeRange, 10, gf),
    enabled: clusterId !== null,
  });
}

export function useWafDashboardRequestMethods(clusterId: number | null, timeRange: TimeRange, intervalMs = 60_000, gf?: DashboardGlobalFilter) {
  return useQuery({
    ...sharedOpts(intervalMs),
    queryKey: ['waf-dashboard-methods', clusterId, timeRange].concat([JSON.stringify(gf ?? {})] as const),
    queryFn: () => wafDashboardApi.getRequestMethods(clusterId!, timeRange, gf),
    enabled: clusterId !== null,
  });
}

export function useWafDashboardSeverity(clusterId: number | null, timeRange: TimeRange, intervalMs = 60_000, gf?: DashboardGlobalFilter) {
  return useQuery({
    ...sharedOpts(intervalMs),
    queryKey: ['waf-dashboard-severity', clusterId, timeRange].concat([JSON.stringify(gf ?? {})] as const),
    queryFn: () => wafDashboardApi.getSeverity(clusterId!, timeRange, gf),
    enabled: clusterId !== null,
  });
}

export function useWafDashboardTopSignatures(clusterId: number | null, timeRange: TimeRange, intervalMs = 60_000, gf?: DashboardGlobalFilter) {
  return useQuery({
    ...sharedOpts(intervalMs),
    queryKey: ['waf-dashboard-top-signatures', clusterId, timeRange].concat([JSON.stringify(gf ?? {})] as const),
    queryFn: () => wafDashboardApi.getTopSignatures(clusterId!, timeRange, 10, gf),
    enabled: clusterId !== null,
  });
}

export function useWafDashboardTopInstances(clusterId: number | null, timeRange: TimeRange, intervalMs = 60_000, gf?: DashboardGlobalFilter) {
  return useQuery({
    ...sharedOpts(intervalMs),
    queryKey: ['waf-dashboard-top-instances', clusterId, timeRange].concat([JSON.stringify(gf ?? {})] as const),
    queryFn: () => wafDashboardApi.getTopInstances(clusterId!, timeRange, 10, gf),
    enabled: clusterId !== null,
  });
}

export function useWafDashboardTopSubviolations(clusterId: number | null, namespace: string, hours: number, intervalMs = 60_000) {
  return useQuery({
    ...sharedOpts(intervalMs),
    queryKey: ['waf-dashboard-top-subviolations', clusterId, namespace, hours],
    queryFn: () => wafDashboardApi.getTopSubviolations(clusterId!, namespace, hours, 8),
    enabled: clusterId !== null,
  });
}

export function useWafDashboardTopGeolocations(clusterId: number | null, namespace: string, hours: number, intervalMs = 60_000) {
  return useQuery({
    ...sharedOpts(intervalMs),
    queryKey: ['waf-dashboard-top-geolocations', clusterId, namespace, hours],
    queryFn: () => wafDashboardApi.getTopGeolocations(clusterId!, namespace, hours, 12),
    enabled: clusterId !== null,
  });
}
