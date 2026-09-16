/**
 * Benchmarks page — run-first redesign (Overview / Runs / Setup).
 *
 * 3 primary tabs. Overview (default landing) is the daily-use dashboard +
 * "Run benchmark" wizard entry point. Runs is UNCHANGED from the prior
 * entity-first redesign (drill-ins, trends, badges — do not regress it).
 * Setup hosts the existing Targets/Agents/Configs tab bodies, unmodified, as
 * sub-sections. Legacy ?tab=targets|agents|configs deep-links still resolve
 * to the right Setup sub-section (see derivePrimaryTabState).
 */
import { useCallback, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { PageHeader } from '@/components/layout/PageHeader';
import { usePageRefresh } from '@/hooks/usePageRefresh';
import { Tabs, TabsContent } from '@/components/ui/tabs';
import { ResourceViewTabs } from '@/components/layout/ResourceViewTabs';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import {
  Zap,
  GitCompare,
  Settings2,
  Activity,
  Server,
  LayoutDashboard,
  ArrowLeft,
  X,
  BookOpen,
  ChevronDown,
  ChevronRight,
  SearchX,
} from 'lucide-react';

import { BenchmarkTargetsTab } from './BenchmarkTargetsTab';
import { BenchmarkAgentsTab } from './BenchmarkAgentsTab';
import { BenchmarkConfigsTab } from './BenchmarkConfigsTab';
import { BenchmarkRunsTab } from './BenchmarkRunsTab';
import { BenchmarkRunDetail } from './BenchmarkRunDetail';
import { BenchmarkCompareTab } from './BenchmarkCompareTab';
import { BenchmarkTrendsView } from './BenchmarkTrendsView';
import { BenchmarkRunGroupView } from './BenchmarkRunGroupView';
import { BenchmarkOverviewTab } from './BenchmarkOverviewTab';
import { RunBenchmarkWizard, type RunBenchmarkWizardLaunchResult } from './RunBenchmarkWizard';
import { deriveRunsViewState, derivePrimaryTabState, type SetupSection } from './benchmark-runs-view';

// ──────────────────────────────────────────────────────────────────────────────
// Getting Started Banner — dismissible, collapses to a re-openable pill (never
// gone for good). localStorage remembers collapsed-vs-expanded, not "dismissed".
// ──────────────────────────────────────────────────────────────────────────────

const GUIDE_COLLAPSED_KEY = 'benchmarks_guide_collapsed';

function GettingStartedBanner({
  collapsed,
  onCollapse,
  onGoToAgents,
}: {
  collapsed: boolean;
  onCollapse: () => void;
  onGoToAgents: () => void;
}) {
  const [expanded, setExpanded] = useState(false);

  if (collapsed) return null;

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
          onClick={onCollapse}
          title="Collapse (re-open any time from the Setup guide button)"
          aria-label="Collapse quick-start banner"
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

/** Small pill shown once the banner is collapsed — the "never gone" re-open affordance. */
function SetupGuidePill({ onExpand }: { onExpand: () => void }) {
  return (
    <Button variant="outline" size="sm" className="text-xs gap-1.5" onClick={onExpand}>
      <BookOpen className="h-3.5 w-3.5" />
      Setup guide
    </Button>
  );
}



// ──────────────────────────────────────────────────────────────────────────────
// Setup tab — Targets/Agents/Configs as sub-sections, existing components as-is
// ──────────────────────────────────────────────────────────────────────────────

function BenchmarkSetupSection({
  activeSection,
  onSectionChange,
}: {
  activeSection: SetupSection;
  onSectionChange: (section: SetupSection) => void;
}) {
  return (
    <Tabs value={activeSection} onValueChange={(v) => onSectionChange(v as SetupSection)}>
      <ResourceViewTabs
        variant="inline"
        aria-label="Setup sections"
        active={activeSection}
        onChange={(key) => onSectionChange(key as SetupSection)}
        tabs={[
          { key: 'targets', label: 'Targets', icon: Zap },
          { key: 'agents', label: 'Agents', icon: Server },
          { key: 'configs', label: 'Configs', icon: Settings2 },
        ]}
      />
      <TabsContent value="targets" className="mt-6">
        <BenchmarkTargetsTab />
      </TabsContent>
      <TabsContent value="agents" className="mt-6">
        <BenchmarkAgentsTab />
      </TabsContent>
      <TabsContent value="configs" className="mt-6">
        <BenchmarkConfigsTab />
      </TabsContent>
    </Tabs>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Runs tab — list + Detail/Compare/Trends/Group drill-ins, URL-driven so
// refresh/back preserve context. UNCHANGED behavior from the prior redesign;
// only additive support for ?group=<id> (scenario launches from the wizard).
// ──────────────────────────────────────────────────────────────────────────────

function BenchmarkRunsSection({ searchParams, setSearchParams }: {
  searchParams: URLSearchParams;
  setSearchParams: (params: URLSearchParams) => void;
}) {
  const [proxyFilter, setProxyFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [pendingCompareIds, setPendingCompareIds] = useState<number[]>([]);

  const { subView, selectedRunId, compareRunIds, selectedGroupId } = deriveRunsViewState(searchParams);

  const goToList = useCallback(() => {
    const next = new URLSearchParams(searchParams);
    next.delete('run');
    next.delete('group');
    next.delete('compare');
    next.delete('view');
    next.set('tab', 'runs');
    setSearchParams(next);
  }, [searchParams, setSearchParams]);

  const goToDetail = useCallback((id: number) => {
    const next = new URLSearchParams();
    next.set('tab', 'runs');
    next.set('run', String(id));
    setSearchParams(next);
  }, [setSearchParams]);

  const goToCompare = useCallback((ids: number[]) => {
    if (ids.length < 2) return;
    const next = new URLSearchParams();
    next.set('tab', 'runs');
    next.set('compare', ids.join(','));
    setSearchParams(next);
  }, [setSearchParams]);

  const goToTrends = useCallback(() => {
    const next = new URLSearchParams();
    next.set('tab', 'runs');
    next.set('view', 'trends');
    setSearchParams(next);
  }, [setSearchParams]);

  const toggleCompare = useCallback((id: number) => {
    setPendingCompareIds((prev) => (prev.includes(id) ? prev.filter((r) => r !== id) : [...prev, id]));
  }, []);

  if (subView === 'detail') {
    if (selectedRunId == null) {
      return (
        <EmptyState
          icon={SearchX}
          title="Invalid run id"
          description="The run id in the URL isn't valid."
          action={{ label: 'Back to runs', onClick: goToList, variant: 'outline' }}
        />
      );
    }
    return <BenchmarkRunDetail runId={selectedRunId} onBack={goToList} />;
  }

  if (subView === 'group') {
    if (selectedGroupId == null) {
      return (
        <EmptyState
          icon={SearchX}
          title="Invalid run-group id"
          description="The run-group id in the URL isn't valid."
          action={{ label: 'Back to runs', onClick: goToList, variant: 'outline' }}
        />
      );
    }
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" className="gap-1.5" onClick={goToList}>
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to runs
        </Button>
        <BenchmarkRunGroupView groupId={selectedGroupId} />
      </div>
    );
  }

  if (subView === 'compare') {
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" className="gap-1.5" onClick={goToList}>
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to runs
        </Button>
        {compareRunIds.length >= 2 ? (
          <BenchmarkCompareTab runIds={compareRunIds} />
        ) : (
          <EmptyState
            icon={GitCompare}
            title="Nothing to compare"
            description="Pick at least 2 runs from the Runs list to compare them side-by-side."
            action={{ label: 'Back to runs', onClick: goToList, variant: 'outline' }}
          />
        )}
      </div>
    );
  }

  if (subView === 'trends') {
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" className="gap-1.5" onClick={goToList}>
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to runs
        </Button>
        <BenchmarkTrendsView />
      </div>
    );
  }

  return (
    <BenchmarkRunsTab
      proxyFilter={proxyFilter}
      onProxyFilterChange={setProxyFilter}
      statusFilter={statusFilter}
      onStatusFilterChange={setStatusFilter}
      searchQuery={searchQuery}
      onSearchChange={setSearchQuery}
      onSelectRun={goToDetail}
      compareRunIds={pendingCompareIds}
      onToggleCompare={toggleCompare}
      onCompare={() => goToCompare(pendingCompareIds)}
      onViewTrends={goToTrends}
    />
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Main page
// ──────────────────────────────────────────────────────────────────────────────

export default function Benchmarks() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [guideCollapsed, setGuideCollapsed] = useState(() => {
    try { return localStorage.getItem(GUIDE_COLLAPSED_KEY) === '1'; } catch { return false; }
  });
  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizardReRunLast, setWizardReRunLast] = useState(false);

  const { primaryTab, setupSection } = derivePrimaryTabState(searchParams);

  const goToPrimaryTab = useCallback((tab: string) => {
    const next = new URLSearchParams();
    if (tab === 'runs') {
      next.set('tab', 'runs');
    } else if (tab === 'setup') {
      next.set('tab', 'setup');
      next.set('section', setupSection);
    } else {
      next.set('tab', 'overview');
    }
    setSearchParams(next);
  }, [setSearchParams, setupSection]);

  const goToSetupSection = useCallback((section: SetupSection) => {
    const next = new URLSearchParams();
    next.set('tab', 'setup');
    next.set('section', section);
    setSearchParams(next);
  }, [setSearchParams]);

  const goToRunDetail = useCallback((runId: number) => {
    const next = new URLSearchParams();
    next.set('tab', 'runs');
    next.set('run', String(runId));
    setSearchParams(next);
  }, [setSearchParams]);

  const goToRunGroup = useCallback((groupId: number) => {
    const next = new URLSearchParams();
    next.set('tab', 'runs');
    next.set('group', String(groupId));
    setSearchParams(next);
  }, [setSearchParams]);

  const goToTrends = useCallback(() => {
    const next = new URLSearchParams();
    next.set('tab', 'runs');
    next.set('view', 'trends');
    setSearchParams(next);
  }, [setSearchParams]);

  const openWizard = useCallback((reRunLast = false) => {
    setWizardReRunLast(reRunLast);
    setWizardOpen(true);
  }, []);

  const handleWizardLaunched = useCallback((result: RunBenchmarkWizardLaunchResult) => {
    if (result.runId != null) {
      goToRunDetail(result.runId);
    } else if (result.groupId != null) {
      goToRunGroup(result.groupId);
    }
  }, [goToRunDetail, goToRunGroup]);

  const collapseGuide = useCallback(() => {
    setGuideCollapsed(true);
    try { localStorage.setItem(GUIDE_COLLAPSED_KEY, '1'); } catch { /* ignore */ }
  }, []);

  const expandGuide = useCallback(() => {
    setGuideCollapsed(false);
    try { localStorage.setItem(GUIDE_COLLAPSED_KEY, '0'); } catch { /* ignore */ }
  }, []);

  const { refresh: handleRefresh, isRefreshing } = usePageRefresh();

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <PageHeader
          title="Performance Benchmarks"
          subtitle="Compare proxy / load-balancer performance for LLM inference traffic."
          onRefresh={handleRefresh}
          isRefreshing={isRefreshing}
        />
        {guideCollapsed && <SetupGuidePill onExpand={expandGuide} />}
      </div>

      <GettingStartedBanner
        collapsed={guideCollapsed}
        onCollapse={collapseGuide}
        onGoToAgents={() => goToSetupSection('agents')}
      />

      <Tabs value={primaryTab} onValueChange={goToPrimaryTab}>
        <ResourceViewTabs
          variant="inline"
          aria-label="Benchmark sections"
          active={primaryTab}
          onChange={goToPrimaryTab}
          tabs={[
            { key: 'overview', label: 'Overview', icon: LayoutDashboard },
            { key: 'runs', label: 'Runs', icon: Activity },
            { key: 'setup', label: 'Setup', icon: Settings2 },
          ]}
        />

        <TabsContent value="overview" className="mt-6">
          <BenchmarkOverviewTab
            onGoToSetup={goToSetupSection}
            onGoToRun={goToRunDetail}
            onGoToRunsList={() => goToPrimaryTab('runs')}
            onGoToTrends={goToTrends}
            onOpenWizard={() => openWizard(false)}
          />
        </TabsContent>
        <TabsContent value="runs" className="mt-6">
          <BenchmarkRunsSection searchParams={searchParams} setSearchParams={setSearchParams} />
        </TabsContent>
        <TabsContent value="setup" className="mt-6">
          <BenchmarkSetupSection activeSection={setupSection} onSectionChange={goToSetupSection} />
        </TabsContent>
      </Tabs>

      <RunBenchmarkWizard
        open={wizardOpen}
        onOpenChange={setWizardOpen}
        onLaunched={handleWizardLaunched}
        onGoToSetup={goToSetupSection}
        initialReRunLast={wizardReRunLast}
      />
    </div>
  );
}
