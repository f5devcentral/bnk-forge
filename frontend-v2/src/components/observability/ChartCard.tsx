/**
 * ChartCard — the standard framed panel for an observability chart.
 *
 * Provides: title, an optional animated total (counts up on value change),
 * a legend that collapses to "+N more" past a cap, a controls slot (chart-type
 * toggle, selectors), a loading skeleton, and an unavailable state driven by
 * the degradation envelope. Chrome uses the shared theme tokens; the chart
 * itself is passed as children.
 */
import * as React from 'react';
import { AlertTriangle } from 'lucide-react';
import { SectionCard } from '@/components/ui/section-card';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';

export interface ChartLegendItem {
  name: string;
  color: string;
}

export interface ChartCardProps {
  title: string;
  /** Formatted headline total shown top-right, animated on change. */
  total?: string;
  /** Raw numeric value backing an animated count-up (optional). */
  totalValue?: number;
  legend?: ChartLegendItem[];
  /** Max legend chips before collapsing to "+N more". */
  maxLegend?: number;
  /** Right-aligned controls (toggles, selectors). */
  controls?: React.ReactNode;
  loading?: boolean;
  /** From the degradation envelope — false renders the unavailable state. */
  available?: boolean;
  /** Reason string surfaced in the unavailable state. */
  unavailableReason?: string;
  className?: string;
  children?: React.ReactNode;
}

/** Count up to `target` over ~500ms; respects reduced motion by snapping. */
function useAnimatedNumber(target: number | undefined): number {
  const [value, setValue] = React.useState(target ?? 0);
  const fromRef = React.useRef(target ?? 0);

  React.useEffect(() => {
    if (target == null) return;
    const prefersReduced =
      typeof window !== 'undefined' &&
      window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    if (prefersReduced) {
      setValue(target);
      fromRef.current = target;
      return;
    }
    const from = fromRef.current;
    const start = performance.now();
    const duration = 500;
    let raf = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setValue(from + (target - from) * eased);
      if (t < 1) raf = requestAnimationFrame(tick);
      else fromRef.current = target;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target]);

  return value;
}

export function ChartCard({
  title,
  total,
  totalValue,
  legend,
  maxLegend = 4,
  controls,
  loading,
  available = true,
  unavailableReason,
  className,
  children,
}: ChartCardProps) {
  const animated = useAnimatedNumber(totalValue);
  const headline =
    total ?? (totalValue != null ? Math.round(animated).toLocaleString() : undefined);

  const shown = legend?.slice(0, maxLegend) ?? [];
  const hiddenCount = (legend?.length ?? 0) - shown.length;

  return (
    <SectionCard compact className={cn('flex flex-col', className)}>
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="min-w-0">
          <h4 className="text-sm font-semibold text-foreground truncate">{title}</h4>
          {headline != null && (
            <span
              className="text-xl font-semibold font-mono text-foreground tabular-nums"
              aria-live="polite"
            >
              {headline}
            </span>
          )}
        </div>
        {controls && <div className="flex items-center gap-1.5 flex-shrink-0">{controls}</div>}
      </div>

      {loading ? (
        <Skeleton className="h-[200px] w-full" />
      ) : !available ? (
        <div className="flex h-[200px] flex-col items-center justify-center gap-2 text-center text-muted-foreground">
          <AlertTriangle className="h-5 w-5 text-warning" />
          <p className="text-xs">{unavailableReason || 'Data source unavailable'}</p>
        </div>
      ) : (
        <div className="flex-1">{children}</div>
      )}

      {!loading && available && shown.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1">
          {shown.map((item) => (
            <div key={item.name} className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <span
                className="h-2 w-2 rounded-full flex-shrink-0"
                style={{ backgroundColor: item.color }}
              />
              <span className="truncate max-w-[140px]">{item.name}</span>
            </div>
          ))}
          {hiddenCount > 0 && (
            <span className="text-xs text-muted-foreground/70">+{hiddenCount} more</span>
          )}
        </div>
      )}
    </SectionCard>
  );
}
