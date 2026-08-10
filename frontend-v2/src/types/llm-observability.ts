/**
 * AI Gateway Observability (Tier 1: request analytics).
 *
 * Local TS types matching the backend response-shape contract in
 * `docs/superpowers/specs/…-ai-gateway-observability-design.md` §"Response shapes".
 * These are hand-written; they will be reconciled to `api-generated.ts`
 * after the backend routes land and `make openapi-types` runs.
 *
 * Every response carries the degradation envelope (`available`, `endpoint`,
 * `updated_at`, `errors`) so panels can render an unavailable state instead
 * of crashing when Loki is unreachable.
 */

/** Time-range presets mapped to Loki start/end nanosecond bounds server-side. */
export type LlmTimeRange = '1h' | '6h' | '24h' | '7d';

/** Shared degradation envelope on every observability response. */
export interface LlmEnvelope {
  available: boolean;
  endpoint: string | null;
  updated_at: string;
  errors: Record<string, string>;
}

export interface LlmStats extends LlmEnvelope {
  total_requests: number;
  success_rate: number;
  avg_latency_ms: number;
  total_tokens: number;
  total_cost: number;
  models: number;
}

export interface LlmSeriesPoint {
  ts: string;
  value: number;
}

export interface LlmSeries {
  name: string;
  points: LlmSeriesPoint[];
}

/** Metrics selectable on the histogram endpoint. */
export type LlmHistogramMetric = 'requests' | 'tokens' | 'cost' | 'models' | 'latency';

export interface LlmHistogram extends LlmEnvelope {
  metric: string;
  step_s: number;
  series: LlmSeries[];
}

export interface LlmRankingTrend {
  requests: number | null;
  tokens: number | null;
  cost: number | null;
}

export interface LlmRankingRow {
  model: string;
  provider: string;
  requests: number;
  success_rate: number;
  tokens: number;
  cost: number;
  avg_latency_ms: number;
  trend: LlmRankingTrend;
}

export interface LlmRankings extends LlmEnvelope {
  rows: LlmRankingRow[];
}

/** Provider-usage shares the histogram envelope; series are keyed by provider. */
export type LlmProviderMetric = 'cost' | 'tokens' | 'latency';
export type LlmProviderUsage = LlmHistogram;

export interface LlmLogRow {
  ts: string;
  type: string;
  message: string;
  model: string;
  latency_ms: number;
  prompt_tk: number;
  comp_tk: number;
  total_tk: number;
  cost: number;
  status: string;
  req_body: string;
  resp_body: string;
}

export interface LlmLogs extends LlmEnvelope {
  rows: LlmLogRow[];
  next_end: string | null;
}

export interface LlmFilterData extends LlmEnvelope {
  models: string[];
  statuses: string[];
}

/** Shared query params accepted by every dashboard endpoint. */
export interface LlmObservabilityParams {
  range?: LlmTimeRange;
  model?: string;
  status?: string;
}
