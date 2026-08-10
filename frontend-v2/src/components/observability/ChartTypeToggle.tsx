/**
 * ChartTypeToggle — small bar↔area segmented toggle for time-series panels.
 */
import { BarChart3, AreaChart } from 'lucide-react';
import { cn } from '@/lib/utils';

export type ChartType = 'bar' | 'area';

export interface ChartTypeToggleProps {
  value: ChartType;
  onChange: (next: ChartType) => void;
}

export function ChartTypeToggle({ value, onChange }: ChartTypeToggleProps) {
  return (
    <div className="inline-flex rounded-md border border-border p-0.5" role="group" aria-label="Chart type">
      {(
        [
          { key: 'bar', icon: BarChart3, label: 'Bar' },
          { key: 'area', icon: AreaChart, label: 'Area' },
        ] as const
      ).map(({ key, icon: Icon, label }) => (
        <button
          key={key}
          type="button"
          aria-label={label}
          aria-pressed={value === key}
          onClick={() => onChange(key)}
          className={cn(
            'flex h-6 w-6 items-center justify-center rounded transition-colors',
            value === key
              ? 'bg-primary/10 text-primary'
              : 'text-muted-foreground hover:text-foreground',
          )}
        >
          <Icon className="h-3.5 w-3.5" />
        </button>
      ))}
    </div>
  );
}
