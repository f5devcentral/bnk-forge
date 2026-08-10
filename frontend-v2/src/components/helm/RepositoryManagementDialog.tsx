/**
 * Repository Management Dialog
 *
 * Add, remove, and update Helm chart repositories
 */

import { useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
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
import { Skeleton } from '@/components/ui/skeleton';
import { EmptyState } from '@/components/ui/empty-state';
import {
  useHelmRepositories,
  useAddHelmRepository,
  useRemoveHelmRepository,
  useUpdateHelmRepositories,
} from '@/hooks/useHelm';
import { notify, notifyError } from '@/lib/notify';
import { Library, Loader2, Plus, RefreshCcw, Trash2, ExternalLink } from 'lucide-react';
import { SectionCard } from '@/components/ui/section-card';
import { cn } from '@/lib/utils';
import type { HelmRepository } from '@/types';

// Extended HelmRepository from API (includes optional extra fields)
interface HelmRepositoryExtended extends HelmRepository {
  charts_count?: number;
  last_update?: string;
}

// Popular Helm repositories for quick add
const POPULAR_HELM_REPOS = [
  { name: 'bitnami', url: 'https://charts.bitnami.com/bitnami', description: 'Bitnami Application Catalog' },
  { name: 'ingress-nginx', url: 'https://kubernetes.github.io/ingress-nginx', description: 'NGINX Ingress Controller' },
  { name: 'jetstack', url: 'https://charts.jetstack.io', description: 'cert-manager and more' },
  { name: 'prometheus-community', url: 'https://prometheus-community.github.io/helm-charts', description: 'Prometheus, Alertmanager, Grafana stack' },
  { name: 'grafana', url: 'https://grafana.github.io/helm-charts', description: 'Grafana, Loki, Tempo' },
  { name: 'elastic', url: 'https://helm.elastic.co', description: 'Elasticsearch, Kibana, Filebeat' },
  { name: 'hashicorp', url: 'https://helm.releases.hashicorp.com', description: 'Vault, Consul, Terraform' },
  { name: 'external-secrets', url: 'https://charts.external-secrets.io', description: 'External Secrets Operator' },
  { name: 'argo', url: 'https://argoproj.github.io/argo-helm', description: 'Argo CD, Workflows, Rollouts' },
  { name: 'traefik', url: 'https://traefik.github.io/charts', description: 'Traefik Proxy' },
  { name: 'metallb', url: 'https://metallb.github.io/metallb', description: 'MetalLB Load Balancer' },
  { name: 'aws-eks', url: 'https://aws.github.io/eks-charts', description: 'AWS Load Balancer Controller, etc.' },
];

interface RepositoryManagementDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function RepositoryManagementDialog({ open, onOpenChange }: RepositoryManagementDialogProps) {
  const [showAddForm, setShowAddForm] = useState(false);
  const [repoToDelete, setRepoToDelete] = useState<string | null>(null);

  // Form state
  const [repoName, setRepoName] = useState('');
  const [repoUrl, setRepoUrl] = useState('');

  // Queries and mutations
  const { data: reposData, isLoading } = useHelmRepositories();
  const addMutation = useAddHelmRepository();
  const removeMutation = useRemoveHelmRepository();
  const updateMutation = useUpdateHelmRepositories();

  const repositories = reposData?.repositories || [];

  // Handle add repository
  const handleAddRepository = async () => {
    if (!repoName.trim() || !repoUrl.trim()) {
      notify.error('Repository name and URL are required');
      return;
    }

    try {
      await addMutation.mutateAsync({
        name: repoName,
        url: repoUrl,
      });

      setRepoName('');
      setRepoUrl('');
      setShowAddForm(false);
    } catch (error) {
      notifyError(error);
    }
  };

  // Handle remove repository
  const handleRemoveRepository = async (name: string) => {
    try {
      await removeMutation.mutateAsync(name);

      notify.success(`Successfully removed repository ${name}`, undefined, { category: 'system' });
      setRepoToDelete(null);
    } catch (error) {
      notifyError(error);
    }
  };

  // Handle update repositories
  const handleUpdateRepositories = async () => {
    try {
      await updateMutation.mutateAsync();

      notify.success('Successfully updated all repositories', undefined, { category: 'system' });
    } catch (error) {
      notifyError(error);
    }
  };

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-3xl max-h-[80vh]">
          <DialogHeader>
            <DialogTitle className="text-xl font-bold">
              Helm Repositories
            </DialogTitle>
            <DialogDescription>
              Manage Helm chart repositories for installing packages
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            {/* Action Buttons */}
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                onClick={() => setShowAddForm(!showAddForm)}
                disabled={addMutation.isPending}
              >
                <Plus className="w-4 h-4 mr-2" />
                Add Repository
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={handleUpdateRepositories}
                disabled={updateMutation.isPending}
              >
                <RefreshCcw className={cn('w-4 h-4 mr-2', updateMutation.isPending && 'animate-spin')} />
                Update All
              </Button>
            </div>

            {/* Add Repository Form */}
            {showAddForm && (
              <SectionCard title="Add Repository" compact className="bg-muted/30">
              <div className="space-y-4">
                {/* Popular Repos Quick Add */}
                <div>
                  <Label className="text-xs text-muted-foreground mb-2 block">Quick Add Popular Repository</Label>
                  <div className="flex flex-wrap gap-1.5">
                    {POPULAR_HELM_REPOS.filter(
                      (pr) => !repositories.some((r: HelmRepositoryExtended) => r.name === pr.name)
                    ).slice(0, 8).map((pr) => (
                      <Button
                        key={pr.name}
                        variant="outline"
                        size="sm"
                        className="text-xs h-7"
                        onClick={() => {
                          setRepoName(pr.name);
                          setRepoUrl(pr.url);
                        }}
                        title={pr.description}
                      >
                        {pr.name}
                      </Button>
                    ))}
                  </div>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="repo-name">Repository Name *</Label>
                  <Input
                    id="repo-name"
                    placeholder="bitnami"
                    value={repoName}
                    onChange={(e) => setRepoName(e.target.value)}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="repo-url">Repository URL *</Label>
                  <Input
                    id="repo-url"
                    placeholder="https://charts.bitnami.com/bitnami"
                    value={repoUrl}
                    onChange={(e) => setRepoUrl(e.target.value)}
                  />
                </div>

                <div className="flex justify-end gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      setShowAddForm(false);
                      setRepoName('');
                      setRepoUrl('');
                    }}
                    disabled={addMutation.isPending}
                  >
                    Cancel
                  </Button>
                  <Button
                    size="sm"
                    onClick={handleAddRepository}
                    disabled={addMutation.isPending || !repoName.trim() || !repoUrl.trim()}
                  >
                    {addMutation.isPending ? (
                      <>
                        <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                        Adding...
                      </>
                    ) : (
                      'Add Repository'
                    )}
                  </Button>
                </div>
              </div>
              </SectionCard>
            )}

            {/* Repository List */}
            <div className="border border-border rounded-lg overflow-hidden">
              {isLoading ? (
                <div className="p-4 space-y-3">
                  {[...Array(3)].map((_, i) => (
                    <RepositoryItemSkeleton key={i} />
                  ))}
                </div>
              ) : repositories.length === 0 ? (
                <div className="p-8">
                  <EmptyState
                    icon={Library}
                    title="No Repositories"
                    description="Add a Helm repository to browse and install charts"
                  />
                </div>
              ) : (
                <div className="divide-y divide-border">
                  {repositories.map((repo: HelmRepositoryExtended) => (
                    <div
                      key={repo.name}
                      className="p-4 flex items-start justify-between hover:bg-muted/50"
                    >
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <h4 className="font-semibold text-sm">{repo.name}</h4>
                          <Badge variant="outline" className="text-[10px]">
                            {repo.charts_count || 0} charts
                          </Badge>
                        </div>
                        <p className="text-xs text-muted-foreground truncate">
                          {repo.url}
                        </p>
                        {repo.last_update && (
                          <p className="text-[10px] text-muted-foreground mt-1">
                            Updated: {new Date(repo.last_update).toLocaleDateString()}
                          </p>
                        )}
                      </div>

                      <div className="flex items-center gap-2 ml-4">
                        {repo.url && (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => window.open(repo.url, '_blank')}
                          >
                            <ExternalLink className="w-4 h-4" />
                          </Button>
                        )}
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setRepoToDelete(repo.name)}
                          disabled={removeMutation.isPending}
                          className="text-destructive hover:text-destructive hover:bg-destructive/10"
                        >
                          <Trash2 className="w-4 h-4" />
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={!!repoToDelete} onOpenChange={() => setRepoToDelete(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove Repository?</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to remove the repository{' '}
              <code className="font-mono font-semibold">{repoToDelete}</code>?
              You won't be able to install charts from this repository anymore.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={removeMutation.isPending}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={() => repoToDelete && handleRemoveRepository(repoToDelete)}
              disabled={removeMutation.isPending}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {removeMutation.isPending ? 'Removing...' : 'Remove'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

function RepositoryItemSkeleton() {
  return (
    <div className="flex items-start justify-between">
      <div className="flex-1">
        <Skeleton className="h-4 w-32 mb-2" />
        <Skeleton className="h-3 w-full" />
      </div>
      <Skeleton className="w-20 h-8" />
    </div>
  );
}
