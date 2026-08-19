/**
 * Benchmark shared constants, helpers, and badge components.
 * Used by all benchmark tab components.
 */
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import {
  Clock,
  Loader2,
  CheckCircle2,
  AlertCircle,
  XCircle,
  Server,
  Anchor,
  TrendingUp,
  TrendingDown,
} from 'lucide-react';
import type { BenchmarkRunStatus } from '@/types';

// ============================================================================
// Constants
// ============================================================================

export const PROXY_COLORS: Record<string, string> = {
  envoy: '#8b5cf6',     // violet
  nginx: '#10b981',     // emerald
  haproxy: '#f59e0b',   // amber
  'f5-bnk': '#ef4444',   // red
  nodeport: '#3b82f6',  // blue
  'envoy-ai-gateway': '#ec4899',  // pink
  'llm-d-router': '#06b6d4',  // cyan
};

export const PROXY_LABELS: Record<string, string> = {
  envoy: 'Envoy',
  nginx: 'Nginx',
  haproxy: 'HAProxy',
  'f5-bnk': 'F5 BNK',
  nodeport: 'NodePort (No Proxy)',
  'envoy-ai-gateway': 'Envoy AI Gateway',
  'llm-d-router': 'llm-d Router',
};

export const STATUS_CONFIG: Record<BenchmarkRunStatus, { label: string; variant: 'default' | 'secondary' | 'destructive' | 'outline' | 'success' | 'info' | 'warning' | 'muted'; icon: typeof CheckCircle2 }> = {
  pending: { label: 'Pending', variant: 'muted', icon: Clock },
  running: { label: 'Running', variant: 'info', icon: Loader2 },
  completed: { label: 'Completed', variant: 'success', icon: CheckCircle2 },
  failed: { label: 'Failed', variant: 'destructive', icon: AlertCircle },
  cancelled: { label: 'Cancelled', variant: 'muted', icon: XCircle },
};

export const TARGET_STATUS_CONFIG: Record<string, { label: string; variant: 'default' | 'secondary' | 'destructive' | 'outline'; color: string }> = {
  active: { label: 'Active', variant: 'default', color: '#10b981' },
  inactive: { label: 'Inactive', variant: 'secondary', color: '#71717a' },
  validating: { label: 'Validating', variant: 'outline', color: '#f59e0b' },
  error: { label: 'Error', variant: 'destructive', color: '#ef4444' },
};

export const PROXY_DEPLOY_STATUS_CONFIG: Record<string, { label: string; variant: 'default' | 'secondary' | 'destructive' | 'outline'; color: string }> = {
  discovered: { label: 'Discovered', variant: 'default', color: '#3b82f6' },
  pending: { label: 'Pending', variant: 'secondary', color: '#71717a' },
  deploying: { label: 'Deploying', variant: 'outline', color: '#f59e0b' },
  ready: { label: 'Ready', variant: 'default', color: '#10b981' },
  failed: { label: 'Failed', variant: 'destructive', color: '#ef4444' },
  uninstalling: { label: 'Uninstalling', variant: 'outline', color: '#f59e0b' },
  uninstalled: { label: 'Uninstalled', variant: 'secondary', color: '#71717a' },
};

export const AVAILABLE_PROXY_TYPES = ['envoy', 'nginx', 'haproxy', 'f5-bnk', 'envoy-ai-gateway', 'llm-d-router'] as const;

// ============================================================================
// Badge Components
// ============================================================================

export function StatusBadge({ status }: { status: BenchmarkRunStatus }) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.pending;
  const Icon = config.icon;
  return (
    <Badge variant={config.variant} className="gap-1">
      <Icon className={cn('h-3 w-3', status === 'running' && 'animate-spin')} />
      {config.label}
    </Badge>
  );
}

export function ProxyBadge({ proxy }: { proxy: string }) {
  const color = PROXY_COLORS[proxy] || '#71717a';
  const label = PROXY_LABELS[proxy] || proxy;
  return (
    <Badge variant="outline" className="gap-1" style={{ borderColor: color, color }}>
      <Server className="h-3 w-3" />
      {label}
    </Badge>
  );
}

// ============================================================================
// Baseline / Regression — shared threshold + pure classifier
// ============================================================================

/** A run regresses when latency_p99 is this much WORSE, or overall_rps this much
 * LOWER, than its context's baseline. Shared between Runs list, Run Detail, and
 * the vitest coverage in benchmark-utils.test.ts. */
export const REGRESSION_THRESHOLD_PCT = 10;

export type RegressionStatus = 'baseline' | 'regression' | 'improvement' | 'neutral' | null;

interface RegressionInput {
  is_baseline?: boolean;
  latency_p99?: number | null;
  overall_rps?: number | null;
  baseline_latency_p99?: number | null;
  baseline_overall_rps?: number | null;
}

/**
 * Classify a completed run against its (target, scenario/config) baseline.
 * Returns null when the run has no baseline context to compare against —
 * callers must render no badge in that case (never a false "neutral").
 */
export function getRegressionStatus(run: RegressionInput): RegressionStatus {
  if (run.is_baseline) return 'baseline';

  const hasLatency = run.baseline_latency_p99 != null && run.latency_p99 != null;
  const hasRps = run.baseline_overall_rps != null && run.overall_rps != null;
  if (!hasLatency && !hasRps) return null;

  const factor = REGRESSION_THRESHOLD_PCT / 100;
  const latencyWorse = hasLatency && run.latency_p99! > run.baseline_latency_p99! * (1 + factor);
  const rpsWorse = hasRps && run.overall_rps! < run.baseline_overall_rps! * (1 - factor);
  if (latencyWorse || rpsWorse) return 'regression';

  const latencyBetter = hasLatency && run.latency_p99! < run.baseline_latency_p99! * (1 - factor);
  const rpsBetter = hasRps && run.overall_rps! > run.baseline_overall_rps! * (1 + factor);
  if (latencyBetter || rpsBetter) return 'improvement';

  return 'neutral';
}

export function RegressionBadge({ run }: { run: RegressionInput }) {
  const status = getRegressionStatus(run);
  if (!status || status === 'neutral') return null;

  if (status === 'baseline') {
    return (
      <Badge variant="info" className="gap-1">
        <Anchor className="h-3 w-3" />
        Baseline
      </Badge>
    );
  }
  if (status === 'regression') {
    return (
      <Badge variant="destructive" className="gap-1">
        <TrendingDown className="h-3 w-3" />
        Regression
      </Badge>
    );
  }
  return (
    <Badge variant="success" className="gap-1">
      <TrendingUp className="h-3 w-3" />
      Improved
    </Badge>
  );
}

// ============================================================================
// Format Helpers
// ============================================================================

export function fmtDuration(s: number | null | undefined): string {
  if (!s) return '—';
  if (s < 60) return `${s.toFixed(1)}s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

export function fmtLatency(s: number | null | undefined): string {
  if (s == null) return '—';
  if (s < 0.001) return `${(s * 1_000_000).toFixed(0)}µs`;
  if (s < 1) return `${(s * 1000).toFixed(1)}ms`;
  return `${s.toFixed(2)}s`;
}

export function fmtNum(n: number | null | undefined, decimals = 1): string {
  if (n == null) return '—';
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return n.toFixed(decimals);
}

export function fmtPct(n: number | null | undefined): string {
  if (n == null) return '—';
  return `${n.toFixed(1)}%`;
}

// ============================================================================
// Utility Helpers
// ============================================================================

/** Download helper — creates a Blob and triggers download */
export function downloadJson(data: object, filename: string) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
