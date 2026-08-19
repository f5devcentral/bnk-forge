/**
 * React Query hooks for AI Gateway Observability (Tier 1: request analytics).
 *
 * All hooks are keyed by cluster + range + model + status so switching any
 * filter fetches a fresh slice. Dashboard hooks are `enabled`-gated by the
 * caller (active tab) so inactive tabs don't hit Loki. The logs hook polls
 * every 10s when Live is on; React Query's default
 * `refetchIntervalInBackground: false` pauses that poll while the tab is
 * unfocused. No mutation feeds any poller, so plain `refetchInterval` is safe.
 */
import { useQuery } from '@tanstack/react-query';
import { llmObservabilityApi } from '@/lib/api/llmObservability';
import { queryKeys } from '@/lib/queryKeys';
import { POLL_INTERVALS } from '@/lib/constants';
import type {
  LlmHistogramMetric,
  LlmObservabilityParams,
  LlmProviderMetric,
} from '@/types/llm-observability';

/** Build a plain string param map for stable query keys (drops undefined). */
function keyParams(params: Record<string, string | number | undefined>): Record<string, string | undefined> {
  const out: Record<string, string | undefined> = {};
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined) out[k] = String(v);
  }
  return out;
}

export function useLlmStats(
  clusterId: number | undefined,
  params: LlmObservabilityParams,
  enabled = true,
) {
  return useQuery({
    queryKey: queryKeys.llmObservability.stats(clusterId ?? 0, keyParams({ ...params })),
    queryFn: () => llmObservabilityApi.getStats(clusterId!, params),
    enabled: enabled && !!clusterId,
    placeholderData: (prev) => prev,
  });
}

export function useLlmHistogram(
  clusterId: number | undefined,
  params: LlmObservabilityParams & { metric: LlmHistogramMetric },
  enabled = true,
) {
  return useQuery({
    queryKey: queryKeys.llmObservability.histogram(clusterId ?? 0, keyParams({ ...params })),
    queryFn: () => llmObservabilityApi.getHistogram(clusterId!, params),
    enabled: enabled && !!clusterId,
    placeholderData: (prev) => prev,
  });
}

export function useLlmRankings(
  clusterId: number | undefined,
  params: LlmObservabilityParams,
  enabled = true,
) {
  return useQuery({
    queryKey: queryKeys.llmObservability.rankings(clusterId ?? 0, keyParams({ ...params })),
    queryFn: () => llmObservabilityApi.getRankings(clusterId!, params),
    enabled: enabled && !!clusterId,
    placeholderData: (prev) => prev,
  });
}

export function useLlmProviderUsage(
  clusterId: number | undefined,
  params: LlmObservabilityParams & { metric: LlmProviderMetric },
  enabled = true,
) {
  return useQuery({
    queryKey: queryKeys.llmObservability.providerUsage(clusterId ?? 0, keyParams({ ...params })),
    queryFn: () => llmObservabilityApi.getProviderUsage(clusterId!, params),
    enabled: enabled && !!clusterId,
    placeholderData: (prev) => prev,
  });
}

export function useLlmLogs(
  clusterId: number | undefined,
  params: LlmObservabilityParams & { limit?: number; end?: string; content_search?: string },
  options?: { live?: boolean; enabled?: boolean },
) {
  const enabled = options?.enabled ?? true;
  return useQuery({
    queryKey: queryKeys.llmObservability.logs(clusterId ?? 0, keyParams({ ...params })),
    queryFn: () => llmObservabilityApi.getLogs(clusterId!, params),
    enabled: enabled && !!clusterId,
    refetchInterval: options?.live ? POLL_INTERVALS.STANDARD : false,
  });
}

export function useLlmFilterData(clusterId: number | undefined, range: LlmObservabilityParams['range']) {
  return useQuery({
    queryKey: queryKeys.llmObservability.filterData(clusterId ?? 0, keyParams({ range })),
    queryFn: () => llmObservabilityApi.getFilterData(clusterId!, { range }),
    enabled: !!clusterId,
  });
}
