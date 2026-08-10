/**
 * Runtime-metrics hook for an F5BigAnalyzer.
 *
 * Fetches aggregated Prometheus metrics per backend for the given analyzer.
 * The backend service-proxies PromQL through the K8s API server, so no
 * external Prometheus exposure is required.
 */
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/lib/api/client';

export interface AnalyzerRuntimeMetrics {
  available: boolean;
  reason?: string;
  endpoint?: string | null;
  backends?: string[];
  per_backend?: Record<string, Record<string, number | null>>;
  errors?: Record<string, string>;
  updated_at?: string;
}

export function useAnalyzerRuntimeMetrics(
  clusterId: number | null | undefined,
  namespace: string | null | undefined,
  name: string | null | undefined,
  enabled = true,
) {
  return useQuery<AnalyzerRuntimeMetrics>({
    queryKey: ['analyzer-metrics', clusterId, namespace, name],
    queryFn: () =>
      apiClient
        .get<AnalyzerRuntimeMetrics>(
          `/api/k8s/clusters/${clusterId}/analyzers/${namespace}/${name}/runtime-metrics`,
        )
        .then((res) => res.data),
    enabled: enabled && !!clusterId && !!namespace && !!name,
    refetchInterval: 15000, // Same cadence as Prometheus scrape default
    staleTime: 10000,
    retry: 1,
  });
}
