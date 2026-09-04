/**
 * Shared filter state for the AI Gateway observability pages, synced to the
 * URL search params (cluster/range/model/status/tab) so views are linkable and
 * survive reloads. Cluster defaults to the HGX cluster when present, else the
 * first cluster.
 */
import { useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { LlmTimeRange } from '@/types/llm-observability';

const RANGES: LlmTimeRange[] = ['1h', '6h', '24h', '7d'];

export interface ObservabilityFilters {
  clusterId: number | undefined;
  range: LlmTimeRange;
  model: string;
  status: string;
  tab: string;
  setClusterId: (id: number | undefined) => void;
  setRange: (r: LlmTimeRange) => void;
  setModel: (m: string) => void;
  setStatus: (s: string) => void;
  setTab: (t: string) => void;
  /** Params object for hooks: undefined for "all" filters. */
  queryParams: { range: LlmTimeRange; model?: string; status?: string };
}

export function useObservabilityFilters(defaultTab: string): ObservabilityFilters {
  const [params, setParams] = useSearchParams();

  const rangeParam = params.get('range');
  const range: LlmTimeRange = RANGES.includes(rangeParam as LlmTimeRange)
    ? (rangeParam as LlmTimeRange)
    : '1h';
  const model = params.get('model') ?? '';
  const status = params.get('status') ?? '';
  const tab = params.get('tab') ?? defaultTab;
  const clusterParam = params.get('cluster');
  const clusterId = clusterParam && clusterParam !== 'all' && !isNaN(Number(clusterParam)) ? Number(clusterParam) : undefined;

  const update = useCallback(
    (patch: Record<string, string | null>) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [k, v] of Object.entries(patch)) {
            if (v === null || v === '' || v === 'all') next.delete(k);
            else next.set(k, v);
          }
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  return {
    clusterId,
    range,
    model,
    status,
    tab,
    setClusterId: (id) => update({ cluster: id != null ? String(id) : null }),
    setRange: (r) => update({ range: r }),
    setModel: (m) => update({ model: m || null }),
    setStatus: (s) => update({ status: s || null }),
    setTab: (t) => update({ tab: t }),
    queryParams: {
      range,
      ...(model ? { model } : {}),
      ...(status ? { status } : {}),
    },
  };
}
