/**
 * DPF Infrastructure Panel
 *
 * Read-only dashboard for NVIDIA DPF (DOCA Platform Framework) status.
 * Shows operator health, device inventory, DPU clusters, BFB images,
 * and service chain summary.
 *
 * Backed by a single unified data fetch (GET /api/k8s/clusters/{id}/dpf/data)
 * that is cached under one React Query key — switching between sub-views
 * is instant.
 *
 * Polls every 30s. Color-coded severity. Auto-refreshes.
 */

import { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { useDpfData } from '@/hooks/k8s/useDpf';
import { DPUDeviceList } from './DPUDeviceList';
import { DPUClusterDetail } from './DPUClusterDetail';
import { DPUProvisioningTab } from './DPUProvisioningTab';
import { DPUServicesTab } from './DPUServicesTab';
import { DPFSetupWizard } from './DPFSetupWizard';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import type {
  DpfDpuDevice, DpfDpuCluster, DpfDpu, DpfDpuSet, DpfBfb, DpfDpuFlavor,
  DpfDpuService, DpfDpuDeployment, DpfDpuServiceChain, DpfDpuServiceInterface,
  DpfServiceChain, DpfServiceInterface,
} from '@/types';
import {
  CheckCircle2,
  AlertTriangle,
  XCircle,
  HelpCircle,
  Loader2,
  Server,
  Cpu,
  HardDrive,
  Network,
  Layers,
  Box,
  RefreshCw,
  Wand2,
} from 'lucide-react';
import type { DpfHealthResponse } from '@/types';
import { ErrorState } from '@/components/ui/error-state';
import { parseApiError } from '@/lib/error-handler';

// ── Props ─────────────────────────────────────────────────────────────────

interface DPFInfrastructurePanelProps {
  clusterId: number;
}

// ── Status Helpers ────────────────────────────────────────────────────────

type DpfStatus = DpfHealthResponse['status'];
type BadgeVariant = 'success' | 'warning' | 'destructive' | 'muted';

const STATUS_CONFIG: Record<DpfStatus, { label: string; variant: BadgeVariant; icon: typeof CheckCircle2 }> = {
  healthy:       { label: 'Healthy',       variant: 'success',     icon: CheckCircle2 },
  partial:       { label: 'Partial',       variant: 'warning',     icon: AlertTriangle },
  degraded:      { label: 'Degraded',      variant: 'destructive', icon: XCircle },
  no_devices:    { label: 'No Devices',    variant: 'muted',       icon: HelpCircle },
  not_installed: { label: 'Not Installed', variant: 'muted',       icon: HelpCircle },
};

function StatusBadge({ status }: { status: DpfStatus }) {
  const cfg = STATUS_CONFIG[status] ?? STATUS_CONFIG.not_installed;
  const Icon = cfg.icon;
  return (
    <Badge variant={cfg.variant} className="gap-1.5">
      <Icon className="h-3.5 w-3.5" />
      {cfg.label}
    </Badge>
  );
}

// ── Stat Card ─────────────────────────────────────────────────────────────

function StatCard({
  icon: Icon,
  label,
  value,
  detail,
}: {
  icon: typeof Server;
  label: string;
  value: number | string;
  detail?: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-muted/50">
          <Icon className="h-5 w-5 text-foreground/80" />
        </div>
        <div>
          <p className="text-2xl font-semibold text-foreground">
            {value}
          </p>
          <p className="text-xs text-muted-foreground">
            {label}
          </p>
        </div>
      </div>
      {detail && (
        <p className="mt-2 text-xs text-muted-foreground">
          {detail}
        </p>
      )}
    </div>
  );
}

// ── Ready/Total Bar ───────────────────────────────────────────────────────

function ReadyBar({ ready, total }: { ready: number; total: number }) {
  if (total === 0) return null;
  const pct = Math.round((ready / total) * 100);
  const barColor = pct === 100 ? 'bg-success' : pct > 50 ? 'bg-warning' : 'bg-destructive';

  return (
    <div className="mt-2">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-muted-foreground">
          {ready}/{total} ready
        </span>
        <span className="text-xs font-medium text-foreground/80">
          {pct}%
        </span>
      </div>
      <div className="h-1.5 rounded-full bg-muted">
        <div className={cn('h-1.5 rounded-full transition-all', barColor)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

// ── Operator Section ──────────────────────────────────────────────────────

function OperatorSection({ health }: { health: DpfHealthResponse }) {
  const op = health.operator;

  return (
    <div className="rounded-lg border border-border bg-card p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-foreground">
          DPF Operator
        </h3>
        <div className="flex items-center gap-2">
          {op.version && (
            <Badge variant="outline" className="text-xs">
              {/* status.version already carries a leading 'v' (e.g. "v26.4.0"); strip it
                  so we render a single 'v' whether or not upstream includes one. */}
              v{op.version.replace(/^v/i, '')}
            </Badge>
          )}
          {op.ready ? (
            <Badge variant="success" className="gap-1">
              <CheckCircle2 className="h-3 w-3" /> Ready
            </Badge>
          ) : op.configured ? (
            <Badge variant="warning" className="gap-1">
              <AlertTriangle className="h-3 w-3" /> Not Ready
            </Badge>
          ) : (
            <Badge variant="muted" className="gap-1">
              <HelpCircle className="h-3 w-3" /> Not Configured
            </Badge>
          )}
        </div>
      </div>

      {op.conditions.length > 0 && (
        <div className="space-y-1.5">
          {op.conditions.map((cond) => (
            <div
              key={cond.type}
              className="flex items-center justify-between rounded px-3 py-1.5 text-xs bg-muted/50"
            >
              <span className="font-medium text-foreground/80">
                {cond.type}
              </span>
              <div className="flex items-center gap-2">
                <span className={cond.status === 'True' ? 'text-success' : 'text-muted-foreground'}>
                  {cond.status}
                </span>
                {cond.reason && (
                  <span className="text-xs text-muted-foreground">
                    ({cond.reason})
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Device Inventory Section ──────────────────────────────────────────────

function DeviceInventory({ health }: { health: DpfHealthResponse }) {
  const { devices } = health;
  const typeEntries = useMemo(
    () => Object.entries(devices.byType).sort((a, b) => b[1] - a[1]),
    [devices.byType],
  );

  return (
    <div className="rounded-lg border border-border bg-card p-5">
      <h3 className="text-sm font-semibold mb-3 text-foreground">
        DPU Devices
      </h3>

      <ReadyBar ready={devices.ready} total={devices.total} />

      {typeEntries.length > 0 && (
        <div className="mt-3 space-y-1">
          {typeEntries.map(([type, count]) => (
            <div key={type} className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">{type}</span>
              <span className="font-medium text-foreground/80">{count}</span>
            </div>
          ))}
        </div>
      )}

      {devices.total === 0 && (
        <p className="text-xs mt-2 text-muted-foreground">
          No DPU devices discovered on this cluster.
        </p>
      )}
    </div>
  );
}

// ── Clusters Section ──────────────────────────────────────────────────────

function ClustersSection({ health }: { health: DpfHealthResponse }) {
  const { clusters } = health;

  return (
    <div className="rounded-lg border border-border bg-card p-5">
      <h3 className="text-sm font-semibold mb-3 text-foreground">
        DPU Clusters
      </h3>

      <ReadyBar ready={clusters.ready} total={clusters.total} />

      {clusters.clusters.length > 0 && (
        <div className="mt-3 space-y-2">
          {clusters.clusters.map((c) => (
            <div
              key={`${c.namespace}/${c.name}`}
              className="flex items-center justify-between rounded px-3 py-2 text-xs bg-muted/50"
            >
              <div>
                <span className="font-medium text-foreground/80">
                  {c.name}
                </span>
                <span className="ml-2 text-muted-foreground">
                  {c.namespace}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <Badge variant="outline" className="text-[10px] px-1.5 py-0">
                  {c.type}
                </Badge>
                {c.ready ? (
                  <CheckCircle2 className="h-3.5 w-3.5 text-success" />
                ) : (
                  <AlertTriangle className="h-3.5 w-3.5 text-warning" />
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {clusters.total === 0 && (
        <p className="text-xs mt-2 text-muted-foreground">
          No DPU clusters configured.
        </p>
      )}
    </div>
  );
}

// ── BFB Images Section ────────────────────────────────────────────────────

function BfbSection({ health }: { health: DpfHealthResponse }) {
  const { bfbs } = health;

  return (
    <div className="rounded-lg border border-border bg-card p-5">
      <h3 className="text-sm font-semibold mb-3 text-foreground">
        BFB Images
      </h3>

      <ReadyBar ready={bfbs.ready} total={bfbs.total} />

      {bfbs.images.length > 0 && (
        <div className="mt-3 space-y-2">
          {bfbs.images.map((img) => (
            <div
              key={img.name}
              className="rounded px-3 py-2 text-xs bg-muted/50"
            >
              <div className="flex items-center justify-between">
                <span className="font-medium text-foreground/80">
                  {img.name}
                </span>
                {img.ready ? (
                  <CheckCircle2 className="h-3.5 w-3.5 text-success" />
                ) : (
                  <Loader2 className="h-3.5 w-3.5 text-warning animate-spin" />
                )}
              </div>
              {img.url && (
                <p className="mt-1 truncate text-muted-foreground">
                  {img.url}
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      {bfbs.total === 0 && (
        <p className="text-xs mt-2 text-muted-foreground">
          No BFB images registered.
        </p>
      )}
    </div>
  );
}

// ── Not Installed State ───────────────────────────────────────────────────

function NotInstalledState({ onSetup }: { onSetup?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-border bg-muted/50 p-12 text-center">
      <HelpCircle className="h-12 w-12 mb-4 text-muted-foreground" />
      <h3 className="text-lg font-semibold mb-2 text-foreground/80">
        DPF Not Installed
      </h3>
      <p className="text-sm max-w-md mb-4 text-muted-foreground">
        NVIDIA DOCA Platform Framework was not detected on this cluster.
        Install the DPF operator to manage DPU devices, clusters, and services.
      </p>
      {onSetup && (
        <Button
          size="sm"
          className="gap-1.5"
          onClick={onSetup}
        >
          <Wand2 className="h-4 w-4" /> Setup DPF
        </Button>
      )}
    </div>
  );
}

// ── Loading Skeleton ──────────────────────────────────────────────────────

function DPFSkeleton() {
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-6 w-24" />
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-24 rounded-lg" />
        ))}
      </div>
      <Skeleton className="h-40 rounded-lg" />
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Skeleton className="h-40 rounded-lg" />
        <Skeleton className="h-40 rounded-lg" />
      </div>
    </div>
  );
}

// ── Tab Types ─────────────────────────────────────────────────────────────

type DpfTab = 'overview' | 'devices' | 'clusters' | 'provisioning' | 'services';

const TABS: { key: DpfTab; label: string }[] = [
  { key: 'overview',      label: 'Overview' },
  { key: 'devices',       label: 'Devices' },
  { key: 'clusters',      label: 'Clusters' },
  { key: 'provisioning',  label: 'Provisioning' },
  { key: 'services',      label: 'Services' },
];

// ── Overview Tab ──────────────────────────────────────────────────────────

function OverviewTab({ health }: { health: DpfHealthResponse }) {
  return (
    <div className="space-y-6">
      {/* Summary Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          icon={Cpu}
          label="DPU Devices"
          value={health.devices.total}
          detail={health.devices.total > 0 ? `${health.devices.ready} ready` : undefined}
        />
        <StatCard
          icon={Server}
          label="DPU Clusters"
          value={health.clusters.total}
          detail={health.clusters.total > 0 ? `${health.clusters.ready} ready` : undefined}
        />
        <StatCard
          icon={HardDrive}
          label="BFB Images"
          value={health.bfbs.total}
          detail={health.bfbs.total > 0 ? `${health.bfbs.ready} ready` : undefined}
        />
        <StatCard
          icon={Network}
          label="DPU Services"
          value={health.services.total}
          detail={health.services.total > 0 ? `${health.services.ready} ready` : undefined}
        />
      </div>

      {/* Operator */}
      <OperatorSection health={health} />

      {/* Detail Sections */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <DeviceInventory health={health} />
        <ClustersSection health={health} />
      </div>

      {/* BFB + Additional Stats */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <BfbSection health={health} />

        {/* Additional Resources Summary */}
        <div className="rounded-lg border border-border bg-card p-5">
          <h3 className="text-sm font-semibold mb-3 text-foreground">
            Additional Resources
          </h3>
          <div className="space-y-2">
            <ResourceRow icon={Layers} label="DPU Sets" count={health.dpusets.total} />
            <ResourceRow icon={Box} label="DPU Flavors" count={health.flavors.total} />
            <ResourceRow icon={Cpu} label="DPUs (lifecycle)" count={health.dpus.total} />
            <ResourceRow icon={Network} label="Deployments" count={health.deployments.total} />
            <ResourceRow icon={Layers} label="Service Chains" count={health.serviceChains.total} />
            <ResourceRow icon={Network} label="Service Interfaces" count={health.serviceInterfaces.total} />
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────────

export function DPFInfrastructurePanel({ clusterId }: DPFInfrastructurePanelProps) {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState<DpfTab>('overview');
  const [showWizard, setShowWizard] = useState(false);
  const { data, isLoading, error, isFetching } = useDpfData(clusterId);

  const health = data?.health;
  const devices = (data?.resources?.dpudevice ?? []) as DpfDpuDevice[];
  const clusters = (data?.resources?.dpucluster ?? []) as DpfDpuCluster[];
  const dpus = (data?.resources?.dpu ?? []) as DpfDpu[];
  const dpuSets = (data?.resources?.dpuset ?? []) as DpfDpuSet[];
  const bfbs = (data?.resources?.bfb ?? []) as DpfBfb[];
  const dpuFlavors = (data?.resources?.dpuflavor ?? []) as DpfDpuFlavor[];
  const dpuServices = (data?.resources?.dpuservice ?? []) as DpfDpuService[];
  const dpuDeployments = (data?.resources?.dpudeployment ?? []) as DpfDpuDeployment[];
  const dpuServiceChains = (data?.resources?.dpuservicechain ?? []) as DpfDpuServiceChain[];
  const dpuServiceInterfaces = (data?.resources?.dpuserviceinterface ?? []) as DpfDpuServiceInterface[];
  const realizedChains = (data?.resources?.servicechain ?? []) as DpfServiceChain[];
  const realizedInterfaces = (data?.resources?.serviceinterface ?? []) as DpfServiceInterface[];

  if (isLoading) {
    return <DPFSkeleton />;
  }

  if (error) {
    const parsedDpfError = parseApiError(error);
    const dpfErrorRoute = parsedDpfError.action?.route;
    return (
      <ErrorState
        error={error}
        size="sm"
        {...(dpfErrorRoute ? {
          secondaryAction: {
            label: parsedDpfError.action!.label,
            onClick: () => navigate(dpfErrorRoute),
          },
        } : {})}
      />
    );
  }

  if (!health || health.status === 'not_installed') {
    if (showWizard) {
      return <DPFSetupWizard clusterId={clusterId} onClose={() => setShowWizard(false)} />;
    }
    return <NotInstalledState onSetup={() => setShowWizard(true)} />;
  }

  if (showWizard) {
    return <DPFSetupWizard clusterId={clusterId} onClose={() => setShowWizard(false)} />;
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h3 className="text-lg font-semibold text-foreground">
            DPU Infrastructure
          </h3>
          <StatusBadge status={health.status} />
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs gap-1.5"
            onClick={() => setShowWizard(true)}
          >
            <Wand2 className="h-3.5 w-3.5" /> Setup Wizard
          </Button>
          {isFetching && (
            <RefreshCw className="h-4 w-4 animate-spin text-muted-foreground" />
          )}
        </div>
      </div>

      {/* Tab Navigation */}
      <div className="flex gap-1 p-1 rounded-lg border border-border bg-muted/50">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={cn(
              'flex-1 px-4 py-2 rounded-md text-sm font-medium transition-colors',
              activeTab === tab.key
                ? 'bg-card text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {tab.label}
            {tab.key === 'devices' && devices.length > 0 && (
              <span className="ml-1.5 text-xs text-muted-foreground">
                ({devices.length})
              </span>
            )}
            {tab.key === 'clusters' && clusters.length > 0 && (
              <span className="ml-1.5 text-xs text-muted-foreground">
                ({clusters.length})
              </span>
            )}
            {tab.key === 'provisioning' && dpuSets.length + bfbs.length + dpuFlavors.length > 0 && (
              <span className="ml-1.5 text-xs text-muted-foreground">
                ({dpuSets.length + bfbs.length + dpuFlavors.length})
              </span>
            )}
            {tab.key === 'services' && dpuServices.length + dpuServiceChains.length + dpuServiceInterfaces.length > 0 && (
              <span className="ml-1.5 text-xs text-muted-foreground">
                ({dpuServices.length + dpuServiceChains.length + dpuServiceInterfaces.length})
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      {activeTab === 'overview' && (
        <OverviewTab health={health} />
      )}
      {activeTab === 'devices' && (
        <DPUDeviceList devices={devices} isLoading={isLoading} />
      )}
      {activeTab === 'clusters' && (
        <DPUClusterDetail clusters={clusters} isLoading={isLoading} />
      )}
      {activeTab === 'provisioning' && (
        <DPUProvisioningTab dpuSets={dpuSets} dpus={dpus} bfbs={bfbs} flavors={dpuFlavors} isLoading={isLoading} />
      )}
      {activeTab === 'services' && (
        <DPUServicesTab
          services={dpuServices}
          deployments={dpuDeployments}
          serviceChains={dpuServiceChains}
          serviceInterfaces={dpuServiceInterfaces}
          realizedChains={realizedChains}
          realizedInterfaces={realizedInterfaces}
          isLoading={isLoading}
        />
      )}
    </div>
  );
}

// ── Resource Row helper ───────────────────────────────────────────────────

function ResourceRow({
  icon: Icon,
  label,
  count,
}: {
  icon: typeof Server;
  label: string;
  count: number;
}) {
  return (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-2">
        <Icon className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-xs text-muted-foreground">{label}</span>
      </div>
      <span className="text-sm font-medium text-foreground/80">
        {count}
      </span>
    </div>
  );
}
