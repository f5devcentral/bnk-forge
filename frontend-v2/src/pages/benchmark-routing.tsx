/**
 * Per-pod routing components (requests, share, prefix-cache hits) for the run detail and
 * compare views. Labels and summaries live in benchmark-labels.ts.
 */
import type { ModelServerPodStats, ModelServerStats } from '@/types';

/** Per-pod requests, share and prefix-cache hits — what the router did. */
export function RoutingTable({ stats }: { stats: ModelServerStats }) {
  const { pods, totals } = stats;
  return (
    <div className="space-y-3">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-muted-foreground border-b border-border">
            <th className="pb-2 pr-4 font-medium">Pod</th>
            <th className="pb-2 pr-4 text-right font-medium">Requests</th>
            <th className="pb-2 pr-4 text-right font-medium">Share</th>
            <th className="pb-2 font-medium w-1/2">Prefix-cache hit</th>
          </tr>
        </thead>
        <tbody>
          {pods.map((p: ModelServerPodStats) => (
            <tr key={p.pod} className="border-b border-border/60">
              <td className="py-1.5 pr-4 font-mono text-xs text-foreground/80" title={p.node ?? undefined}>
                {p.pod}
                {p.counted_from && <span className="ml-1 text-muted-foreground">(restarted)</span>}
              </td>
              <td className="py-1.5 pr-4 text-right font-mono tabular-nums">{(p.requests ?? 0).toLocaleString()}</td>
              <td className="py-1.5 pr-4 text-right font-mono tabular-nums">{p.share_pct != null ? `${p.share_pct.toFixed(0)}%` : '—'}</td>
              <td className="py-1.5">
                {p.hit_pct != null ? <HitBar pct={p.hit_pct} /> : <span className="text-muted-foreground">—</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-muted-foreground">
        {totals.load_spread != null
          ? `Load spread (busiest / quietest pod): ${totals.load_spread.toFixed(2)}×`
          : 'Some pods received no requests'}
        {totals.max_share_pct != null && ` · busiest pod took ${totals.max_share_pct.toFixed(0)}% of requests`}
        {totals.preemptions != null && ` · ${totals.preemptions.toLocaleString()} preemptions`}
        {' · from the model-server pods’ /metrics (counts include warm-up requests)'}
      </p>
    </div>
  );
}

export function HitBar({ pct, color = 'hsl(var(--primary))' }: { pct: number; color?: string }) {
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 flex-1 rounded bg-muted overflow-hidden">
        <div className="h-full rounded" style={{ width: `${Math.min(100, Math.max(0, pct))}%`, backgroundColor: color }} />
      </div>
      <span className="w-12 text-right font-mono text-xs tabular-nums">{pct.toFixed(0)}%</span>
    </div>
  );
}
