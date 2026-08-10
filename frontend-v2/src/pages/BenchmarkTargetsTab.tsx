/**
 * BenchmarkTargetsTab — D-020: target CRUD, proxy deployment management,
 * scan/add dialogs. Detail view + list view rendered in SectionCards;
 * status conveyed via Badge variants only (no inline color styles).
 * Action buttons are outline/ghost; primary form-submit stays solid.
 */
import { useState, useMemo } from 'react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Badge, type BadgeProps } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { Skeleton } from '@/components/ui/skeleton';
import { TimeAgo } from '@/components/ui/TimeAgo';
import { SectionCard } from '@/components/ui/section-card';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Zap,
  Trash2,
  Search,
  CheckCircle2,
  Loader2,
  Plus,
  Play,
  RefreshCw,
} from 'lucide-react';
import {
  useBenchmarkTargets,
  useBenchmarkTarget,
  useCreateBenchmarkTarget,
  useDeleteBenchmarkTarget,
  useValidateBenchmarkTarget,
  useDeployProxy,
  useDeleteProxyDeployment,
  useRedeployProxy,
  useDiscoverProxies,
  useDiscoverTargets,
  useTriggerRun,
  useRunScenario,
  useScenarios,
  useBenchmarkAgents,
} from '@/hooks/useBenchmarks';
import { useAllClusters } from '@/hooks/useK8sClusters';
import { useClusterNamespaces, useClusterResources } from '@/hooks/useK8sResources';
import {
  AVAILABLE_PROXY_TYPES,
  ProxyBadge,
} from './benchmark-utils';
import { BenchmarkRunGroupView } from './BenchmarkRunGroupView';
import type { BenchmarkTargetDetail } from '@/types';

type TargetStatus = 'active' | 'inactive' | 'validating' | 'error';
type ProxyDeployStatus =
  | 'discovered'
  | 'pending'
  | 'deploying'
  | 'ready'
  | 'failed'
  | 'uninstalling'
  | 'uninstalled';

const TARGET_STATUS_BADGE: Record<TargetStatus, { label: string; variant: BadgeProps['variant'] }> = {
  active: { label: 'Active', variant: 'success' },
  inactive: { label: 'Inactive', variant: 'muted' },
  validating: { label: 'Validating', variant: 'warning' },
  error: { label: 'Error', variant: 'destructive' },
};

const PROXY_DEPLOY_BADGE: Record<ProxyDeployStatus, { label: string; variant: BadgeProps['variant'] }> = {
  discovered: { label: 'Discovered', variant: 'info' },
  pending: { label: 'Pending', variant: 'muted' },
  deploying: { label: 'Deploying', variant: 'warning' },
  ready: { label: 'Ready', variant: 'success' },
  failed: { label: 'Failed', variant: 'destructive' },
  uninstalling: { label: 'Uninstalling', variant: 'warning' },
  uninstalled: { label: 'Uninstalled', variant: 'muted' },
};

function targetBadge(status: string) {
  return TARGET_STATUS_BADGE[(status as TargetStatus)] ?? TARGET_STATUS_BADGE.active;
}
function proxyDeployBadge(status: string) {
  return PROXY_DEPLOY_BADGE[(status as ProxyDeployStatus)] ?? PROXY_DEPLOY_BADGE.pending;
}

// ============================================================================
// Main Component
// ============================================================================

