/**
 * ParallelExecutionDialog Component
 *
 * Shows execution plan with time comparison and allows user to start parallel deployment.
 * Displays layer grouping and estimated time savings.
 */

import { useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Loader2, Zap, Clock, TrendingDown, AlertTriangle } from 'lucide-react';
import { useExecutionPlan, useDeployAllModules, useDestroyAllModules } from '@/hooks/useParallelExecution';
import { LayerCard } from './LayerCard';
import { notify } from '@/lib/notify';

interface ParallelExecutionDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: number;
  action: 'deploy' | 'destroy';
  onExecutionStarted?: (executionId: string) => void;
}

export function ParallelExecutionDialog({
  open,
  onOpenChange,
  projectId,
  action,
  onExecutionStarted,
}: ParallelExecutionDialogProps) {
  const [isStarting, setIsStarting] = useState(false);

  const { data: plan, isLoading, error } = useExecutionPlan(projectId, open);
  const deployMutation = useDeployAllModules();
  const destroyMutation = useDestroyAllModules();

  const handleStart = async () => {
    try {
      setIsStarting(true);

      const mutation = action === 'deploy' ? deployMutation : destroyMutation;
      const result = await mutation.mutateAsync({ projectId, parallel: true });

      notify.success(
        `${action === 'deploy' ? 'Deployment' : 'Destruction'} Started`,
        `Executing ${result.total_modules} modules across ${result.total_layers} layers`,
        { category: 'deployment' },
      );

      if (onExecutionStarted && result.orchestrator_task_id) {
        onExecutionStarted(result.orchestrator_task_id);
      }

      onOpenChange(false);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'An error occurred';
      notify.error(
        'Failed to Start',
        message
      );
    } finally {
      setIsStarting(false);
    }
  };

  if (isLoading) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        </DialogContent>
      </Dialog>
    );
  }

  if (error || plan?.error) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-4xl">
          <DialogHeader>
            <DialogTitle>Cannot Execute</DialogTitle>
          </DialogHeader>
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertDescription>
              {error?.message || plan?.error || 'Failed to generate execution plan'}
            </AlertDescription>
          </Alert>
          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    );
  }

  if (!plan || plan.total_modules === 0) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-4xl">
          <DialogHeader>
            <DialogTitle>No Modules Found</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            There are no modules to {action} in this project.
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    );
  }

  const actionTitle = action === 'deploy' ? 'Deploy All Modules' : 'Destroy All Modules';

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{actionTitle}</DialogTitle>
          <DialogDescription>
            Parallel execution will {action} modules layer by layer with concurrency within each layer
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-6">
          {/* Time Comparison */}
          <div className="grid grid-cols-3 gap-4">
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-1">
                  <Clock className="h-4 w-4" />
                  Sequential
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-bold text-foreground">
                  {plan.total_estimated_time_sequential} min
                </p>
                <p className="text-xs text-muted-foreground mt-1">One at a time</p>
              </CardContent>
            </Card>

            <Card className="border-success/40 bg-success/5">
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium text-success flex items-center gap-1">
                  <Zap className="h-4 w-4" />
                  Parallel
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-bold text-success">
                  {plan.total_estimated_time_parallel} min
                </p>
                <p className="text-xs text-success mt-1">Recommended</p>
              </CardContent>
            </Card>

            <Card className="border-primary/40 bg-primary/5">
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium text-primary flex items-center gap-1">
                  <TrendingDown className="h-4 w-4" />
                  Time Savings
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-bold text-primary">
                  {plan.time_savings_percent}%
                </p>
                <p className="text-xs text-primary mt-1">
                  {plan.time_savings_minutes} min faster
                </p>
              </CardContent>
            </Card>
          </div>

          {/* Execution Details */}
          <div className="flex items-center justify-between p-3 bg-muted/50 rounded-md">
            <div className="flex items-center gap-4 text-sm">
              <span>
                <strong>{plan.total_modules}</strong> modules
              </span>
              <span>
                <strong>{plan.total_layers}</strong> layers
              </span>
              <span>
                <strong>{plan.parallelization_factor}x</strong> parallelization
              </span>
            </div>
          </div>

          {/* Execution Plan Layers */}
          <div>
            <h3 className="text-sm font-semibold text-foreground mb-3">Execution Plan</h3>
            <div className="space-y-3 max-h-[400px] overflow-y-auto">
              {plan.layers.map((layer) => (
                <LayerCard key={layer.layer_index} layer={layer} />
              ))}
            </div>
          </div>

          {action === 'destroy' && (
            <Alert variant="destructive">
              <AlertTriangle className="h-4 w-4" />
              <AlertDescription>
                <strong>Warning:</strong> This will destroy all infrastructure in reverse dependency order.
                This action cannot be undone.
              </AlertDescription>
            </Alert>
          )}
        </div>

        <DialogFooter className="gap-2">
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={isStarting}
          >
            Cancel
          </Button>
          <Button
            onClick={handleStart}
            disabled={isStarting}
            variant={action === 'destroy' ? 'destructive' : 'default'}
          >
            {isStarting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {action === 'deploy' ? 'Start Deployment' : 'Start Destruction'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
