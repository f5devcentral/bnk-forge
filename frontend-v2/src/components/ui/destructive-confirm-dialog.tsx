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
import { AlertTriangle, Trash2, XCircle } from 'lucide-react';
import { cn } from '@/lib/utils';

interface DestructiveConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  confirmText: string; // What user must type to confirm (e.g., "DELETE", resource name)
  onConfirm: () => void;
  isPending?: boolean;
  warningItems?: string[]; // List of items that will be affected (e.g., "3 pods", "1 VPC")
  consequences?: string[]; // List of consequences (e.g., "All data will be lost")
  variant?: 'danger' | 'warning';
}

export function DestructiveConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmText,
  onConfirm,
  isPending = false,
  warningItems = [],
  consequences = [],
  variant = 'danger',
}: DestructiveConfirmDialogProps) {
  const [inputValue, setInputValue] = useState('');
  const [isValid, setIsValid] = useState(false);

  useEffect(() => {
    setIsValid(inputValue.trim() === confirmText);
  }, [inputValue, confirmText]);

  // Reset when dialog closes
  useEffect(() => {
    if (!open) {
      setInputValue('');
      setIsValid(false);
    }
  }, [open]);

  const handleConfirm = () => {
    if (isValid) {
      onConfirm();
    }
  };

  const isDanger = variant === 'danger';
  const WarningIcon = isDanger ? XCircle : AlertTriangle;
  const iconColor = isDanger ? 'text-red-500' : 'text-orange-500';
  const bgColor = isDanger ? 'bg-red-500/10 border-red-500/20' : 'bg-orange-500/10 border-orange-500/20';
  const textColor = isDanger ? 'text-red-700 dark:text-red-400' : 'text-orange-700 dark:text-orange-400';

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <WarningIcon className={cn('h-5 w-5', iconColor)} />
            {title}
          </DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          {/* Warning Items */}
          {warningItems.length > 0 && (
            <div className={cn('rounded-lg border p-4', bgColor)}>
              <p className={cn('text-sm font-medium mb-2', textColor)}>
                The following will be affected:
              </p>
              <ul className="text-sm space-y-1 ml-4">
                {warningItems.map((item, index) => (
                  <li key={index} className="flex items-start gap-2">
                    <Trash2 className={cn('h-4 w-4 mt-0.5 flex-shrink-0', iconColor)} />
                    <span className={textColor}>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Consequences */}
          {consequences.length > 0 && (
            <div className="rounded-lg bg-muted p-4 space-y-2">
              <p className="text-sm font-medium text-foreground">
                Consequences:
              </p>
              <ul className="text-sm text-muted-foreground space-y-1">
                {consequences.map((consequence, index) => (
                  <li key={index}>• {consequence}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Type to Confirm */}
          <div className="space-y-2">
            <Label htmlFor="confirm-input" className="text-sm font-medium">
              Type <code className="px-1.5 py-0.5 bg-muted rounded text-foreground font-mono">{confirmText}</code> to confirm
            </Label>
            <Input
              id="confirm-input"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              placeholder={`Type "${confirmText}" here`}
              className={cn(
                'font-mono',
                isValid && 'border-green-500 focus-visible:ring-green-500'
              )}
              autoFocus
              autoComplete="off"
              disabled={isPending}
            />
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
            variant="destructive"
            onClick={handleConfirm}
            disabled={!isValid || isPending}
          >
            {isPending ? 'Processing...' : isDanger ? 'Delete Permanently' : 'Confirm'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