export function BenchmarkTargetsTab() {
  const { data: targetsData, isLoading } = useBenchmarkTargets();
  const [selectedTargetId, setSelectedTargetId] = useState<number | null>(null);
  const { data: targetDetail } = useBenchmarkTarget(selectedTargetId ?? undefined);
  const [showCreateDialog, setShowCreateDialog] = useState(false);

  const createTarget = useCreateBenchmarkTarget();
  const deleteTarget = useDeleteBenchmarkTarget();
  const validateTarget = useValidateBenchmarkTarget();
  const deployProxy = useDeployProxy();
  const deleteProxyDeploy = useDeleteProxyDeployment();
  const redeployProxy = useRedeployProxy();
  const discoverProxies = useDiscoverProxies();
  const discoverTargets = useDiscoverTargets();
  const triggerRun = useTriggerRun();
  const runScenario = useRunScenario();

  // Scenario picker + the run-group launched from this view (parent + N child runs)
  const { data: scenarioCatalog } = useScenarios();
  const scenarios = scenarioCatalog?.scenarios ?? [];
  const [selectedScenarioKey, setSelectedScenarioKey] = useState('');
  const [activeRunGroupId, setActiveRunGroupId] = useState<number | null>(null);

  // Check if any agents are connected (needed for "Run Test" button)
  const { data: agents } = useBenchmarkAgents();
  const hasConnectedAgent = (agents ?? []).some(a => a.status === 'connected');

  // K8s discovery hooks for the form
  const { data: clustersData } = useAllClusters();
  const clusters = clustersData?.clusters ?? [];

  // Scan Cluster state
  const [showScanDialog, setShowScanDialog] = useState(false);
  const [scanClusterId, setScanClusterId] = useState('');
  const [selectedScanUrls, setSelectedScanUrls] = useState<Set<string>>(new Set());

  // Create form state
  const [formName, setFormName] = useState('');
  const [formDescription, setFormDescription] = useState('');
  const [formClusterId, setFormClusterId] = useState('');
  const [formLlmBaseUrl, setFormLlmBaseUrl] = useState('');
  const [formLlmModel, setFormLlmModel] = useState('');
  const [formLlmNamespace, setFormLlmNamespace] = useState('');
  const [formProxyNamespace, setFormProxyNamespace] = useState('perf-proxies');

  // Fetch namespaces + services when a cluster is selected
  const { data: nsData } = useClusterNamespaces(Number(formClusterId) || 0, { enabled: !!formClusterId });
  const namespaces = nsData?.namespaces ?? [];

  const { data: svcData } = useClusterResources(
    Number(formClusterId) || 0,
    'service',
    formLlmNamespace ? { namespace: formLlmNamespace } : undefined,
    { enabled: !!formClusterId && !!formLlmNamespace },
  );

  // Build service URL options from discovered services
  const serviceOptions = useMemo(() => {
    if (!svcData?.resources) return [];
    return svcData.resources.map(svc => {
      const ports = (svc.spec as Record<string, unknown>)?.ports as Array<{ port: number; name?: string }> | undefined;
      const port = ports?.[0]?.port ?? 80;
      const ns = svc.metadata?.namespace || formLlmNamespace || 'default';
      const url = `http://${svc.metadata.name}.${ns}:${port}`;
      return { name: svc.metadata.name, url, port };
    });
  }, [svcData, formLlmNamespace]);

  const resetForm = () => {
    setFormName('');
    setFormDescription('');
    setFormClusterId('');
    setFormLlmBaseUrl('');
    setFormLlmModel('');
    setFormLlmNamespace('');
    setFormProxyNamespace('perf-proxies');
  };

  const targets = targetsData?.targets ?? [];

  if (isLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    );
  }

  // Detail view when a target is selected
  if (selectedTargetId && targetDetail) {
    const proxies = (targetDetail as BenchmarkTargetDetail).proxy_deployments ?? [];
    const deployedTypes = new Set(proxies.map(p => p.proxy_type));
    const availableTypes = AVAILABLE_PROXY_TYPES.filter(t => !deployedTypes.has(t));
    const tBadge = targetBadge(targetDetail.status);

    return (
      <div className="space-y-6">
        {/* Back + Header */}
        <div className="flex items-center gap-3 flex-wrap">
          <Button variant="outline" size="sm" onClick={() => { setSelectedTargetId(null); setActiveRunGroupId(null); }}>
            ← Back
          </Button>
          <div className="flex-1 min-w-0">
            <h3 className="text-lg font-semibold text-foreground truncate">
              {targetDetail.name}
            </h3>
            <p className="text-sm text-muted-foreground truncate">{targetDetail.description || 'No description'}</p>
          </div>
          <Badge variant={tBadge.variant}>{tBadge.label}</Badge>
          <Button
            variant="outline" size="sm"
            onClick={() => discoverProxies.mutate(selectedTargetId)}
            disabled={discoverProxies.isPending}
          >
            {discoverProxies.isPending ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Search className="h-4 w-4 mr-1" />}
            {discoverProxies.isPending ? 'Scanning...' : 'Discover Proxies'}
          </Button>
          <Button variant="outline" size="sm" onClick={() => validateTarget.mutate(selectedTargetId)}>
            <CheckCircle2 className="h-4 w-4 mr-1" />
            Validate
          </Button>
          <Button variant="outline" size="sm" className="text-destructive hover:text-destructive" onClick={() => {
            if (confirm(`Delete target "${targetDetail.name}"? This will remove all proxy deployments.`)) {
              deleteTarget.mutate(selectedTargetId, { onSuccess: () => { setSelectedTargetId(null); setActiveRunGroupId(null); } });
            }
          }}>
            <Trash2 className="h-4 w-4 mr-1" />
            Delete
          </Button>
        </div>

        {/* Target Info */}
        <SectionCard title="Target details" compact>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            <div>
              <span className="text-xs uppercase tracking-wider text-muted-foreground">LLM endpoint</span>
              <p className="font-mono text-xs mt-1 text-foreground/80">{targetDetail.llm_base_url}</p>
            </div>
            <div>
              <span className="text-xs uppercase tracking-wider text-muted-foreground">Model</span>
              <p className="font-mono text-xs mt-1 text-foreground/80">{targetDetail.llm_model}</p>
            </div>
            <div>
              <span className="text-xs uppercase tracking-wider text-muted-foreground">LLM namespace</span>
              <p className="font-mono text-xs mt-1 text-foreground/80">{targetDetail.llm_namespace}</p>
            </div>
            <div>
              <span className="text-xs uppercase tracking-wider text-muted-foreground">Proxy namespace</span>
              <p className="font-mono text-xs mt-1 text-foreground/80">{targetDetail.proxy_namespace}</p>
            </div>
            {targetDetail.last_validated && (
              <div>
                <span className="text-xs uppercase tracking-wider text-muted-foreground">Last validated</span>
                <p className="text-xs mt-1 text-foreground/80">
                  <TimeAgo dateStr={targetDetail.last_validated} />
                </p>
              </div>
            )}
          </div>
        </SectionCard>

        {/* Proxy Deployments */}
        <div>
          <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
            <h4 className="text-sm font-semibold text-foreground">
              Proxy deployments ({proxies.length})
            </h4>
            <div className="flex gap-2 items-center flex-wrap">
              {scenarios.length > 0 && (
                <Select value={selectedScenarioKey} onValueChange={setSelectedScenarioKey}>
                  <SelectTrigger className="h-7 w-[200px] text-xs">
                    <SelectValue placeholder="Select scenario…" />
                  </SelectTrigger>
                  <SelectContent>
                    {scenarios.map(s => (
                      <SelectItem key={s.key} value={s.key} className="text-xs">
                        <span className="flex items-center gap-2">
                          {s.name}
                          {s.trace_driven && (
                            <Badge variant="outline" className="h-4 px-1 text-[10px] border-info text-info">
                              production trace
                            </Badge>
                          )}
                          <span className="text-muted-foreground">({s.child_run_count} runs)</span>
                        </span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
              {availableTypes.length > 0 && (
                <div className="flex gap-1 flex-wrap">
                  {availableTypes.map(ptype => (
                    <Button key={ptype} variant="outline" size="sm" className="h-7 text-xs" onClick={() =>
                      deployProxy.mutate({ targetId: selectedTargetId, data: { proxy_type: ptype } })
                    }>
                      <Plus className="h-3 w-3 mr-1" />
                      {ptype}
                    </Button>
                  ))}
                </div>
              )}
            </div>
          </div>

          {proxies.length === 0 ? (
            <SectionCard>
              <div className="text-center py-6">
                <Search className="h-8 w-8 mx-auto text-muted-foreground mb-2" />
                <p className="text-sm text-foreground mb-3">No proxies found yet.</p>
                <Button
                  variant="outline" size="sm"
                  onClick={() => discoverProxies.mutate(selectedTargetId)}
                  disabled={discoverProxies.isPending}
                >
                  {discoverProxies.isPending ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Search className="h-4 w-4 mr-1" />}
                  {discoverProxies.isPending ? 'Scanning cluster...' : 'Discover existing proxies'}
                </Button>
                <p className="text-xs text-muted-foreground mt-3">
                  Scans the cluster for envoy, nginx, haproxy, F5 BNK, and nodeport access.
                  You can also deploy a new proxy manually above.
                </p>
              </div>
            </SectionCard>
          ) : (
            <div className="grid gap-3 md:grid-cols-2">
              {proxies.map(proxy => {
                const pBadge = proxyDeployBadge(proxy.status);
                const isTransitional = proxy.status === 'deploying' || proxy.status === 'uninstalling' || proxy.status === 'pending';
                const canRun = proxy.status === 'ready' || proxy.status === 'discovered';
                const canRedeploy = proxy.status === 'ready' || proxy.status === 'failed' || proxy.status === 'uninstalled';

                return (
                  <SectionCard key={proxy.id} compact>
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <ProxyBadge proxy={proxy.proxy_type} />
                        <Badge variant={pBadge.variant} className="text-xs gap-1">
                          {isTransitional && <Loader2 className="h-3 w-3 animate-spin" />}
                          {pBadge.label}
                        </Badge>
                      </div>
                      <div className="flex items-center gap-1">
                        {canRun && (
                          <TooltipProvider delayDuration={200}>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <span tabIndex={0}>
                                  <Button
                                    variant="outline" size="sm"
                                    className={cn(
                                      'h-6 px-2 text-xs',
                                      !hasConnectedAgent && 'opacity-50',
                                    )}
                                    onClick={() => {
                                      if (!hasConnectedAgent) return;
                                      triggerRun.mutate({
                                        targetId: selectedTargetId,
                                        proxyId: proxy.id,
                                        data: { run_label: `${proxy.proxy_type}-${targetDetail.name}` },
                                      });
                                    }}
                                    disabled={!hasConnectedAgent || triggerRun.isPending}
                                  >
                                    <Play className={cn('h-3 w-3 mr-1', triggerRun.isPending && 'animate-pulse')} />
                                    Run test
                                  </Button>
                                </span>
                              </TooltipTrigger>
                              <TooltipContent side="top" className="max-w-[250px] text-xs">
                                {hasConnectedAgent
                                  ? 'Trigger a benchmark run via the connected agent'
                                  : 'No connected agent — register one on the Agents tab first'}
                              </TooltipContent>
                            </Tooltip>
                          </TooltipProvider>
                        )}
                        {canRun && (
                          <TooltipProvider delayDuration={200}>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <span tabIndex={0}>
                                  <Button
                                    variant="ghost" size="sm"
                                    className={cn(
                                      'h-6 px-2 text-xs',
                                      hasConnectedAgent && selectedScenarioKey
                                        ? 'text-primary hover:text-primary hover:bg-primary/10'
                                        : 'text-muted-foreground opacity-50'
                                    )}
                                    onClick={() => {
                                      if (!hasConnectedAgent || !selectedScenarioKey) return;
                                      runScenario.mutate(
                                        {
                                          targetId: selectedTargetId,
                                          proxyId: proxy.id,
                                          data: {
                                            scenario_key: selectedScenarioKey,
                                            run_label: `${selectedScenarioKey}-${proxy.proxy_type}-${targetDetail.name}`,
                                          },
                                        },
                                        { onSuccess: (res) => setActiveRunGroupId(res.run_group_id) },
                                      );
                                    }}
                                    disabled={!hasConnectedAgent || !selectedScenarioKey || runScenario.isPending}
                                  >
                                    <Zap className={cn('h-3 w-3 mr-1', runScenario.isPending && 'animate-pulse')} />
                                    Run Scenario
                                  </Button>
                                </span>
                              </TooltipTrigger>
                              <TooltipContent side="top" className="max-w-[250px] text-xs">
                                {!hasConnectedAgent
                                  ? 'No connected agent — register one on the Agents tab first'
                                  : !selectedScenarioKey
                                    ? 'Select a scenario above first'
                                    : 'Expand the scenario into a run-group (parent + N child runs)'}
                              </TooltipContent>
                            </Tooltip>
                          </TooltipProvider>
                        )}
                        {canRedeploy && (
                          <Button
                            variant="ghost" size="sm" className="h-6 px-2 text-xs text-muted-foreground hover:text-foreground"
                            onClick={() => redeployProxy.mutate({ targetId: selectedTargetId, proxyId: proxy.id })}
                            disabled={redeployProxy.isPending}
                          >
                            <RefreshCw className={cn('h-3 w-3 mr-1', redeployProxy.isPending && 'animate-spin')} />
                            Redeploy
                          </Button>
                        )}
                        <Button
                          variant="ghost" size="sm"
                          className="h-6 w-6 p-0 text-muted-foreground hover:text-destructive"
                          disabled={isTransitional}
                          onClick={() => {
                            if (confirm(`Delete ${proxy.proxy_type} deployment?`)) {
                              deleteProxyDeploy.mutate({ targetId: selectedTargetId, proxyId: proxy.id });
                            }
                          }}
                        >
                          <Trash2 className="h-3 w-3" />
                        </Button>
                      </div>
                    </div>
                    <div className="space-y-1 text-xs">
                      {proxy.helm_chart && (
                        <p className="text-muted-foreground">
                          Chart: <span className="font-mono text-foreground/80">{proxy.helm_chart}:{proxy.helm_version}</span>
                        </p>
                      )}
                      {proxy.proxy_url && (
                        <p className="text-muted-foreground">
                          Internal: <span className="font-mono text-foreground/80">{proxy.proxy_url}</span>
                        </p>
                      )}
                      {proxy.external_url && (
                        <p className="text-muted-foreground">
                          External: <span className="font-mono text-foreground/80">{proxy.external_url}</span>
                        </p>
                      )}
                      {proxy.deployed_at && (
                        <p className="text-muted-foreground">
                          Deployed: <TimeAgo dateStr={proxy.deployed_at} />
                        </p>
                      )}
                      {proxy.status_message && (
                        <p className={cn('italic', proxy.status === 'failed' ? 'text-destructive' : 'text-muted-foreground')}>
                          {proxy.status_message}
                        </p>
                      )}
                    </div>
                  </SectionCard>
                );
              })}
            </div>
          )}
        </div>

        {/* Scenario run-group results (parent aggregate + child runs) */}
        {activeRunGroupId !== null && (
          <BenchmarkRunGroupView groupId={activeRunGroupId} />
        )}
      </div>
    );
  }

  // List view
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <p className="text-sm text-muted-foreground">
          K8s clusters + LLM endpoints used as benchmark targets. Deploy proxies and trigger tests.
        </p>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => { setScanClusterId(''); setShowScanDialog(true); }}>
            <Search className="h-4 w-4 mr-1" />
            Scan cluster
          </Button>
          <Button size="sm" onClick={() => { resetForm(); setShowCreateDialog(true); }}>
            <Plus className="h-4 w-4 mr-1" />
            Add target
          </Button>
        </div>
      </div>

      {targets.length === 0 ? (
        <SectionCard>
          <div className="text-center py-8">
            <Zap className="h-10 w-10 mx-auto text-muted-foreground mb-3" />
            <h3 className="text-sm font-semibold mb-1 text-foreground">
              No benchmark targets
            </h3>
            <p className="text-sm text-muted-foreground max-w-md mx-auto mb-4">
              Scan a cluster to auto-discover LLM services and proxies, or add a target manually.
            </p>
            <div className="flex gap-2 justify-center">
              <Button size="sm" onClick={() => { setScanClusterId(''); setShowScanDialog(true); }}>
                <Search className="h-4 w-4 mr-1" />
                Scan cluster
              </Button>
              <Button variant="outline" size="sm" onClick={() => { resetForm(); setShowCreateDialog(true); }}>
                <Plus className="h-4 w-4 mr-1" />
                Add manually
              </Button>
            </div>
          </div>
        </SectionCard>
      ) : (
        <SectionCard title={`${targets.length} ${targets.length === 1 ? 'target' : 'targets'}`} compact>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>LLM endpoint</TableHead>
                  <TableHead>Model</TableHead>
                  <TableHead>Proxies</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="w-[50px]"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {targets.map(target => {
                  const tBadge = targetBadge(target.status);
                  return (
                    <TableRow key={target.id} className="cursor-pointer" onClick={() => { setActiveRunGroupId(null); setSelectedTargetId(target.id); }}>
                      <TableCell className="font-medium text-foreground">{target.name}</TableCell>
                      <TableCell className="font-mono text-xs text-foreground/80">{target.llm_base_url}</TableCell>
                      <TableCell className="text-xs text-foreground/80">{target.llm_model}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-xs">
                          {target.proxy_count ?? 0}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <Badge variant={tBadge.variant} className="text-xs">
                          {tBadge.label}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        <TimeAgo dateStr={target.created_at} />
                      </TableCell>
                      <TableCell>
                        <Button variant="ghost" size="sm" className="h-6 w-6 p-0 text-muted-foreground hover:text-destructive" onClick={(e) => {
                          e.stopPropagation();
                          if (confirm(`Delete target "${target.name}"?`)) {
                            deleteTarget.mutate(target.id);
                          }
                        }}>
                          <Trash2 className="h-3 w-3" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        </SectionCard>
      )}

      {/* Create Target Dialog */}
      <Dialog open={showCreateDialog} onOpenChange={setShowCreateDialog}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Add Benchmark Target</DialogTitle>
            <DialogDescription>
              Define a K8s cluster + LLM endpoint to benchmark against.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div>
              <Label>Name</Label>
              <Input placeholder="e.g. gpu-cluster-1" value={formName} onChange={e => setFormName(e.target.value)} />
            </div>
            <div>
              <Label>Description</Label>
              <Input placeholder="Optional description" value={formDescription} onChange={e => setFormDescription(e.target.value)} />
            </div>

            {/* Cluster dropdown */}
            <div>
              <Label>Cluster</Label>
              <Select value={formClusterId} onValueChange={(val) => { setFormClusterId(val); setFormLlmNamespace(''); setFormLlmBaseUrl(''); }}>
                <SelectTrigger>
                  <SelectValue placeholder={clusters.length === 0 ? 'No clusters available' : 'Select a cluster'} />
                </SelectTrigger>
                <SelectContent>
                  {clusters.map(c => (
                    <SelectItem key={c.id} value={String(c.id)}>
                      {c.name} {c.status !== 'active' && `(${c.status})`}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* LLM Namespace dropdown */}
            <div>
              <Label>LLM Service Namespace</Label>
              <Select value={formLlmNamespace} onValueChange={(val) => { setFormLlmNamespace(val); setFormLlmBaseUrl(''); }} disabled={!formClusterId}>
                <SelectTrigger>
                  <SelectValue placeholder={!formClusterId ? 'Select a cluster first' : namespaces.length === 0 ? 'Loading...' : 'Select namespace'} />
                </SelectTrigger>
                <SelectContent>
                  {namespaces.map(ns => (
                    <SelectItem key={ns.name} value={ns.name}>{ns.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* LLM Service dropdown */}
            <div>
              <Label>LLM Service (Base URL)</Label>
              {serviceOptions.length > 0 ? (
                <Select value={formLlmBaseUrl} onValueChange={setFormLlmBaseUrl} disabled={!formLlmNamespace}>
                  <SelectTrigger>
                    <SelectValue placeholder={!formLlmNamespace ? 'Select a namespace first' : 'Select a service'} />
                  </SelectTrigger>
                  <SelectContent>
                    {serviceOptions.map(svc => (
                      <SelectItem key={svc.url} value={svc.url}>
                        {svc.name} <span className="text-muted-foreground">:{svc.port}</span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : (
                <Input
                  placeholder={!formLlmNamespace ? 'Select namespace first' : 'http://service-name.namespace:port'}
                  value={formLlmBaseUrl}
                  onChange={e => setFormLlmBaseUrl(e.target.value)}
                  disabled={!formLlmNamespace}
                />
              )}
              <p className="text-xs text-muted-foreground mt-1">The upstream LLM endpoint — all proxies will route here</p>
            </div>

            {/* LLM Model — text input (app-level concept) */}
            <div>
              <Label>LLM Model</Label>
              <Input placeholder="e.g. openai/gpt-oss-120b" value={formLlmModel} onChange={e => setFormLlmModel(e.target.value)} />
            </div>

            {/* Proxy Namespace dropdown */}
            <div>
              <Label>Proxy Namespace</Label>
              <Select value={formProxyNamespace} onValueChange={setFormProxyNamespace} disabled={!formClusterId}>
                <SelectTrigger>
                  <SelectValue placeholder={!formClusterId ? 'Select a cluster first' : 'Select namespace for proxies'} />
                </SelectTrigger>
                <SelectContent>
                  {namespaces.map(ns => (
                    <SelectItem key={ns.name} value={ns.name}>{ns.name}</SelectItem>
                  ))}
                  {/* Always include perf-proxies as an option even if it doesn't exist yet */}
                  {!namespaces.some(ns => ns.name === 'perf-proxies') && (
                    <SelectItem value="perf-proxies">perf-proxies <span className="text-muted-foreground">(will be created)</span></SelectItem>
                  )}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground mt-1">Where proxy pods will be deployed</p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowCreateDialog(false)}>Cancel</Button>
            <Button disabled={!formName || !formClusterId || !formLlmBaseUrl || !formLlmModel || !formLlmNamespace}
              onClick={() => {
                createTarget.mutate({
                  name: formName,
                  description: formDescription || undefined,
                  cluster_id: Number(formClusterId),
                  llm_base_url: formLlmBaseUrl,
                  llm_model: formLlmModel,
                  llm_namespace: formLlmNamespace,
                  llm_endpoint: '/v1/chat/completions',
                  proxy_namespace: formProxyNamespace,
                }, { onSuccess: () => { setShowCreateDialog(false); resetForm(); } });
              }}
            >
              Create target
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Scan Cluster Dialog — two-step: scan → select → create */}
      <Dialog open={showScanDialog} onOpenChange={(open) => {
        setShowScanDialog(open);
        if (!open) { discoverTargets.reset(); setSelectedScanUrls(new Set()); }
      }}>
        <DialogContent className="sm:max-w-lg flex max-h-[85vh] flex-col">
          <DialogHeader>
            <DialogTitle>Scan Cluster for LLM Services</DialogTitle>
            <DialogDescription>
              Auto-discover LLM inference services (vLLM, TGI, LiteLLM, etc.) and existing proxies.
              Select which services to add as benchmark targets.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2 flex-1 overflow-y-auto min-h-0">
            {/* Step 1: Pick cluster */}
            <div>
              <Label>Cluster</Label>
              <Select value={scanClusterId} onValueChange={(val) => { setScanClusterId(val); discoverTargets.reset(); setSelectedScanUrls(new Set()); }}>
                <SelectTrigger>
                  <SelectValue placeholder={clusters.length === 0 ? 'No clusters available' : 'Select a cluster to scan'} />
                </SelectTrigger>
                <SelectContent>
                  {clusters.map(c => (
                    <SelectItem key={c.id} value={String(c.id)}>
                      {c.name} {c.status !== 'active' && `(${c.status})`}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Step 2: Show scan results with checkboxes */}
            {discoverTargets.data && discoverTargets.data.discovered_count > 0 && !discoverTargets.data.created_targets.length && (
              <div className="rounded-md border border-border bg-muted/40 p-3 text-sm space-y-2">
                <div className="flex items-center justify-between">
                  <p className="font-medium text-foreground">Found {discoverTargets.data.discovered_count} LLM service(s)</p>
                  <button
                    className="text-xs text-muted-foreground hover:text-foreground"
                    onClick={() => {
                      const all = new Set(discoverTargets.data!.discovered_services.map(s => s.base_url));
                      setSelectedScanUrls(selectedScanUrls.size === all.size ? new Set() : all);
                    }}
                  >
                    {selectedScanUrls.size === discoverTargets.data.discovered_count ? 'Deselect all' : 'Select all'}
                  </button>
                </div>
                {discoverTargets.data.discovered_services.map((svc) => (
                  <label key={svc.base_url} className="flex items-start gap-2 cursor-pointer py-1">
                    <Checkbox
                      checked={selectedScanUrls.has(svc.base_url)}
                      onCheckedChange={(checked) => {
                        const next = new Set(selectedScanUrls);
                        if (checked) next.add(svc.base_url); else next.delete(svc.base_url);
                        setSelectedScanUrls(next);
                      }}
                      className="mt-0.5"
                    />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <Badge
                          variant={
                            svc.confidence === 'high'
                              ? 'success'
                              : svc.confidence === 'medium'
                                ? 'warning'
                                : 'muted'
                          }
                          className="text-[10px] shrink-0"
                        >
                          {svc.confidence}
                        </Badge>
                        <span className="font-mono text-xs truncate text-foreground/80">{svc.service_name}</span>
                        <span className="text-xs text-muted-foreground shrink-0">in {svc.namespace}</span>
                      </div>
                      <div className="text-[11px] text-muted-foreground mt-0.5">
                        {svc.base_url}
                        {svc.gpu_count > 0 && <span className="ml-2">• {svc.gpu_count} GPU</span>}
                        {svc.image && <span className="ml-2">• {svc.image.split('/').pop()}</span>}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70 mt-0.5">{svc.reason}</div>
                    </div>
                  </label>
                ))}
              </div>
            )}

            {/* No services found */}
            {discoverTargets.data && discoverTargets.data.discovered_count === 0 && (
              <div className="rounded-md border border-border bg-muted/40 p-4 text-center text-sm">
                <p className="text-foreground">No LLM services found on this cluster.</p>
                <p className="text-xs text-muted-foreground mt-1">
                  Services are detected by name patterns (vllm, tgi, etc.), known ports, container images, and GPU requests.
                </p>
              </div>
            )}

            {/* Creation results */}
            {discoverTargets.data && discoverTargets.data.created_targets.length > 0 && (
              <div className="rounded-md border border-success/30 bg-success/10 p-3 text-sm">
                <p className="font-medium text-success mb-1">
                  Created {discoverTargets.data.created_targets.length} target(s)
                </p>
                {discoverTargets.data.created_targets.map(t => (
                  <div key={t.id} className="text-xs text-muted-foreground">
                    <span className="font-mono text-foreground/80">{t.name}</span> → {t.llm_base_url}
                  </div>
                ))}
                {discoverTargets.data.proxy_results.length > 0 && (
                  <p className="text-xs text-muted-foreground mt-2">
                    Proxies: {discoverTargets.data.proxy_results.reduce((sum, r) => sum + (r.discovered_proxies || 0), 0)} proxy type(s) discovered
                  </p>
                )}
                {(discoverTargets.data.created_configs?.length ?? 0) > 0 && (
                  <p className="text-xs text-muted-foreground mt-1">
                    Configs: {discoverTargets.data.created_configs.length} benchmark config(s) generated
                  </p>
                )}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setShowScanDialog(false); discoverTargets.reset(); setSelectedScanUrls(new Set()); }}>
              {discoverTargets.data?.created_targets.length ? 'Done' : 'Cancel'}
            </Button>
            {/* Show "Create Selected" button when there are scan results with no creation yet */}
            {discoverTargets.data && discoverTargets.data.discovered_count > 0 && !discoverTargets.data.created_targets.length && (
              <Button
                disabled={selectedScanUrls.size === 0 || discoverTargets.isPending}
                onClick={() => {
                  discoverTargets.mutate({
                    cluster_id: Number(scanClusterId),
                    auto_create: true,
                    selected_services: Array.from(selectedScanUrls),
                  });
                }}
              >
                {discoverTargets.isPending ? (
                  <><Loader2 className="h-4 w-4 mr-1 animate-spin" /> Creating...</>
                ) : (
                  <><Plus className="h-4 w-4 mr-1" /> Create {selectedScanUrls.size} target(s)</>
                )}
              </Button>
            )}
            {/* Show Scan button when no results yet or after creation (to scan again) */}
            {(!discoverTargets.data || discoverTargets.data.created_targets.length > 0) && (
              <Button
                disabled={!scanClusterId || discoverTargets.isPending}
                onClick={() => {
                  setSelectedScanUrls(new Set());
                  discoverTargets.mutate(
                    { cluster_id: Number(scanClusterId) },
                  );
                }}
              >
                {discoverTargets.isPending ? (
                  <><Loader2 className="h-4 w-4 mr-1 animate-spin" /> Scanning...</>
                ) : (
                  <><Search className="h-4 w-4 mr-1" /> {discoverTargets.data ? 'Scan again' : 'Scan cluster'}</>
                )}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
