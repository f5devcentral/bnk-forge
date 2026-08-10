import { useMemo, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useChangeModuleVersion, useModuleLibrary } from '@/hooks/useModules';
import type { ProjectModule } from '@/types';
import { Info, Loader2 } from 'lucide-react';

interface ChangeModuleVersionDialogProps {
  module: ProjectModule;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * D-033: re-pin a project module to a different immutable catalog version of
 * its module path. Nothing is re-deployed by the swap — the new version's
 * manifest takes effect on the module's next plan/apply/destroy.
 */
export function ChangeModuleVersionDialog({
  module,
  open,
  onOpenChange,
}: ChangeModuleVersionDialogProps) {
  const currentPath = module.library_module?.path;
  const currentVersion = module.library_module?.version;
  const [targetVersion, setTargetVersion] = useState<string>('');
  const changeVersion = useChangeModuleVersion();
  const { data: libraryModules, isLoading } = useModuleLibrary();

  const currentSourceId = module.library_module?.module_source_id;
  const versionOptions = useMemo(() => {
    if (!currentPath) return [];
    return (libraryModules || [])
      .filter(
        (m) =>
          m.path === currentPath &&
          m.version &&
          // Re-pins are source-scoped (the backend enforces this): another
          // source's same-path rows are different content, not upgrades.
          (currentSourceId === undefined || m.module_source_id === currentSourceId),
      )
      .map((m) => ({ version: m.version, isLatest: m.is_latest !== false }));
  }, [libraryModules, currentPath, currentSourceId]);

  // Count only versions we could actually re-pin to: the current version may
  // be absent from the (active-only) options — e.g. its row was deactivated —
  // and re-pinning to the one remaining active version is exactly the
  // recovery path this dialog exists for.
  const selectableVersions = versionOptions.filter((o) => o.version !== currentVersion);

  const handleConfirm = () => {
    if (!targetVersion) return;
    changeVersion.mutate(
      { moduleId: module.id, targetVersion },
      {
        onSuccess: () => {
          onOpenChange(false);
          setTargetVersion('');
        },
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Change Module Version</DialogTitle>
          <DialogDescription>
            {currentPath ? (
              <>
                <span className="font-mono">{currentPath}</span> is pinned to{' '}
                <span className="font-mono">{currentVersion || 'unknown'}</span>.
              </>
            ) : (
              'This module has no catalog path; version cannot be changed.'
            )}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          {isLoading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading catalog versions…
            </div>
          ) : selectableVersions.length === 0 ? (
            <Alert>
              <Info className="h-4 w-4" />
              <AlertDescription>
                No other versions of this module are in the active catalog. Sync the
                module source to pick up newer releases.
              </AlertDescription>
            </Alert>
          ) : (
            <Select value={targetVersion} onValueChange={setTargetVersion}>
              <SelectTrigger>
                <SelectValue placeholder="Select target version" />
              </SelectTrigger>
              <SelectContent>
                {versionOptions.map((opt) => (
                  <SelectItem
                    key={opt.version}
                    value={opt.version}
                    disabled={opt.version === currentVersion}
                  >
                    <span className="font-mono">{opt.version}</span>
                    {opt.isLatest ? (
                      <Badge variant="info" className="ml-2 text-xs">
                        latest
                      </Badge>
                    ) : null}
                    {opt.version === currentVersion ? ' (current)' : ''}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}

          <Alert>
            <Info className="h-4 w-4" />
            <AlertDescription>
              The swap only re-pins the module. The new version takes effect on the
              next plan, apply, or destroy.
            </AlertDescription>
          </Alert>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={handleConfirm}
            disabled={!targetVersion || targetVersion === currentVersion || changeVersion.isPending}
          >
            {changeVersion.isPending ? (
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
            ) : null}
            Change Version
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
