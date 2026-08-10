/**
 * ParallelExecutionProgress Component
 *
 * Real-time progress tracking for parallel execution.
 * Shows current layer, module statuses, and overall progress.
 *
 * D-001 Phase 3 S3a: now driven by the new /orchestration/{run_handle} endpoint
 * (RunProgress shape) instead of the old int-keyed parallel-executions route.
 */

import { SectionCard } from '@/components/ui/section-card';
import { Progress } from '@/components/ui/progress';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { useRunProgress } from '@/hooks/useParallelExecution';
import { LayerCard } from './LayerCard';
import { Loader2, CheckCircle2, XCircle, Clock, Layers, AlertTriangle } from 'lucide-react';

interface ParallelExecutionProgressProps {
  projectId: number;
  /** String run handle returned by deploy-all / destroy-all as orchestrator_task_id */
  runHandle: string;
  action: 'deploy' | 'destroy';
}

export function ParallelExecutionProgress({
  projectId,
  runHandle,
  action,
}: ParallelExecutionProgressProps) {
  const { data: status, isLoading } = useRunProgress(projectId, runHandle, {
    refetchInterval: 2000, // Poll every 2 seconds
  });

  if (isLoading || !status) {
    return (
      <SectionCard>
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
          Loading Execution Status...
        </div>
      </SectionCard>
    );
  }

  const getStatusBadgeVariant = () => {
    switch (status.status) {
      case 'completed':
        return 'success' as const;
      case 'failed':
        return 'destructive' as const;
      case 'in_progress':
        return 'secondary' as const;
      default:
        return 'outline' as const;
    }
  };

  const formatDuration = (seconds?: number) => {
    if (!seconds) return '0s';
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
  };

  const successCount = status.successful_modules?.length || 0;
  const failedCount = status.failed_modules?.length || 0;

  return (
    <div className="space-y-4" role="status" aria-live="polite" aria-label={`${action === 'deploy' ? 'Deployment' : 'Destruction'} ${status.status}: layer ${status.current_layer + 1} of ${status.total_layers}`}>
      {/* Overall Status */}
      <SectionCard title={`${action === 'deploy' ? 'Parallel Deployment' : 'Parallel Destruction'} Progress`}>
        <div className="flex items-center justify-end mb-4">
          <Badge variant={getStatusBadgeVariant()}>
            {status.status.toUpperCase()}
          </Badge>
        </div>
        <div className="space-y-4">
          {/* Progress Bar */}
          <div className="space-y-2">
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">
                Layer {status.current_layer + 1} of {status.total_layers}
              </span>
              <span className="font-medium text-foreground">
                {Math.round(status.progress_percent)}%
              </span>
            </div>
            <Progress value={status.progress_percent} />
          </div>

          {/* Stats */}
          <div className="grid grid-cols-3 gap-4 pt-2">
            <div className="space-y-1">
              <p className="text-xs text-muted-foreground">Successful</p>
              <p className="text-2xl font-bold text-success">{successCount}</p>
            </div>
            <div className="space-y-1">
              <p className="text-xs text-muted-foreground">Failed</p>
              <p className="text-2xl font-bold text-destructive">{failedCount}</p>
            </div>
            <div className="space-y-1">
              <p className="text-xs text-muted-foreground">Duration</p>
              <p className="text-2xl font-bold text-foreground">
                {formatDuration(status.duration_seconds ?? undefined)}
              </p>
            </div>
          </div>

          {/* Timing */}
          {status.started_at && (
            <div className="flex items-center justify-between text-sm text-muted-foreground pt-2 border-t border-border">
              <div className="flex items-center gap-1">
                <Clock className="h-4 w-4" />
                <span>Started: {new Date(status.started_at).toLocaleTimeString()}</span>
              </div>
            </div>
          )}
        </div>
      </SectionCard>

      {/* Error Message */}
      {status.error_message && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>{status.error_message}</AlertDescription>
        </Alert>
      )}

      {/* Current Layer Details */}
      {status.layers && status.layers.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
            <Layers className="h-4 w-4" />
            Execution Layers
          </h3>
          <div className="space-y-3 max-h-[500px] overflow-y-auto">
            {status.layers.map((layer) => (
              <LayerCard key={layer.layer_index} layer={layer} showStatus={true} />
            ))}
          </div>
        </div>
      )}

      {/* Completion Message */}
      {status.status === 'completed' && (
        <Alert>
          <CheckCircle2 className="h-4 w-4 text-success" />
          <AlertDescription>
            <strong>Success!</strong> All modules {action === 'deploy' ? 'deployed' : 'destroyed'} successfully.
            {status.completed_at && (
              <span className="ml-1">
                Completed at {new Date(status.completed_at).toLocaleTimeString()}.
              </span>
            )}
          </AlertDescription>
        </Alert>
      )}

      {status.status === 'failed' && (
        <Alert variant="destructive">
          <XCircle className="h-4 w-4" />
          <AlertDescription>
            <strong>Failed.</strong> {failedCount} module(s) failed during {action}.
            Check module logs for details.
          </AlertDescription>
        </Alert>
      )}
    </div>
  );
}
