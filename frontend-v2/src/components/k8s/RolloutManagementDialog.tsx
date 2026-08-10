import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
  History,
  Activity,
  Settings,
  RotateCcw,
  AlertCircle,
  CheckCircle2,
  Clock,
  Loader2,
  Pause,
  Play,
} from 'lucide-react';
import type { K8sCondition, K8sRolloutHistoryEntry } from '@/types';
import { notify } from '@/lib/notify';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

interface RolloutManagementDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clusterId: number;
  namespace: string;
  deploymentName: string;
}

export function RolloutManagementDialog({
  open,
  onOpenChange,
  clusterId,
  namespace,
  deploymentName,
}: RolloutManagementDialogProps) {
  const queryClient = useQueryClient();
  const [selectedTab, setSelectedTab] = useState('status');

  // Fetch rollout history
  const { data: historyData, isLoading: historyLoading } = useQuery({
    queryKey: ['rollout-history', clusterId, deploymentName, namespace],
    queryFn: () => api.getRolloutHistory(clusterId, deploymentName, namespace),
    enabled: open,
  });

  // Fetch rollout status
  const { data: statusData, isLoading: statusLoading } = useQuery({
    queryKey: ['rollout-status', clusterId, deploymentName, namespace],
    queryFn: () => api.getRolloutStatus(clusterId, deploymentName, namespace),
    enabled: open,
    refetchInterval: 5000, // Refresh every 5 seconds
  });

  // Rollout restart mutation
  const restartMutation = useAppMutation({
    mutationFn: () => api.rolloutRestart(clusterId, deploymentName, namespace),
    onSuccess: () => {
      notify.success('Deployment restarted successfully', undefined, { category: 'cluster' });
      queryClient.invalidateQueries({ queryKey: ['rollout-status', clusterId, deploymentName, namespace] });
      queryClient.invalidateQueries({ queryKey: ['rollout-history', clusterId, deploymentName, namespace] });
    },
    onError: (error: unknown) => {
      const message = error instanceof Error ? error.message : 'Unknown error';
      notify.error(`Failed to restart deployment: ${message}`, undefined, { category: 'cluster' });
    },
  });

  // Rollout undo mutation
  const undoMutation = useAppMutation({
    mutationFn: (revision?: number) => api.rolloutUndo(clusterId, deploymentName, namespace, revision),
    onSuccess: () => {
      notify.success('Deployment rolled back successfully', undefined, { category: 'cluster' });
      queryClient.invalidateQueries({ queryKey: ['rollout-status', clusterId, deploymentName, namespace] });
      queryClient.invalidateQueries({ queryKey: ['rollout-history', clusterId, deploymentName, namespace] });
    },
    onError: (error: unknown) => {
      const message = error instanceof Error ? error.message : 'Unknown error';
      notify.error(`Failed to rollback deployment: ${message}`, undefined, { category: 'cluster' });
    },
  });

  // Rollout pause mutation
  const pauseMutation = useAppMutation({
    mutationFn: () => api.rolloutPause(clusterId, deploymentName, namespace),
    onSuccess: (data) => {
      if (data.success) {
        notify.success('Deployment rollout paused', undefined, { category: 'cluster' });
      } else {
        notify.info(data.message, undefined, { category: 'cluster' });
      }
      queryClient.invalidateQueries({ queryKey: ['rollout-status', clusterId, deploymentName, namespace] });
    },
    onError: (error: unknown) => {
      const message = error instanceof Error ? error.message : 'Unknown error';
      notify.error(`Failed to pause deployment: ${message}`, undefined, { category: 'cluster' });
    },
  });

  // Rollout resume mutation
  const resumeMutation = useAppMutation({
    mutationFn: () => api.rolloutResume(clusterId, deploymentName, namespace),
    onSuccess: (data) => {
      if (data.success) {
        notify.success('Deployment rollout resumed', undefined, { category: 'cluster' });
      } else {
        notify.info(data.message, undefined, { category: 'cluster' });
      }
      queryClient.invalidateQueries({ queryKey: ['rollout-status', clusterId, deploymentName, namespace] });
      queryClient.invalidateQueries({ queryKey: ['rollout-history', clusterId, deploymentName, namespace] });
    },
    onError: (error: unknown) => {
      const message = error instanceof Error ? error.message : 'Unknown error';
      notify.error(`Failed to resume deployment: ${message}`, undefined, { category: 'cluster' });
    },
  });

  const handlePause = () => {
    if (confirm('Are you sure you want to pause this rollout? No new pods will be created until resumed.')) {
      pauseMutation.mutate();
    }
  };

  const handleResume = () => {
    if (confirm('Resume this rollout? The deployment controller will continue progressing the rollout.')) {
      resumeMutation.mutate();
    }
  };

  const handleRestart = () => {
    if (confirm('Are you sure you want to restart this deployment? This will trigger a rolling update of all pods.')) {
      restartMutation.mutate();
    }
  };

  const handleUndo = (revision?: number) => {
    const message = revision
      ? `Are you sure you want to rollback to revision ${revision}?`
      : 'Are you sure you want to rollback to the previous revision?';
    if (confirm(message)) {
      undoMutation.mutate(revision);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl max-h-[80vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Activity className="h-5 w-5" />
            Rollout Management: {deploymentName}
          </DialogTitle>
          <DialogDescription>
            Manage deployment rollouts, view history, and perform rollback operations
          </DialogDescription>
        </DialogHeader>

        <Tabs value={selectedTab} onValueChange={setSelectedTab} className="flex-1 flex flex-col">
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="status" className="flex items-center gap-2">
              <Activity className="h-4 w-4" />
              Status
            </TabsTrigger>
            <TabsTrigger value="history" className="flex items-center gap-2">
              <History className="h-4 w-4" />
              History
            </TabsTrigger>
            <TabsTrigger value="actions" className="flex items-center gap-2">
              <Settings className="h-4 w-4" />
              Actions
            </TabsTrigger>
          </TabsList>

          <TabsContent value="status" className="flex-1 overflow-auto space-y-4">
            {statusLoading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-6 w-6 animate-spin" />
              </div>
            ) : statusData ? (
              <>
                <Alert>
                  <CheckCircle2 className="h-4 w-4" />
                  <AlertDescription>
                    {statusData.message || 'Deployment is up to date'}
                  </AlertDescription>
                </Alert>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <div className="text-sm font-medium">Replicas</div>
                    <div className="flex items-center gap-4">
                      <Badge variant="outline">Desired: {statusData.desired_replicas ?? statusData.replicas ?? 0}</Badge>
                      <Badge variant="outline">Current: {statusData.current_replicas ?? 0}</Badge>
                      <Badge variant="outline">Ready: {statusData.ready_replicas ?? 0}</Badge>
                      <Badge variant="outline">Updated: {statusData.updated_replicas ?? 0}</Badge>
                    </div>
                  </div>

                  <div className="space-y-2">
                    <div className="text-sm font-medium">Current Revision</div>
                    <Badge>{statusData.current_revision || 'N/A'}</Badge>
                  </div>
                </div>

                {statusData.conditions && statusData.conditions.length > 0 && (
                  <div className="space-y-2">
                    <div className="text-sm font-medium">Conditions</div>
                    <div className="space-y-2">
                      {statusData.conditions.map((condition: K8sCondition, idx: number) => (
                        <div key={idx} className="flex items-center gap-2 text-sm">
                          {condition.status === 'True' ? (
                            <CheckCircle2 className="h-4 w-4 text-success" />
                          ) : (
                            <AlertCircle className="h-4 w-4 text-warning" />
                          )}
                          <span className="font-medium">{condition.type}:</span>
                          <span>{condition.message || condition.reason || 'OK'}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            ) : (
              <Alert>
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>No status information available</AlertDescription>
              </Alert>
            )}
          </TabsContent>

          <TabsContent value="history" className="flex-1 overflow-auto">
            {historyLoading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-6 w-6 animate-spin" />
              </div>
            ) : historyData?.history && historyData.history.length > 0 ? (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Revision</TableHead>
                    <TableHead>Change Cause</TableHead>
                    <TableHead>Created</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {historyData.history.map((revision: K8sRolloutHistoryEntry) => (
                    <TableRow key={revision.revision}>
                      <TableCell className="font-mono">{revision.revision}</TableCell>
                      <TableCell className="text-sm">{revision.change_cause || 'No change cause recorded'}</TableCell>
                      <TableCell className="text-sm flex items-center gap-2">
                        <Clock className="h-3 w-3" />
                        {revision.created_at || 'Unknown'}
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => handleUndo(revision.revision)}
                          disabled={undoMutation.isPending}
                        >
                          <RotateCcw className="h-3 w-3 mr-1" />
                          Rollback
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : (
              <Alert>
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>No rollout history available</AlertDescription>
              </Alert>
            )}
          </TabsContent>

          <TabsContent value="actions" className="flex-1 overflow-auto space-y-4">
            <div className="grid gap-4">
              <div className="border rounded-lg p-4 space-y-3">
                <div className="flex items-center gap-2">
                  <RotateCcw className="h-5 w-5" />
                  <div>
                    <div className="font-medium">Restart Deployment</div>
                    <div className="text-sm text-muted-foreground">
                      Trigger a rolling restart of all pods in this deployment
                    </div>
                  </div>
                </div>
                <Button
                  onClick={handleRestart}
                  disabled={restartMutation.isPending}
                  className="w-full"
                >
                  {restartMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                  Restart Deployment
                </Button>
              </div>

              <div className="border rounded-lg p-4 space-y-3">
                <div className="flex items-center gap-2">
                  <History className="h-5 w-5" />
                  <div>
                    <div className="font-medium">Rollback to Previous</div>
                    <div className="text-sm text-muted-foreground">
                      Rollback to the previous revision
                    </div>
                  </div>
                </div>
                <Button
                  onClick={() => handleUndo()}
                  disabled={undoMutation.isPending}
                  variant="outline"
                  className="w-full"
                >
                  {undoMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                  Rollback to Previous Revision
                </Button>
              </div>

              <div className="border rounded-lg p-4 space-y-3">
                <div className="flex items-center gap-2">
                  <Pause className="h-5 w-5" />
                  <div>
                    <div className="font-medium">Pause Rollout</div>
                    <div className="text-sm text-muted-foreground">
                      Pause the current rollout — no new pods will be created
                    </div>
                  </div>
                </div>
                <Button
                  onClick={handlePause}
                  disabled={pauseMutation.isPending}
                  variant="outline"
                  className="w-full"
                >
                  {pauseMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                  Pause Rollout
                </Button>
              </div>

              <div className="border rounded-lg p-4 space-y-3">
                <div className="flex items-center gap-2">
                  <Play className="h-5 w-5" />
                  <div>
                    <div className="font-medium">Resume Rollout</div>
                    <div className="text-sm text-muted-foreground">
                      Resume a paused rollout — the deployment controller will continue
                    </div>
                  </div>
                </div>
                <Button
                  onClick={handleResume}
                  disabled={resumeMutation.isPending}
                  variant="outline"
                  className="w-full"
                >
                  {resumeMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                  Resume Rollout
                </Button>
              </div>
            </div>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
