import { useState } from 'react';
import { Loader2, FolderGit2 } from 'lucide-react';
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
import { useImportRegistryGit } from '@/hooks/useRegistry';
import { isAxiosError } from 'axios';
import { notify } from '@/lib/notify';
import type { ModuleLibrary } from '@/types';

interface PublicGitImportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onImported?: (module: ModuleLibrary) => void;
}

const URL_PATTERN = /^https?:\/\/[^\s]+$|^git@[^:\s]+:[^\s]+$/;

export function PublicGitImportDialog({ open, onOpenChange, onImported }: PublicGitImportDialogProps) {
  const [url, setUrl] = useState('');
  const [ref, setRef] = useState('');
  const [subpath, setSubpath] = useState('');
  const [versionLabel, setVersionLabel] = useState('');
  const [displayName, setDisplayName] = useState('');

  const importMutation = useImportRegistryGit();
  const urlValid = !url || URL_PATTERN.test(url);

  const reset = () => {
    setUrl('');
    setRef('');
    setSubpath('');
    setVersionLabel('');
    setDisplayName('');
  };

  const handleImport = async () => {
    if (!url || !ref || !urlValid) return;
    try {
      const result = await importMutation.mutateAsync({
        url: url.trim(),
        ref: ref.trim(),
        subpath: subpath.trim() || undefined,
        version_label: versionLabel.trim() || undefined,
        display_name: displayName.trim() || undefined,
      });
      notify.success(`Module imported from ${url}`, undefined, { category: 'system' });
      reset();
      onOpenChange(false);
      onImported?.(result.module);
    } catch (err: unknown) {
      const msg = isAxiosError(err)
        ? err.response?.data?.detail || err.message
        : err instanceof Error ? err.message : 'Unknown error';
      notify.error(`Failed to import: ${msg}`);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) reset(); }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FolderGit2 className="h-5 w-5" />
            Import Module from Git URL
          </DialogTitle>
          <DialogDescription>
            Pin a public git repository at a specific tag, branch, or commit.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div>
            <Label htmlFor="git-url">Git URL *</Label>
            <Input
              id="git-url"
              placeholder="https://github.com/cloudposse/terraform-null-label"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              className={!urlValid ? 'border-destructive' : ''}
            />
            {!urlValid && (
              <p className="text-xs text-destructive mt-1">
                URL should look like https://… or git@host:path
              </p>
            )}
          </div>

          <div>
            <Label htmlFor="git-ref">Ref (tag, branch, or commit) *</Label>
            <Input
              id="git-ref"
              placeholder="0.25.0"
              value={ref}
              onChange={(e) => setRef(e.target.value)}
            />
          </div>

          <div>
            <Label htmlFor="git-subpath">Subpath (optional)</Label>
            <Input
              id="git-subpath"
              placeholder="modules/vpc"
              value={subpath}
              onChange={(e) => setSubpath(e.target.value)}
            />
            <p className="text-xs text-muted-foreground mt-1">
              For monorepos that contain multiple modules.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label htmlFor="git-version">Version label</Label>
              <Input
                id="git-version"
                placeholder="defaults to ref"
                value={versionLabel}
                onChange={(e) => setVersionLabel(e.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="git-name">Display name</Label>
              <Input
                id="git-name"
                placeholder="auto-derived"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
              />
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            onClick={handleImport}
            disabled={!url || !ref || !urlValid || importMutation.isPending}
          >
            {importMutation.isPending ? (
              <><Loader2 className="h-4 w-4 mr-2 animate-spin" /> Importing…</>
            ) : (
              'Import Module'
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
