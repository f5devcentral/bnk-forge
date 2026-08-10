/**
 * Manage Helm Repositories Dialog
 *
 * Add, view, and remove Helm chart repositories
 */

import { useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useHelmRepositories, useAddHelmRepository, useRemoveHelmRepository, useUpdateHelmRepositories } from '@/hooks/useHelm';
import { notify, notifyError } from '@/lib/notify';
import { Globe, Plus, RefreshCcw, Loader2, Lock, Trash2 } from 'lucide-react';
import { SectionCard } from '@/components/ui/section-card';
import type { HelmRepository } from '@/types';

interface ManageRepositoriesDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

// Popular public repositories
const popularRepos = [
  {
    name: 'bitnami',
    url: 'https://charts.bitnami.com/bitnami',
    description: 'Popular production-ready application charts',
  },
  {
    name: 'ingress-nginx',
    url: 'https://kubernetes.github.io/ingress-nginx',
    description: 'NGINX Ingress Controller',
  },
  {
    name: 'jetstack',
    url: 'https://charts.jetstack.io',
    description: 'Cert-manager and other Jetstack tools',
  },
  {
    name: 'prometheus-community',
    url: 'https://prometheus-community.github.io/helm-charts',
    description: 'Prometheus monitoring stack',
  },
  {
    name: 'grafana',
    url: 'https://grafana.github.io/helm-charts',
    description: 'Grafana dashboards and Loki',
  },
];

