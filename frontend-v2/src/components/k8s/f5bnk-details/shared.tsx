/* eslint-disable react-refresh/only-export-components */
/**
 * Shared helpers, types, and sub-components used across F5 BNK detail panels.
 */

import {
  CheckCircle2,
  XCircle,
  AlertCircle,
  Clock,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import type { K8sResource, K8sCondition } from '@/types';

// ─── Shared Helpers ────────────────────────────────────────────────────

export function getConditionIcon(status: string) {
  switch (status?.toLowerCase()) {
    case 'true':
      return <CheckCircle2 className="h-4 w-4 text-success" />;
    case 'false':
      return <XCircle className="h-4 w-4 text-destructive" />;
    case 'unknown':
      return <AlertCircle className="h-4 w-4 text-warning" />;
    default:
      return <Clock className="h-4 w-4 text-muted-foreground" />;
  }
}

export function getConditionColor(status: string) {
  switch (status?.toLowerCase()) {
    case 'true':
      return 'text-success';
    case 'false':
      return 'text-destructive';
    case 'unknown':
      return 'text-warning';
    default:
      return 'text-muted-foreground';
  }
}

export interface DetailPanelProps {
  resource: K8sResource;
  isDark?: boolean;
  clusterId?: number;
}

export function InfoRow({ label, value, isDark, mono = false }: { label: string; value: string | number | boolean | null | undefined; isDark?: boolean; mono?: boolean }) {
  if (value === undefined || value === null || value === '') return null;
  return (
    <div className="flex justify-between items-start gap-2">
      <span className="text-muted-foreground shrink-0">{label}:</span>
      {mono ? (
        <code className={cn('font-mono text-right break-all min-w-0', isDark ? 'text-muted-foreground/70' : 'text-foreground/80')}>
          {String(value)}
        </code>
      ) : (
        <span className={cn('text-right break-words min-w-0', isDark ? 'text-muted-foreground/70' : 'text-foreground/80')}>
          {String(value)}
        </span>
      )}
    </div>
  );
}

export function Section({ title, isDark, children }: { title: string; isDark?: boolean; children: React.ReactNode }) {
  return (
    <div className={cn('p-3 rounded-lg', isDark ? 'bg-card/50' : 'bg-muted/50')}>
      <h4 className="text-xs font-semibold mb-2">{title}</h4>
      <div className="space-y-1.5 text-xs">
        {children}
      </div>
    </div>
  );
}

export function ConditionsTab({ conditions, isDark }: { conditions: K8sCondition[]; isDark?: boolean }) {
  if (!conditions || conditions.length === 0) {
    return (
      <div className={cn('p-6 text-center rounded-lg', isDark ? 'bg-card/50' : 'bg-muted/50')}>
        <AlertCircle className="h-8 w-8 mx-auto mb-2 opacity-50" />
        <p className="text-xs text-muted-foreground">No status conditions available</p>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      {conditions.map((condition: K8sCondition, idx: number) => (
        <div
          key={idx}
          className={cn(
            'p-3 rounded-lg border',
            isDark ? 'bg-card/50 border-border' : 'bg-muted/50 border-border'
          )}
        >
          <div className="flex items-center gap-2 mb-2">
            {getConditionIcon(condition.status)}
            <span className={cn('font-medium text-sm', getConditionColor(condition.status))}>
              {condition.type}
            </span>
          </div>
          <div className="space-y-1 text-xs">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Status:</span>
              <span className={cn('font-medium', getConditionColor(condition.status))}>
                {condition.status}
              </span>
            </div>
            {condition.reason && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Reason:</span>
                <span className={isDark ? 'text-muted-foreground/70' : 'text-foreground/80'}>{condition.reason}</span>
              </div>
            )}
            {condition.message && (
              <div className="mt-2">
                <p className={cn('text-xs', isDark ? 'text-muted-foreground' : 'text-muted-foreground')}>
                  {condition.message}
                </p>
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
