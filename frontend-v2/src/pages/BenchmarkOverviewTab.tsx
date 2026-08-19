/**
 * BenchmarkOverviewTab — run-first landing dashboard (default tab).
 *
 * Readiness (targets/agents/configs at a glance) + last-run-vs-baseline +
 * a compact trend sparkline + recent runs, all built from EXISTING hooks
 * (summary, targets, agents, configs, runs list, trends) — no new backend
 * endpoints. Primary CTA opens the Run benchmark wizard.
 */
import { useMemo } from 'react';
import {
  LineChart,
  Line,
  ResponsiveContainer,
  YAxis,
} from 'recharts';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { SectionCard } from '@/components/ui/section-card';
import { EmptyState } from '@/components/ui/empty-state';
import { TimeAgo } from '@/components/ui/TimeAgo';
import {
  Zap,
  Server,
  Settings2,
  ArrowRight,
  CheckCircle2,
  AlertCircle,
  Play,
  History,
  LineChart as LineChartIcon,
} from 'lucide-react';
import {
  useBenchmarkTargets,
  useBenchmarkAgents,
  useBenchmarkConfigs,
  useBenchmarkRuns,
  useBenchmarkTrends,
  useBenchmarkSummary,
} from '@/hooks/useBenchmarks';
import { StatusBadge, RegressionBadge, fmtPct } from './benchmark-utils';
import type { SetupSection } from './benchmark-runs-view';
import type { BenchmarkRun } from '@/types';

const RECENT_RUNS_LIMIT = 5;

interface BenchmarkOverviewTabProps {
  onGoToSetup: (section: SetupSection) => void;
  onGoToRun: (runId: number) => void;
  onGoToRunsList: () => void;
  onGoToTrends: () => void;
  /** Opens the Run benchmark wizard. Pass `true` to prefill from the most
   * recent completed run ("Re-run last"). */
  onOpenWizard: (reRunLast?: boolean) => void;
}

