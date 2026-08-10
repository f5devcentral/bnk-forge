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
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Slider } from '@/components/ui/slider';
import { Loader2, TrendingUp, TrendingDown } from 'lucide-react';
import type { K8sResource } from '@/types';

interface ScaleDeploymentDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  deployment: K8sResource | null;
  onSubmit: (replicas: number) => void;
  isLoading?: boolean;
}

export function ScaleDeploymentDialog({
  open,
  onOpenChange,
  deployment,
  onSubmit,
  isLoading = false,
}: ScaleDeploymentDialogProps) {
  const [replicas, setReplicas] = useState(1);
  const currentReplicas = deployment?.spec?.replicas || 1;

  useEffect(() => {
    if (deployment) {
      setReplicas(currentReplicas);
    }
  }, [deployment, currentReplicas]);

  const handleSubmit = () => {
    onSubmit(replicas);
  };

  if (!deployment) return null;

  const isScalingUp = replicas > currentReplicas;
  const noChange = replicas === currentReplicas;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Scale Deployment</DialogTitle>
          <DialogDescription>
            Adjust the number of replicas for <strong>{deployment.metadata.name}</strong>
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-6 py-4">
          <div className="space-y-2">
            <Label>Current replicas: {currentReplicas}</Label>
            <div className="text-2xl font-bold text-muted-foreground">
              {currentReplicas} replica{currentReplicas !== 1 ? 's' : ''}
            </div>
          </div>

          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <Label htmlFor="replicas">New replica count:</Label>
              <Input
                id="replicas"
                type="number"
                min={0}
                max={20}
                value={replicas}
                onChange={(e) => setReplicas(parseInt(e.target.value) || 0)}
                className="w-20"
              />
            </div>

            <Slider
              value={[replicas]}
              onValueChange={(value) => setReplicas(value[0])}
              min={0}
              max={20}
              step={1}
              className="w-full"
            />

            <div className="flex justify-between text-xs text-muted-foreground">
              <span>0</span>
              <span>10</span>
              <span>20</span>
            </div>
          </div>

          {!noChange && (
            <div className="flex items-center gap-2 p-3 bg-muted rounded-lg">
              {isScalingUp ? (
                <>
                  <TrendingUp className="h-5 w-5 text-success" />
                  <div className="flex-1">
                    <div className="font-medium text-sm">Scaling up</div>
                    <div className="text-xs text-muted-foreground">
                      Adding {replicas - currentReplicas} replica{replicas - currentReplicas !== 1 ? 's' : ''}
                    </div>
                  </div>
                </>
              ) : (
                <>
                  <TrendingDown className="h-5 w-5 text-warning" />
                  <div className="flex-1">
                    <div className="font-medium text-sm">Scaling down</div>
                    <div className="text-xs text-muted-foreground">
                      Removing {currentReplicas - replicas} replica{currentReplicas - replicas !== 1 ? 's' : ''}
                    </div>
                  </div>
                </>
              )}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={isLoading}
          >
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={isLoading || noChange}>
            {isLoading ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Scaling...
              </>
            ) : (
              'Scale'
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
