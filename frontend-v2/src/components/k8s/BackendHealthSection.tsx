/**
 * BackendHealthSection — per-pod inference backend health (llmtop-style).
 *
 * Embedded inside each F5BigAnalyzer card. Operators see "the analyzer is
 * shifting weight toward Nova Lite" right next to "because Gemma has 88%
 * KV cache pressure and 7 in queue".
 *
 * Wiring:
 *   F5BigAnalyzer.spec.applications[]
 *     → HTTPRoute.spec.rules[].backendRefs[]
 *     → matching Service.spec.selector
 *     → Pods → /metrics scrape via K8s API server proxy (llmtop subprocess)
 *
 * The backend route subprocesses ``llmtop --once --output json`` scoped to
 * those pods and returns one row per pod. GPU columns are null until a DCGM
 * exporter is wired (intentional — Bedrock shims have no GPU).
 */

import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { Skeleton } from '@/components/ui/skeleton';
import { Cpu } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useBackendHealth, type BackendKind, type BackendPodHealth } from '@/hooks/useBackendHealth';
import { parseApiError } from '@/lib/error-handler';

// Categorical labels for backend engine kinds. Mapped to token-pure styles —
// distinct semantic tokens give each engine a recognizable color without
// hand-rolled palette classes.
const BACKEND_KIND_COLOR: Record<BackendKind, string> = {
  vllm: 'bg-info/10 text-info border-info/30',
  sglang: 'bg-primary/10 text-primary border-primary/30',
  nim: 'bg-success/10 text-success border-success/30',
  tgi: 'bg-warning/10 text-warning border-warning/30',
  lmcache: 'bg-secondary text-secondary-foreground border-border',
  unknown: 'bg-muted text-muted-foreground border-border',
};

function pressureColor(pct: number): string {
  if (pct >= 85) return 'text-destructive';
  if (pct >= 65) return 'text-warning';
  return 'text-success';
}

