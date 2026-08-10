import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
  Server,
  Ban,
  Play,
  Droplets,
  AlertTriangle,
  Loader2,
  CheckCircle2,
} from 'lucide-react';
import { notify } from '@/lib/notify';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

interface NodeOperationsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clusterId: number;
  nodeName: string;
  nodeStatus: { spec?: { unschedulable?: boolean } } | null;
}

export function NodeOperationsDialog({
  open,
  onOpenChange,
  clusterId,
  nodeName,
  nodeStatus,
}: NodeOperationsDialogProps) {
  const queryClient = useQueryClient();
  const [drainOptions, setDrainOptions] = useState({
    ignore_daemonsets: true,
    delete_emptydir_data: false,
    force: false,
    grace_period: 30,
  });

  const isSchedulable = !nodeStatus?.spec?.unschedulable;

  const cordonMutation = useAppMutation({
    mutationFn: () => api.cordonNode(clusterId, nodeName),
    onSuccess: () => {
      notify.success(`Node ${nodeName} cordoned successfully`, undefined, { category: 'cluster' });
      queryClient.invalidateQueries({ queryKey: ['k8s', 'clusters', clusterId, 'resources', 'node'] });
    },
    onError: (error: unknown) => {
      const message = error instanceof Error ? error.message : 'Unknown error';
      notify.error(`Failed to cordon node: ${message}`, undefined, { category: 'cluster' });
    },
  });

  const uncordonMutation = useAppMutation({
    mutationFn: () => api.uncordonNode(clusterId, nodeName),
    onSuccess: () => {
      notify.success(`Node ${nodeName} uncordoned successfully`, undefined, { category: 'cluster' });
      queryClient.invalidateQueries({ queryKey: ['k8s', 'clusters', clusterId, 'resources', 'node'] });
    },
    onError: (error: unknown) => {
      const message = error instanceof Error ? error.message : 'Unknown error';
      notify.error(`Failed to uncordon node: ${message}`, undefined, { category: 'cluster' });
    },
  });

  const drainMutation = useAppMutation({
    mutationFn: () => api.drainNode(clusterId, nodeName, drainOptions),
    onSuccess: (data) => {
      const evictedCount = data.evicted_pods?.length || 0;
      notify.success(`Node ${nodeName} drained. ${evictedCount} pods evicted.`, undefined, { category: 'cluster' });
      if (data.failed_pods && data.failed_pods.length > 0) {
        notify.warning(`${data.failed_pods.length} pods failed to evict`, undefined, { category: 'cluster' });
      }
      // Invalidate both node and pod resources since drain affects pods
      queryClient.invalidateQueries({ queryKey: ['k8s', 'clusters', clusterId, 'resources', 'node'] });
      queryClient.invalidateQueries({ queryKey: ['k8s', 'clusters', clusterId, 'resources', 'pod'] });
    },
    onError: (error: unknown) => {
      const message = error instanceof Error ? error.message : 'Unknown error';
      notify.error(`Failed to drain node: ${message}`, undefined, { category: 'cluster' });
    },
  });

  const handleCordon = () => {
    if (confirm(`Mark ${nodeName} as unschedulable?`)) {
      cordonMutation.mutate();
    }
  };

  const handleUncordon = () => {
    if (confirm(`Mark ${nodeName} as schedulable?`)) {
      uncordonMutation.mutate();
    }
  };

  const handleDrain = () => {
    if (confirm(`Drain ${nodeName}? This will evict all pods.`)) {
      drainMutation.mutate();
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Server className="h-5 w-5" />
            Node Operations: {nodeName}
          </DialogTitle>
          <DialogDescription>
            Manage node scheduling and maintenance
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-6">
          <div className="border rounded-lg p-4 space-y-2">
            <div className="font-medium flex items-center gap-2">
              {isSchedulable ? (
                <CheckCircle2 className="h-4 w-4 text-success" />
              ) : (
                <Ban className="h-4 w-4 text-warning" />
              )}
              Node Status
            </div>
            <div className="text-sm text-muted-foreground">
              {isSchedulable ? 'Schedulable' : 'Unschedulable'}
            </div>
          </div>

          <div className="border rounded-lg p-4 space-y-3">
            {isSchedulable ? (
              <Button onClick={handleCordon} disabled={cordonMutation.isPending} variant="outline" className="w-full">
                {cordonMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                <Ban className="h-4 w-4 mr-2" />
                Cordon Node
              </Button>
            ) : (
              <Button onClick={handleUncordon} disabled={uncordonMutation.isPending} className="w-full">
                {uncordonMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                <Play className="h-4 w-4 mr-2" />
                Uncordon Node
              </Button>
            )}
          </div>

          <div className="border rounded-lg p-4 space-y-4">
            <div className="flex items-center gap-2">
              <Droplets className="h-5 w-5" />
              <div className="font-medium">Drain Node</div>
            </div>

            <Alert variant="destructive">
              <AlertTriangle className="h-4 w-4" />
              <AlertDescription>
                Warning: Draining will evict all pods from this node
              </AlertDescription>
            </Alert>

            <div className="space-y-4">
              <div className="flex items-center space-x-2">
                <Checkbox
                  id="ignore_daemonsets"
                  checked={drainOptions.ignore_daemonsets}
                  onCheckedChange={(checked) =>
                    setDrainOptions({ ...drainOptions, ignore_daemonsets: checked as boolean })
                  }
                />
                <Label htmlFor="ignore_daemonsets">Ignore DaemonSets</Label>
              </div>

              <div className="flex items-center space-x-2">
                <Checkbox
                  id="delete_emptydir"
                  checked={drainOptions.delete_emptydir_data}
                  onCheckedChange={(checked) =>
                    setDrainOptions({ ...drainOptions, delete_emptydir_data: checked as boolean })
                  }
                />
                <Label htmlFor="delete_emptydir">Delete emptyDir data</Label>
              </div>

              <div className="flex items-center space-x-2">
                <Checkbox
                  id="force"
                  checked={drainOptions.force}
                  onCheckedChange={(checked) =>
                    setDrainOptions({ ...drainOptions, force: checked as boolean })
                  }
                />
                <Label htmlFor="force">Force eviction</Label>
              </div>

              <div className="space-y-2">
                <Label htmlFor="grace_period">Grace Period (seconds)</Label>
                <Input
                  id="grace_period"
                  type="number"
                  min="0"
                  value={drainOptions.grace_period}
                  onChange={(e) =>
                    setDrainOptions({ ...drainOptions, grace_period: parseInt(e.target.value) || 30 })
                  }
                />
              </div>
            </div>

            <Button onClick={handleDrain} disabled={drainMutation.isPending} variant="destructive" className="w-full">
              {drainMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              <Droplets className="h-4 w-4 mr-2" />
              Drain Node
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
