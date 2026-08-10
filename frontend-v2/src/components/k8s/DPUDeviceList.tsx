/**
 * DPU Device Inventory List
 *
 * Tabular view of DPUDevice CRD resources showing:
 *   - Device name, type, serial number
 *   - PCI address, BMC address, MAC
 *   - Host node attachment
 *   - Condition badges (Discovered → NodeAttached → Initialized → Ready)
 *   - Expandable rows with firmware info + full conditions
 *
 * Data comes from the shared DPF unified data cache — no extra API calls.
 */

import { useState, useMemo } from 'react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import {
  Cpu,
  Search,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  HelpCircle,
  ChevronDown,
  ChevronRight,
  Server,
} from 'lucide-react';
import type { DpfDpuDevice } from '@/types';
import { deviceStage } from '@/lib/dpu-device-status';

// ── Props ─────────────────────────────────────────────────────────────────

interface DPUDeviceListProps {
  devices: DpfDpuDevice[];
  isLoading?: boolean;
}

// ── Condition Helpers ─────────────────────────────────────────────────────

type ConditionStatus = 'True' | 'False' | 'Unknown' | undefined;

function conditionIcon(status: ConditionStatus) {
  switch (status) {
    case 'True':  return CheckCircle2;
    case 'False': return XCircle;
    default:      return HelpCircle;
  }
}

function conditionColor(status: ConditionStatus) {
  switch (status) {
    case 'True':  return 'text-success';
    case 'False': return 'text-destructive';
    default:      return 'text-muted-foreground';
  }
}

type StageBadgeVariant = 'success' | 'info' | 'warning' | 'destructive' | 'muted';

function stageBadgeVariant(stage: string): StageBadgeVariant {
  switch (stage) {
    case 'Ready':        return 'success';
    case 'Initialized':  return 'info';
    case 'NodeAttached': return 'info';
    case 'Discovered':   return 'warning';
    case 'Error':        return 'destructive';
    default:             return 'muted';
  }
}

// ── Device Row ────────────────────────────────────────────────────────────

