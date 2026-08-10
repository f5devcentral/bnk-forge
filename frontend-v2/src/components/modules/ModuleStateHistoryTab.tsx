import { Clock, AlertCircle } from 'lucide-react';

import { Badge, type BadgeProps } from '@/components/ui/badge';
import { useModuleStateHistory } from '@/hooks/useModuleStateHistory';

interface Props {
  moduleId: number;
  currentStatus?: string;
}

const TERMINAL_FAIL_STATUSES = new Set([
  'init_failed', 'plan_failed', 'apply_failed', 'destroy_failed', 'failed',
]);

const TERMINAL_SUCCESS_STATUSES = new Set([
  'initialized', 'planned', 'applied', 'destroyed',
]);

const TRANSITIONAL_STATUSES = new Set([
  'initializing', 'planning', 'applying', 'destroying',
]);

function statusBadgeVariant(status: string): BadgeProps['variant'] {
  if (TERMINAL_FAIL_STATUSES.has(status)) return 'destructive';
  if (TERMINAL_SUCCESS_STATUSES.has(status)) return 'success';
  if (TRANSITIONAL_STATUSES.has(status)) return 'warning';
  return 'muted';
}

function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  const now = Date.now();
  const seconds = Math.max(0, Math.floor((now - then) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  return `${months}mo ago`;
}

function formatAbsolute(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

/**
 * Phase 2 state-machine: vertical timeline of every status transition for a
 * project module. Auto-polls every 5s while the module is in a transitional
 * status (initializing/planning/applying/destroying) so the timeline updates
 * in near-real-time during a deploy.
 */
export function ModuleStateHistoryTab({ moduleId, currentStatus }: Props) {
  const { data, isLoading, isError, error } = useModuleStateHistory(moduleId, {
    currentStatus,
    limit: 50,
  });

  if (isLoading) {
    return (
      <div className="p-4 rounded-lg bg-muted/40">
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-12 rounded animate-pulse bg-muted" />
          ))}
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="p-4 rounded-lg flex items-start gap-2 bg-destructive/10 text-destructive">
        <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
        <div className="text-sm">
          Failed to load state history: {(error as Error)?.message ?? 'unknown error'}
        </div>
      </div>
    );
  }

  const transitions = data?.transitions ?? [];

  if (transitions.length === 0) {
    return (
      <div className="p-6 rounded-lg text-center text-sm bg-muted/40 text-muted-foreground">
        <Clock className="h-6 w-6 mx-auto mb-2 opacity-40" />
        No state transitions recorded yet. As you init / plan / apply / destroy this
        module, each status change will appear here.
      </div>
    );
  }

  return (
    <div className="p-4 rounded-lg bg-muted/40">
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-sm font-semibold">State History</h4>
        <span className="text-xs text-muted-foreground">
          {transitions.length} transition{transitions.length === 1 ? '' : 's'}
        </span>
      </div>

      <ol className="space-y-3">
        {transitions.map((t, idx) => {
          const isLast = idx === transitions.length - 1;
          return (
            <li key={t.id} className="relative pl-6">
              <span className="absolute left-0 top-2 h-2 w-2 rounded-full bg-primary" />
              {!isLast && (
                <span className="absolute left-[3px] top-4 bottom-[-12px] w-px bg-border" />
              )}

              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Badge variant={statusBadgeVariant(t.from_status)} className="font-mono text-xs">
                  {t.from_status}
                </Badge>
                <span className="text-muted-foreground">→</span>
                <Badge variant={statusBadgeVariant(t.to_status)} className="font-mono text-xs">
                  {t.to_status}
                </Badge>
                {t.fence_token != null && (
                  <span
                    className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground"
                    title="Lock fence token at the moment of the transition"
                  >
                    fence={t.fence_token}
                  </span>
                )}
                {t.task_id != null && (
                  <span
                    className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground"
                    title="Task ID that drove the transition"
                  >
                    task#{t.task_id}
                  </span>
                )}
              </div>

              {t.reason && (
                <div className="mt-1 text-xs italic text-muted-foreground">
                  {t.reason}
                </div>
              )}

              <div
                className="mt-1 text-[11px] text-muted-foreground"
                title={formatAbsolute(t.at)}
              >
                {formatRelative(t.at)}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
