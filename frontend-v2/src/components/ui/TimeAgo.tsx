/**
 * Shared TimeAgo component — renders a relative timestamp.
 *
 * Replaces 3 duplicate implementations in Fleet.tsx, Operators.tsx,
 * and SnapshotHistory.tsx with a single source of truth backed by
 * lib/time-utils.ts.
 *
 * @example
 * <TimeAgo dateStr={operator.last_seen} />
 * <TimeAgo dateStr={snapshot.created_at} fallback="Never" />
 */

import { formatTimeAgo } from '@/lib/time-utils';

interface TimeAgoProps {
  /** ISO timestamp string, or null/undefined */
  dateStr: string | null | undefined;
  /** Text to show when dateStr is null/undefined (default: "--") */
  fallback?: string;
  /** Optional className for the wrapper span */
  className?: string;
}

export function TimeAgo({ dateStr, fallback = '--', className }: TimeAgoProps) {
  if (!dateStr) {
    return <span className={className ?? 'text-muted-foreground'}>{fallback}</span>;
  }

  const label = formatTimeAgo(dateStr);
  const fullDate = new Date(dateStr).toLocaleString();

  return (
    <span title={fullDate} className={className}>
      {label || fallback}
    </span>
  );
}
