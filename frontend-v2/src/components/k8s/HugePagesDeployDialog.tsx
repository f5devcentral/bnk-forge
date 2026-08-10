import { useState } from 'react';
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Loader2, MemoryStick } from 'lucide-react';
import type { BnkDeploymentSize } from '@/types';

// F5 BNK published sizing -- keep in sync with backend/services/bnk/sizing.py.
const SIZE_TO_PAGES: Record<BnkDeploymentSize, number> = {
  small: 1536,
  medium: 3072,
  large: 6144,
  max: 12288,
};

const SIZE_LABEL: Record<BnkDeploymentSize, string> = {
  small: 'Small',
  medium: 'Medium',
  large: 'Large',
  max: 'Max',
};

function gibForSize(size: BnkDeploymentSize): number {
  return (SIZE_TO_PAGES[size] * 2) / 1024;
}

interface HugePagesDeployDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (size: BnkDeploymentSize) => void;
  isLoading?: boolean;
  defaultSize?: BnkDeploymentSize;
}

export function HugePagesDeployDialog({
  open,
  onOpenChange,
  onConfirm,
  isLoading = false,
  defaultSize = 'small',
}: HugePagesDeployDialogProps) {
  const [size, setSize] = useState<BnkDeploymentSize>(defaultSize);
  const pages = SIZE_TO_PAGES[size];
  const gib = gibForSize(size);

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle className="flex items-center gap-2">
            <MemoryStick className="h-5 w-5 text-primary" />
            Configure HugePages on TMM nodes
          </AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="space-y-3 text-sm">
              <p>
                Dispatches a one-shot Kubernetes Job that reserves 2Mi HugePages on every
                node labelled <code>app=f5-tmm</code> or{' '}
                <code>node-role.kubernetes.io/f5-tmm</code>. The page count is driven by
                the BNK deployment size below.
              </p>

              <div className="space-y-2">
                <label className="text-xs font-medium text-muted-foreground">
                  BNK Deployment Size
                </label>
                <Select
                  value={size}
                  onValueChange={(v) => setSize(v as BnkDeploymentSize)}
                  disabled={isLoading}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {(Object.keys(SIZE_TO_PAGES) as BnkDeploymentSize[]).map((s) => (
                      <SelectItem key={s} value={s}>
                        {SIZE_LABEL[s]} — {SIZE_TO_PAGES[s].toLocaleString()} × 2Mi (
                        {gibForSize(s)} GiB)
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="rounded-md border border-warning/30 bg-warning/10 p-3 text-xs text-warning">
                <p className="font-medium">Each TMM node will reserve:</p>
                <p className="mt-1">
                  <strong>{pages.toLocaleString()}</strong> × 2Mi pages ={' '}
                  <strong>{gib} GiB</strong> of HugePage-backed memory
                </p>
              </div>

              <p className="text-xs text-muted-foreground">
                Runtime allocation is immediate. A best-effort write to{' '}
                <code>/etc/sysctl.d/</code> persists the value across reboots where the
                host filesystem allows it (fails harmlessly on Container-Optimized OS;
                for GKE COS use NodePool <code>linuxNodeConfig</code> for reboot
                survival).
              </p>
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isLoading}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={(e) => {
              e.preventDefault();
              onConfirm(size);
            }}
            disabled={isLoading}
          >
            {isLoading ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Dispatching…
              </>
            ) : (
              'Deploy HugePages'
            )}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