function fmtMs(ms: number | null): string {
  if (ms == null) return '—';
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

interface BackendHealthSectionProps {
  clusterId: number;
  analyzerNamespace: string | undefined;
  analyzerName: string | undefined;
}

export function BackendHealthSection({
  clusterId,
  analyzerNamespace,
  analyzerName,
}: BackendHealthSectionProps) {
  const { data, isLoading, isError, error } = useBackendHealth(
    clusterId,
    analyzerNamespace,
    analyzerName,
  );
  const parsedBackendError = isError ? parseApiError(error) : null;

  return (
    <div>
      <h4 className="text-sm font-medium mb-2 flex items-center gap-2">
        <Cpu className="h-4 w-4 text-primary" />
        Backend Health
        <span className="text-xs text-muted-foreground font-normal ml-1">
          per-pod LLM inference metrics (KV cache · queue · TTFT/ITL · GPU)
        </span>
      </h4>

      {isLoading && (
        <div className="border rounded-lg p-3 space-y-2">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-3/4" />
        </div>
      )}

      {isError && parsedBackendError && (
        <div className="border border-destructive/30 bg-destructive/10 rounded-lg p-3 text-xs text-destructive">
          {parsedBackendError.title}: {parsedBackendError.message}
        </div>
      )}

      {!isLoading && !isError && data && data.available === false && (
        <div className="border rounded-lg p-3 text-xs text-muted-foreground">
          Backend health unavailable: {data.reason ?? 'no resolvable backend pods'}
        </div>
      )}

      {!isLoading && !isError && data && data.available && data.backends.length === 0 && (
        <div className="border rounded-lg p-3 text-xs text-muted-foreground">
          No backend pods found for this analyzer.
        </div>
      )}

      {!isLoading && !isError && data && data.available && data.backends.length > 0 && (
        <div className="border rounded-lg overflow-hidden">
          <table className="w-full text-xs">
            <thead className="bg-muted/40">
              <tr className="text-muted-foreground">
                <th className="text-left py-2 px-3 font-medium">Pod / Backend</th>
                <th className="text-left py-2 px-3 font-medium w-[140px]">KV Cache</th>
                <th className="text-left py-2 px-3 font-medium w-[80px]">Queue</th>
                <th className="text-left py-2 px-3 font-medium w-[80px]">TTFT p99</th>
                <th className="text-left py-2 px-3 font-medium w-[80px]">ITL p99</th>
                <th className="text-left py-2 px-3 font-medium w-[80px]">Out TPS</th>
                <th className="text-left py-2 px-3 font-medium w-[140px]">GPU</th>
              </tr>
            </thead>
            <tbody>
              {data.backends.map((b) => (
                <BackendRow key={`${b.namespace}/${b.pod_name}`} b={b} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data?.available && data.backends.length > 0 && (
        <p className="text-[10px] italic text-muted-foreground mt-1">
          Source: per-pod <code>/metrics</code> via K8s API server proxy, vLLM-shape parser. Correlate with router-level p50/p95 above to identify queue-vs-backend bottlenecks.
        </p>
      )}
    </div>
  );
}

function BackendRow({ b }: { b: BackendPodHealth }) {
  const kvColor = pressureColor(b.kv_cache_used_pct);
  const gpuColor = b.gpu_util_pct != null ? pressureColor(b.gpu_util_pct) : 'text-muted-foreground';

  return (
    <tr className="border-t">
      <td className="py-2 px-3">
        <div className="flex items-center gap-2">
          <Badge variant="outline" className={cn('text-[9px]', BACKEND_KIND_COLOR[b.backend_kind])}>
            {b.backend_kind}
          </Badge>
          <div>
            <div className="font-mono text-xs truncate max-w-[200px]" title={b.pod_name}>
              {b.pod_name}
            </div>
            <div className="text-[10px] text-muted-foreground truncate max-w-[200px]">
              {b.model || 'unknown model'}
              <span
                className={cn(
                  'ml-2',
                  b.status === 'healthy'
                    ? 'text-success'
                    : b.status === 'degraded'
                      ? 'text-warning'
                      : 'text-destructive',
                )}
              >
                ● {b.status}
              </span>
            </div>
          </div>
        </div>
      </td>
      <td className="py-2 px-3">
        <div className={cn('font-semibold tabular-nums', kvColor)}>
          {b.kv_cache_used_pct}%
        </div>
        <Progress value={b.kv_cache_used_pct} className="h-1 mt-1" />
      </td>
      <td className="py-2 px-3 tabular-nums">
        <span className="font-semibold">{b.running}</span>
        <span className="text-muted-foreground"> / </span>
        <span className={cn(b.waiting > 0 ? 'text-warning font-semibold' : 'text-muted-foreground')}>
          {b.waiting}
        </span>
      </td>
      <td className="py-2 px-3 tabular-nums">
        <span
          className={cn(
            'font-semibold',
            b.ttft_p95_ms != null && b.ttft_p95_ms > 1000 ? 'text-warning' : '',
          )}
        >
          {fmtMs(b.ttft_p95_ms)}
        </span>
      </td>
      <td className="py-2 px-3 tabular-nums">
        <span
          className={cn(
            'font-semibold',
            b.itl_p95_ms != null && b.itl_p95_ms > 80 ? 'text-warning' : '',
          )}
        >
          {fmtMs(b.itl_p95_ms)}
        </span>
      </td>
      <td className="py-2 px-3 tabular-nums font-semibold">{b.output_tps}</td>
      <td className="py-2 px-3 tabular-nums">
        {b.gpu_util_pct != null ? (
          <>
            <div className={cn('font-semibold', gpuColor)}>{b.gpu_util_pct}%</div>
            <div className="text-[10px] text-muted-foreground">
              {b.vram_used_gb?.toFixed(1) ?? '—'}/{b.vram_total_gb ?? '—'}GB · {b.gpu_temp_c ?? '—'}°C
            </div>
          </>
        ) : (
          <span className="text-muted-foreground text-[10px]">N/A — no DCGM</span>
        )}
      </td>
    </tr>
  );
}
