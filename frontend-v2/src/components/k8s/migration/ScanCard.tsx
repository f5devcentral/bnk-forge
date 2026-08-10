/**
 * ScanCard + StatusBadge + StatusDot — shared primitives for scan result
 * display. Originally private to ClusterScanResults.tsx; extracted here so
 * the Migration tab shared cards can reuse them without duplication.
 */

import { useState } from 'react';
import { ChevronDown, ChevronRight, CheckCircle2, AlertTriangle, XCircle, HelpCircle } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import type { Server } from 'lucide-react';

// ---- Types ----

export type PrereqStatus = 'detected' | 'partial' | 'missing' | 'unknown';

// ---- Config ----

export const statusConfig: Record<
  PrereqStatus,
  { icon: typeof CheckCircle2; color: string; bg: string; border: string; label: string }
> = {
  detected: { icon: CheckCircle2,  color: 'text-success',          bg: 'bg-success/10',     border: 'border-success/20',     label: 'Detected' },
  partial:  { icon: AlertTriangle, color: 'text-warning',          bg: 'bg-warning/10',     border: 'border-warning/20',     label: 'Partial' },
  missing:  { icon: XCircle,       color: 'text-destructive',      bg: 'bg-destructive/10', border: 'border-destructive/20', label: 'Missing' },
  unknown:  { icon: HelpCircle,    color: 'text-muted-foreground', bg: 'bg-muted',          border: 'border-border',         label: 'Unknown' },
};

// ---- StatusBadge ----

export function StatusBadge({ status }: { status: PrereqStatus }) {
  const config = statusConfig[status] || statusConfig.unknown;
  const Icon = config.icon;
  return (
    <Badge className={cn('gap-1 font-medium', config.bg, config.color, config.border)}>
      <Icon className="h-3 w-3" />
      {config.label}
    </Badge>
  );
}

// ---- StatusDot ----

export function StatusDot({ status }: { status: PrereqStatus }) {
  const colors: Record<PrereqStatus, string> = {
    detected: 'bg-success',
    partial: 'bg-warning',
    missing: 'bg-destructive',
    unknown: 'bg-muted-foreground',
  };
  return (
    <span className={cn('inline-block h-2.5 w-2.5 rounded-full', colors[status] || colors.unknown)} />
  );
}

// ---- ScanCard ----

export function ScanCard({
  title,
  icon: Icon,
  status,
  children,
  collapsible = false,
  defaultOpen = true,
  className,
}: {
  title: string;
  icon: typeof Server;
  status: PrereqStatus;
  children: React.ReactNode;
  collapsible?: boolean;
  defaultOpen?: boolean;
  className?: string;
}) {
  const config = statusConfig[status] || statusConfig.unknown;
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div
      className={cn(
        'rounded-lg border p-5',
        'bg-card border-border',
        className,
      )}
    >
      <div
        className={cn('flex items-center justify-between', collapsible && 'cursor-pointer')}
        onClick={collapsible ? () => setOpen(!open) : undefined}
      >
        <div className="flex items-center gap-2.5">
          {collapsible && (
            open
              ? <ChevronDown className="h-4 w-4 text-muted-foreground" />
              : <ChevronRight className="h-4 w-4 text-muted-foreground" />
          )}
          <div className={cn('p-2 rounded-lg', config.bg)}>
            <Icon className={cn('h-4 w-4', config.color)} />
          </div>
          <h3 className="font-semibold text-sm">{title}</h3>
        </div>
        <StatusBadge status={status} />
      </div>
      {(!collapsible || open) && (
        <div className="space-y-3 mt-4">
          {children}
        </div>
      )}
    </div>
  );
}
