/**
 * Canonical health severity configuration (PLAT-REL-001 / UX-OPS-002).
 *
 * Single source of truth for mapping HealthSeverity values to visual
 * presentation: icons, colors, backgrounds, labels, and borders.
 *
 * Replaces duplicated `severityConfig` objects in:
 * - BNKHealthDashboard.tsx
 * - HealthDetailCard.tsx
 * - And any future health-displaying component
 *
 * Supports both canonical ("degraded"/"unhealthy") and legacy
 * ("warning"/"critical") terms from BNK health API.
 */

import {
  AlertTriangle,
  CheckCircle2,
  HelpCircle,
  XCircle,
  type LucideIcon,
} from 'lucide-react';

import type { HealthSeverity } from '@/types/f5bnk';

export interface SeverityConfig {
  icon: LucideIcon;
  label: string;
  color: string;      // Text/icon color class
  bg: string;         // Background color class
  border: string;     // Border color class
  dot: string;        // Status dot color class
  pulse: boolean;     // Animate pulse for urgent states
}

/**
 * Canonical severity configuration map.
 *
 * Includes both canonical (PLAT-REL-001) and legacy BNK terms.
 */
export const SEVERITY_CONFIG: Record<string, SeverityConfig> = {
  // ── Canonical (PLAT-REL-001) — token-pure (D-020) ──
  healthy: {
    icon: CheckCircle2,
    label: 'Healthy',
    color: 'text-success',
    bg: 'bg-success/10',
    border: 'border-success/20',
    dot: 'bg-success',
    pulse: false,
  },
  degraded: {
    icon: AlertTriangle,
    label: 'Degraded',
    color: 'text-warning',
    bg: 'bg-warning/10',
    border: 'border-warning/20',
    dot: 'bg-warning',
    pulse: false,
  },
  unhealthy: {
    icon: XCircle,
    label: 'Unhealthy',
    color: 'text-destructive',
    bg: 'bg-destructive/10',
    border: 'border-destructive/20',
    dot: 'bg-destructive',
    pulse: true,
  },
  unknown: {
    icon: HelpCircle,
    label: 'Unknown',
    color: 'text-muted-foreground',
    bg: 'bg-muted',
    border: 'border-border',
    dot: 'bg-muted-foreground',
    pulse: false,
  },

  // ── Legacy (backward compat with BNK health API) ──
  warning: {
    icon: AlertTriangle,
    label: 'Warning',
    color: 'text-warning',
    bg: 'bg-warning/10',
    border: 'border-warning/20',
    dot: 'bg-warning',
    pulse: false,
  },
  critical: {
    icon: XCircle,
    label: 'Critical',
    color: 'text-destructive',
    bg: 'bg-destructive/10',
    border: 'border-destructive/20',
    dot: 'bg-destructive',
    pulse: true,
  },
};

/**
 * Get severity config for a given severity string.
 * Falls back to "unknown" for unrecognized values.
 */
export function getSeverityConfig(severity: HealthSeverity | string): SeverityConfig {
  return SEVERITY_CONFIG[severity] ?? SEVERITY_CONFIG.unknown;
}

/**
 * Severity ordering for sorting/comparison (lower = worse).
 *
 * Supports both canonical and legacy terms.
 */
const SEVERITY_ORDER: Record<string, number> = {
  unhealthy: 0,
  critical: 0,   // legacy alias
  degraded: 1,
  warning: 1,    // legacy alias
  unknown: 2,
  healthy: 3,
};

/**
 * Compare two severities. Returns negative if a is worse than b.
 */
export function compareSeverity(a: string, b: string): number {
  return (SEVERITY_ORDER[a] ?? 2) - (SEVERITY_ORDER[b] ?? 2);
}

/**
 * Get the worst severity from a list.
 */
export function worstSeverity(severities: string[]): HealthSeverity {
  if (severities.length === 0) return 'unknown';
  return severities.reduce((worst, current) =>
    compareSeverity(current, worst) < 0 ? current : worst
  ) as HealthSeverity;
}
