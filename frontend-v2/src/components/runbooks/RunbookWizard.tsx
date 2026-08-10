/**
 * RunbookWizard — two-mode component for BNK diagnostic runbooks (UX-010)
 *
 * Modes:
 *   1. Picker — card grid showing available runbooks. Click to run.
 *   2. Runner — vertical stepper showing step-by-step execution results.
 *
 * D-020: token-pure surfaces (`bg-card`, `border-border`, `text-muted-foreground`)
 * and semantic status colors (`text-success`/`text-destructive`/`text-warning`/
 * `text-primary`). No `useTheme`/`useThemeClasses` plumbing.
 */

import { useState, useCallback, useEffect } from 'react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { SectionCard } from '@/components/ui/section-card';
import { useRunbooks, useExecuteRunbook } from '@/hooks/useRunbooks';
import type {
  RunbookDefinition,
  RunbookExecutionResult,
  RunbookStepResult,
  RunbookResourceLink,
} from '@/types';
import {
  ArrowLeft,
  CheckCircle2,
  XCircle,
  MinusCircle,
  Loader2,
  Network,
  Server,
  ArrowUpCircle,
  Play,
  RotateCcw,
  Stethoscope,
  ExternalLink,
  ChevronDown,
  ChevronRight,
  AlertCircle,
} from 'lucide-react';

// ---------------------------------------------------------------------------
// Category icon/color mapping — token-pure
// ---------------------------------------------------------------------------

