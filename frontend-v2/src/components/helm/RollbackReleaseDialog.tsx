/**
 * Rollback Release Dialog
 *
 * Dialog for rolling back Helm releases to previous revisions
 */

import { useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Badge, type BadgeProps } from '@/components/ui/badge';
import { useRollbackHelmRelease, useHelmHistory } from '@/hooks/useHelm';
import { notify, notifyError } from '@/lib/notify';
import { Undo2, Loader2, AlertTriangle } from 'lucide-react';
import { cn } from '@/lib/utils';
import { formatTimeAgo } from '@/lib/time-utils';

interface RollbackReleaseDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clusterId: number;
  releaseName: string;
  releaseNamespace: string;
  currentRevision: number;
}

export function RollbackReleaseDialog({
  open,
  onOpenChange,
  clusterId,
  releaseName,
  releaseNamespace,
  currentRevision,
}: RollbackReleaseDialogProps) {
  const rollbackMutation = useRollbackHelmRelease();

  // Fetch release history
  const { data: historyData, isLoading: historyLoading } = useHelmHistory(
    clusterId,
    releaseName,
    releaseNamespace,
    { enabled: open }
  );

  const history = historyData?.history || [];

  // Form state
  const [selectedRevision, setSelectedRevision] = useState<number | null>(null);
  const [wait, setWait] = useState(true);
  const [timeout, setTimeout] = useState('5m');

  // Reset form when dialog closes
  const handleOpenChange = (newOpen: boolean) => {
    if (!newOpen) {
      setSelectedRevision(null);
      setWait(true);
      setTimeout('5m');
    }
    onOpenChange(newOpen);
  };

  const handleRollback = async () => {
    if (!selectedRevision) {
      notify.error('Please select a revision to rollback to');
      return;
    }

    if (selectedRevision === currentRevision) {
      notify.error('Selected revision is the current revision');
      return;
    }

    const confirmed = window.confirm(
      `Are you sure you want to rollback "${releaseName}" to revision ${selectedRevision}?\n\nThis will create a new revision with the configuration from revision ${selectedRevision}.`
    );

    if (!confirmed) return;

    try {
      await rollbackMutation.mutateAsync({
        clusterId,
        releaseName,
        namespace: releaseNamespace,
        request: {
          revision: selectedRevision,
          wait,
          timeout,
        },
      });

      notify.success(`Rollback of ${releaseName} to revision ${selectedRevision} queued — refresh the Releases tab in ~30s to confirm`, undefined, { category: 'deployment' });
      handleOpenChange(false);
    } catch (error) {
      notifyError(error);
    }
  };

  const getStatusVariant = (status: string): BadgeProps['variant'] => {
    const statusLower = status.toLowerCase();
    if (statusLower === 'deployed') return 'success';
    if (statusLower === 'failed') return 'destructive';
    return 'muted';
  };

  // Use centralized time utility
  const calculateAge = formatTimeAgo;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Undo2 className="h-5 w-5" />
            Rollback Helm Release
          </DialogTitle>
          <DialogDescription>
            Rollback <code className="text-xs font-mono bg-muted px-1 py-0.5 rounded">{releaseName}</code> to a previous revision.
            This will create a new revision with the configuration from the selected revision.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          {/* Warning Banner */}
          <div className="flex items-start gap-3 p-3 rounded-lg border border-warning/30 bg-warning/10">
            <AlertTriangle className="h-5 w-5 text-warning flex-shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-medium text-warning">Important</p>
              <p className="text-xs text-muted-foreground mt-1">
                Rolling back will create a new revision. To undo a rollback, you'll need to rollback again or upgrade.
              </p>
            </div>
          </div>

          {/* Revision History Table */}
          <div className="space-y-2">
            <Label>Revision History</Label>
            {historyLoading ? (
              <div className="flex items-center justify-center h-32 border border-border rounded-lg">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : history.length === 0 ? (
              <div className="p-8 rounded-lg border border-border bg-muted/50 text-center">
                <p className="text-sm text-muted-foreground">No revision history found</p>
              </div>
            ) : (
              <div className="border border-border rounded-lg overflow-hidden">
                <table className="w-full">
                  <thead className="text-xs bg-muted/50">
                    <tr className="text-muted-foreground">
                      <th className="text-left py-2 px-3 font-medium">Rev</th>
                      <th className="text-left py-2 px-3 font-medium">Updated</th>
                      <th className="text-left py-2 px-3 font-medium">Status</th>
                      <th className="text-left py-2 px-3 font-medium">Chart</th>
                      <th className="text-left py-2 px-3 font-medium">App Version</th>
                      <th className="text-left py-2 px-3 font-medium">Description</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.map((rev) => {
                      const isSelected = selectedRevision === rev.revision;
                      const isCurrent = rev.revision === currentRevision;

                      return (
                        <tr
                          key={rev.revision}
                          className={cn(
                            'cursor-pointer transition-colors border-t border-border',
                            isSelected
                              ? 'bg-primary/10'
                              : 'hover:bg-muted/50',
                            isCurrent && 'font-medium'
                          )}
                          onClick={() => !isCurrent && setSelectedRevision(rev.revision)}
                        >
                          <td className="py-2 px-3">
                            <div className="flex items-center gap-2">
                              <span className="text-sm">{rev.revision}</span>
                              {isCurrent && (
                                <Badge variant="info" className="text-[9px] px-1.5 py-0">
                                  CURRENT
                                </Badge>
                              )}
                            </div>
                          </td>
                          <td className="py-2 px-3 text-xs text-muted-foreground">
                            {calculateAge(rev.updated)}
                          </td>
                          <td className="py-2 px-3">
                            <Badge variant={getStatusVariant(rev.status)} className="text-[10px]">
                              {rev.status}
                            </Badge>
                          </td>
                          <td className="py-2 px-3">
                            <code className="text-xs font-mono">{rev.chart}</code>
                          </td>
                          <td className="py-2 px-3">
                            <code className="text-xs font-mono">{rev.app_version}</code>
                          </td>
                          <td className="py-2 px-3 text-xs text-muted-foreground max-w-xs truncate">
                            {rev.description || 'No description'}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            <p className="text-xs text-muted-foreground">
              {selectedRevision
                ? `Selected revision: ${selectedRevision}`
                : 'Click a row to select a revision to rollback to'}
            </p>
          </div>

          {/* Options */}
          {selectedRevision && (
            <div className="space-y-3">
              <Label>Options</Label>

              <div className="flex items-center space-x-2">
                <Checkbox
                  id="wait"
                  checked={wait}
                  onCheckedChange={(checked) => setWait(checked as boolean)}
                />
                <label
                  htmlFor="wait"
                  className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70 cursor-pointer"
                >
                  Wait for resources to be ready
                </label>
              </div>

              <div className="flex items-center gap-4">
                <Label htmlFor="timeout" className="whitespace-nowrap">Timeout:</Label>
                <Select value={timeout} onValueChange={setTimeout}>
                  <SelectTrigger id="timeout" className="w-32">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="1m">1 minute</SelectItem>
                    <SelectItem value="5m">5 minutes</SelectItem>
                    <SelectItem value="10m">10 minutes</SelectItem>
                    <SelectItem value="15m">15 minutes</SelectItem>
                    <SelectItem value="30m">30 minutes</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          )}
        </div>

        <div className="flex justify-end gap-3">
          <Button
            variant="outline"
            onClick={() => handleOpenChange(false)}
            disabled={rollbackMutation.isPending}
          >
            Cancel
          </Button>
          <Button
            onClick={handleRollback}
            disabled={rollbackMutation.isPending || !selectedRevision}
          >
            {rollbackMutation.isPending ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Rolling back...
              </>
            ) : (
              <>
                <Undo2 className="h-4 w-4 mr-2" />
                Rollback
              </>
            )}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
