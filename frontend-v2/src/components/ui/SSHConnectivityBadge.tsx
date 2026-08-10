/**
 * Reusable SSH/jumphost connectivity indicator.
 *
 * Renders a Network icon that pulses warning while a connectivity probe is
 * in flight, turns success on success, destructive on failure, and muted when
 * no probe data is available yet. Hover tooltip surfaces the SSH banner /
 * hostname / error message so users get real diagnostic info instead of a
 * silent spinner.
 *
 * Two render variants:
 *   - default ("inline"): icon + label, suitable for stat rows / project headers
 *   - "compact": just the icon, suitable for cluster-list rows / tight grids
 *
 * Use this anywhere a project/cluster has an associated SSH jumphost.
 */
import { Network } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useSSHConnectivity } from '@/hooks/useSSHConnectivity';
import { useRole } from '@/hooks/useRole';

interface SSHConnectivityBadgeProps {
  /** SSH credential ID to probe. If null/undefined, the badge renders nothing. */
  credentialId: number | null | undefined;
  /** Display label for the SSH credential (e.g. "host-1.lab.example.com") */
  label?: string;
  /** "user@host" string, used in the tooltip */
  base?: string;
  /** Render mode — "inline" shows icon + label, "compact" shows just the icon */
  variant?: 'inline' | 'compact';
}

export function SSHConnectivityBadge({
  credentialId,
  label,
  base,
  variant = 'inline',
}: SSHConnectivityBadgeProps) {
  const { isOperator } = useRole();
  const probe = useSSHConnectivity(credentialId, isOperator);

  if (!credentialId) return null;

  const baseLabel = base ?? label ?? 'SSH';

  let iconClass: string;
  let tooltip: string;

  if (probe.isFetching) {
    iconClass = 'text-warning animate-pulse';
    tooltip = `Jumphost: ${baseLabel} — checking connectivity...`;
  } else if (probe.isError) {
    iconClass = 'text-destructive';
    tooltip = `Jumphost: ${baseLabel} — connection check failed: ${(probe.error as Error)?.message || 'unknown error'}`;
  } else if (probe.data?.success) {
    const detail = probe.data.ssh_banner || probe.data.hostname || probe.data.message;
    iconClass = 'text-success';
    tooltip = `Jumphost: ${baseLabel} — reachable${detail ? ` (${detail})` : ''}`;
  } else if (probe.data && !probe.data.success) {
    iconClass = 'text-destructive';
    tooltip = `Jumphost: ${baseLabel} — unreachable: ${probe.data.error || probe.data.message || 'unknown error'}`;
  } else {
    iconClass = 'text-muted-foreground';
    tooltip = `Jumphost: ${baseLabel}`;
  }

  if (variant === 'compact') {
    return (
      <span title={tooltip} className="inline-flex items-center">
        <Network className={cn('h-3.5 w-3.5', iconClass)} />
      </span>
    );
  }

  return (
    <div className="flex items-center gap-2" title={tooltip}>
      <Network className={cn('h-4 w-4', iconClass)} />
      {label && (
        <span className="text-muted-foreground">
          Jumphost: {label}
        </span>
      )}
    </div>
  );
}
