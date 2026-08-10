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
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { AlertTriangle, Check, Terminal } from 'lucide-react';

interface ApplyConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  moduleName: string;
  onConfirm: (autoApprove: boolean) => void;
  isPending?: boolean;
  engineType?: string;
}

export function ApplyConfirmDialog({
  open,
  onOpenChange,
  moduleName,
  onConfirm,
  isPending = false,
  engineType,
}: ApplyConfirmDialogProps) {
  const [autoApprove, setAutoApprove] = useState(true);
  const isSsh = engineType === 'ssh';

  const handleConfirm = () => {
    // SSH modules always auto-execute — pass true unconditionally
    onConfirm(isSsh ? true : autoApprove);
    onOpenChange(false);
  };

  if (isSsh) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-[500px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Terminal className="h-5 w-5 text-info" />
              Run Module
            </DialogTitle>
            <DialogDescription>
              You are about to execute <strong>{moduleName}</strong> on the target host
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-4">
            <div className="rounded-lg bg-muted p-4 space-y-3">
              <p className="text-sm text-muted-foreground">
                This operation will:
              </p>
              <ul className="text-sm space-y-1 ml-4">
                <li className="flex items-start gap-2">
                  <Check className="h-4 w-4 mt-0.5 text-success flex-shrink-0" />
                  <span>Connect to the bare-metal host via SSH</span>
                </li>
                <li className="flex items-start gap-2">
                  <Check className="h-4 w-4 mt-0.5 text-success flex-shrink-0" />
                  <span>Execute module commands on the target</span>
                </li>
                <li className="flex items-start gap-2">
                  <Check className="h-4 w-4 mt-0.5 text-success flex-shrink-0" />
                  <span>Collect outputs for downstream modules</span>
                </li>
              </ul>
            </div>
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={isPending}
            >
              Cancel
            </Button>
            <Button
              onClick={handleConfirm}
              disabled={isPending}
            >
              {isPending ? 'Running...' : 'Run'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-warning" />
            Apply Infrastructure Changes
          </DialogTitle>
          <DialogDescription>
            You are about to apply infrastructure changes for <strong>{moduleName}</strong>
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          <div className="rounded-lg bg-muted p-4 space-y-3">
            <p className="text-sm text-muted-foreground">
              This operation will:
            </p>
            <ul className="text-sm space-y-1 ml-4">
              <li className="flex items-start gap-2">
                <Check className="h-4 w-4 mt-0.5 text-success flex-shrink-0" />
                <span>Execute tofu apply on your infrastructure</span>
              </li>
              <li className="flex items-start gap-2">
                <Check className="h-4 w-4 mt-0.5 text-success flex-shrink-0" />
                <span>Create, update, or modify cloud resources</span>
              </li>
              <li className="flex items-start gap-2">
                <Check className="h-4 w-4 mt-0.5 text-success flex-shrink-0" />
                <span>Update the OpenTofu state file</span>
              </li>
            </ul>
          </div>

          <div className="flex items-start space-x-3 rounded-lg border p-4">
            <Checkbox
              id="auto-approve"
              checked={autoApprove}
              onCheckedChange={(checked) => setAutoApprove(checked as boolean)}
            />
            <div className="flex-1 space-y-1">
              <Label
                htmlFor="auto-approve"
                className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70 cursor-pointer"
              >
                Auto-approve changes
              </Label>
              <p className="text-sm text-muted-foreground">
                {autoApprove ? (
                  <>
                    OpenTofu will automatically apply changes without prompting for confirmation.
                    <strong className="text-foreground"> Recommended for automated workflows.</strong>
                  </>
                ) : (
                  <>
                    OpenTofu will ask for manual confirmation before applying changes.
                    <strong className="text-warning"> This will cause the operation to fail</strong> since
                    the system cannot provide interactive input.
                  </>
                )}
              </p>
            </div>
          </div>

          {!autoApprove && (
            <div className="rounded-lg bg-warning/10 border border-warning/20 p-3">
              <div className="flex items-start gap-2">
                <AlertTriangle className="h-4 w-4 text-warning mt-0.5 flex-shrink-0" />
                <p className="text-sm text-warning">
                  <strong>Warning:</strong> Without auto-approve, the apply operation will fail with an
                  "Error: error asking for approval: EOF" message because the system cannot provide
                  interactive input. Auto-approve is required for background operations.
                </p>
              </div>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={isPending}
          >
            Cancel
          </Button>
          <Button
            onClick={handleConfirm}
            disabled={isPending}
          >
            {isPending ? 'Applying...' : autoApprove ? 'Apply with Auto-Approve' : 'Apply (Manual Confirm)'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
