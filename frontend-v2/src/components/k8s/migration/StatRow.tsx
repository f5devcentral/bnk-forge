/**
 * StatRow — shared label/value row used in scan result cards.
 * Extracted from ClusterScanResults.tsx.
 */

import { cn } from '@/lib/utils';
import { StatusDot } from './ScanCard';
import type { PrereqStatus } from './ScanCard';

export function StatRow({
  label,
  value,
  total,
  suffix,
  status,
  mono,
}: {
  label: string;
  value: number | string;
  total?: number | string;
  suffix?: string;
  status?: PrereqStatus;
  mono?: boolean;
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-muted-foreground">{label}</span>
      <div className="flex items-center gap-2">
        {status && <StatusDot status={status} />}
        <span className={cn('text-sm font-medium tabular-nums', mono && 'font-mono')}>
          {value}{total !== undefined ? `/${total}` : ''}{suffix ? ` ${suffix}` : ''}
        </span>
      </div>
    </div>
  );
}