export function ManageRepositoriesDialog({ open, onOpenChange }: ManageRepositoriesDialogProps) {
  const { data: reposData, isLoading: reposLoading } = useHelmRepositories({ enabled: open });
  const addRepoMutation = useAddHelmRepository();
  const removeRepoMutation = useRemoveHelmRepository();
  const updateReposMutation = useUpdateHelmRepositories();

  const repositories = reposData?.repositories || [];

  // Add custom repository form
  const [customName, setCustomName] = useState('');
  const [customUrl, setCustomUrl] = useState('');
  const [customUsername, setCustomUsername] = useState('');
  const [customPassword, setCustomPassword] = useState('');
  const [showAuth, setShowAuth] = useState(false);

  const handleAddPopularRepo = async (repo: typeof popularRepos[0]) => {
    // Check if already added
    if (repositories.some((r: HelmRepository) => r.name === repo.name)) {
      notify.info(`Repository "${repo.name}" is already configured`, undefined, { category: 'system' });
      return;
    }

    try {
      await addRepoMutation.mutateAsync({
        name: repo.name,
        url: repo.url,
      });
    } catch (error) {
      notifyError(error);
    }
  };

  const handleAddCustomRepo = async () => {
    if (!customName.trim() || !customUrl.trim()) {
      notify.error('Repository name and URL are required');
      return;
    }

    try {
      await addRepoMutation.mutateAsync({
        name: customName,
        url: customUrl,
        username: customUsername || undefined,
        password: customPassword || undefined,
      });

      // Reset form
      setCustomName('');
      setCustomUrl('');
      setCustomUsername('');
      setCustomPassword('');
      setShowAuth(false);
    } catch (error) {
      notifyError(error);
    }
  };

  const handleRemoveRepo = async (repoName: string) => {
    const confirmed = window.confirm(
      `Are you sure you want to remove the repository "${repoName}"?\n\nThis will remove it from your Helm configuration. Charts from this repository will no longer be searchable.`
    );

    if (!confirmed) return;

    try {
      await removeRepoMutation.mutateAsync(repoName);
      notify.success(`Successfully removed ${repoName}`, undefined, { category: 'system' });
    } catch (error) {
      notifyError(error);
    }
  };

  const handleUpdateRepos = async () => {
    try {
      await updateReposMutation.mutateAsync();
      notify.success('Repository indexes updated', undefined, { category: 'system' });
    } catch (error) {
      notifyError(error);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="text-xl font-bold">
            Manage Helm Repositories
          </DialogTitle>
          <DialogDescription>
            Add chart repositories to search and install applications. Public repositories require no authentication, while private repositories need credentials.
          </DialogDescription>
        </DialogHeader>

        <Tabs defaultValue="configured" className="mt-4">
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="configured">
              Configured ({repositories.length})
            </TabsTrigger>
            <TabsTrigger value="popular">Popular</TabsTrigger>
            <TabsTrigger value="custom">Add Custom</TabsTrigger>
          </TabsList>

          {/* Configured Repositories */}
          <TabsContent value="configured" className="space-y-3 mt-4">
            {reposLoading ? (
              <div className="flex items-center justify-center py-8">
                <RefreshCcw className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : repositories.length === 0 ? (
              <div className="text-center py-8 text-muted-foreground">
                <Globe className="h-12 w-12 mx-auto mb-3 text-muted-foreground/60" />
                <p className="text-sm mb-2">No repositories configured</p>
                <p className="text-xs">Add a popular repository or your own custom repository to get started.</p>
              </div>
            ) : (
              <>
                <div className="flex justify-end mb-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={handleUpdateRepos}
                    disabled={updateReposMutation.isPending}
                  >
                    {updateReposMutation.isPending ? (
                      <>
                        <Loader2 className="h-3.5 w-3.5 mr-2 animate-spin" />
                        Updating...
                      </>
                    ) : (
                      <>
                        <RefreshCcw className="h-3.5 w-3.5 mr-2" />
                        Update All
                      </>
                    )}
                  </Button>
                </div>

                {repositories.map((repo: HelmRepository) => (
                  <div
                    key={repo.name}
                    className="p-3 rounded-lg border border-border bg-card"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <code className="text-sm font-mono font-semibold truncate">{repo.name}</code>
                          <Badge variant="outline" className="text-[10px] flex-shrink-0">
                            {repo.url.includes('https://') ? 'HTTPS' : 'HTTP'}
                          </Badge>
                        </div>
                        <p className="text-xs truncate text-muted-foreground">
                          {repo.url}
                        </p>
                      </div>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-8 w-8 p-0 text-destructive hover:text-destructive hover:bg-destructive/10 flex-shrink-0"
                        onClick={() => handleRemoveRepo(repo.name)}
                        disabled={removeRepoMutation.isPending}
                        title="Remove repository"
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>
                ))}
              </>
            )}
          </TabsContent>

          {/* Popular Repositories */}
          <TabsContent value="popular" className="space-y-2 mt-4">
            {popularRepos.map((repo) => {
              const isAdded = repositories.some((r: HelmRepository) => r.name === repo.name);

              return (
                <div
                  key={repo.name}
                  className="p-3 rounded-lg border border-border bg-card"
                >
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="font-semibold text-sm">{repo.name}</span>
                        {isAdded && (
                          <Badge variant="success" className="text-[10px]">
                            Added
                          </Badge>
                        )}
                      </div>
                      <p className="text-xs mb-2 text-muted-foreground">
                        {repo.description}
                      </p>
                      <code className="text-xs text-muted-foreground">
                        {repo.url}
                      </code>
                    </div>
                    <Button
                      size="sm"
                      variant={isAdded ? 'outline' : 'default'}
                      onClick={() => handleAddPopularRepo(repo)}
                      disabled={isAdded || addRepoMutation.isPending}
                    >
                      {isAdded ? (
                        'Added'
                      ) : addRepoMutation.isPending ? (
                        <>
                          <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                          Adding...
                        </>
                      ) : (
                        <>
                          <Plus className="h-3.5 w-3.5 mr-1.5" />
                          Add
                        </>
                      )}
                    </Button>
                  </div>
                </div>
              );
            })}
          </TabsContent>

          {/* Add Custom Repository */}
          <TabsContent value="custom" className="space-y-4 mt-4">
            <SectionCard title="Repository Details" compact>
              <div className="space-y-4">
              {/* Repository Name */}
              <div className="space-y-2">
                <Label htmlFor="repo-name">Repository Name *</Label>
                <Input
                  id="repo-name"
                  placeholder="my-repo"
                  value={customName}
                  onChange={(e) => setCustomName(e.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  Short name to reference this repository
                </p>
              </div>

              {/* Repository URL */}
              <div className="space-y-2">
                <Label htmlFor="repo-url">Repository URL *</Label>
                <Input
                  id="repo-url"
                  placeholder="https://charts.example.com"
                  value={customUrl}
                  onChange={(e) => setCustomUrl(e.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  HTTPS URL of the Helm chart repository
                </p>
              </div>

              {/* Authentication Toggle */}
              <div className="flex items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setShowAuth(!showAuth)}
                  className="text-xs"
                >
                  <Lock className="h-3.5 w-3.5 mr-1.5" />
                  {showAuth ? 'Hide' : 'Add'} Authentication
                </Button>
                {showAuth && (
                  <span className="text-xs text-muted-foreground">
                    Required for private repositories
                  </span>
                )}
              </div>

              {/* Authentication Fields */}
              {showAuth && (
                <>
                  <div className="space-y-2">
                    <Label htmlFor="repo-username">Username</Label>
                    <Input
                      id="repo-username"
                      placeholder="username"
                      value={customUsername}
                      onChange={(e) => setCustomUsername(e.target.value)}
                    />
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="repo-password">Password / Token</Label>
                    <Input
                      id="repo-password"
                      type="password"
                      placeholder="••••••••"
                      value={customPassword}
                      onChange={(e) => setCustomPassword(e.target.value)}
                    />
                    <p className="text-xs text-muted-foreground">
                      Use a personal access token for GitHub/GitLab repositories
                    </p>
                  </div>
                </>
              )}

              {/* Add Button */}
              <div className="flex justify-end pt-2">
                <Button
                  onClick={handleAddCustomRepo}
                  disabled={addRepoMutation.isPending || !customName.trim() || !customUrl.trim()}
                >
                  {addRepoMutation.isPending ? (
                    <>
                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                      Adding...
                    </>
                  ) : (
                    <>
                      <Plus className="h-4 w-4 mr-2" />
                      Add Repository
                    </>
                  )}
                </Button>
              </div>
              </div>
            </SectionCard>
          </TabsContent>
        </Tabs>

        <div className="flex justify-end pt-4 border-t border-border">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
