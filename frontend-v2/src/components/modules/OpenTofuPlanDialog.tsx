import { useState, useEffect } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
  CheckCircle2,
  XCircle,
  AlertTriangle,
  FileText,
  Play,
  RefreshCw,
  Loader2
} from 'lucide-react';
import { usePlanModule, useApplyModule } from '@/hooks/useModules';
import { apiClient } from '@/lib/api';
import type { ProjectModule } from '@/types';
import { logger } from '@/lib/logger';

interface OpenTofuPlanDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  module: ProjectModule;
}

interface PlanSummary {
  to_add: number;
  to_change: number;
  to_destroy: number;
  plan_output?: string;
  error?: string;
}

export function OpenTofuPlanDialog({
  open,
  onOpenChange,
  module,
}: OpenTofuPlanDialogProps) {
  const [planData, setPlanData] = useState<PlanSummary | null>(null);
  const [isLoadingPlan, setIsLoadingPlan] = useState(false);
  const [pollInterval, setPollInterval] = useState<ReturnType<typeof setInterval> | null>(null);
  const [progressLogs, setProgressLogs] = useState<string[]>([]);
  const [elapsedTime, setElapsedTime] = useState(0);

  const planMutation = usePlanModule();
  const applyMutation = useApplyModule();

  // Cleanup polling interval on unmount or dialog close
  useEffect(() => {
    return () => {
      if (pollInterval) {
        clearInterval(pollInterval);
        setPollInterval(null);
      }
    };
  }, [pollInterval]);

  useEffect(() => {
    if (open && !planData) {
      handleRunPlan();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- handleRunPlan and planData intentionally omitted; we only want to trigger on dialog open
  }, [open]);

  const parsePlanOutput = (output: string): PlanSummary => {
    // Parse OpenTofu plan output to extract resource counts
    const addMatch = output.match(/Plan: (\d+) to add/);
    const changeMatch = output.match(/(\d+) to change/);
    const destroyMatch = output.match(/(\d+) to destroy/);

    return {
      to_add: addMatch ? parseInt(addMatch[1]) : 0,
      to_change: changeMatch ? parseInt(changeMatch[1]) : 0,
      to_destroy: destroyMatch ? parseInt(destroyMatch[1]) : 0,
      plan_output: output,
    };
  };

  const handleRunPlan = async () => {
    setIsLoadingPlan(true);
    setPlanData(null); // Clear previous plan data
    setProgressLogs([]); // Clear previous progress
    setElapsedTime(0); // Reset timer
    try {
      // Run the plan mutation - this queues the task
      const result = await planMutation.mutateAsync(module.id);

      if (!result.success || !result.task_id) {
        setIsLoadingPlan(false);
        setPlanData({
          to_add: 0,
          to_change: 0,
          to_destroy: 0,
          error: result.message || 'Failed to queue plan task',
        });
        return;
      }

      // Poll for task completion
      const taskId = result.task_id;
      const startTime = Date.now();

      const interval = setInterval(async () => {
        try {
          // Update elapsed time
          setElapsedTime(Math.floor((Date.now() - startTime) / 1000));

          const response = await apiClient.get(`/api/tasks/${taskId}`);
          const task = response.data;

          // Update progress logs if available - show more lines for better visibility
          if (task.logs) {
            const lines = task.logs.split('\n').filter((line: string) => line.trim());
            const lastFewLines = lines.slice(-10); // Show last 10 lines (was 3)
            setProgressLogs(lastFewLines);
          } else if (task.status === 'in_progress') {
            // Show simulated progress messages based on elapsed time when no logs yet
            const elapsed = Math.floor((Date.now() - startTime) / 1000);
            const simulatedProgress = [];

            if (elapsed > 5) simulatedProgress.push('⏳ Initializing OpenTofu workspace...');
            if (elapsed > 15) simulatedProgress.push('📥 Downloading provider plugins (aws, etc.)...');
            if (elapsed > 30) simulatedProgress.push('🔍 Analyzing infrastructure configuration...');
            if (elapsed > 45) simulatedProgress.push('📊 Calculating resource changes...');
            if (elapsed > 60) simulatedProgress.push('⚙️  Generating execution plan...');

            if (simulatedProgress.length > 0) {
              setProgressLogs(simulatedProgress);
            }
          }

          if (task.status === 'completed') {
            clearInterval(interval);
            setPollInterval(null);
            setIsLoadingPlan(false);
            setProgressLogs([]);

            if (task.exit_code === 0 && task.logs) {
              // Parse successful plan output
              const parsed = parsePlanOutput(task.logs);
              setPlanData(parsed);
            } else {
              // Plan failed
              setPlanData({
                to_add: 0,
                to_change: 0,
                to_destroy: 0,
                error: task.error || task.logs || 'Plan failed. Check the logs for details.',
              });
            }
          } else if (task.status === 'failed') {
            clearInterval(interval);
            setPollInterval(null);
            setIsLoadingPlan(false);
            setProgressLogs([]);
            setPlanData({
              to_add: 0,
              to_change: 0,
              to_destroy: 0,
              error: task.error || 'Plan task failed',
            });
          }
        } catch (pollError) {
          logger.error('Error polling task:', pollError);
        }
      }, 1000); // Poll every 1 second for better responsiveness

      setPollInterval(interval);
    } catch (error: unknown) {
      setIsLoadingPlan(false);
      const message = error instanceof Error ? error.message : 'Failed to start plan. Check the logs for details.';
      setPlanData({
        to_add: 0,
        to_change: 0,
        to_destroy: 0,
        error: message,
      });
    }
  };


  const handleApply = async () => {
    await applyMutation.mutateAsync({ moduleId: module.id, autoApprove: true });
    onOpenChange(false);
  };

  const handleClose = () => {
    // Clear any active polling interval
    if (pollInterval) {
      clearInterval(pollInterval);
      setPollInterval(null);
    }
    setPlanData(null);
    setIsLoadingPlan(false);
    setProgressLogs([]);
    onOpenChange(false);
  };

  const hasDangerousChanges = planData && planData.to_destroy > 0;
  const hasChanges = planData && (planData.to_add > 0 || planData.to_change > 0 || planData.to_destroy > 0);

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="max-w-4xl max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FileText className="h-5 w-5" />
            OpenTofu Plan Preview
          </DialogTitle>
          <DialogDescription>
            Review the changes OpenTofu will make to your infrastructure
          </DialogDescription>
        </DialogHeader>

        {isLoadingPlan ? (
          <div className="flex-1 flex flex-col items-center justify-center py-8 space-y-6">
            <div className="text-center space-y-4">
              <Loader2 className="h-12 w-12 animate-spin mx-auto text-primary" />
              <div>
                <p className="font-medium text-lg">Generating plan...</p>
                <p className="text-sm text-muted-foreground">
                  Elapsed: {elapsedTime}s
                  {progressLogs.length === 0 && ' • Initializing...'}
                </p>
              </div>
            </div>
            <div className="w-full max-w-3xl">
              <div className="rounded-lg border bg-muted/30 p-4">
                <div className="flex items-center justify-between mb-3">
                  <p className="text-sm font-medium flex items-center gap-2">
                    <RefreshCw className="h-4 w-4 animate-spin" />
                    Live Output
                  </p>
                  {progressLogs.length > 0 && (
                    <Badge variant="secondary" className="text-xs">
                      {progressLogs.length} lines
                    </Badge>
                  )}
                </div>
                {progressLogs.length > 0 ? (
                  <ScrollArea className="h-[200px]">
                    <div className="space-y-0.5 font-mono text-xs pr-4">
                      {progressLogs.map((log, index) => (
                        <div
                          key={index}
                          className="text-foreground/70 leading-relaxed"
                          style={{ wordBreak: 'break-all' }}
                        >
                          {log}
                        </div>
                      ))}
                    </div>
                  </ScrollArea>
                ) : (
                  <div className="h-[200px] flex items-center justify-center text-sm text-muted-foreground">
                    <div className="text-center space-y-2">
                      <Loader2 className="h-6 w-6 animate-spin mx-auto opacity-50" />
                      <p>Waiting for output...</p>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        ) : planData?.error ? (
          <div className="flex-1 py-4">
            <Alert variant="destructive">
              <XCircle className="h-4 w-4" />
              <AlertDescription>{planData.error}</AlertDescription>
            </Alert>
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="outline" onClick={handleClose}>
                Close
              </Button>
              <Button onClick={handleRunPlan}>
                <RefreshCw className="h-4 w-4 mr-2" />
                Retry
              </Button>
            </div>
          </div>
        ) : planData ? (
          <>
            {/* Summary Cards */}
            <div className="grid grid-cols-3 gap-4">
              <div className="border rounded-lg p-4">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium text-muted-foreground">To Add</p>
                    <p className="text-2xl font-bold text-success">{planData.to_add}</p>
                  </div>
                  <CheckCircle2 className="h-8 w-8 text-success" />
                </div>
              </div>

              <div className="border rounded-lg p-4">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium text-muted-foreground">To Change</p>
                    <p className="text-2xl font-bold text-warning">{planData.to_change}</p>
                  </div>
                  <AlertTriangle className="h-8 w-8 text-warning" />
                </div>
              </div>

              <div className="border rounded-lg p-4">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium text-muted-foreground">To Destroy</p>
                    <p className="text-2xl font-bold text-destructive">{planData.to_destroy}</p>
                  </div>
                  <XCircle className="h-8 w-8 text-destructive" />
                </div>
              </div>
            </div>

            {/* Warnings */}
            {hasDangerousChanges && (
              <Alert variant="destructive">
                <AlertTriangle className="h-4 w-4" />
                <AlertDescription>
                  This plan will destroy {planData.to_destroy} resource{planData.to_destroy !== 1 ? 's' : ''}.
                  Please review carefully before applying.
                </AlertDescription>
              </Alert>
            )}

            {!hasChanges && (
              <Alert>
                <CheckCircle2 className="h-4 w-4" />
                <AlertDescription>
                  No changes detected. Your infrastructure is up to date.
                </AlertDescription>
              </Alert>
            )}

            {/* Plan Output */}
            <Tabs defaultValue="formatted" className="flex-1 flex flex-col min-h-0">
              <TabsList>
                <TabsTrigger value="formatted">Formatted</TabsTrigger>
                <TabsTrigger value="raw">Raw Output</TabsTrigger>
              </TabsList>

              <TabsContent value="formatted" className="flex-1 mt-4 min-h-0">
                <ScrollArea className="h-[300px] w-full border rounded-lg p-4">
                  <pre className="text-sm font-mono whitespace-pre-wrap">
                    {planData.plan_output}
                  </pre>
                </ScrollArea>
              </TabsContent>

              <TabsContent value="raw" className="flex-1 mt-4 min-h-0">
                <ScrollArea className="h-[300px] w-full border border-border rounded-lg p-4 bg-muted text-foreground">
                  <pre className="text-sm font-mono whitespace-pre-wrap">
                    {planData.plan_output}
                  </pre>
                </ScrollArea>
              </TabsContent>
            </Tabs>
          </>
        ) : null}

        <DialogFooter className="flex items-center justify-between">
          <div>
            {planData && hasChanges && (
              <Badge variant="outline" className="text-xs">
                Plan generated for {module.library_module?.name || 'module'}
              </Badge>
            )}
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={handleClose}>
              Cancel
            </Button>
            {planData && !planData.error && (
              <>
                <Button variant="outline" onClick={handleRunPlan} disabled={isLoadingPlan}>
                  <RefreshCw className="h-4 w-4 mr-2" />
                  Refresh Plan
                </Button>
                {hasChanges && (
                  <Button
                    onClick={handleApply}
                    disabled={applyMutation.isPending}
                    variant={hasDangerousChanges ? 'destructive' : 'default'}
                  >
                    <Play className="h-4 w-4 mr-2" />
                    {applyMutation.isPending ? 'Applying...' : 'Apply Changes'}
                  </Button>
                )}
              </>
            )}
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