export function BenchmarkOverviewTab({
  onGoToSetup,
  onGoToRun,
  onGoToRunsList,
  onGoToTrends,
  onOpenWizard,
}: BenchmarkOverviewTabProps) {
  const { data: targetsData, isLoading: targetsLoading } = useBenchmarkTargets();
  const { data: agents, isLoading: agentsLoading } = useBenchmarkAgents();
  const { data: configs, isLoading: configsLoading } = useBenchmarkConfigs();
  const { data: summary } = useBenchmarkSummary();
  const { data: runsData, isLoading: runsLoading } = useBenchmarkRuns({
    limit: RECENT_RUNS_LIMIT,
    pollingEnabled: true,
  });
  const { data: completedRunsData } = useBenchmarkRuns({ status: 'completed', limit: 1, pollingEnabled: false });
  const hasCompletedRun = (completedRunsData?.runs?.length ?? 0) > 0;

  const targets = targetsData?.targets ?? [];
  const validatedTargets = targets.filter((t) => t.last_validated != null).length;
  const connectedAgents = (agents ?? []).filter((a) => a.status === 'connected').length;
  const totalAgents = (agents ?? []).length;
  const configCount = (configs ?? []).length;

  const recentRuns = runsData?.runs ?? [];
  const lastRun = recentRuns[0] as BenchmarkRun | undefined;

  const trendsParams = lastRun?.target_id != null
    ? { target_id: lastRun.target_id, scenario_key: lastRun.scenario_key ?? undefined }
    : undefined;
  const { data: trends, isLoading: trendsLoading } = useBenchmarkTrends(trendsParams, !!trendsParams);
  const trendPoints = useMemo(() => trends?.points ?? [], [trends]);

  const p99Data = useMemo(
    () => trendPoints.map((p, i) => ({ i, v: p.latency_p99 != null ? p.latency_p99 * 1000 : null })),
    [trendPoints],
  );
  const rpsData = useMemo(
    () => trendPoints.map((p, i) => ({ i, v: p.overall_rps })),
    [trendPoints],
  );

  const isLoading = targetsLoading || agentsLoading || configsLoading;
  const hasNothingSetUp = !isLoading && targets.length === 0 && (agents ?? []).length === 0;

  if (isLoading) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-48 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  if (hasNothingSetUp) {
    return (
      <SectionCard>
        <EmptyState
          icon={Zap}
          title="Let's get your first benchmark running"
          description="Add a target and connect a test agent on the Setup tab, then come back here to run and track results."
          action={{ label: 'Go to Setup', onClick: () => onGoToSetup('targets'), variant: 'default' }}
        />
      </SectionCard>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="text-sm text-muted-foreground">
          {summary && (
            <span>
              {summary.total_runs} total runs · {fmtPct(summary.avg_success_rate)} avg success ·{' '}
              {summary.runs_last_7d} in the last 7 days
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {hasCompletedRun && (
            <Button variant="outline" onClick={() => onOpenWizard(true)} className="gap-1.5">
              <History className="h-4 w-4" />
              Re-run last
            </Button>
          )}
          <Button onClick={() => onOpenWizard(false)} className="gap-1.5">
            <Play className="h-4 w-4" />
            Run benchmark
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Readiness card */}
        <SectionCard title="Readiness" compact>
          <div className="space-y-3">
            <ReadinessRow
              icon={Zap}
              label="Targets"
              value={`${targets.length}${targets.length > 0 ? ` (${validatedTargets} validated)` : ''}`}
              ok={targets.length > 0}
              onClick={() => onGoToSetup('targets')}
            />
            <ReadinessRow
              icon={Server}
              label="Agents"
              value={`${connectedAgents} connected${totalAgents > connectedAgents ? ` of ${totalAgents}` : ''}`}
              ok={connectedAgents > 0}
              onClick={() => onGoToSetup('agents')}
            />
            <ReadinessRow
              icon={Settings2}
              label="Configs"
              value={`${configCount}`}
              ok={configCount > 0}
              onClick={() => onGoToSetup('configs')}
            />
          </div>
        </SectionCard>

        {/* Last run card */}
        <SectionCard title="Last run" compact>
          {!lastRun ? (
            <p className="text-sm text-muted-foreground py-4 text-center">No runs yet.</p>
          ) : (
            <button
              className="w-full text-left rounded-md p-2 -m-2 hover:bg-muted/40 transition-colors"
              onClick={() => onGoToRun(lastRun.id)}
            >
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-medium text-foreground truncate">
                  {lastRun.run_label || `${lastRun.proxy} → ${lastRun.model}`}
                </p>
                <StatusBadge status={lastRun.status} />
              </div>
              <div className="flex items-center gap-2 mt-2">
                <TimeAgo dateStr={lastRun.created_at} />
                <RegressionBadge run={lastRun} />
              </div>
            </button>
          )}
        </SectionCard>

        {/* Trend sparkline card */}
        <SectionCard title="Trend" compact>
          {!lastRun ? (
            <p className="text-sm text-muted-foreground py-4 text-center">Run a benchmark to see trends.</p>
          ) : trendsLoading ? (
            <Skeleton className="h-16 w-full" />
          ) : trendPoints.length < 2 ? (
            <div className="text-center py-4">
              <LineChartIcon className="h-6 w-6 mx-auto text-muted-foreground mb-2" />
              <p className="text-xs text-muted-foreground">
                Not enough data yet — run this target a second time to see a trend.
              </p>
            </div>
          ) : (
            <button className="w-full text-left" onClick={onGoToTrends}>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <p className="text-xs text-muted-foreground mb-1">P99 latency</p>
                  <ResponsiveContainer width="100%" height={48}>
                    <LineChart data={p99Data}>
                      <YAxis hide domain={['dataMin', 'dataMax']} />
                      <Line type="monotone" dataKey="v" stroke="#ef4444" strokeWidth={2} dot={false} connectNulls />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground mb-1">RPS</p>
                  <ResponsiveContainer width="100%" height={48}>
                    <LineChart data={rpsData}>
                      <YAxis hide domain={['dataMin', 'dataMax']} />
                      <Line type="monotone" dataKey="v" stroke="#3b82f6" strokeWidth={2} dot={false} connectNulls />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </button>
          )}
        </SectionCard>

        {/* Recent runs card */}
        <SectionCard title="Recent runs" compact>
          {runsLoading ? (
            <div className="space-y-2">{[1, 2, 3].map((i) => <Skeleton key={i} className="h-8 w-full" />)}</div>
          ) : recentRuns.length === 0 ? (
            <p className="text-sm text-muted-foreground py-4 text-center">No runs yet.</p>
          ) : (
            <div className="space-y-1">
              {recentRuns.map((run) => (
                <button
                  key={run.id}
                  className="w-full flex items-center justify-between gap-2 rounded-md px-2 py-1.5 hover:bg-muted/40 transition-colors text-left"
                  onClick={() => onGoToRun(run.id)}
                >
                  <span className="text-sm text-foreground truncate flex-1">
                    {run.run_label || `${run.proxy} → ${run.model}`}
                  </span>
                  <RegressionBadge run={run} />
                  <StatusBadge status={run.status} />
                  <TimeAgo dateStr={run.created_at} />
                </button>
              ))}
              <Button variant="ghost" size="sm" className="w-full mt-1 gap-1" onClick={onGoToRunsList}>
                <History className="h-3.5 w-3.5" />
                View all runs
                <ArrowRight className="h-3.5 w-3.5" />
              </Button>
            </div>
          )}
        </SectionCard>
      </div>
    </div>
  );
}

function ReadinessRow({
  icon: Icon,
  label,
  value,
  ok,
  onClick,
}: {
  icon: typeof Zap;
  label: string;
  value: string;
  ok: boolean;
  onClick: () => void;
}) {
  return (
    <button
      className="w-full flex items-center justify-between gap-2 rounded-md px-2 py-1.5 -mx-2 hover:bg-muted/40 transition-colors text-left"
      onClick={onClick}
    >
      <span className="flex items-center gap-2 text-sm text-foreground">
        <Icon className="h-4 w-4 text-muted-foreground" />
        {label}
      </span>
      <span className="flex items-center gap-2">
        <span className="text-sm text-muted-foreground">{value}</span>
        {ok ? (
          <Badge variant="success" className="gap-1 text-[10px]"><CheckCircle2 className="h-3 w-3" />Ready</Badge>
        ) : (
          <Badge variant="warning" className="gap-1 text-[10px]"><AlertCircle className="h-3 w-3" />Set up</Badge>
        )}
      </span>
    </button>
  );
}
