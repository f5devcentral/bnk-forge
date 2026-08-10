/**
 * Shared chart chrome tokens for the AI Gateway Observability surfaces.
 *
 * Mirrors `pages/BenchmarkRunDetail.tsx` so axis/grid/tooltip chrome tracks
 * the theme (light/dark) via CSS variables. Series colors are a fixed palette
 * cycled by index — deliberately hard-coded (not tokens) so a given model or
 * provider keeps a stable color across panels.
 */

export const CHART_GRID = 'hsl(var(--border))';
export const CHART_TEXT = 'hsl(var(--muted-foreground))';
export const CHART_TOOLTIP = {
  backgroundColor: 'hsl(var(--card))',
  border: '1px solid hsl(var(--border))',
  borderRadius: 8,
  fontSize: 12,
  color: 'hsl(var(--foreground))',
} as const;

/** Categorical series palette (cycled by index). */
export const SERIES_COLORS = [
  '#3b82f6', // blue
  '#14b8a6', // teal
  '#f59e0b', // amber
  '#8b5cf6', // violet
  '#ef4444', // red
  '#10b981', // emerald
  '#ec4899', // pink
  '#06b6d4', // cyan
  '#a3a3a3', // neutral (Other)
] as const;

export const seriesColor = (index: number): string =>
  SERIES_COLORS[index % SERIES_COLORS.length];

/** Semantic colors for success / error request-volume splits. */
export const SUCCESS_COLOR = '#10b981';
export const ERROR_COLOR = '#ef4444';

/** Fixed provider colors so provider panels stay visually consistent. */
export const PROVIDER_COLORS: Record<string, string> = {
  openai: '#10b981',
  anthropic: '#d97757',
  google: '#4285f4',
  unknown: '#a3a3a3',
};

export const providerColor = (provider: string): string =>
  PROVIDER_COLORS[provider] ?? '#a3a3a3';
