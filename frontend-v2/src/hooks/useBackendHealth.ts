/**
 * Per-pod inference-backend health for one F5BigAnalyzer's monitored apps.
 *
 * Backend route subprocesses ``llmtop --once --output json`` scoped to the
 * analyzer's resolved backend services, and reshapes the result into one row
 * per pod with KV cache, queue depth, latency percentiles, and token rate.
 */
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/lib/api/client';

export type BackendKind = 'vllm' | 'sglang' | 'nim' | 'tgi' | 'lmcache' | 'unknown';

export interface BackendPodHealth {
  pod_name: string;
  namespace: string;
  backend_kind: BackendKind;
  model: string;
  status: 'healthy' | 'degraded' | 'unhealthy';
  kv_cache_used_pct: number;
  running: number;
  waiting: number;
  ttft_p95_ms: number | null;
  itl_p95_ms: number | null;
  output_tps: number;
  gpu_util_pct: number | null;
  vram_used_gb: number | null;
  vram_total_gb: number | null;
  gpu_temp_c: number | null;
}

export interface BackendHealth {
  available: boolean;
  reason?: string;
  backends: BackendPodHealth[];
  errors: Record<string, string>;
  updated_at: string;
}

export function useBackendHealth(
  clusterId: number | null | undefined,
  namespace: string | null | undefined,
  name: string | null | undefined,
  enabled = true,
) {
  return useQuery<BackendHealth>({
    queryKey: ['analyzer-backend-health', clusterId, namespace, name],
    queryFn: () =>
      apiClient
        .get<BackendHealth>(
          `/api/k8s/clusters/${clusterId}/analyzers/${namespace}/${name}/backend-health`,
        )
        .then((res) => res.data),
    enabled: enabled && !!clusterId && !!namespace && !!name,
    refetchInterval: 10000,
    staleTime: 5000,
    retry: 1,
  });
}
