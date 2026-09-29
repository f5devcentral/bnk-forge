/**
 * Shared filter state for the AI Gateway observability pages, synced to the
 * URL search params (cluster/range/model/status/tab) so views are linkable and
 * survive reloads. Cluster defaults to the HGX cluster when present, else the
 * first cluster; the fleet aggregate is an explicit `?cluster=all`.
 */
import { useCallback, useEffect, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAllClusters } from '@/hooks/useK8sClusters';
import type { LlmClusterScope } from '@/hooks/useLlmObservability';
import type { LlmTimeRange } from '@/types/llm-observability';

const RANGES: LlmTimeRange[] = ['1h', '6h', '24h', '7d'];

export interface ObservabilityFilters {
  clusterId: LlmClusterScope;
  range: LlmTimeRange;
  model: string;
  status: string;
  tab: string;
  setClusterId: (id: number | null) => void;
  setRange: (r: LlmTimeRange) => void;
  setModel: (m: string) => void;
  setStatus: (s: string) => void;
  setTab: (t: string) => void;
  /** Params object for hooks: undefined for "all" filters. */
  queryParams: { range: LlmTimeRange; model?: string; status?: string };
}

export function useObservabilityFilters(defaultTab: string): ObservabilityFilters {
  const [params, setParams] = useSearchParams();
  const { data: clustersData } = useAllClusters();
  const clusters = useMemo(() => clustersData?.clusters ?? [], [clustersData]);

  const rangeParam = params.get('range');
  const range: LlmTimeRange = RANGES.includes(rangeParam as LlmTimeRange)
    ? (rangeParam as LlmTimeRange)
    : '1h';
  const model = params.get('model') ?? '';
  const status = params.get('status') ?? '';
  const tab = params.get('tab') ?? defaultTab;
  const clusterParam = params.get('cluster');
  const clusterId: LlmClusterScope =
    clusterParam === 'all'
      ? null
      : clusterParam && !isNaN(Number(clusterParam))
        ? Number(clusterParam)
        : undefined;

  const update = useCallback(
    (patch: Record<string, string | null>) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [k, v] of Object.entries(patch)) {
            if (v === null || v === '') next.delete(k);
            else next.set(k, v);
          }
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  // Default the cluster to HGX (or the first cluster) once clusters load.
  useEffect(() => {
    if (clusterId !== undefined || clusters.length === 0) return;
    const hgx = clusters.find(
      (c) =>
        c.name.toLowerCase().includes('hgx') ||
        c.context?.toLowerCase().includes('kubernetes-admin'),
    );
    update({ cluster: String((hgx ?? clusters[0]).id) });
  }, [clusterId, clusters, update]);

  return {
    clusterId,
    range,
    model,
    status,
    tab,
    setClusterId: (id) => update({ cluster: id === null ? 'all' : String(id) }),
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
