import { useState } from 'react';
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
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  useRolloutHistory,
  useRolloutStatus,
  useRolloutUndo,
  useRolloutRestart,
} from '@/hooks/useK8s';
import { Loader2, RotateCw, Undo2, CheckCircle2, AlertCircle, Clock } from 'lucide-react';
import { notify, notifyError } from '@/lib/notify';
import { formatDateTime } from '@/lib/time-utils';
import type { K8sCondition, K8sRolloutHistoryEntry } from '@/types';


interface DeploymentRolloutManagerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clusterId: number;
  deploymentName: string;
  namespace: string;
}

export function DeploymentRolloutManager({
  open,
  onOpenChange,
  clusterId,
  deploymentName,
  namespace,
}: DeploymentRolloutManagerProps) {
  const [confirmUndoOpen, setConfirmUndoOpen] = useState(false);
  const [confirmRestartOpen, setConfirmRestartOpen] = useState(false);
  const [selectedRevision, setSelectedRevision] = useState<number | undefined>();

  // Fetch rollout data
  const { data: historyData, isLoading: historyLoading } = useRolloutHistory(
    clusterId,
    deploymentName,
    namespace,
    { enabled: open }
  );

  const { data: statusData, isLoading: statusLoading } = useRolloutStatus(
    clusterId,
    deploymentName,
    namespace,
    { enabled: open, pollingEnabled: open }
  );

  // Mutations
  const undoMutation = useRolloutUndo();
  const restartMutation = useRolloutRestart();

  const handleUndo = (revision?: number) => {
    setSelectedRevision(revision);
    setConfirmUndoOpen(true);
  };

  const confirmUndo = () => {
    undoMutation.mutate(
      {
        clusterId,
        deploymentName,
        namespace,
        revision: selectedRevision,
      },
      {
        onSuccess: (data) => {
          notify.success(data.message || 'Rollback initiated successfully', undefined, { category: 'cluster' });
          setConfirmUndoOpen(false);
          setSelectedRevision(undefined);
        },
        onError: (error) => {
          notifyError(error);
        },
      }
    );
  };

  const handleRestart = () => {
    setConfirmRestartOpen(true);
  };

  const confirmRestart = () => {
    restartMutation.mutate(
      {
        clusterId,
        deploymentName,
        namespace,
      },
      {
        onSuccess: (data) => {
          notify.success(data.message || 'Deployment restart initiated', undefined, { category: 'cluster' });
          setConfirmRestartOpen(false);
        },
        onError: (error) => {
          notifyError(error);
        },
      }
    );
  };

  // Use centralized time utility
  const formatDate = (dateString?: string) => formatDateTime(dateString) || 'N/A';

  const getRolloutStatusBadge = () => {
    if (!statusData) return null;

    if (statusData.rollout_complete) {
      return (
        <Badge variant="success" className="gap-1">
          <CheckCircle2 className="h-3 w-3" />
          Complete
        </Badge>
      );
    }

    if ((statusData.unavailable_replicas ?? 0) > 0) {
      return (
        <Badge variant="warning" className="gap-1">
          <AlertCircle className="h-3 w-3" />
          In Progress
        </Badge>
      );
    }

    return (
      <Badge variant="info" className="gap-1">
        <Clock className="h-3 w-3" />
        Updating
      </Badge>
    );
  };

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Deployment Rollout Management</DialogTitle>
            <DialogDescription>
              Manage rollout history and status for deployment: <span className="font-mono">{deploymentName}</span>
            </DialogDescription>
          </DialogHeader>

          <Tabs defaultValue="status" className="w-full">
            <TabsList className="grid w-full grid-cols-2">
              <TabsTrigger value="status">Current Status</TabsTrigger>
              <TabsTrigger value="history">Rollout History</TabsTrigger>
            </TabsList>

            <TabsContent value="status" className="space-y-4 mt-4">
              {statusLoading ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              ) : statusData ? (
                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <div className="space-y-1">
                      <p className="text-sm font-medium">Rollout Status</p>
                      <p className="text-sm text-muted-foreground">{statusData.message}</p>
                    </div>
                    {getRolloutStatusBadge()}
                  </div>

                  <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                    <div className="space-y-1">
                      <p className="text-xs text-muted-foreground">Desired</p>
                      <p className="text-2xl font-bold">{statusData.desired_replicas}</p>
                    </div>
                    <div className="space-y-1">
                      <p className="text-xs text-muted-foreground">Current</p>
                      <p className="text-2xl font-bold">{statusData.current_replicas}</p>
                    </div>
                    <div className="space-y-1">
                      <p className="text-xs text-muted-foreground">Updated</p>
                      <p className="text-2xl font-bold">{statusData.updated_replicas}</p>
                    </div>
                    <div className="space-y-1">
                      <p className="text-xs text-muted-foreground">Ready</p>
                      <p className="text-2xl font-bold text-success">{statusData.ready_replicas}</p>
                    </div>
                    <div className="space-y-1">
                      <p className="text-xs text-muted-foreground">Available</p>
                      <p className="text-2xl font-bold">{statusData.available_replicas}</p>
                    </div>
                  </div>

                  {statusData.conditions && statusData.conditions.length > 0 && (
                    <div className="space-y-2">
                      <p className="text-sm font-medium">Conditions</p>
                      <div className="space-y-1">
                        {statusData.conditions.map((condition: K8sCondition, index: number) => (
                          <div
                            key={index}
                            className="flex items-center justify-between p-2 bg-muted rounded text-sm"
                          >
                            <span className="font-medium">{condition.type}</span>
                            <div className="flex items-center gap-2">
                              <Badge variant={condition.status === 'True' ? 'outline' : 'secondary'}>
                                {condition.status}
                              </Badge>
                              {condition.message && (
                                <span className="text-xs text-muted-foreground max-w-xs truncate">
                                  {condition.message}
                                </span>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  <div className="flex gap-2 pt-4">
                    <Button
                      onClick={() => handleUndo()}
                      variant="outline"
                      disabled={undoMutation.isPending}
                    >
                      {undoMutation.isPending ? (
                        <Loader2 className="h-4 w-4 animate-spin mr-2" />
                      ) : (
                        <Undo2 className="h-4 w-4 mr-2" />
                      )}
                      Rollback to Previous
                    </Button>
                    <Button
                      onClick={handleRestart}
                      variant="outline"
                      disabled={restartMutation.isPending}
                    >
                      {restartMutation.isPending ? (
                        <Loader2 className="h-4 w-4 animate-spin mr-2" />
                      ) : (
                        <RotateCw className="h-4 w-4 mr-2" />
                      )}
                      Restart Deployment
                    </Button>
                  </div>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground text-center py-8">
                  No status data available
                </p>
              )}
            </TabsContent>

            <TabsContent value="history" className="mt-4">
              {historyLoading ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              ) : historyData?.history && historyData.history.length > 0 ? (
                <div className="space-y-4">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Revision</TableHead>
                        <TableHead>Change Cause</TableHead>
                        <TableHead>Replicas</TableHead>
                        <TableHead>Created</TableHead>
                        <TableHead>Actions</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {historyData.history.map((revision: K8sRolloutHistoryEntry) => (
                        <TableRow key={revision.revision}>
                          <TableCell className="font-mono">
                            <div className="flex items-center gap-2">
                              #{revision.revision}
                              {revision.is_current && (
                                <Badge variant="outline" className="text-xs">
                                  Current
                                </Badge>
                              )}
                            </div>
                          </TableCell>
                          <TableCell className="max-w-md truncate" title={revision.change_cause}>
                            {revision.change_cause}
                          </TableCell>
                          <TableCell>
                            {revision.ready_replicas}/{revision.replicas}
                          </TableCell>
                          <TableCell className="text-sm text-muted-foreground">
                            {formatDate(revision.created_at)}
                          </TableCell>
                          <TableCell>
                            {!revision.is_current && (
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => handleUndo(revision.revision)}
                                disabled={undoMutation.isPending}
                              >
                                <Undo2 className="h-3 w-3 mr-1" />
                                Rollback
                              </Button>
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground text-center py-8">
                  No rollout history available
                </p>
              )}
            </TabsContent>
          </Tabs>
        </DialogContent>
      </Dialog>

      {/* Undo Confirmation Dialog */}
      <AlertDialog open={confirmUndoOpen} onOpenChange={setConfirmUndoOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm Rollback</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to rollback deployment <span className="font-mono">{deploymentName}</span>
              {selectedRevision
                ? ` to revision #${selectedRevision}`
                : ' to the previous revision'}?
              <br />
              <br />
              This will trigger a new rollout with the selected revision's pod template.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={confirmUndo} disabled={undoMutation.isPending}>
              {undoMutation.isPending && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
              Rollback
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Restart Confirmation Dialog */}
      <AlertDialog open={confirmRestartOpen} onOpenChange={setConfirmRestartOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm Restart</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to restart deployment <span className="font-mono">{deploymentName}</span>?
              <br />
              <br />
              This will trigger a rolling restart of all pods by updating the pod template with a restart annotation.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={confirmRestart} disabled={restartMutation.isPending}>
              {restartMutation.isPending && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
              Restart
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
