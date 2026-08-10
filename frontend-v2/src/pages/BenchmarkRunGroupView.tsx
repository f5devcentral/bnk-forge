/**
 * BenchmarkRunGroupView — parent aggregate + child-run table for a scenario run-group.
 *
 * A SCENARIO expands into a run-group: one parent (aggregate rollup) plus N child
 * runs (one per concurrency point / phase variant). This surfaces the parent
 * aggregate metrics and a table of the child runs. Polls while non-terminal via
 * `useRunGroup`.
 */
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Loader2 } from 'lucide-react';
import { useRunGroup } from '@/hooks/useBenchmarks';
import { StatusBadge, fmtLatency, fmtNum } from './benchmark-utils';
import type { BenchmarkRunStatus } from '@/types';

const TERMINAL = new Set(['completed', 'failed', 'cancelled']);

function MetricStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="space-y-0.5">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="text-lg font-semibold tabular-nums">{value}</p>
    </div>
  );
}

export function BenchmarkRunGroupView({ groupId }: { groupId: number }) {
  const { data: group, isLoading, isError, error } = useRunGroup(groupId);

  if (isError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Run group unavailable</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-destructive">
            Failed to load run-group results
            {error instanceof Error ? `: ${error.message}` : '.'}
          </p>
        </CardContent>
      </Card>
    );
  }

  if (isLoading || !group) {
    return (
      <Card>
        <CardHeader>
          <Skeleton className="h-6 w-48" />
        </CardHeader>
        <CardContent>
          <Skeleton className="h-32 w-full" />
        </CardContent>
      </Card>
    );
  }

  const isActive = !TERMINAL.has(group.status);
  const runs = group.runs ?? [];

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <div>
            <CardTitle className="flex items-center gap-2 text-lg">
              {group.scenario_name ?? group.scenario_key}
              {isActive && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
            </CardTitle>
            <CardDescription>
              {group.run_label ?? group.scenario_key} · {group.completed_runs}/{group.total_runs} runs complete
              {group.failed_runs > 0 && ` · ${group.failed_runs} failed`}
            </CardDescription>
          </div>
          <StatusBadge status={group.status as BenchmarkRunStatus} />
        </div>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <MetricStat label="Avg p50" value={fmtLatency(group.avg_latency_p50)} />
          <MetricStat label="Avg p99" value={fmtLatency(group.avg_latency_p99)} />
          <MetricStat label="Peak RPS" value={fmtNum(group.peak_rps)} />
          <MetricStat label="Output tokens" value={fmtNum(group.total_output_tokens, 0)} />
        </div>

        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Variant</TableHead>
                <TableHead className="text-right">Concurrency</TableHead>
                <TableHead className="text-right">p50</TableHead>
                <TableHead className="text-right">p99</TableHead>
                <TableHead className="text-right">RPS</TableHead>
                <TableHead className="text-right">Tokens/s</TableHead>
                <TableHead className="text-right">Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {runs.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="text-center text-sm text-muted-foreground">
                    No child runs yet.
                  </TableCell>
                </TableRow>
              ) : (
                runs.map((run) => (
                  <TableRow key={run.id}>
                    <TableCell className="font-medium">{run.variant_label ?? `Run #${run.id}`}</TableCell>
                    <TableCell className="text-right tabular-nums">{fmtNum(run.concurrency, 0)}</TableCell>
                    <TableCell className="text-right tabular-nums">{fmtLatency(run.latency_p50)}</TableCell>
                    <TableCell className="text-right tabular-nums">{fmtLatency(run.latency_p99)}</TableCell>
                    <TableCell className="text-right tabular-nums">{fmtNum(run.overall_rps)}</TableCell>
                    <TableCell className="text-right tabular-nums">{fmtNum(run.tokens_per_sec)}</TableCell>
                    <TableCell className="flex justify-end">
                      <StatusBadge status={run.status as BenchmarkRunStatus} />
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
}