function DeviceRow({ device }: { device: DpfDpuDevice }) {
  const [expanded, setExpanded] = useState(false);
  const conditions = device.status?.conditions ?? [];
  const stage = deviceStage(conditions);
  const stageVariant = stageBadgeVariant(stage);

  return (
    <div className="border-b last:border-b-0 border-border">
      {/* Main row */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center w-full gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/50"
      >
        {/* Expand chevron */}
        {expanded
          ? <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
          : <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
        }

        {/* Icon */}
        <Cpu className="h-4 w-4 shrink-0 text-muted-foreground" />

        {/* Name */}
        <span className="font-medium text-sm min-w-[140px] truncate text-foreground">
          {device.metadata.name}
        </span>

        {/* Type */}
        <Badge variant="outline" className="text-[10px] px-1.5 py-0 shrink-0">
          {device.status?.dpuType ?? 'Unknown'}
        </Badge>

        {/* Serial */}
        {device.status?.serial && (
          <span className="text-xs font-mono hidden md:inline truncate max-w-[140px] text-muted-foreground">
            {device.status.serial}
          </span>
        )}

        {/* PCI address */}
        {device.status?.pciAddress && (
          <span className="text-xs font-mono hidden lg:inline text-muted-foreground">
            {device.status.pciAddress}
          </span>
        )}

        {/* Node */}
        {device.status?.nodeName && (
          <div className="hidden xl:flex items-center gap-1">
            <Server className="h-3 w-3 text-muted-foreground" />
            <span className="text-xs truncate max-w-[120px] text-muted-foreground">
              {device.status.nodeName}
            </span>
          </div>
        )}

        {/* Spacer */}
        <div className="flex-1" />

        {/* Stage badge */}
        <Badge variant={stageVariant} className="text-[10px] px-2 py-0">
          {stage}
        </Badge>
      </button>

      {/* Expanded detail */}
      {expanded && (
        <div className="px-4 pb-4 pl-12 space-y-4 bg-muted/50">
          {/* Key-value details */}
          <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-2 text-xs">
            <DetailField label="Type" value={device.status?.dpuType} />
            <DetailField label="Serial" value={device.status?.serial} mono />
            <DetailField label="PCI Address" value={device.status?.pciAddress} mono />
            <DetailField label="MAC" value={device.status?.mac} mono />
            <DetailField label="BMC Address" value={device.spec?.bmc?.address} mono />
            <DetailField label="Host Node" value={device.status?.nodeName} />
            <DetailField label="Interface" value={device.spec?.dpuInterface} mono />
            <DetailField label="Phase" value={device.status?.phase} />
            <DetailField label="Created" value={device.metadata.creationTimestamp} />
          </div>

          {/* Firmware */}
          {device.status?.firmware && (
            <div>
              <h4 className="text-xs font-semibold mb-1.5 text-foreground/80">
                Firmware
              </h4>
              <div className="grid grid-cols-3 gap-x-6 gap-y-1 text-xs">
                <DetailField label="BMC" value={device.status.firmware.bmc} mono />
                <DetailField label="UEFI" value={device.status.firmware.uefi} mono />
                <DetailField label="BSP" value={device.status.firmware.bsp} mono />
              </div>
            </div>
          )}

          {/* Conditions */}
          {conditions.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold mb-1.5 text-foreground/80">
                Conditions
              </h4>
              <div className="space-y-1">
                {conditions.map((cond) => {
                  const Icon = conditionIcon(cond.status as ConditionStatus);
                  return (
                    <div
                      key={cond.type}
                      className="flex items-center gap-2 rounded px-3 py-1.5 text-xs bg-card"
                    >
                      <Icon className={cn('h-3.5 w-3.5 shrink-0', conditionColor(cond.status as ConditionStatus))} />
                      <span className="font-medium min-w-[160px] text-foreground/80">
                        {cond.type}
                      </span>
                      {cond.reason && (
                        <span className="text-muted-foreground">{cond.reason}</span>
                      )}
                      {cond.message && (
                        <span className="truncate text-muted-foreground">
                          {cond.message}
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Detail Field Helper ───────────────────────────────────────────────────

function DetailField({
  label,
  value,
  mono,
}: {
  label: string;
  value: string | undefined | null;
  mono?: boolean;
}) {
  if (!value) return null;
  return (
    <div>
      <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <p className={cn('mt-0.5 text-foreground/80', mono && 'font-mono')}>
        {value}
      </p>
    </div>
  );
}

// ── Filter Summary Cards ──────────────────────────────────────────────────

type FilterMode = 'all' | 'ready' | 'not_ready';

function FilterCard({
  label,
  value,
  icon: Icon,
  active,
  onClick,
  color,
}: {
  label: string;
  value: number;
  icon: React.ComponentType<{ className?: string }>;
  active?: boolean;
  onClick?: () => void;
  color?: string;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'flex items-center gap-3 px-4 py-3 rounded-lg border transition-all text-left',
        active
          ? 'border-primary/50 bg-primary/10'
          : 'border-border bg-card hover:border-foreground/20',
      )}
    >
      <Icon className={cn('h-5 w-5', color ?? 'text-muted-foreground')} />
      <div>
        <p className="text-lg font-semibold leading-none text-foreground">
          {value}
        </p>
        <p className="text-xs text-muted-foreground">
          {label}
        </p>
      </div>
    </button>
  );
}

// ── Main Component ────────────────────────────────────────────────────────

export function DPUDeviceList({ devices, isLoading }: DPUDeviceListProps) {
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<FilterMode>('all');

  const readyCount = useMemo(
    () => devices.filter((d) => deviceStage(d.status?.conditions) === 'Ready').length,
    [devices],
  );

  const filteredDevices = useMemo(() => {
    let result = devices;

    // Text search
    if (search.trim()) {
      const q = search.toLowerCase();
      result = result.filter((d) =>
        d.metadata.name.toLowerCase().includes(q) ||
        (d.status?.serial ?? '').toLowerCase().includes(q) ||
        (d.status?.dpuType ?? '').toLowerCase().includes(q) ||
        (d.status?.pciAddress ?? '').toLowerCase().includes(q) ||
        (d.status?.nodeName ?? '').toLowerCase().includes(q) ||
        (d.status?.mac ?? '').toLowerCase().includes(q)
      );
    }

    // Status filter
    if (filter === 'ready') {
      result = result.filter((d) => deviceStage(d.status?.conditions) === 'Ready');
    } else if (filter === 'not_ready') {
      result = result.filter((d) => deviceStage(d.status?.conditions) !== 'Ready');
    }

    return result;
  }, [devices, search, filter]);

  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="h-12 rounded-lg animate-pulse bg-muted/50" />
        ))}
      </div>
    );
  }

  if (devices.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center rounded-lg border border-border bg-muted/50 p-10 text-center">
        <Cpu className="h-10 w-10 mb-3 text-muted-foreground" />
        <h3 className="text-base font-semibold mb-1 text-foreground/80">
          No DPU Devices
        </h3>
        <p className="text-sm text-muted-foreground">
          No DPUDevice resources found. Run a DPU discovery or check that DPF is detecting hardware.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Filter cards */}
      <div className="grid grid-cols-3 gap-3">
        <FilterCard
          label="Total Devices"
          value={devices.length}
          icon={Cpu}
          active={filter === 'all'}
          onClick={() => setFilter('all')}
        />
        <FilterCard
          label="Ready"
          value={readyCount}
          icon={CheckCircle2}
          active={filter === 'ready'}
          onClick={() => setFilter('ready')}
          color="text-success"
        />
        <FilterCard
          label="Not Ready"
          value={devices.length - readyCount}
          icon={AlertTriangle}
          active={filter === 'not_ready'}
          onClick={() => setFilter('not_ready')}
          color={devices.length - readyCount > 0 ? 'text-warning' : 'text-muted-foreground'}
        />
      </div>

      {/* Search */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <Input
          placeholder="Search by name, serial, type, PCI, node, MAC..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="pl-9"
        />
      </div>

      {/* Device list */}
      <div className="rounded-lg border overflow-hidden border-border">
        {/* Header */}
        <div className="flex items-center gap-3 px-4 py-2 text-xs font-medium border-b bg-muted/50 border-border text-muted-foreground">
          <span className="w-4" /> {/* chevron space */}
          <span className="w-4" /> {/* icon space */}
          <span className="min-w-[140px]">Name</span>
          <span className="w-[80px]">Type</span>
          <span className="hidden md:inline w-[140px]">Serial</span>
          <span className="hidden lg:inline w-[100px]">PCI</span>
          <span className="hidden xl:inline w-[120px]">Node</span>
          <span className="flex-1" />
          <span className="w-[70px] text-right">Status</span>
        </div>

        {/* Rows */}
        {filteredDevices.map((device) => (
          <DeviceRow key={device.metadata.uid ?? device.metadata.name} device={device} />
        ))}

        {filteredDevices.length === 0 && (
          <div className="px-4 py-8 text-center text-sm text-muted-foreground">
            No devices match the current filter.
          </div>
        )}
      </div>
    </div>
  );
}
