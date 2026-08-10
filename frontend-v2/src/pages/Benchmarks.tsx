/**
 * Benchmarks page — D-020 redesign.
 *
 * Bold heading, dismissible info callout, a quiet stepper banner, and the
 * standard tab strip. Tab bodies render in their own files; this file is
 * shell + setup-progress UI only.
 */
import { useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import { PageHeader } from '@/components/layout/PageHeader';
import { usePageRefresh } from '@/hooks/usePageRefresh';
import { Tabs, TabsContent } from '@/components/ui/tabs';
import { ResourceViewTabs } from '@/components/layout/ResourceViewTabs';
import { Button } from '@/components/ui/button';
import {
  BarChart3,
  Zap,
  GitCompare,
  Settings2,
  Activity,
  Server,
  CheckCircle2,
  Circle,
  ArrowRight,
  X,
  BookOpen,
  ChevronDown,
  ChevronRight,
} from 'lucide-react';
import { useBenchmarkTargets, useBenchmarkAgents } from '@/hooks/useBenchmarks';

import { BenchmarkTargetsTab } from './BenchmarkTargetsTab';
import { BenchmarkAgentsTab } from './BenchmarkAgentsTab';
import { BenchmarkConfigsTab } from './BenchmarkConfigsTab';
import { BenchmarkRunsTab } from './BenchmarkRunsTab';
import { BenchmarkRunDetail } from './BenchmarkRunDetail';
import { BenchmarkCompareTab } from './BenchmarkCompareTab';

interface StepState {
  label: string;
  description: string;
  done: boolean;
  tab: string;
}

// ──────────────────────────────────────────────────────────────────────────────
// Getting Started Banner — dismissible, info-toned left border
// ──────────────────────────────────────────────────────────────────────────────

const GUIDE_DISMISSED_KEY = 'benchmarks_guide_dismissed';

function GettingStartedBanner({ onGoToAgents }: { onGoToAgents: () => void }) {
  const [dismissed, setDismissed] = useState(() => {
    try { return localStorage.getItem(GUIDE_DISMISSED_KEY) === '1'; } catch { return false; }
  });
  const [expanded, setExpanded] = useState(false);

  const dismiss = useCallback(() => {
    setDismissed(true);
    try { localStorage.setItem(GUIDE_DISMISSED_KEY, '1'); } catch { /* ignore */ }
  }, []);

  if (dismissed) return null;

  return (
    <div className="rounded-lg border border-border bg-card border-l-2 border-l-info px-4 py-3">
      <div className="flex items-center gap-3">
        <BookOpen className="h-5 w-5 text-info shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-foreground">New to Benchmarks?</p>
          <p className="text-xs text-muted-foreground mt-0.5">
            Install aiperf, register a test agent, and push your first results.
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="shrink-0 text-xs"
          onClick={() => setExpanded((e) => !e)}
        >
          {expanded ? (
            <ChevronDown className="h-3.5 w-3.5 mr-1" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 mr-1" />
          )}
          {expanded ? 'Collapse' : 'Quick start'}
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 shrink-0 text-muted-foreground"
          onClick={dismiss}
          title="Dismiss"
          aria-label="Dismiss quick-start banner"
        >
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>

      {expanded && (
        <div className="mt-3 pt-3 border-t border-border text-xs text-muted-foreground">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
            <div>
              <p className="font-medium mb-1 text-foreground/80">1. Add a target</p>
              <p>Scan a cluster to find LLM services, or add one manually. Sets up the endpoint to benchmark against.</p>
            </div>
            <div>
              <p className="font-medium mb-1 text-foreground/80">2. Deploy proxies</p>
              <p>Click into your target and deploy envoy, nginx, haproxy, or F5 BNK proxies. Discover existing ones too.</p>
            </div>
            <div>
              <p className="font-medium mb-1 text-foreground/80">3. Connect an agent</p>
              <p>
                Register a test machine on the{' '}
                <button className="text-primary hover:underline" onClick={onGoToAgents}>
                  Agents tab
                </button>{' '}
                — it runs aiperf and pushes results.
              </p>
            </div>
            <div>
              <p className="font-medium mb-1 text-foreground/80">4. Run tests</p>
              <p>
                Hit “Run test” on a proxy card, or use{' '}
                <code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">
                  aiperf profile
                </code>{' '}
                and push the JSON.
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Stepper banner — token-pure progress bar + step buttons
// ──────────────────────────────────────────────────────────────────────────────

function StepperBanner({
  steps,
  onStepClick,
}: {
  steps: StepState[];
  onStepClick: (tab: string) => void;
}) {
  const completedCount = steps.filter((s) => s.done).length;
  const allDone = completedCount === steps.length;
  if (allDone) return null;

  return (
    <div className="rounded-lg border border-border bg-card px-4 py-3">
      <div className="flex items-center gap-2 mb-2">
        <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Setup progress — {completedCount}/{steps.length}
        </p>
        <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
          <div
            className="h-full bg-success rounded-full transition-all duration-500"
            style={{ width: `${(completedCount / steps.length) * 100}%` }}
          />
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-0.5">
        {steps.map((step, i) => (
          <div key={step.label} className="flex items-center">
            <button
              onClick={() => onStepClick(step.tab)}
              className={cn(
                'flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs transition-colors',
                step.done
                  ? 'text-success'
                  : 'text-muted-foreground hover:text-foreground hover:bg-muted/50',
              )}
            >
              {step.done ? (
                <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
              ) : (
                <Circle className="h-3.5 w-3.5 shrink-0" />
              )}
              <span className="font-medium">{step.label}</span>
            </button>
            {i < steps.length - 1 && (
              <ArrowRight className="h-3 w-3 text-muted-foreground/60 mx-0.5 shrink-0" />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Main page
// ──────────────────────────────────────────────────────────────────────────────

export default function Benchmarks() {
  const [activeTab, setActiveTab] = useState('targets');
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [compareRunIds, setCompareRunIds] = useState<number[]>([]);
  const [proxyFilter, setProxyFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [searchQuery, setSearchQuery] = useState('');

  const { refresh: handleRefresh, isRefreshing } = usePageRefresh();

  const { data: targetsData } = useBenchmarkTargets();
  const { data: agents } = useBenchmarkAgents();
  const targets = targetsData?.targets ?? [];
  const hasTargets = targets.length > 0;
  const hasProxies = targets.some((t) => (t.proxy_count ?? 0) > 0);
  const hasAgent = (agents ?? []).length > 0;

  const steps: StepState[] = [
    {
      label: 'Add target',
      description: 'Scan or add a K8s cluster + LLM endpoint',
      done: hasTargets,
      tab: 'targets',
    },
    {
      label: 'Deploy proxies',
      description: 'Deploy envoy/nginx/haproxy/BNK',
      done: hasProxies,
      tab: 'targets',
    },
    {
      label: 'Connect agent',
      description: 'Register a test machine',
      done: hasAgent,
      tab: 'agents',
    },
    {
      label: 'Run test',
      description: 'Trigger a benchmark run',
      done: hasTargets && hasProxies && hasAgent,
      tab: 'runs',
    },
  ];

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <PageHeader
        title="Performance Benchmarks"
        subtitle="Compare proxy / load-balancer performance for LLM inference traffic."
        onRefresh={handleRefresh}
        isRefreshing={isRefreshing}
      />

      <GettingStartedBanner onGoToAgents={() => setActiveTab('agents')} />
      <StepperBanner steps={steps} onStepClick={setActiveTab} />

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <ResourceViewTabs
          variant="inline"
          aria-label="Benchmark sections"
          active={activeTab}
          onChange={setActiveTab}
          tabs={[
            { key: 'targets', label: 'Targets', icon: Zap },
            { key: 'configs', label: 'Configs', icon: Settings2 },
            { key: 'agents', label: 'Agents', icon: Server },
            { key: 'runs', label: 'Runs', icon: Activity },
            { key: 'detail', label: 'Detail', icon: BarChart3, disabled: !selectedRunId },
            {
              key: 'compare',
              label: `Compare (${compareRunIds.length})`,
              icon: GitCompare,
              disabled: compareRunIds.length < 2,
            },
          ]}
        />

        <TabsContent value="targets" className="mt-6">
          <BenchmarkTargetsTab />
        </TabsContent>
        <TabsContent value="configs" className="mt-6">
          <BenchmarkConfigsTab />
        </TabsContent>
        <TabsContent value="agents" className="mt-6">
          <BenchmarkAgentsTab />
        </TabsContent>
        <TabsContent value="runs" className="mt-6">
          <BenchmarkRunsTab
            proxyFilter={proxyFilter}
            onProxyFilterChange={setProxyFilter}
            statusFilter={statusFilter}
            onStatusFilterChange={setStatusFilter}
            searchQuery={searchQuery}
            onSearchChange={setSearchQuery}
            onSelectRun={(id) => {
              setSelectedRunId(id);
              setActiveTab('detail');
            }}
            compareRunIds={compareRunIds}
            onToggleCompare={(id) =>
              setCompareRunIds((prev) =>
                prev.includes(id) ? prev.filter((r) => r !== id) : [...prev, id],
              )
            }
            onCompare={() => setActiveTab('compare')}
          />
        </TabsContent>
        <TabsContent value="detail" className="mt-6">
          {selectedRunId && <BenchmarkRunDetail runId={selectedRunId} />}
        </TabsContent>
        <TabsContent value="compare" className="mt-6">
          {compareRunIds.length >= 2 && <BenchmarkCompareTab runIds={compareRunIds} />}
        </TabsContent>
      </Tabs>
    </div>
  );
}
