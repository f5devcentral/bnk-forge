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
      return <CheckCircle2 className="h-4 w-4 text-green-500" />;
    case 'false':
      return <XCircle className="h-4 w-4 text-red-500" />;
    case 'unknown':
      return <AlertCircle className="h-4 w-4 text-yellow-500" />;
    default:
      return <Clock className="h-4 w-4 text-slate-500" />;
  }
}

export function getConditionColor(status: string) {
  switch (status?.toLowerCase()) {
    case 'true':
      return 'text-green-500';
    case 'false':
      return 'text-red-500';
    case 'unknown':
      return 'text-yellow-500';
    default:
      return 'text-slate-500';
  }
}

export interface DetailPanelProps {
  resource: K8sResource;
  isDark?: boolean;
}

export function InfoRow({ label, value, isDark, mono = false }: { label: string; value: string | number | boolean | null | undefined; isDark?: boolean; mono?: boolean }) {
  if (value === undefined || value === null || value === '') return null;
  return (
    <div className="flex justify-between items-start gap-2">
      <span className="text-slate-500 shrink-0">{label}:</span>
      {mono ? (
        <code className={cn('font-mono text-right break-all min-w-0', isDark ? 'text-slate-300' : 'text-slate-700')}>
          {String(value)}
        </code>
      ) : (
        <span className={cn('text-right break-words min-w-0', isDark ? 'text-slate-300' : 'text-slate-700')}>
          {String(value)}
        </span>
      )}
    </div>
  );
}

export function Section({ title, isDark, children }: { title: string; isDark?: boolean; children: React.ReactNode }) {
  return (
    <div className={cn('p-3 rounded-lg', isDark ? 'bg-slate-800/50' : 'bg-slate-50')}>
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
      <div className={cn('p-6 text-center rounded-lg', isDark ? 'bg-slate-800/50' : 'bg-slate-50')}>
        <AlertCircle className="h-8 w-8 mx-auto mb-2 opacity-50" />
        <p className="text-xs text-slate-500">No status conditions available</p>
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
            isDark ? 'bg-slate-800/50 border-slate-700' : 'bg-slate-50 border-slate-200'
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
              <span className="text-slate-500">Status:</span>
              <span className={cn('font-medium', getConditionColor(condition.status))}>
                {condition.status}
              </span>
            </div>
            {condition.reason && (
              <div className="flex justify-between">
                <span className="text-slate-500">Reason:</span>
                <span className={isDark ? 'text-slate-300' : 'text-slate-700'}>{condition.reason}</span>
              </div>
            )}
            {condition.message && (
              <div className="mt-2">
                <p className={cn('text-xs', isDark ? 'text-slate-400' : 'text-slate-600')}>
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
