/**
 * Module Library config panel — where bnk-forge fetches the OpenTofu module
 * catalog from (git URL + branch + PAT).
 *
 * D-020: bnkhealth shape — SectionCard with eyebrow label, quieter actions.
 */
import { useEffect, useState } from 'react';
import ModuleLibraryVersion from '@/components/settings/ModuleLibraryVersion';
import { SectionCard } from '@/components/ui/section-card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import {
  CheckCircle,
  GitBranch,
  Key,
  Link,
  Loader2,
  Pencil,
  Save,
} from 'lucide-react';
import { useSettings } from '@/hooks/useSettings';

export default function ModuleLibraryPanel() {
  const { settings, isLoading, batchUpdateSettings, isUpdating } = useSettings();

  const [moduleLibraryPAT, setModuleLibraryPAT] = useState('');
  const [moduleLibraryURL, setModuleLibraryURL] = useState('');
  const [moduleLibraryRef, setModuleLibraryRef] = useState('');
  const [isEditing, setIsEditing] = useState(false);

  const [savedURL, setSavedURL] = useState('');
  const [savedRef, setSavedRef] = useState('');
  const [savedPATConfigured, setSavedPATConfigured] = useState(false);

  useEffect(() => {
    if (settings.module_library) {
      const patSetting = settings.module_library.find(
        (s: { key: string; value?: string }) => s.key === 'module_library.git_token',
      );
      const urlSetting = settings.module_library.find(
        (s: { key: string; value?: string }) => s.key === 'module_library.git_url',
      );
      const refSetting = settings.module_library.find(
        (s: { key: string; value?: string }) => s.key === 'module_library.git_ref',
      );
      setSavedURL(urlSetting?.value || '');
      setSavedRef(refSetting?.value || 'main');
      setSavedPATConfigured(!!patSetting?.value);
      if (patSetting?.value) setModuleLibraryPAT(patSetting.value);
      if (urlSetting?.value) setModuleLibraryURL(urlSetting.value);
      if (refSetting?.value) setModuleLibraryRef(refSetting.value);
    }
  }, [settings]);

  const handleSave = () => {
    const updates: Record<string, string> = {};
    if (moduleLibraryPAT) updates['module_library.git_token'] = moduleLibraryPAT;
    if (moduleLibraryURL) updates['module_library.git_url'] = moduleLibraryURL;
    if (moduleLibraryRef) updates['module_library.git_ref'] = moduleLibraryRef;
    batchUpdateSettings(updates);
    setIsEditing(false);
  };

  const handleCancel = () => {
    setModuleLibraryURL(savedURL);
    setModuleLibraryRef(savedRef);
    setModuleLibraryPAT('');
    setIsEditing(false);
  };

  const isConfigured = savedURL || savedPATConfigured;

  if (isLoading) {
    return (
      <div className="flex justify-center p-8">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <ModuleLibraryVersion />

      {!isEditing ? (
        <SectionCard>
          <div className="flex items-start justify-between gap-4 mb-5">
            <div>
              <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground/70">
                Module library
              </h3>
              <p className="text-sm text-muted-foreground mt-1">
                Git repository for OpenTofu modules.
              </p>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {isConfigured ? (
                <Badge variant="success" className="text-xs gap-1">
                  <CheckCircle className="h-3 w-3" />
                  Configured
                </Badge>
              ) : (
                <Badge variant="warning" className="text-xs">
                  Not configured
                </Badge>
              )}
              <Button variant="outline" size="sm" onClick={() => setIsEditing(true)}>
                <Pencil className="h-4 w-4 mr-1.5" />
                Edit
              </Button>
            </div>
          </div>

          {isConfigured ? (
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-sm">
                <Link className="h-4 w-4 text-muted-foreground" />
                <span className="text-muted-foreground w-20">Repository</span>
                <span className="font-mono text-foreground/80">{savedURL || 'Not set'}</span>
              </div>
              <div className="flex items-center gap-2 text-sm">
                <GitBranch className="h-4 w-4 text-muted-foreground" />
                <span className="text-muted-foreground w-20">Branch</span>
                <span className="font-mono text-foreground/80">{savedRef || 'main'}</span>
              </div>
              <div className="flex items-center gap-2 text-sm">
                <Key className="h-4 w-4 text-muted-foreground" />
                <span className="text-muted-foreground w-20">PAT</span>
                {savedPATConfigured ? (
                  <Badge variant="success" className="text-xs gap-1">
                    <CheckCircle className="h-3 w-3" />
                    Configured
                  </Badge>
                ) : (
                  <Badge variant="warning" className="text-xs">
                    Not set
                  </Badge>
                )}
              </div>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              Click Edit to configure your module library repository.
            </p>
          )}
        </SectionCard>
      ) : (
        <SectionCard>
          <div className="mb-5">
            <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground/70">
              Edit module library
            </h3>
            <p className="text-sm text-muted-foreground mt-1">
              Configure access to your module library repository.
            </p>
          </div>

          <div className="space-y-4">
            <div className="grid gap-2">
              <Label htmlFor="module-library-url">Repository URL</Label>
              <Input
                id="module-library-url"
                placeholder="https://github.com/your-org/modules.git"
                value={moduleLibraryURL}
                onChange={(e) => setModuleLibraryURL(e.target.value)}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="module-library-ref">Branch / tag</Label>
              <Input
                id="module-library-ref"
                placeholder="main"
                value={moduleLibraryRef}
                onChange={(e) => setModuleLibraryRef(e.target.value)}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="module-library-pat">
                Personal Access Token (PAT)
                {savedPATConfigured && (
                  <span className="text-xs text-muted-foreground ml-2">
                    (leave blank to keep existing)
                  </span>
                )}
              </Label>
              <Input
                id="module-library-pat"
                type="password"
                placeholder={savedPATConfigured ? '••••••••••••••••' : 'ghp_xxxxxxxxxxxxx'}
                value={moduleLibraryPAT}
                onChange={(e) => setModuleLibraryPAT(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                GitHub PAT with repo read permissions (encrypted in database). Not needed for
                public repos.
              </p>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" onClick={handleCancel}>
                Cancel
              </Button>
              <Button onClick={handleSave} disabled={isUpdating}>
                {isUpdating ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    Saving…
                  </>
                ) : (
                  <>
                    <Save className="h-4 w-4 mr-2" />
                    Save
                  </>
                )}
              </Button>
            </div>
          </div>
        </SectionCard>
      )}
    </div>
  );
}
