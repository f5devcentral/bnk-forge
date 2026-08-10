/**
 * StatTile — compact KPI tile for the Logs page stat strip.
 * Icon + label + value with a tone-driven accent color.
 */
import * as React from 'react';
import { SectionCard } from '@/components/ui/section-card';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';

type Tone = 'info' | 'success' | 'warning' | 'destructive' | 'primary' | 'muted';

const TONE_CLASS: Record<Tone, string> = {
  info: 'text-info',
  success: 'text-success',
  warning: 'text-warning',
  destructive: 'text-destructive',
  primary: 'text-primary',
  muted: 'text-muted-foreground',
};

export interface StatTileProps {
  icon: React.ElementType;
  label: string;
  value: string;
  tone?: Tone;
  loading?: boolean;
}

export function StatTile({ icon: Icon, label, value, tone = 'muted', loading }: StatTileProps) {
  return (
    <SectionCard compact>
      <div className="flex items-center gap-1.5 mb-1">
        <Icon className={cn('h-3.5 w-3.5', TONE_CLASS[tone])} />
        <span className="text-xs text-muted-foreground">{label}</span>
      </div>
      {loading ? (
        <Skeleton className="h-6 w-20" />
      ) : (
        <span className="text-lg font-semibold font-mono text-foreground tabular-nums">{value}</span>
      )}
    </SectionCard>
  );
}
