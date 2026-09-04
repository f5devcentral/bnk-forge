/**
 * AI Gateway Observability API (Tier 1: request analytics).
 *
 * Thin axios wrappers on the shared `apiClient` for the cluster-scoped
 * Loki-proxy endpoints under
 * `/api/k8s/clusters/{clusterId}/llm-observability/*`.
 * All params go on the query string; the backend maps `range` to Loki
 * start/end nanosecond bounds.
 */
import { apiClient } from './client';
import type {
  LlmFilterData,
  LlmHistogram,
  LlmHistogramMetric,
  LlmLogs,
  LlmObservabilityParams,
  LlmProviderMetric,
  LlmProviderUsage,
  LlmRankings,
  LlmStats,
} from '@/types/llm-observability';

const base = (clusterId?: number) =>
  clusterId ? `/api/k8s/clusters/${clusterId}/llm-observability` : `/api/k8s/llm-observability`;

export const llmObservabilityApi = {
  getStats: (clusterId?: number, params?: LlmObservabilityParams) =>
    apiClient
      .get<LlmStats>(`${base(clusterId)}/stats`, { params })
      .then((res) => res.data),

  getHistogram: (
    clusterId?: number,
    params?: LlmObservabilityParams & { metric?: LlmHistogramMetric },
  ) =>
    apiClient
      .get<LlmHistogram>(`${base(clusterId)}/histogram`, { params })
      .then((res) => res.data),

  getRankings: (clusterId?: number, params?: LlmObservabilityParams) =>
    apiClient
      .get<LlmRankings>(`${base(clusterId)}/rankings`, { params })
      .then((res) => res.data),

  getProviderUsage: (
    clusterId?: number,
    params?: LlmObservabilityParams & { metric?: LlmProviderMetric },
  ) =>
    apiClient
      .get<LlmProviderUsage>(`${base(clusterId)}/provider-usage`, { params })
      .then((res) => res.data),

  getLogs: (
    clusterId?: number,
    params?: LlmObservabilityParams & {
      limit?: number;
      end?: string;
      content_search?: string;
    },
  ) =>
    apiClient
      .get<LlmLogs>(`${base(clusterId)}/logs`, { params })
      .then((res) => res.data),

  getFilterData: (clusterId?: number, params?: { range?: LlmObservabilityParams['range'] }) =>
    apiClient
      .get<LlmFilterData>(`${base(clusterId)}/filterdata`, { params })
      .then((res) => res.data),
};
