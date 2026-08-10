/**
 * Cluster Scan Results
 *
 * Shows the results of a cluster prerequisite scan with:
 *   - Cluster info (version, distribution, node count)
 *   - Prerequisites status (cert-manager, Multus, SR-IOV, HugePages, storage, Gateway API)
 *   - BNK installation status (FLO, TMM, CNEInstance, VLANs)
 *   - Actionable recommendations (deploy/skip/upgrade/investigate)
 *
 * Visual pattern matches BNKHealthDashboard: card-based layout with severity badges.
 */

import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Server,
  Cpu,
  Network,
  Shield,
  HardDrive,
  Loader2,
  AlertCircle,
  ScanSearch,
  RefreshCw,
  Layers,
  Boxes,
  Zap,
  ChevronDown,
  ChevronRight,
  MemoryStick,
  Rocket,
  SkipForward,
  Ban,
  Search,
  Variable,
  ArrowRight,
  Info,
  CircuitBoard,
  Cable,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  useClusterScan,
  useAdaptiveModulePlan,
  useDeployHugePages,
  useProbeNodeReadiness,
} from '@/hooks/useK8s';
import { queryKeys } from '@/lib/queryKeys';
import type { BnkDeploymentSize, ClusterScanPrerequisites } from '@/types';
import { getPlatformProfileLabel } from '@/lib/platform-context';
import { HugePagesDeployDialog } from './HugePagesDeployDialog';
import {
  CisMigrationCard,
  ExistingProxiesCard,
  ScanCard,
  StatRow,
  StatusDot,
  statusConfig,
} from './migration';
import type { PrereqStatus } from './migration';

// ---- Types ----

interface ClusterScanResultsProps {
  clusterId: number;
  clusterName?: string;
}

// ---- Formatters ----

/**
 * Human-readable scan duration. Sub-second → "230 ms"; up to a minute →
 * "3.2 s"; longer → "1m 23s". Avoids the raw "52877ms" that required
 * mental math to parse.
 */
function formatScanDuration(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return seconds === 0 ? `${minutes}m` : `${minutes}m ${seconds}s`;
}

// ---- Status helpers ----

type RecSeverity = 'required' | 'recommended' | 'info';
type RecStatus = 'deploy' | 'skip' | 'upgrade' | 'investigate';

const OPTIONAL_PREREQ_KEYS = new Set<keyof ClusterScanPrerequisites>(['dpf', 'kamaji', 'existing_proxies']);

const recSeverityConfig: Record<RecSeverity, { color: string; bg: string; border: string }> = {
  required:    { color: 'text-destructive',      bg: 'bg-destructive/10', border: 'border-destructive/20' },
  recommended: { color: 'text-warning',          bg: 'bg-warning/10',     border: 'border-warning/20' },
  info:        { color: 'text-info',             bg: 'bg-info/10',        border: 'border-info/20' },
};

const recStatusConfig: Record<RecStatus, { label: string; color: string; bg: string }> = {
  deploy:      { label: 'Deploy',      color: 'text-success',          bg: 'bg-success/10' },
  skip:        { label: 'Skip',        color: 'text-muted-foreground', bg: 'bg-muted' },
  upgrade:     { label: 'Upgrade',     color: 'text-info',             bg: 'bg-info/10' },
  investigate: { label: 'Investigate', color: 'text-warning',          bg: 'bg-warning/10' },
};

// ---- Main component ----

