/**
 * DPU↔DPU connectivity matrix card.
 *
 * The matrix renders inside the same `ConnectivityMatrix` React
 * component the Discovery tab uses — the data shape is identical, only
 * the group key differs ("dpu" instead of "builtin"/"bluefield").
 *
 * Triggered manually by a button (unlike per-DPU diagnostics which
 * piggy-back on Probe DPU OS) because a pairwise sweep across N DPUs
 * is N × N pings; we don't want it to fire automatically.
 */
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Loader2, Network } from 'lucide-react';
import { ConnectivityMatrix } from '@/components/discovery/ConnectivityMatrix';
import {
  useDpuConnectivityMatrix,
  useRunDpuConnectivityMatrix,
} from '@/hooks/useDpus';
import { notify, notifyError } from '@/lib/notify';

interface Props {
  projectId: number;
  disabled?: boolean;
}

export function DpuConnectivityMatrixCard({ projectId, disabled }: Props) {
  const { data, isLoading } = useDpuConnectivityMatrix(projectId);
  const runMut = useRunDpuConnectivityMatrix(projectId);
  const status = data?.status ?? null;
  const matrix = data?.matrix ?? null;
  const running = status === 'in_progress' || status === 'pending' || runMut.isPending;

  // Roll up endpoint + result counts so the card surfaces "0 of N pings
  // succeeded" instead of silently rendering an empty grid the operator
  // might mistake for "nothing happened". Iterate every group so this
  // works regardless of grouping ("dpu" today, more later).
  const groups = (matrix?.groups ?? {}) as Record<string, {
    endpoints?: unknown[];
    results?: Array<{ reachable?: boolean }>;
  }>;
  let endpointCount = 0;
  let resultCount = 0;
  let reachableCount = 0;
  for (const g of Object.values(groups)) {
    endpointCount += (g.endpoints ?? []).length;
    const rs = g.results ?? [];
    resultCount += rs.length;
    reachableCount += rs.filter((r) => r.reachable === true).length;
  }
  const allFailed = status === 'completed' && endpointCount > 0 && resultCount === 0;

  const triggerRun = async () => {
    try {
      await runMut.mutateAsync();
      notify.info('DPU connectivity matrix queued');
    } catch (err) {
      notifyError(err, 'Failed to start DPU connectivity matrix');
    }
  };

  return (
    <Card className="mt-4">
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div className="space-y-1">
          <CardTitle className="flex items-center gap-2">
            <Network className="h-4 w-4" />
            DPU connectivity matrix
          </CardTitle>
          <CardDescription>
            Pairwise ping across every probed DPU's VLAN data-plane
            addresses (external/internal/extras). Manual trigger —
            N × N SSH-driven pings.
          </CardDescription>
          {data?.run_at && (
            <p className="text-xs text-muted-foreground">
              Last run: {new Date(data.run_at).toLocaleString()}
              {status === 'completed' && (
                <> · {reachableCount} / {resultCount} pings reachable across {endpointCount} endpoints</>
              )}
            </p>
          )}
        </div>
        <Button
          onClick={triggerRun}
          disabled={disabled || running}
          size="sm"
          variant="outline"
        >
          {running ? (
            <>
              <Loader2 className="h-3 w-3 mr-1 animate-spin" />
              Running…
            </>
          ) : (
            <>
              <Network className="h-3 w-3 mr-1" />
              Run matrix
            </>
          )}
        </Button>
      </CardHeader>
      <CardContent>
        {!data && isLoading && (
          <p className="text-sm text-muted-foreground italic">Loading…</p>
        )}
        {data && status === null && !runMut.isPending && (
          <p className="text-sm text-muted-foreground italic">
            Not yet run for this project.
          </p>
        )}
        {status === 'skipped' && (
          <p className="text-sm text-muted-foreground italic">
            Skipped — fewer than two DPUs have a reachable OS.
            {matrix?.note ? ` (${matrix.note})` : null}
          </p>
        )}
        {allFailed && (
          <div className="text-xs text-warning bg-warning/10 border border-warning/20 rounded p-3 mb-3">
            <p className="font-medium mb-1">All pairwise pings failed.</p>
            <p>
              The matrix uses each DPU's <span className="font-mono">dpu_os_ip</span> as
              the SSH source. For in-band DPUs that's
              <span className="font-mono"> 192.168.100.2</span> (the rshim tmfifo
              loopback) which isn't reachable from the bnk-forge backend
              container — SSH has to be tunneled through the host. Until
              that path is wired up, every source will time out and the
              matrix will be empty.
            </p>
          </div>
        )}
        <ConnectivityMatrix
          status={
            status === 'skipped'
              ? null
              : (status as 'pending' | 'in_progress' | 'completed' | 'failed' | null)
          }
          matrix={matrix as Parameters<typeof ConnectivityMatrix>[0]['matrix']}
        />
      </CardContent>
    </Card>
  );
}
