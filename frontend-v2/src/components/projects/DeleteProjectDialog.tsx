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
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useDeleteProject } from '@/hooks/useProjects';
import type { Project } from '@/types';
import { AlertTriangle, Loader2, Trash2 } from 'lucide-react';

interface DeleteProjectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  project: Project | null;
}

export function DeleteProjectDialog({ open, onOpenChange, project }: DeleteProjectDialogProps) {
  const deleteMutation = useDeleteProject();
  const [confirmName, setConfirmName] = useState('');

  const handleDelete = async () => {
    if (!project || confirmName !== project.name) return;

    try {
      await deleteMutation.mutateAsync(project.id);
      onOpenChange(false);
      setConfirmName('');
    } catch (_error) {
      // Error is handled by the mutation
    }
  };

  const handleClose = () => {
    onOpenChange(false);
    setTimeout(() => setConfirmName(''), 300);
  };

  if (!project) return null;

  const hasModules = project.module_count > 0;
  const hasDeployments = project.deployed_count > 0;

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-[525px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-destructive">
            <AlertTriangle className="h-5 w-5" />
            Delete Project
          </DialogTitle>
          <DialogDescription>
            This action cannot be undone. This will permanently delete the project and all associated data.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          {/* Project Info */}
          <div className="p-4 bg-muted rounded-md">
            <p className="font-semibold mb-1">{project.name}</p>
            {project.description && (
              <p className="text-sm text-muted-foreground">{project.description}</p>
            )}
          </div>

          {/* Consequences */}
          <div className="space-y-2">
            <Label className="text-base font-semibold">What will be deleted:</Label>
            <ul className="space-y-2 text-sm">
              <li className="flex items-start gap-2">
                <span className="text-destructive">•</span>
                <span>
                  <strong>Project configuration</strong> - Git repository reference, branch settings
                </span>
              </li>
              {hasModules && (
                <li className="flex items-start gap-2">
                  <span className="text-destructive">•</span>
                  <span>
                    <strong>{project.module_count} module(s)</strong> - All module configurations and variable overrides
                  </span>
                </li>
              )}
              {hasDeployments && (
                <li className="flex items-start gap-2">
                  <span className="text-destructive">•</span>
                  <span>
                    <strong>Deployment history</strong> - {project.deployed_count} successful deployment(s) and {project.failed_count} failed deployment(s)
                  </span>
                </li>
              )}
              <li className="flex items-start gap-2">
                <span className="text-destructive">•</span>
                <span>
                  <strong>Deployment logs</strong> - All execution logs and output history
                </span>
              </li>
            </ul>
          </div>

          {/* Warning Box */}
          <div className="p-4 bg-destructive/10 border border-destructive/30 rounded-md">
            <p className="text-sm text-destructive font-medium flex items-center gap-2">
              <AlertTriangle className="h-4 w-4" />
              Infrastructure Not Affected
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              This only deletes the BNK Forge project configuration. Your actual cloud infrastructure will NOT be destroyed.
              If you want to destroy infrastructure, use the deployment destroy action before deleting the project.
            </p>
          </div>

          {/* Confirmation Input */}
          <div className="space-y-2">
            <Label htmlFor="confirm-name">
              Type <span className="font-mono font-bold">{project.name}</span> to confirm deletion:
            </Label>
            <Input
              id="confirm-name"
              value={confirmName}
              onChange={(e) => setConfirmName(e.target.value)}
              placeholder={project.name}
              autoComplete="off"
            />
          </div>
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={handleClose}
            disabled={deleteMutation.isPending}
          >
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={handleDelete}
            disabled={confirmName !== project.name || deleteMutation.isPending}
          >
            {deleteMutation.isPending ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Deleting...
              </>
            ) : (
              <>
                <Trash2 className="h-4 w-4 mr-2" />
                Delete Project
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