const CATEGORY_CONFIG: Record<string, { icon: typeof Network; color: string; bg: string }> = {
  networking: { icon: Network, color: 'text-primary', bg: 'bg-primary/10' },
  platform: { icon: Server, color: 'text-info', bg: 'bg-info/10' },
  upgrade: { icon: ArrowUpCircle, color: 'text-warning', bg: 'bg-warning/10' },
};

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface RunbookWizardProps {
  clusterId: number;
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export function RunbookWizard({ clusterId }: RunbookWizardProps) {
  const [selectedRunbook, setSelectedRunbook] = useState<RunbookDefinition | null>(null);
  const [executionResult, setExecutionResult] = useState<RunbookExecutionResult | null>(null);

  const handleBack = useCallback(() => {
    setSelectedRunbook(null);
    setExecutionResult(null);
  }, []);

  if (selectedRunbook) {
    return (
      <RunbookRunner
        runbook={selectedRunbook}
        clusterId={clusterId}
        result={executionResult}
        onResultChange={setExecutionResult}
        onBack={handleBack}
      />
    );
  }

  return (
    <RunbookPicker
      clusterId={clusterId}
      onSelect={(rb) => {
        setSelectedRunbook(rb);
        setExecutionResult(null);
      }}
    />
  );
}

// ---------------------------------------------------------------------------
// Picker Mode
// ---------------------------------------------------------------------------

interface RunbookPickerProps {
  clusterId: number;
  onSelect: (rb: RunbookDefinition) => void;
}

function RunbookPicker({ onSelect }: RunbookPickerProps) {
  const { data, isLoading, isError } = useRunbooks();

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
        <span className="ml-3 text-sm text-muted-foreground">Loading runbooks...</span>
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="flex flex-col items-center justify-center py-20 gap-3">
        <AlertCircle className="h-10 w-10 text-destructive" />
        <p className="text-sm text-destructive">Failed to load runbooks</p>
      </div>
    );
  }

  const runbooks = data.runbooks;

  return (
    <div className="space-y-6">
      {/* Intro */}
      <div className="rounded-lg border border-info/20 bg-info/10 p-5 flex items-start gap-4">
        <Stethoscope className="h-6 w-6 mt-0.5 shrink-0 text-info" />
        <div>
          <h3 className="font-semibold text-base mb-1 text-foreground">Automated Diagnostics</h3>
          <p className="text-sm text-muted-foreground">
            Select a runbook to diagnose a common issue. Each runbook runs a sequence of checks against
            your cluster, showing pass/fail per step with actionable guidance.
          </p>
        </div>
      </div>

      {/* Runbook Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {runbooks.map((rb) => {
          const catConfig = CATEGORY_CONFIG[rb.category] || CATEGORY_CONFIG.networking;
          const CatIcon = catConfig.icon;

          return (
            <button
              key={rb.id}
              onClick={() => onSelect(rb)}
              className={cn(
                'rounded-lg border border-border bg-card p-5 text-left transition-all',
                'hover:border-primary/50 hover:shadow-md',
                'focus:outline-none focus:ring-2 focus:ring-primary/50',
              )}
            >
              <div className="flex items-start gap-3 mb-3">
                <div className={cn('rounded-lg p-2', catConfig.bg)}>
                  <CatIcon className={cn('h-5 w-5', catConfig.color)} />
                </div>
                <div className="flex-1 min-w-0">
                  <h4 className="font-semibold text-sm mb-1 text-foreground">{rb.name}</h4>
                  <Badge variant="outline" className="text-xs capitalize">
                    {rb.category}
                  </Badge>
                </div>
              </div>

              <p className="text-xs leading-relaxed mb-3 text-muted-foreground">
                {rb.description}
              </p>

              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">
                  {rb.step_count} diagnostic steps
                </span>
                <div className="flex items-center gap-1.5 text-primary">
                  <Play className="h-3.5 w-3.5" />
                  <span className="text-xs font-medium">Run</span>
                </div>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Runner Mode
// ---------------------------------------------------------------------------

interface RunbookRunnerProps {
  runbook: RunbookDefinition;
  clusterId: number;
  result: RunbookExecutionResult | null;
  onResultChange: (result: RunbookExecutionResult) => void;
  onBack: () => void;
}

function RunbookRunner({ runbook, clusterId, result, onResultChange, onBack }: RunbookRunnerProps) {
  const executeMutation = useExecuteRunbook();

  const isRunning = executeMutation.isPending;

  const handleRun = useCallback(() => {
    executeMutation.mutate(
      { runbookId: runbook.id, clusterId },
      {
        onSuccess: (data) => {
          onResultChange(data);
        },
      },
    );
  }, [executeMutation, runbook.id, clusterId, onResultChange]);

  // Auto-run on first mount if no result yet
  const [hasAutoRun, setHasAutoRun] = useState(false);
  useEffect(() => {
    if (!hasAutoRun && !result && !isRunning) {
      setHasAutoRun(true);
      handleRun();
    }
  }, [hasAutoRun, result, isRunning, handleRun]);

  const catConfig = CATEGORY_CONFIG[runbook.category] || CATEGORY_CONFIG.networking;
  const CatIcon = catConfig.icon;

  // Overall result badge — token-pure
  const overallConfig: Record<string, { color: string; bg: string; border: string; label: string }> = {
    pass: { color: 'text-success', bg: 'bg-success/10', border: 'border-success/30', label: 'All Checks Passed' },
    fail: { color: 'text-destructive', bg: 'bg-destructive/10', border: 'border-destructive/30', label: 'Checks Failed' },
    partial: { color: 'text-warning', bg: 'bg-warning/10', border: 'border-warning/30', label: 'Partial Pass' },
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={onBack} className="h-8 w-8 p-0">
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div className={cn('rounded-lg p-2', catConfig.bg)}>
            <CatIcon className={cn('h-5 w-5', catConfig.color)} />
          </div>
          <div>
            <h3 className="font-semibold text-base text-foreground">{runbook.name}</h3>
            <p className="text-xs text-muted-foreground">{runbook.step_count} diagnostic steps</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {result && (
            <Badge
              className={cn(
                'text-xs',
                overallConfig[result.status]?.bg,
                overallConfig[result.status]?.color,
                overallConfig[result.status]?.border,
              )}
            >
              {overallConfig[result.status]?.label}
            </Badge>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={handleRun}
            disabled={isRunning}
            className="gap-1.5"
          >
            {isRunning ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Running...
              </>
            ) : (
              <>
                <RotateCcw className="h-3.5 w-3.5" />
                Run Again
              </>
            )}
          </Button>
        </div>
      </div>

      {/* Summary Banner */}
      {result && (
        <div className={cn(
          'rounded-lg border p-4',
          overallConfig[result.status]?.bg || 'bg-muted/50',
          overallConfig[result.status]?.border || 'border-border',
        )}>
          <p className={cn('text-sm font-medium', overallConfig[result.status]?.color)}>
            {result.summary}
          </p>
        </div>
      )}

      {/* Steps Stepper */}
      <div className="space-y-1">
        {runbook.steps.map((step, idx) => {
          const stepResult = result?.steps.find((s) => s.step_index === idx);
          // Backend runs all steps and returns the complete result at once.
          // While the mutation is pending: show sequential "running" animation.
          // Once result arrives: show actual pass/fail/skip per step.
          let status: 'running' | 'pass' | 'fail' | 'skip' | 'pending';
          if (stepResult) {
            status = stepResult.status as typeof status;
          } else if (isRunning) {
            // No result yet — animate steps sequentially for visual feedback
            // Show step 0 as running immediately, then each subsequent step
            // after a short stagger (purely visual, not tied to actual progress)
            status = idx === 0 ? 'running' : 'pending';
          } else {
            status = 'pending';
          }

          return (
            <StepCard
              key={idx}
              index={idx}
              name={step.name}
              description={step.description}
              status={status}
              result={stepResult || null}
              isLast={idx === runbook.steps.length - 1}
              clusterId={clusterId}
            />
          );
        })}
      </div>

      {/* Back button at bottom */}
      <div className="flex justify-start pt-2">
        <Button variant="outline" size="sm" onClick={onBack} className="gap-1.5">
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to Runbooks
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step Card
// ---------------------------------------------------------------------------

interface StepCardProps {
  index: number;
  name: string;
  description: string;
  status: 'running' | 'pass' | 'fail' | 'skip' | 'pending';
  result: RunbookStepResult | null;
  isLast: boolean;
  clusterId: number;
}

function StepCard({
  index,
  name,
  description,
  status,
  result,
  isLast,
}: StepCardProps) {
  const [expanded, setExpanded] = useState(false);

  // Auto-expand failed steps
  const shouldAutoExpand = status === 'fail' && result?.details;

  const StatusIcon = STATUS_ICONS[status];
  const statusColor = STATUS_COLORS[status];

  const dotClass =
    status === 'pass' ? 'border-success/30 bg-success/10' :
    status === 'fail' ? 'border-destructive/30 bg-destructive/10' :
    status === 'running' ? 'border-primary/30 bg-primary/10' :
    status === 'skip' ? 'border-muted-foreground/30 bg-muted' :
    'border-border bg-muted';

  return (
    <div className="flex gap-3">
      {/* Vertical line + icon */}
      <div className="flex flex-col items-center">
        <div className={cn('rounded-full p-1 border', dotClass)}>
          <StatusIcon className={cn('h-4 w-4', statusColor)} />
        </div>
        {!isLast && (
          <div className="w-px flex-1 min-h-[16px] bg-border" />
        )}
      </div>

      {/* Content */}
      <div className={cn('flex-1 pb-4', isLast && 'pb-0')}>
        <div
          className={cn(
            'rounded-lg border border-border bg-card p-4 transition-colors',
            (status === 'fail' || (shouldAutoExpand && !expanded)) && 'cursor-pointer',
          )}
          onClick={() => {
            if (result?.details) setExpanded(!expanded);
          }}
        >
          {/* Header row */}
          <div className="flex items-start justify-between gap-2">
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <span className="text-xs font-mono text-muted-foreground">
                  {String(index + 1).padStart(2, '0')}
                </span>
                <h4 className="text-sm font-medium text-foreground">{name}</h4>
              </div>
              <p className="text-xs mt-0.5 text-muted-foreground">
                {description}
              </p>
            </div>

            {/* Status badge */}
            {status !== 'pending' && (
              <Badge
                variant="outline"
                className={cn(
                  'text-xs shrink-0 capitalize',
                  status === 'pass' && 'border-success/30 text-success',
                  status === 'fail' && 'border-destructive/30 text-destructive',
                  status === 'running' && 'border-primary/30 text-primary',
                  status === 'skip' && 'border-border text-muted-foreground',
                )}
              >
                {status}
              </Badge>
            )}
          </div>

          {/* Result message */}
          {result && (
            <div className={cn(
              'mt-2 text-xs rounded-md px-3 py-2',
              status === 'pass' ? 'bg-success/10 text-success' :
              status === 'fail' ? 'bg-destructive/10 text-destructive' :
              'bg-muted text-muted-foreground',
            )}>
              {result.message}
            </div>
          )}

          {/* Resource link */}
          {result?.resource_link && status === 'fail' && (
            <ResourceLinkBadge link={result.resource_link} />
          )}

          {/* Expandable details */}
          {result?.details && (
            <>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setExpanded(!expanded);
                }}
                className="flex items-center gap-1 mt-2 text-xs text-muted-foreground hover:text-foreground"
              >
                {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                {expanded ? 'Hide details' : 'Show details'}
              </button>

              {(expanded || shouldAutoExpand) && (
                <StepDetails details={result.details} />
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Resource Link Badge
// ---------------------------------------------------------------------------

function ResourceLinkBadge({ link }: { link: RunbookResourceLink }) {
  return (
    <div className="mt-2">
      <span className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-md bg-muted text-primary">
        <ExternalLink className="h-3 w-3" />
        {link.type}: {link.namespace}/{link.name}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step Details — renders the details object in a readable format
// ---------------------------------------------------------------------------

function StepDetails({ details }: { details: Record<string, unknown> }) {
  // Show issues array prominently if present
  const issues = details.issues as string[] | undefined;
  const events = details.events as Array<Record<string, unknown>> | undefined;

  return (
    <SectionCard
      title="Details"
      compact
      className="mt-3 bg-muted/50"
    >
      <div className="space-y-2">
        {/* Issues list */}
        {issues && issues.length > 0 && (
          <div>
            <span className="text-xs font-medium text-muted-foreground">
              Issues:
            </span>
            <ul className="mt-1 space-y-0.5">
              {issues.map((issue, i) => (
                <li key={i} className="flex items-start gap-1.5 text-xs">
                  <XCircle className="h-3 w-3 text-destructive mt-0.5 shrink-0" />
                  <span className="text-muted-foreground">{issue}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Events list */}
        {events && events.length > 0 && (
          <div>
            <span className="text-xs font-medium text-muted-foreground">
              Events ({events.length}):
            </span>
            <div className="mt-1 space-y-1">
              {events.slice(0, 5).map((ev, i) => (
                <div
                  key={i}
                  className="flex items-start gap-2 text-xs rounded px-2 py-1 bg-card"
                >
                  <Badge variant="outline" className="text-[10px] shrink-0">
                    {String(ev.reason || 'Unknown')}
                  </Badge>
                  <span className="truncate text-muted-foreground">
                    {String(ev.message || ev.object || '')}
                  </span>
                </div>
              ))}
              {events.length > 5 && (
                <p className="text-xs text-muted-foreground">
                  ...and {events.length - 5} more
                </p>
              )}
            </div>
          </div>
        )}

        {/* Generic key-value display for other details */}
        {Object.entries(details)
          .filter(([key]) => !['issues', 'events'].includes(key))
          .map(([key, value]) => {
            // Skip complex nested objects for display
            if (typeof value === 'object' && value !== null && !Array.isArray(value)) return null;
            // Skip arrays (already handled above or too complex)
            if (Array.isArray(value) && value.length > 0 && typeof value[0] === 'object') return null;

            return (
              <div key={key} className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">
                  {key.replace(/_/g, ' ')}
                </span>
                <span className="font-mono text-foreground">
                  {Array.isArray(value) ? value.join(', ') : String(value ?? '-')}
                </span>
              </div>
            );
          })}
      </div>
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Status icon/color maps — token-pure
// ---------------------------------------------------------------------------

const STATUS_ICONS: Record<string, typeof CheckCircle2> = {
  pass: CheckCircle2,
  fail: XCircle,
  skip: MinusCircle,
  running: Loader2,
  pending: MinusCircle,
};

const STATUS_COLORS: Record<string, string> = {
  pass: 'text-success',
  fail: 'text-destructive',
  skip: 'text-muted-foreground',
  running: 'text-primary animate-spin',
  pending: 'text-muted-foreground',
};
