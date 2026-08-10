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
import { Loader2, AlertTriangle } from 'lucide-react';
import type { K8sResource } from '@/types';

interface ResourceDeleteDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  resource: K8sResource | null;
  onConfirm: () => void;
  isLoading?: boolean;
}

export function ResourceDeleteDialog({
  open,
  onOpenChange,
  resource,
  onConfirm,
  isLoading = false,
}: ResourceDeleteDialogProps) {
  if (!resource) return null;

  const isDestructive =
    resource.kind === 'Namespace' ||
    resource.kind === 'PersistentVolume' ||
    resource.kind === 'PersistentVolumeClaim';

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-destructive" />
            Delete {resource.kind}?
          </AlertDialogTitle>
          <AlertDialogDescription className="space-y-2">
            <p>
              Are you sure you want to delete <strong>{resource.metadata.name}</strong>?
            </p>

            {resource.metadata.namespace && (
              <p className="text-sm text-muted-foreground">
                Namespace: {resource.metadata.namespace}
              </p>
            )}

            {isDestructive && (
              <div className="mt-3 p-3 bg-destructive/10 border border-destructive/20 rounded">
                <p className="text-sm font-semibold text-destructive">Warning:</p>
                <p className="text-sm text-destructive/90 mt-1">
                  Deleting this {resource.kind} may result in data loss and cannot be undone.
                  {resource.kind === 'Namespace' &&
                    ' All resources in this namespace will also be deleted.'}
                </p>
              </div>
            )}

            <p className="mt-3 text-sm text-muted-foreground">
              This action cannot be undone.
            </p>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isLoading}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={onConfirm}
            disabled={isLoading}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            {isLoading ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Deleting...
              </>
            ) : (
              'Delete'
            )}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
