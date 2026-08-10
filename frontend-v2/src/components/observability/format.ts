/**
 * Shared number/label formatters for the observability surfaces.
 * Kept tiny and dependency-free so both pages and primitives can reuse them.
 */

/** Compact integer with thousands separators (e.g. 12,345). */
export function fmtInt(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—';
  return Math.round(value).toLocaleString();
}

/** Compact large numbers as 1.2K / 3.4M. */
export function fmtCompact(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—';
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (Math.abs(value) >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return Math.round(value).toLocaleString();
}

/** USD cost with adaptive precision. */
export function fmtCost(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—';
  if (value === 0) return '$0.00';
  if (Math.abs(value) < 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** Latency in ms with one decimal. */
export function fmtLatencyMs(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—';
  return `${value.toFixed(1)} ms`;
}

/** Ratio (0..1) as a percentage. */
export function fmtPct(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return '—';
  return `${(value * 100).toFixed(digits)}%`;
}

/** Infer provider from model id — parity with the backend static map. */
export function inferProvider(model: string): string {
  const m = model.toLowerCase();
  if (/^(gpt|o1|o3|o4)/.test(m)) return 'openai';
  if (m.startsWith('claude')) return 'anthropic';
  if (m.startsWith('gemini')) return 'google';
  return 'unknown';
}
