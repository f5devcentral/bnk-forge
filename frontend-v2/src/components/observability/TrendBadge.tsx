/**
 * TrendBadge — up/down percentage delta versus the previous window, or "new"
 * when there's no prior data point. Green for up, red for down; direction is
 * purely magnitude-based (callers decide whether up is "good").
 */
import { ArrowUp, ArrowDown } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface TrendBadgeProps {
  /** Fractional delta (e.g. 0.12 = +12%), or null for "new". */
  delta: number | null;
  className?: string;
}

export function TrendBadge({ delta, className }: TrendBadgeProps) {
  if (delta === null) {
    return (
      <span
        className={cn(
          'inline-flex items-center rounded px-1 py-0.5 text-[10px] font-medium bg-muted text-muted-foreground',
          className,
        )}
      >
        new
      </span>
    );
  }

  if (Math.abs(delta) < 0.001) {
    return <span className={cn('text-[10px] text-muted-foreground/70', className)}>—</span>;
  }

  const up = delta > 0;
  const Icon = up ? ArrowUp : ArrowDown;
  return (
    <span
      className={cn(
        'inline-flex items-center gap-0.5 text-[10px] font-medium tabular-nums',
        up ? 'text-success' : 'text-destructive',
        className,
      )}
    >
      <Icon className="h-2.5 w-2.5" />
      {Math.abs(delta * 100).toFixed(0)}%
    </span>
  );
}