export function ClusterScanResults({ clusterId, clusterName }: ClusterScanResultsProps) {
  const queryClient = useQueryClient();
  const scanMutation = useClusterScan();
  const deployHugePages = useDeployHugePages();
  const [hugePagesDialogOpen, setHugePagesDialogOpen] = useState(false);
  const nodeReadinessMutation = useProbeNodeReadiness();

  const handleProbeNodeReadiness = () => {
    nodeReadinessMutation.mutate({ clusterId });
  };

  const handleConfirmHugePagesDeploy = (size: BnkDeploymentSize) => {
    deployHugePages.mutate(
      { clusterId, payload: { size } },
      {
        onSuccess: () => {
          setHugePagesDialogOpen(false);
          // Re-scan so the recommendation disappears once the Job lands.
          scanMutation.mutate({ clusterId, force: true });
        },
      },
    );
  };

  // Elapsed counter for the loading state — shows honest progress while the
  // scan runs. Big clusters with tens of MB of CRDs can take a minute; a
  // still spinner makes users wonder if it's stuck.
  const [scanElapsedSec, setScanElapsedSec] = useState(0);
  useEffect(() => {
    if (!scanMutation.isPending) {
      setScanElapsedSec(0);
      return;
    }
    const start = Date.now();
    const interval = setInterval(() => {
      setScanElapsedSec(Math.floor((Date.now() - start) / 1000));
    }, 500);
    return () => clearInterval(interval);
  }, [scanMutation.isPending]);

  // Prefer the mutation's own data (freshest), fall back to any result that
  // was cached by a previous visit within the 5-minute staleTime window —
  // useClusterScan.onSuccess populates queryKeys.k8s.clusters.scan(id).
  const cachedScan = queryClient.getQueryData<typeof scanMutation.data>(
    queryKeys.k8s.clusters.scan(clusterId)
  );
  const scanData = scanMutation.data ?? cachedScan;
  const isScanning = scanMutation.isPending;

  // Auto-trigger the scan on mount when there's nothing cached. Keeps the
  // Dashboard view from landing on an empty "Scan Cluster" call-to-action —
  // the user picked a cluster, they want to see its readiness *now*. Users
  // can still click Rescan once results are shown.
  useEffect(() => {
    if (!scanData && !scanMutation.isPending && !scanMutation.isError) {
      scanMutation.mutate(clusterId);
    }
    // Intentionally only runs for this clusterId — mutation state is
    // captured by closure, and we don't want to re-scan on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clusterId]);

  // The Rescan button passes force=true so the backend bypasses its per-cluster
  // scan cache and runs a fresh scan. The auto-scan on mount uses the cached
  // path (force=false) so tab switches / page reloads stay instant.
  const handleScan = () => {
    scanMutation.mutate({ clusterId, force: true });
  };


  // --- No scan yet: show loading state (scan auto-runs on mount) ---
  if (!scanData && !scanMutation.isError) {
    return (
      <div className="space-y-4">
        <div className={cn(
          'rounded-lg border p-8 text-center',
          'bg-card border-border',
        )}>
          {isScanning ? (
            <>
              <Loader2 className={cn('h-12 w-12 mx-auto mb-4 animate-spin', 'text-primary')} />
              <h3 className="text-lg font-semibold mb-2">
                Scanning cluster for BNK readiness
                <span className={cn('ml-2 font-mono text-sm tabular-nums', 'text-muted-foreground')}>
                  {scanElapsedSec}s
                </span>
              </h3>
              <p className={cn('text-sm max-w-lg mx-auto', 'text-muted-foreground')}>
                Checking {clusterName ? <span className="font-medium">{clusterName}</span> : 'this cluster'} for
                prerequisites (cert-manager, Multus, SR-IOV, HugePages, storage, Gateway API)
                and any existing F5 BIG-IP Next for Kubernetes installation.
                Usually a few seconds — results are cached for 10 minutes.
              </p>
            </>
          ) : (
            <>
              <ScanSearch className={cn('h-12 w-12 mx-auto mb-4', 'text-muted-foreground')} />
              <h3 className="text-lg font-semibold mb-2">Scan Cluster Prerequisites</h3>
              <p className={cn('text-sm mb-6 max-w-lg mx-auto', 'text-muted-foreground')}>
                Detect installed prerequisites and any existing F5 BNK installation on{' '}
                {clusterName ? <span className="font-medium">{clusterName}</span> : 'this cluster'}.
              </p>
              <Button onClick={handleScan} size="lg">
                <ScanSearch className="h-4 w-4 mr-2" />
                Scan Cluster
              </Button>
            </>
          )}
        </div>
      </div>
    );
  }

  // --- Error state ---
  if (scanMutation.isError && !scanData) {
    return (
      <div className="flex flex-col items-center justify-center py-20 gap-3">
        <AlertCircle className="h-10 w-10 text-destructive" />
        <p className="text-sm text-destructive">Cluster scan failed</p>
        <p className="text-xs text-muted-foreground">{(scanMutation.error as Error)?.message || 'Unknown error'}</p>
        <Button variant="outline" size="sm" onClick={handleScan}>
          <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
          Retry
        </Button>
      </div>
    );
  }

  // --- Scan results ---
  const { cluster_info, prerequisites, bnk_install, recommendations, scan_metadata } = scanData!;
  const detectedPlatformProfile = scanData?.platform_context?.detected_platform_profile;
  const isGenericOnpremBaseline = detectedPlatformProfile === 'generic_onprem';

  // Effective enabled-prereq IDs for this cluster (backend resolves the
  // user's per-cluster selection or falls back to defaults). Used to hide
  // status cards the user opted out of.
  const enabledPrereqs = new Set<string>(
    ((scanData as unknown as { enabled_prerequisites?: string[] })?.enabled_prerequisites) ?? [
      'cert-manager', 'multus', 'storage', 'gateway-api',
    ],
  );
  const showPrereq = (id: string) => enabledPrereqs.has(id);

  // Map prereq dict keys (cert_manager, gateway_api…) to canonical IDs
  // (cert-manager, gateway-api…) used by the per-cluster enabled set.
  const PREREQ_KEY_TO_ID: Record<string, string> = {
    cert_manager: 'cert-manager',
    multus: 'multus',
    sriov: 'sriov',
    hugepages: 'hugepages',
    storage: 'storage',
    gateway_api: 'gateway-api',
  };
  const isPrereqEnabledByKey = (key: string) => {
    const id = PREREQ_KEY_TO_ID[key];
    return id ? enabledPrereqs.has(id) : true;
  };

  // Overall summary should reflect required prerequisites only.
  // Optional prerequisites (DPF/Kamaji) stay visible as cards but should not
  // make the top-level readiness summary fail when they are missing.
  const requiredPrereqStatuses = Object.entries(prerequisites || {}).flatMap(([key, prereq]) => {
    if (OPTIONAL_PREREQ_KEYS.has(key as keyof ClusterScanPrerequisites)) {
      return [];
    }
    if (!isPrereqEnabledByKey(key)) {
      return [];
    }

    const status = (prereq as { status?: PrereqStatus } | undefined)?.status;
    return status ? [{ key, status }] : [];
  });

  // A missing prereq only counts toward the red "Prerequisites Missing" banner
  // if the backend emitted a recommendation for it with severity==='required'
  // and status==='deploy'. This prevents prereqs like Gateway API (severity
  // 'info', FLO auto-installs) from triggering the alarm banner.
  const requiredSeverityIds = new Set(
    (recommendations ?? [])
      .filter(
        (r) =>
          (r as { category?: string }).category === 'prerequisite' &&
          r.severity === 'required' &&
          r.status === 'deploy',
      )
      .map((r) => r.id),
  );

  const requiredMissingCount = requiredPrereqStatuses.filter(
    ({ key, status }) =>
      status === 'missing' &&
      requiredSeverityIds.has(PREREQ_KEY_TO_ID[key] ?? key),
  ).length;
  const requiredPartialCount = requiredPrereqStatuses.filter(({ status }) => status === 'partial').length;
  const requiredDetectedCount = requiredPrereqStatuses.filter(({ status }) => status === 'detected').length;

  const bnkStatus = bnk_install?.status as string;
  const overallStatus: PrereqStatus =
    bnkStatus === 'installed' && requiredMissingCount === 0 ? 'detected' :
    requiredMissingCount > 0 ? 'missing' :
    requiredPartialCount > 0 ? 'partial' : 'detected';

  const overallConfig = statusConfig[overallStatus];
  const OverallIcon = overallConfig.icon;

  const deployRecs = recommendations?.filter((r) => r.status === 'deploy') || [];
  const skipRecs = recommendations?.filter((r) => r.status === 'skip') || [];
  const investigateRecs = recommendations?.filter((r) => r.status === 'investigate') || [];

  return (
    <div className="space-y-6">
      {/* Overall Status Banner */}
      <div className={cn(
        'rounded-lg border p-4 flex items-center justify-between',
        overallConfig.bg,
        overallConfig.border,
      )}>
        <div className="flex items-center gap-3">
          <OverallIcon className={cn('h-6 w-6', overallConfig.color)} />
          <div>
            <h2 className="font-semibold text-lg">
              {overallStatus === 'detected' ? 'Cluster Ready' :
               overallStatus === 'missing' ? 'Prerequisites Missing' :
               overallStatus === 'partial' ? 'Partial Configuration' : 'Scan Complete'}
            </h2>
            <p className={cn('text-sm', 'text-muted-foreground')}>
              {cluster_info?.distribution} {cluster_info?.version}
              {' · '}
              {cluster_info?.nodes_ready}/{cluster_info?.node_count} nodes ready
              {' · '}
              {requiredDetectedCount}/{requiredPrereqStatuses.length} prerequisites detected
              {bnkStatus === 'installed' && ' · BNK installed'}
            </p>
            {isGenericOnpremBaseline && (
              <p className={cn('text-xs mt-1', 'text-muted-foreground')}>
                Baseline profile: {getPlatformProfileLabel(detectedPlatformProfile)} —
                current recommendations reflect kubeconfig-first, manual-friendly assumptions.
              </p>
            )}
          </div>
        </div>
        <div className="flex items-center gap-3">
          {scan_metadata?.duration_ms && (
            <span className={cn('text-xs', 'text-muted-foreground')}>
              Scanned in {formatScanDuration(scan_metadata.duration_ms)}
            </span>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={handleScan}
            disabled={isScanning}
          >
            <RefreshCw className={cn('h-3.5 w-3.5', isScanning && 'animate-spin')} />
          </Button>
        </div>
      </div>

      {/* Recommendations Summary */}
      {(deployRecs.length > 0 || investigateRecs.length > 0) && (
        <div className={cn(
          'rounded-lg border p-5',
          'bg-card border-border',
        )}>
          <h3 className="font-semibold text-sm mb-4 flex items-center gap-2">
            <Zap className="h-4 w-4 text-primary" />
            Recommendations
          </h3>
          <div className="space-y-3">
            {[...deployRecs, ...investigateRecs].map((rec) => {
              const sevCfg = recSeverityConfig[rec.severity as RecSeverity] || recSeverityConfig.info;
              const statusCfg = recStatusConfig[rec.status as RecStatus] || recStatusConfig.investigate;
              const isHugePagesDeploy = rec.id === 'hugepages' && rec.status === 'deploy';
              return (
                <div
                  key={rec.id}
                  className={cn(
                    'flex items-start gap-3 p-3 rounded-lg border',
                    'border-border bg-muted/50',
                  )}
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-sm font-medium">{rec.title}</span>
                      <Badge className={cn('text-[10px] h-5', sevCfg.bg, sevCfg.color, sevCfg.border)}>
                        {rec.severity}
                      </Badge>
                      {!isHugePagesDeploy && (
                        <Badge className={cn('text-[10px] h-5', statusCfg.bg, statusCfg.color)}>
                          {statusCfg.label}
                        </Badge>
                      )}
                    </div>
                    <p className={cn('text-xs', 'text-muted-foreground')}>
                      {rec.description}
                    </p>
                    {rec.module && (
                      <p className={cn('text-xs mt-1 font-mono', 'text-muted-foreground')}>
                        Module: {rec.module}
                      </p>
                    )}
                  </div>
                  {isHugePagesDeploy && (
                    <Button
                      size="sm"
                      variant="default"
                      onClick={() => setHugePagesDialogOpen(true)}
                      disabled={deployHugePages.isPending}
                      className="shrink-0"
                    >
                      {deployHugePages.isPending ? (
                        <>
                          <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
                          Deploying
                        </>
                      ) : (
                        <>
                          <Rocket className="mr-1.5 h-3 w-3" />
                          Deploy
                        </>
                      )}
                    </Button>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Detail Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">

        {/* Cluster Info */}
        <ScanCard
          title="Cluster"
          icon={Server}
          status={cluster_info?.nodes_ready === cluster_info?.node_count ? 'detected' : 'partial'}
        >
          <StatRow label="Distribution" value={cluster_info?.distribution || 'Unknown'} />
          <StatRow label="Version" value={cluster_info?.version || 'Unknown'} mono />
          <StatRow label="Nodes" value={cluster_info?.nodes_ready ?? 0} total={cluster_info?.node_count ?? 0} suffix="ready" />
          {cluster_info?.hp_nodes > 0 && (
            <StatRow label="HP Nodes (f5-tmm)" value={cluster_info.hp_nodes} status="detected" />
          )}
          <StatRow label="Namespaces" value={cluster_info?.namespaces ?? 0} />
          {cluster_info?.cloud_provider && (
            <StatRow label="Cloud" value={`${cluster_info.cloud_provider}${cluster_info.region ? ` (${cluster_info.region})` : ''}`} />
          )}
          {cluster_info?.hp_node_details?.length > 0 && (
            <div className={cn('pt-2 border-t', 'border-border')}>
              <p className={cn('text-xs mb-2', 'text-muted-foreground')}>
                High-Performance Nodes
              </p>
              {cluster_info.hp_node_details.map((n) => (
                <div key={n.name} className={cn(
                  'flex items-center justify-between text-xs',
                  'text-muted-foreground',
                )}>
                  <span className="font-mono truncate max-w-[160px]">{n.name}</span>
                  <span>{n.instance_type}</span>
                </div>
              ))}
            </div>
          )}
        </ScanCard>

        {/* cert-manager */}
        {showPrereq('cert-manager') && (
        <ScanCard
          title="cert-manager"
          icon={Shield}
          status={prerequisites?.cert_manager?.status || 'unknown'}
        >
          {prerequisites?.cert_manager?.version && (
            <StatRow label="Version" value={`v${prerequisites.cert_manager.version}`} mono />
          )}
          <StatRow
            label="CRDs"
            value={prerequisites?.cert_manager?.crd_count ?? 0}
            status={prerequisites?.cert_manager?.crds_installed ? 'detected' : 'missing'}
          />
          <StatRow
            label="Pods Running"
            value={prerequisites?.cert_manager?.pods?.total_running ?? 0}
            status={
              (prerequisites?.cert_manager?.pods?.total_running ?? 0) > 0 ? 'detected' : 'missing'
            }
          />
          {(prerequisites?.cert_manager?.pods?.total_running ?? 0) > 0 && (
            <>
              <StatRow label="  Controller" value={prerequisites.cert_manager.pods.controller} />
              <StatRow label="  Webhook" value={prerequisites.cert_manager.pods.webhook} />
              <StatRow label="  CA Injector" value={prerequisites.cert_manager.pods.cainjector} />
            </>
          )}
          {prerequisites?.cert_manager?.helm_release && (
            <div className={cn('text-xs', 'text-muted-foreground')}>
              Helm: {prerequisites.cert_manager.helm_release.name} ({prerequisites.cert_manager.helm_release.status})
            </div>
          )}
        </ScanCard>
        )}

        {/* Multus CNI */}
        {showPrereq('multus') && (
        <ScanCard
          title="Multus CNI"
          icon={Network}
          status={prerequisites?.multus?.status || 'unknown'}
        >
          <StatRow
            label="NAD CRD"
            value={prerequisites?.multus?.nad_crd_installed ? 'Installed' : 'Not Found'}
            status={prerequisites?.multus?.nad_crd_installed ? 'detected' : 'missing'}
          />
          <StatRow
            label="Running Pods"
            value={prerequisites?.multus?.running_pods ?? 0}
            status={
              (prerequisites?.multus?.running_pods ?? 0) > 0 ? 'detected' : 'missing'
            }
          />
          {prerequisites?.multus?.daemonset && (
            <>
              <StatRow
                label="DaemonSet"
                value={prerequisites.multus.daemonset.name}
              />
              <StatRow
                label="Ready / Desired"
                value={prerequisites.multus.daemonset.ready}
                total={prerequisites.multus.daemonset.desired}
              />
            </>
          )}
        </ScanCard>
        )}

        {/* SR-IOV */}
        {showPrereq('sriov') && (
        <ScanCard
          title="SR-IOV"
          icon={Cpu}
          status={prerequisites?.sriov?.status || 'unknown'}
        >
          <StatRow
            label="Device Plugin"
            value={prerequisites?.sriov?.device_plugin ? 'Installed' : 'Not Found'}
            status={prerequisites?.sriov?.device_plugin ? 'detected' : 'missing'}
          />
          <StatRow
            label="Nodes with VFs"
            value={prerequisites?.sriov?.nodes_with_vfs ?? 0}
          />
          <StatRow
            label="Total VFs"
            value={prerequisites?.sriov?.total_vfs ?? 0}
          />
          {prerequisites?.sriov?.device_plugin && (
            <div className={cn('text-xs font-mono', 'text-muted-foreground')}>
              {prerequisites.sriov.device_plugin.name}
              {' '}({prerequisites.sriov.device_plugin.ready}/{prerequisites.sriov.device_plugin.desired})
            </div>
          )}
          {prerequisites?.sriov?.node_details?.length > 0 && (
            <div className={cn('pt-2 border-t', 'border-border')}>
              <p className={cn('text-xs mb-2', 'text-muted-foreground')}>
                Node VF Resources
              </p>
              {prerequisites.sriov.node_details.map((n) => (
                <div key={n.name} className={cn(
                  'flex items-center justify-between text-xs mb-1',
                  'text-muted-foreground',
                )}>
                  <span className="font-mono truncate max-w-[140px]">{n.name}</span>
                  <span>{n.vf_count} VFs ({n.instance_type})</span>
                </div>
              ))}
            </div>
          )}
        </ScanCard>
        )}

        {/* HugePages */}
        {showPrereq('hugepages') && (
        <ScanCard
          title="HugePages"
          icon={MemoryStick}
          status={prerequisites?.hugepages?.status || 'unknown'}
        >
          <StatRow
            label="Nodes with HugePages"
            value={prerequisites?.hugepages?.nodes_with_hugepages ?? 0}
          />
          {prerequisites?.hugepages?.node_details?.map((n) => (
            <div key={n.name} className={cn(
              'flex items-center justify-between text-xs',
              'text-muted-foreground',
            )}>
              <span className="font-mono truncate max-w-[140px]">{n.name}</span>
              <div className="flex gap-2">
                {n.hugepages_2mi && n.hugepages_2mi !== '0' && (
                  <Badge variant="outline" className="text-[10px]">2Mi: {n.hugepages_2mi}</Badge>
                )}
                {n.hugepages_1gi && n.hugepages_1gi !== '0' && (
                  <Badge variant="outline" className="text-[10px]">1Gi: {n.hugepages_1gi}</Badge>
                )}
              </div>
            </div>
          ))}
          {(prerequisites?.hugepages?.nodes_with_hugepages ?? 0) === 0 && (
            <p className={cn('text-xs', 'text-muted-foreground')}>
              No HugePages configured. Consider enabling 2Mi or 1Gi HugePages on high-performance nodes for TMM.
            </p>
          )}
        </ScanCard>
        )}

        {/* Storage Classes */}
        {showPrereq('storage') && (
        <ScanCard
          title="Storage"
          icon={HardDrive}
          status={prerequisites?.storage?.status || 'unknown'}
        >
          <StatRow
            label="Storage Classes"
            value={prerequisites?.storage?.count ?? 0}
          />
          {prerequisites?.storage?.default && (
            <StatRow
              label="Default"
              value={prerequisites.storage.default}
              mono
            />
          )}
          {!prerequisites?.storage?.default && (prerequisites?.storage?.count ?? 0) > 0 && (
            <div className="flex items-center gap-1.5">
              <AlertTriangle className="h-3.5 w-3.5 text-warning" />
              <span className="text-xs text-warning">No default storage class set</span>
            </div>
          )}
          {prerequisites?.storage?.classes?.length > 0 && (
            <div className={cn('pt-2 border-t', 'border-border')}>
              {prerequisites.storage.classes.map((sc) => (
                <div key={sc.name} className={cn(
                  'flex items-center justify-between text-xs mb-1',
                  'text-muted-foreground',
                )}>
                  <span className={cn('font-mono', sc.is_default && 'font-semibold text-primary')}>
                    {sc.name}{sc.is_default ? ' (default)' : ''}
                  </span>
                  <span className="truncate max-w-[160px] text-right">{sc.provisioner}</span>
                </div>
              ))}
            </div>
          )}
        </ScanCard>
        )}

        {/* Gateway API */}
        {showPrereq('gateway-api') && (
        <ScanCard
          title="Gateway API"
          icon={Layers}
          status={prerequisites?.gateway_api?.status || 'unknown'}
        >
          <StatRow label="CRDs Installed" value={prerequisites?.gateway_api?.crds_installed ?? 0} />
          <StatRow label="GatewayClasses" value={prerequisites?.gateway_api?.gatewayclasses ?? 0} />
          <StatRow label="Gateways" value={prerequisites?.gateway_api?.gateways ?? 0} />
          {prerequisites?.gateway_api?.api_versions?.length > 0 && (
            <div className="flex items-center gap-2">
              <span className={cn('text-xs', 'text-muted-foreground')}>API Versions:</span>
              <div className="flex gap-1">
                {prerequisites.gateway_api.api_versions.map((v: string) => (
                  <Badge key={v} variant="outline" className="text-[10px] font-mono">{v}</Badge>
                ))}
              </div>
            </div>
          )}
          {prerequisites?.gateway_api?.standard_crds_missing?.length > 0 && (
            <div className={cn('pt-2 border-t', 'border-border')}>
              <p className={cn('text-xs mb-1', 'text-muted-foreground')}>
                Missing standard CRDs:
              </p>
              {prerequisites.gateway_api.standard_crds_missing.map((crd: string) => (
                <div key={crd} className="flex items-center gap-1.5 text-xs text-warning">
                  <XCircle className="h-3 w-3" />
                  <span className="font-mono">{crd}</span>
                </div>
              ))}
            </div>
          )}
          {prerequisites?.gateway_api?.status === 'missing' && (
            <p className={cn('text-xs', 'text-muted-foreground')}>
              Gateway API CRDs are installed when BNK is deployed (FLO installs them automatically in the
              Forge deploy flow; a manual/helm install must install the Gateway API CRDs itself).
            </p>
          )}
        </ScanCard>
        )}

        {/* NVIDIA DPF — optional add-on, only render when actually present */}
        {prerequisites?.dpf && prerequisites.dpf.status !== 'missing' && (
          <ScanCard
            title="NVIDIA DPF"
            icon={CircuitBoard}
            status={prerequisites.dpf.status || 'unknown'}
            collapsible
            defaultOpen
          >
            <>
              {prerequisites.dpf.version && (
                  <StatRow label="Version" value={prerequisites.dpf.version} mono />
                )}
                <StatRow
                  label="CRDs Installed"
                  value={prerequisites.dpf.crds_installed}
                  status={prerequisites.dpf.crds_installed > 0 ? 'detected' : 'missing'}
                />
                <StatRow
                  label="Operator"
                  value={prerequisites.dpf.operator?.ready ? 'Ready' : prerequisites.dpf.operator?.configured ? 'Configured' : 'Not Found'}
                  status={prerequisites.dpf.operator?.ready ? 'detected' : prerequisites.dpf.operator?.configured ? 'partial' : 'missing'}
                />
                <StatRow
                  label="DPU Devices"
                  value={prerequisites.dpf.devices?.ready ?? 0}
                  total={prerequisites.dpf.devices?.total ?? 0}
                  suffix="ready"
                  status={(prerequisites.dpf.devices?.ready ?? 0) > 0 ? 'detected' : (prerequisites.dpf.devices?.total ?? 0) > 0 ? 'partial' : 'missing'}
                />
                <StatRow label="DPU Clusters" value={prerequisites.dpf.dpuclusters ?? 0} />
                <StatRow label="DPU Sets" value={prerequisites.dpf.dpusets ?? 0} />
                <StatRow label="BFB Images" value={prerequisites.dpf.bfbs ?? 0} />
                <StatRow label="DPU Services" value={prerequisites.dpf.dpuservices ?? 0} />

                {/* Operator conditions */}
                {prerequisites.dpf.operator?.conditions?.length > 0 && (
                  <div className={cn('pt-2 border-t', 'border-border')}>
                    <p className={cn('text-xs mb-2', 'text-muted-foreground')}>
                      Operator Conditions
                    </p>
                    {prerequisites.dpf.operator.conditions.map((c) => (
                      <div key={c.type} className={cn(
                        'flex items-center justify-between text-xs mb-1',
                        'text-muted-foreground',
                      )}>
                        <div className="flex items-center gap-1.5">
                          <StatusDot status={c.status === 'True' ? 'detected' : 'partial'} />
                          <span>{c.type}</span>
                        </div>
                        <span className="font-mono text-[10px]">{c.reason || c.status}</span>
                      </div>
                    ))}
                  </div>
                )}

                {/* Core CRDs missing */}
                {prerequisites.dpf.core_crds_missing?.length > 0 && (
                  <div className={cn('pt-2 border-t', 'border-border')}>
                    <p className={cn('text-xs mb-1', 'text-muted-foreground')}>
                      Missing core CRDs:
                    </p>
                    {prerequisites.dpf.core_crds_missing.map((crd: string) => (
                      <div key={crd} className="flex items-center gap-1.5 text-xs text-warning">
                        <XCircle className="h-3 w-3" />
                        <span className="font-mono text-[10px]">{crd}</span>
                      </div>
                    ))}
                  </div>
                )}

              {prerequisites.dpf.helm_release && (
                <div className={cn('text-xs', 'text-muted-foreground')}>
                  Helm: {prerequisites.dpf.helm_release.name} ({prerequisites.dpf.helm_release.status})
                </div>
              )}
            </>
          </ScanCard>
        )}

        {/* Kamaji — optional add-on, only render when actually present */}
        {prerequisites?.kamaji && prerequisites.kamaji.status !== 'missing' && (
          <ScanCard
            title="Kamaji Control Plane"
            icon={Layers}
            status={prerequisites.kamaji.status || 'unknown'}
            collapsible
            defaultOpen
          >
            <>
              {prerequisites.kamaji.version && (
                  <StatRow label="Version" value={prerequisites.kamaji.version} mono />
                )}
                <StatRow
                  label="CRDs Installed"
                  value={prerequisites.kamaji.crds_installed}
                  status={prerequisites.kamaji.crds_installed > 0 ? 'detected' : 'missing'}
                />
                <StatRow
                  label="Pods Running"
                  value={prerequisites.kamaji.pods_running}
                  status={prerequisites.kamaji.pods_running > 0 ? 'detected' : 'missing'}
                />
                <StatRow
                  label="Tenant Control Planes"
                  value={prerequisites.kamaji.tenant_control_planes}
                  status={prerequisites.kamaji.tenant_control_planes > 0 ? 'detected' : 'missing'}
                />

                {/* Core CRDs missing */}
                {prerequisites.kamaji.core_crds_missing?.length > 0 && (
                  <div className={cn('pt-2 border-t', 'border-border')}>
                    <p className={cn('text-xs mb-1', 'text-muted-foreground')}>
                      Missing core CRDs:
                    </p>
                    {prerequisites.kamaji.core_crds_missing.map((crd: string) => (
                      <div key={crd} className="flex items-center gap-1.5 text-xs text-warning">
                        <XCircle className="h-3 w-3" />
                        <span className="font-mono text-[10px]">{crd}</span>
                      </div>
                    ))}
                  </div>
                )}

              {prerequisites.kamaji.helm_release && (
                <div className={cn('text-xs', 'text-muted-foreground')}>
                  Helm: {prerequisites.kamaji.helm_release.name} ({prerequisites.kamaji.helm_release.status})
                </div>
              )}
            </>
          </ScanCard>
        )}

        {/* Existing Proxies — only render when at least one is detected */}
        {prerequisites?.existing_proxies && prerequisites.existing_proxies.discovered_count > 0 && (
          <ExistingProxiesCard proxies={prerequisites.existing_proxies.proxies} clusterId={clusterId} />
        )}

        {/* CIS / BIG-IP Migration (D-023 P3) — only render when CIS is detected */}
        {prerequisites?.cis && prerequisites.cis.status !== 'missing' && (
          <CisMigrationCard cis={prerequisites.cis} clusterId={clusterId} />
        )}


        {/* BNK Installation */}
        <ScanCard
          title="F5 BNK Installation"
          icon={Boxes}
          status={
            bnk_install?.status === 'installed' ? 'detected' :
            bnk_install?.status === 'partial' ? 'partial' :
            bnk_install?.status === 'not_installed' ? 'missing' : 'unknown'
          }
          className="md:col-span-2"
        >
          {bnk_install?.status === 'not_installed' ? (
            <p className={cn('text-sm', 'text-muted-foreground')}>
              No F5 BNK components detected. Use the BNK stack to deploy a full installation.
            </p>
          ) : (
            <>
              {/* Install method */}
              {bnk_install?.install_shape && bnk_install.install_shape !== 'unknown' && (
                <StatRow
                  label="Install Method"
                  value={bnk_install.install_shape === 'flo' ? 'FLO deploy flow' : 'Helm / manual'}
                  status="detected"
                />
              )}

              {/* Namespaces */}
              <div className="flex items-center gap-3">
                <span className={cn('text-sm', 'text-muted-foreground')}>Namespaces</span>
                <div className="flex gap-1.5">
                  <Badge
                    variant="outline"
                    className={cn('text-xs gap-1',
                      bnk_install?.namespaces?.f5_operator
                        ? 'border-success/20 text-success'
                        : 'border-border text-muted-foreground'
                    )}
                  >
                    {bnk_install?.namespaces?.f5_operator ? <CheckCircle2 className="h-3 w-3" /> : <XCircle className="h-3 w-3" />}
                    f5-operator
                  </Badge>
                  <Badge
                    variant="outline"
                    className={cn('text-xs gap-1',
                      bnk_install?.namespaces?.f5_utils
                        ? 'border-success/20 text-success'
                        : 'border-border text-muted-foreground'
                    )}
                  >
                    {bnk_install?.namespaces?.f5_utils ? <CheckCircle2 className="h-3 w-3" /> : <XCircle className="h-3 w-3" />}
                    f5-utils
                  </Badge>
                </div>
              </div>

              {/* F5 CRDs */}
              <StatRow
                label="F5 CRDs"
                value={bnk_install?.crds?.total ?? 0}
                status={(bnk_install?.crds?.total ?? 0) > 0 ? 'detected' : 'missing'}
              />
              {bnk_install?.crds?.groups?.length > 0 && (
                <div className="flex flex-wrap gap-1 pl-4">
                  {bnk_install.crds.groups.map((g: string) => (
                    <Badge key={g} variant="outline" className="text-[10px] font-mono">{g}</Badge>
                  ))}
                </div>
              )}

              {/* FLO (only meaningful for a confirmed FLO deploy flow install) */}
              {bnk_install?.install_shape === 'flo' && (
                <>
                  <StatRow
                    label="FLO Operator"
                    value={bnk_install?.flo?.running ?? 0}
                    total={bnk_install?.flo?.pods ?? 0}
                    suffix="running"
                    status={
                      (bnk_install?.flo?.running ?? 0) > 0 ? 'detected' :
                      (bnk_install?.flo?.pods ?? 0) > 0 ? 'partial' : 'missing'
                    }
                  />
                  {bnk_install?.flo?.version && (
                    <StatRow label="FLO Version" value={bnk_install.flo.version} mono />
                  )}
                </>
              )}

              {/* TMM */}
              <StatRow
                label="TMM Pods"
                value={bnk_install?.tmm?.running ?? 0}
                total={bnk_install?.tmm?.pods ?? 0}
                suffix="running"
                status={
                  (bnk_install?.tmm?.running ?? 0) > 0 ? 'detected' :
                  (bnk_install?.tmm?.pods ?? 0) > 0 ? 'partial' : 'missing'
                }
              />
              {bnk_install?.tmm?.containers && (
                <StatRow
                  label="TMM Containers"
                  value={bnk_install.tmm.containers.containers}
                  suffix="ready"
                />
              )}

              {/* Controller + Analyzer */}
              <StatRow
                label="CNE Controller"
                value={bnk_install?.controller?.running ?? 0}
                total={bnk_install?.controller?.pods ?? 0}
                suffix="running"
              />
              {(bnk_install?.analyzer?.pods ?? 0) > 0 && (
                <StatRow
                  label="F5 Analyzer"
                  value={bnk_install?.analyzer?.running ?? 0}
                  total={bnk_install?.analyzer?.pods ?? 0}
                  suffix="running"
                />
              )}

              {/* CRD Installer (FLO deploy flow artifact — no crd-installer Job on helm/manual installs) */}
              {bnk_install?.install_shape === 'flo' && (
                <StatRow
                  label="CRD Installer"
                  value={bnk_install?.crd_installer?.completed ? 'Completed' : 'Pending'}
                  status={bnk_install?.crd_installer?.completed ? 'detected' : 'partial'}
                />
              )}

              {/* CNEInstance */}
              {bnk_install?.cne_instance && (
                <div className={cn('pt-2 border-t', 'border-border')}>
                  <p className={cn('text-xs mb-2', 'text-muted-foreground')}>
                    CNEInstance: {bnk_install.cne_instance.name}
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {Object.entries(bnk_install.cne_instance.features || {}).map(([key, enabled]) => (
                      <Badge
                        key={key}
                        variant="outline"
                        className={cn(
                          'text-xs gap-1',
                          enabled
                            ? 'border-success/20 text-success bg-success/5'
                            : 'border-border text-muted-foreground bg-muted'
                        )}
                      >
                        {enabled ? <CheckCircle2 className="h-3 w-3" /> : <XCircle className="h-3 w-3" />}
                        {key}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              {/* VLANs */}
              {bnk_install?.vlans?.length > 0 && (
                <div className={cn('pt-2 border-t', 'border-border')}>
                  <p className={cn('text-xs mb-2', 'text-muted-foreground')}>
                    VLANs
                  </p>
                  {bnk_install.vlans.map((v) => (
                    <div key={v.name} className={cn(
                      'flex items-center justify-between text-xs mb-1',
                      'text-muted-foreground',
                    )}>
                      <div className="flex items-center gap-2">
                        <StatusDot status={v.programmed ? 'detected' : 'partial'} />
                        <span className="font-mono">{v.name}</span>
                      </div>
                      <span className="font-mono">
                        {v.self_ips?.join(', ')} ({v.interfaces?.join(', ')})
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </ScanCard>
      </div>

      {/* All OK summary */}
      {skipRecs.length > 0 && deployRecs.length === 0 && investigateRecs.length === 0 && (
        <div className={cn(
          'rounded-lg border p-4 flex items-center gap-3',
          'bg-success/10 border-success/20',
        )}>
          <CheckCircle2 className="h-5 w-5 text-success" />
          <div>
            <p className="text-sm font-medium text-success">All prerequisites detected</p>
            <p className={cn('text-xs', 'text-muted-foreground')}>
              {skipRecs.length} components verified. Cluster is ready for BNK deployment.
            </p>
          </div>
        </div>
      )}

      {/* Deploy BNK — Step 1: cluster prerequisites (read-only node readiness
          detection), Step 2: the adaptive deployment plan. Node readiness used
          to sit as a standalone scan card; it's now grouped with the deployment
          plan so the deploy flow reads as one sequence (issue #387 follow-up).
          This dashboard informs; it does not remediate — no "prepare cluster"
          action lives here. */}
      <div className="space-y-4">
        <h2 className="font-semibold text-base flex items-center gap-2">
          <Rocket className="h-4 w-4 text-primary" />
          Deploy BNK
        </h2>

        {/* Step 1 · Cluster prerequisites (issue #387 part A — node readiness
            detection only; informational, no remediation action here) */}
        <ScanCard
          title="Step 1 · Cluster Prerequisites"
          icon={Cable}
          status={
            nodeReadinessMutation.data
              ? nodeReadinessMutation.data.all_ready
                ? 'detected'
                : 'missing'
              : 'unknown'
          }
        >
          <div className="flex items-center gap-2 flex-wrap">
            {cluster_info?.is_kind !== undefined && (
              <Badge
                variant="outline"
                className={cn('text-[10px]', cluster_info.is_kind && 'border-info/20 text-info')}
              >
                {cluster_info.is_kind ? 'kind cluster' : 'not kind'}
              </Badge>
            )}
            {cluster_info?.is_local !== undefined && (
              <Badge
                variant="outline"
                className={cn('text-[10px]', cluster_info.is_local && 'border-info/20 text-info')}
              >
                {cluster_info.is_local ? 'local / lab cluster' : 'not local'}
              </Badge>
            )}
          </div>

          <p className={cn('text-xs', 'text-muted-foreground')}>
            Checks node-level prerequisites F5 TMM needs: CNI delegate plugins
            (macvlan / host-device / ipvlan) in <code className="font-mono">/opt/cni/bin</code>,
            kernel <code className="font-mono">core_pattern</code> (a bare &quot;core&quot;{' '}
            crashes F5&apos;s crashagent), and 2Mi hugepages capacity. Requires a
            privileged node probe — not part of the normal scan.
          </p>

          <div className="flex items-center gap-2 flex-wrap">
            <Button
              size="sm"
              variant="outline"
              onClick={handleProbeNodeReadiness}
              disabled={nodeReadinessMutation.isPending}
            >
              {nodeReadinessMutation.isPending ? (
                <>
                  <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
                  Probing nodes...
                </>
              ) : (
                <>
                  <Cable className="mr-1.5 h-3 w-3" />
                  {nodeReadinessMutation.data ? 'Re-check node readiness' : 'Check node readiness (CNI / core_pattern)'}
                </>
              )}
            </Button>
            <span className={cn('text-[10px]', 'text-muted-foreground')}>
              Dispatches a short-lived privileged probe pod to each node.
            </span>
          </div>

          {nodeReadinessMutation.isError && (
            <div className="flex items-center gap-1.5 text-xs text-destructive">
              <AlertCircle className="h-3.5 w-3.5" />
              <span>{(nodeReadinessMutation.error as Error)?.message || 'Probe failed'}</span>
            </div>
          )}

          {nodeReadinessMutation.data && (
            <div className="space-y-2 pt-1">
              {nodeReadinessMutation.data.nodes.map((n) => (
                <div key={n.node} className={cn('rounded border p-2 text-xs', 'border-border bg-muted/50')}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-mono truncate max-w-[160px]">{n.node}</span>
                    {n.cni_ok && n.core_pattern_ok ? (
                      <CheckCircle2 className="h-3.5 w-3.5 text-success" />
                    ) : (
                      <XCircle className="h-3.5 w-3.5 text-destructive" />
                    )}
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {(['macvlan', 'host_device', 'ipvlan'] as const).map((plugin) => (
                      <Badge
                        key={plugin}
                        variant="outline"
                        className={cn(
                          'text-[10px] gap-1',
                          n.cni_plugins[plugin]
                            ? 'border-success/20 text-success'
                            : 'border-destructive/20 text-destructive',
                        )}
                      >
                        {n.cni_plugins[plugin] ? (
                          <CheckCircle2 className="h-2.5 w-2.5" />
                        ) : (
                          <XCircle className="h-2.5 w-2.5" />
                        )}
                        {plugin === 'host_device' ? 'host-device' : plugin}
                      </Badge>
                    ))}
                    <Badge
                      variant="outline"
                      className={cn(
                        'text-[10px] font-mono',
                        n.core_pattern_ok
                          ? 'border-success/20 text-success'
                          : 'border-destructive/20 text-destructive',
                      )}
                    >
                      core_pattern: {n.core_pattern ?? 'unknown'}
                    </Badge>
                  </div>
                  {!n.core_pattern_ok && (
                    <p className="text-[10px] text-warning mt-1">
                      A bare &quot;core&quot; core_pattern is incompatible with F5&apos;s crashagent.
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
        </ScanCard>

        {/* Step 2 · Deployment plan */}
        <DeploymentPlanSection clusterId={clusterId} />
      </div>

      {/* HugePages Deploy Dialog */}
      <HugePagesDeployDialog
        open={hugePagesDialogOpen}
        onOpenChange={setHugePagesDialogOpen}
        onConfirm={handleConfirmHugePagesDeploy}
        isLoading={deployHugePages.isPending}
      />
    </div>
  );
}


// ---- Deployment Plan Section ----

type ModuleActionType = 'deploy' | 'skip' | 'investigate' | 'blocked' | 'upgrade';
type ConfidenceLevel = 'high' | 'medium' | 'low';

const actionConfig: Record<ModuleActionType, { icon: typeof Rocket; color: string; bg: string; border: string; label: string }> = {
  deploy:      { icon: Rocket,       color: 'text-success', bg: 'bg-success/10', border: 'border-success/20', label: 'Deploy' },
  skip:        { icon: SkipForward,  color: 'text-muted-foreground',    bg: 'bg-muted',    border: 'border-border',    label: 'Skip' },
  investigate: { icon: Search,       color: 'text-warning',   bg: 'bg-warning/10',   border: 'border-warning/20',   label: 'Investigate' },
  blocked:     { icon: Ban,          color: 'text-destructive',     bg: 'bg-destructive/10',     border: 'border-destructive/20',     label: 'Blocked' },
  upgrade:     { icon: RefreshCw,    color: 'text-info',    bg: 'bg-info/10',    border: 'border-info/20',    label: 'Upgrade' },
};

const confidenceConfig: Record<ConfidenceLevel, { color: string }> = {
  high:   { color: 'text-success' },
  medium: { color: 'text-warning' },
  low:    { color: 'text-destructive' },
};

function DeploymentPlanSection({ clusterId }: { clusterId: number }) {
  const planMutation = useAdaptiveModulePlan();
  const [selectedTemplate, setSelectedTemplate] = useState<string>('f5-bnk-2.2');
  const [showVariables, setShowVariables] = useState(false);
  const [labSizing, setLabSizing] = useState(false);


  const planData = planMutation.data;
  const isLoading = planMutation.isPending;

  const handleGeneratePlan = () => {
    planMutation.mutate({
      clusterId,
      templateSlug: selectedTemplate,
      sizingProfile: labSizing ? 'lab' : undefined,
    });
  };

  return (
    <div className={cn(
      'rounded-lg border p-5',
      'bg-card border-border',
    )}>
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-semibold text-sm flex items-center gap-2">
          <Rocket className="h-4 w-4 text-primary" />
          Step 2 · Adaptive Deployment Plan
        </h3>
        <div className="flex items-center gap-2">
          <Select value={selectedTemplate} onValueChange={setSelectedTemplate}>
            <SelectTrigger className="h-8 text-xs w-[160px]">
              <SelectValue placeholder="Select template" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="f5-bnk-2.2">F5 BNK 2.2</SelectItem>
              <SelectItem value="f5-bnk-2.3">F5 BNK 2.3</SelectItem>
              <SelectItem value="bnk-demo-apps">BNK Demo Apps</SelectItem>
            </SelectContent>
          </Select>
          <Button
            size="sm"
            onClick={handleGeneratePlan}
            disabled={isLoading}
            className="h-8"
          >
            {isLoading ? (
              <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
            ) : (
              <Zap className="h-3.5 w-3.5 mr-1.5" />
            )}
            {isLoading ? 'Analyzing...' : planData ? 'Refresh Plan' : 'Generate Plan'}
          </Button>
        </div>
      </div>

      {/* Lab sizing (non-production) toggle — issue #387 part C */}
      <label className="flex items-center gap-2 mb-3 cursor-pointer select-none">
        <Checkbox
          checked={labSizing}
          onCheckedChange={(v) => setLabSizing(v === true)}
        />
        <span className="text-xs font-medium">Lab sizing (non-production)</span>
        <span className="text-[10px] text-muted-foreground">
          shrinks f5-tmm so it schedules on a small lab VM
        </span>
      </label>

      {labSizing && (
        <div className={cn(
          'flex items-start gap-2 p-3 rounded-lg border mb-3',
          'bg-destructive/5 border-destructive/20',
        )}>
          <AlertTriangle className="h-4 w-4 text-destructive mt-0.5 flex-shrink-0" />
          <span className="text-xs text-destructive">
            Lab sizing is NON-PRODUCTION — blobd/debug/observer are shrunk and TMM 2Gi memory OOMs under real traffic.
          </span>
        </div>
      )}

      {!planData && !planMutation.isError && (
        <p className={cn('text-xs', 'text-muted-foreground')}>
          Generate a deployment plan based on cluster scan results. The plan shows which modules
          to deploy, skip, or investigate — with pre-filled variables from detected cluster state.
        </p>
      )}

      {planMutation.isError && !planData && (
        <div className="flex items-center gap-2 text-destructive text-xs">
          <AlertCircle className="h-3.5 w-3.5" />
          <span>Failed to generate plan: {(planMutation.error as Error)?.message || 'Unknown error'}</span>
        </div>
      )}

      {planData && (
        <div className="space-y-4">
          {/* Summary Bar */}
          <div className="flex items-center gap-3 flex-wrap">
            <span className={cn('text-xs font-medium', 'text-foreground/80')}>
              {planData.template_name}
            </span>
            <span className={cn('text-xs', 'text-muted-foreground')}>·</span>
            {planData.summary?.deploy > 0 && (
              <Badge className="gap-1 text-[10px] h-5 bg-success/10 text-success border-success/20">
                <Rocket className="h-3 w-3" />
                {planData.summary.deploy} deploy
              </Badge>
            )}
            {planData.summary?.skip > 0 && (
              <Badge className="gap-1 text-[10px] h-5 bg-muted text-muted-foreground border-border">
                <SkipForward className="h-3 w-3" />
                {planData.summary.skip} skip
              </Badge>
            )}
            {planData.summary?.investigate > 0 && (
              <Badge className="gap-1 text-[10px] h-5 bg-warning/10 text-warning border-warning/20">
                <Search className="h-3 w-3" />
                {planData.summary.investigate} investigate
              </Badge>
            )}
            {planData.summary?.blocked > 0 && (
              <Badge className="gap-1 text-[10px] h-5 bg-destructive/10 text-destructive border-destructive/20">
                <Ban className="h-3 w-3" />
                {planData.summary.blocked} blocked
              </Badge>
            )}
            {planData.is_ready ? (
              <Badge className="gap-1 text-[10px] h-5 bg-success/10 text-success border-success/20">
                <CheckCircle2 className="h-3 w-3" />
                Ready
              </Badge>
            ) : (
              <Badge className="gap-1 text-[10px] h-5 bg-destructive/10 text-destructive border-destructive/20">
                <AlertCircle className="h-3 w-3" />
                Not Ready
              </Badge>
            )}
          </div>

          {/* Global Blockers */}
          {planData.global_blockers?.length > 0 && (
            <div className="space-y-2">
              {planData.global_blockers.map((b: string, i: number) => (
                <div key={i} className={cn(
                  'flex items-start gap-2 p-3 rounded-lg border',
                  'bg-destructive/5 border-destructive/20',
                )}>
                  <Ban className="h-4 w-4 text-destructive mt-0.5 flex-shrink-0" />
                  <span className="text-xs text-destructive">{b}</span>
                </div>
              ))}
            </div>
          )}

          {/* Global Warnings */}
          {planData.global_warnings?.length > 0 && (
            <div className="space-y-2">
              {planData.global_warnings.map((w: string, i: number) => (
                <div key={i} className={cn(
                  'flex items-start gap-2 p-3 rounded-lg border',
                  'bg-warning/5 border-warning/20',
                )}>
                  <AlertTriangle className="h-4 w-4 text-warning mt-0.5 flex-shrink-0" />
                  <span className="text-xs text-warning">{w}</span>
                </div>
              ))}
            </div>
          )}

          {/* Module List */}
          <div className="space-y-2">
            {planData.modules?.map((mod, idx) => {
              const actionCfg = actionConfig[mod.action as ModuleActionType] || actionConfig.deploy;
              const ActionIcon = actionCfg.icon;
              const confCfg = confidenceConfig[mod.confidence as ConfidenceLevel] || confidenceConfig.medium;
              const hasOverrides = Object.keys(mod.variable_overrides || {}).length > 0;
              const hasWarnings = (mod.warnings?.length || 0) > 0;
              const hasBlockers = (mod.blockers?.length || 0) > 0;

              return (
                <div
                  key={mod.path}
                  className={cn(
                    'rounded-lg border p-3',
                    'border-border bg-muted/50',
                    mod.action === 'skip' && 'opacity-60',
                  )}
                >
                  <div className="flex items-center gap-3">
                    {/* Order number */}
                    <span className={cn(
                      'text-[10px] font-mono rounded-full w-5 h-5 flex items-center justify-center flex-shrink-0',
                      'bg-muted text-muted-foreground',
                    )}>
                      {mod.order || idx + 1}
                    </span>

                    {/* Module info */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-0.5">
                        <span className="text-sm font-medium">{mod.name}</span>
                        <Badge className={cn('gap-1 text-[10px] h-5', actionCfg.bg, actionCfg.color, actionCfg.border)}>
                          <ActionIcon className="h-3 w-3" />
                          {actionCfg.label}
                        </Badge>
                        {hasOverrides && (
                          <Badge className="gap-1 text-[10px] h-5 bg-info/10 text-info border-info/20">
                            <Variable className="h-3 w-3" />
                            Pre-filled
                          </Badge>
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        <span className={cn('text-xs font-mono', 'text-muted-foreground')}>
                          {mod.path}
                        </span>
                        <span className={cn('text-[10px]', confCfg.color)}>
                          {mod.confidence} confidence
                        </span>
                      </div>
                      <p className={cn('text-xs mt-1', 'text-muted-foreground')}>
                        {mod.reason}
                      </p>

                      {/* Warnings */}
                      {hasWarnings && (
                        <div className="mt-1.5 space-y-1">
                          {mod.warnings.map((w: string, wi: number) => (
                            <div key={wi} className="flex items-start gap-1.5 text-[11px] text-warning">
                              <AlertTriangle className="h-3 w-3 mt-0.5 flex-shrink-0" />
                              <span>{w}</span>
                            </div>
                          ))}
                        </div>
                      )}

                      {/* Blockers */}
                      {hasBlockers && (
                        <div className="mt-1.5 space-y-1">
                          {mod.blockers.map((b: string, bi: number) => (
                            <div key={bi} className="flex items-start gap-1.5 text-[11px] text-destructive">
                              <Ban className="h-3 w-3 mt-0.5 flex-shrink-0" />
                              <span>{b}</span>
                            </div>
                          ))}
                        </div>
                      )}

                      {/* Variable Overrides */}
                      {hasOverrides && (
                        <div className={cn(
                          'mt-2 p-2 rounded text-[11px] font-mono space-y-0.5',
                          'bg-muted',
                        )}>
                          {Object.entries(mod.variable_overrides).map(([key, val]) => (
                            <div key={key} className="flex items-center gap-1.5">
                              <span className={'text-primary'}>{key}</span>
                              <ArrowRight className="h-2.5 w-2.5 text-muted-foreground" />
                              <span className={'text-foreground/80'}>
                                {typeof val === 'object' ? JSON.stringify(val) : String(val)}
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Suggested Variables */}
          {Object.keys(planData.suggested_variables || {}).length > 0 && (
            <div>
              <button
                onClick={() => setShowVariables(!showVariables)}
                className={cn(
                  'flex items-center gap-2 text-xs font-medium w-full',
                  'text-muted-foreground hover:text-foreground',
                )}
              >
                {showVariables ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                <Info className="h-3.5 w-3.5 text-primary" />
                Detected Project Variables ({Object.keys(planData.suggested_variables).length})
              </button>
              {showVariables && (
                <div className={cn(
                  'mt-2 p-3 rounded-lg border text-[11px] font-mono space-y-1',
                  'bg-muted border-border',
                )}>
                  {Object.entries(planData.suggested_variables).map(([key, val]) => (
                    <div key={key} className="flex items-center gap-2">
                      <span className={'text-primary'}>{key}</span>
                      <ArrowRight className="h-2.5 w-2.5 text-muted-foreground" />
                      <span className={'text-foreground/80'}>
                        {typeof val === 'object' ? JSON.stringify(val) : String(val)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
