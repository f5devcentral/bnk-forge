/**
 * Shared connection/operator status badge.
 *
 * Supports canonical status vocabulary (PLAT-REL-001) plus legacy terms
 * for backward compatibility during migration.
 *
 * Canonical vocabulary:
 * - Health:       healthy | degraded | unhealthy | unknown
 * - Connectivity: connected | reachable | partial | unreachable | unknown
 * - Operator:     connected | disconnected | error
 *
 * Legacy terms still accepted (mapped to canonical):
 * - "warning" → degraded, "critical" → unhealthy, "offline" → unhealthy,
 *   "port_blocked" → partial, "healthy" (connectivity) → connected
 *
 * @example
 * // Connectivity status
 * <ConnectionStatusBadge status="connected" />
 * <ConnectionStatusBadge status="partial" />
 *
 * // Health status
 * <ConnectionStatusBadge status="healthy" />
 * <ConnectionStatusBadge status="degraded" />
 *
 * // Operators (connected prop overrides)
 * <ConnectionStatusBadge connected={true} status="active" />
 * <ConnectionStatusBadge connected={false} status="disconnected" />
 */

import { cn } from '@/lib/utils';

type BadgeVariant =
  // Canonical health (PLAT-REL-001)
  | 'healthy' | 'degraded' | 'unhealthy'
  // Canonical connectivity (PLAT-REL-001)
  | 'connected' | 'reachable' | 'partial' | 'unreachable'
  // Shared
  | 'unknown'
  // Operator-specific
  | 'disconnected' | 'error'
  // Legacy (backward compat — will be removed)
  | 'warning' | 'critical' | 'offline' | 'port_blocked';

interface ConnectionStatusBadgeProps {
  /** Primary status string — maps to a known variant or falls back to "unknown" */
  status: string;
  /** If provided, overrides status-based variant: true → "connected", false → based on status */
  connected?: boolean;
  /** Optional className override */
  className?: string;
}

const VARIANTS: Record<BadgeVariant, { label: string; color: string; dot: string; bg: string; border: string; pulse?: boolean }> = {
  // ── Canonical Health (PLAT-REL-001) ──
  healthy: {
    label: 'Healthy',
    color: 'text-success',
    dot: 'bg-success',
    bg: 'bg-success/10',
    border: 'border-success/20',
  },
  degraded: {
    label: 'Degraded',
    color: 'text-warning',
    dot: 'bg-warning',
    bg: 'bg-warning/10',
    border: 'border-warning/20',
  },
  unhealthy: {
    label: 'Unhealthy',
    color: 'text-destructive',
    dot: 'bg-destructive',
    bg: 'bg-destructive/10',
    border: 'border-destructive/20',
    pulse: true,
  },

  // ── Canonical Connectivity (PLAT-REL-001) ──
  connected: {
    label: 'Connected',
    color: 'text-success',
    dot: 'bg-success',
    bg: 'bg-success/10',
    border: 'border-success/20',
    pulse: true,
  },
  reachable: {
    label: 'Reachable',
    color: 'text-warning',
    dot: 'bg-warning',
    bg: 'bg-warning/10',
    border: 'border-warning/20',
  },
  partial: {
    label: 'Partial',
    color: 'text-warning',
    dot: 'bg-warning',
    bg: 'bg-warning/10',
    border: 'border-warning/20',
  },
  unreachable: {
    label: 'Unreachable',
    color: 'text-destructive',
    dot: 'bg-destructive',
    bg: 'bg-destructive/10',
    border: 'border-destructive/20',
  },

  // ── Shared ──
  unknown: {
    label: 'Unknown',
    color: 'text-muted-foreground',
    dot: 'bg-muted-foreground',
    bg: 'bg-muted',
    border: 'border-border',
  },

  // ── Operator-specific ──
  disconnected: {
    label: 'Disconnected',
    color: 'text-muted-foreground',
    dot: 'bg-muted-foreground',
    bg: 'bg-muted',
    border: 'border-border',
  },
  error: {
    label: 'Error',
    color: 'text-destructive',
    dot: 'bg-destructive',
    bg: 'bg-destructive/10',
    border: 'border-destructive/20',
  },

  // ── Legacy (backward compat — migrate callers then remove) ──
  warning: {
    label: 'Warning',
    color: 'text-warning',
    dot: 'bg-warning',
    bg: 'bg-warning/10',
    border: 'border-warning/20',
  },
  critical: {
    label: 'Critical',
    color: 'text-destructive',
    dot: 'bg-destructive',
    bg: 'bg-destructive/10',
    border: 'border-destructive/20',
    pulse: true,
  },
  offline: {
    label: 'Offline',
    color: 'text-muted-foreground',
    dot: 'bg-muted-foreground',
    bg: 'bg-muted',
    border: 'border-border',
  },
  port_blocked: {
    label: 'Port Blocked',
    color: 'text-warning',
    dot: 'bg-warning',
    bg: 'bg-warning/10',
    border: 'border-warning/20',
  },
};

function resolveVariant(status: string, connected?: boolean): BadgeVariant {
  // If connected prop is explicitly provided, use it
  if (connected === true) return 'connected';
  if (connected === false) {
    if (status === 'error') return 'error';
    if (status === 'disconnected') return 'disconnected';
    return 'offline';
  }
  // Status-based resolution
  if (status in VARIANTS) return status as BadgeVariant;
  return 'unknown';
}

export function ConnectionStatusBadge({ status, connected, className }: ConnectionStatusBadgeProps) {
  const variant = resolveVariant(status, connected);
  const cfg = VARIANTS[variant];
  const label = variant === 'unknown' ? status : cfg.label;

  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium border',
        cfg.bg,
        cfg.color,
        cfg.border,
        className
      )}
    >
      <span className={cn('w-1.5 h-1.5 rounded-full', cfg.dot, cfg.pulse && 'animate-pulse')} />
      {label}
    </span>
  );
}
