/**
 * Helm Repositories config panel — shows the list of configured repositories
 * with quick stats, and offers a Manage dialog for add/remove flows.
 *
 * Previously lived on /system?tab=helm-repos; moved under /catalog?tab=helm-repos.
 */
import { lazy, Suspense, useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { EmptyState } from '@/components/ui/empty-state';
import { Library, Package, RefreshCcw, ExternalLink } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useHelmRepositories, useUpdateHelmRepositories } from '@/hooks/useHelm';
import { notify, notifyError } from '@/lib/notify';
import type { HelmRepository } from '@/types';

const RepositoryManagementDialog = lazy(() =>
  import('@/components/helm/RepositoryManagementDialog').then((m) => ({ default: m.RepositoryManagementDialog })),
);

interface HelmRepositoryWithStats extends HelmRepository {
  charts_count?: number;
  last_update?: string;
}

export default function HelmReposPanel() {
  const [showDialog, setShowDialog] = useState(false);

  const { data: reposData, isLoading } = useHelmRepositories();
  const updateMutation = useUpdateHelmRepositories();

  const repositories: HelmRepositoryWithStats[] = reposData?.repositories || [];

  const handleUpdateAll = async () => {
    try {
      await updateMutation.mutateAsync();
      notify.success('Successfully updated all repositories', undefined, { category: 'system' });
    } catch (error) {
      notifyError(error);
    }
  };

  return (
    <div className="space-y-4">
      <Card className="bg-card">
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-muted">
              <Package className="h-5 w-5 text-muted-foreground" />
            </div>
            <div>
              <CardTitle className="text-lg">Helm Repositories</CardTitle>
              <CardDescription>
                Chart sources available when installing Helm charts on Kubernetes clusters.
              </CardDescription>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={handleUpdateAll}
              disabled={updateMutation.isPending || repositories.length === 0}
            >
              <RefreshCcw className={cn('w-4 h-4 mr-2', updateMutation.isPending && 'animate-spin')} />
              Update All
            </Button>
            <Button onClick={() => setShowDialog(true)}>
              <Package className="h-4 w-4 mr-2" />
              Manage Repositories
            </Button>
          </div>
        </CardHeader>
        <CardContent className="pt-4">
          <div className="border border-border rounded-lg overflow-hidden">
            {isLoading ? (
              <div className="p-4 space-y-3">
                {[...Array(3)].map((_, i) => (
                  <div key={i} className="flex items-start justify-between">
                    <div className="flex-1">
                      <Skeleton className="h-4 w-32 mb-2" />
                      <Skeleton className="h-3 w-full" />
                    </div>
                    <Skeleton className="w-20 h-8" />
                  </div>
                ))}
              </div>
            ) : repositories.length === 0 ? (
              <div className="p-8">
                <EmptyState
                  icon={Library}
                  title="No Repositories"
                  description="Click 'Manage Repositories' to add a Helm chart source."
                />
              </div>
            ) : (
              <div className="divide-y divide-border">
                {repositories.map((repo) => (
                  <div
                    key={repo.name}
                    className="p-4 flex items-start justify-between hover:bg-muted/50"
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <h4 className="font-semibold text-sm text-foreground">
                          {repo.name}
                        </h4>
                        {typeof repo.charts_count === 'number' && (
                          <Badge variant="outline" className="text-[10px]">
                            {repo.charts_count} charts
                          </Badge>
                        )}
                      </div>
                      <p className="text-xs truncate text-muted-foreground">
                        {repo.url}
                      </p>
                      {repo.last_update && (
                        <p className="text-[10px] mt-1 text-muted-foreground">
                          Updated: {new Date(repo.last_update).toLocaleDateString()}
                        </p>
                      )}
                    </div>
                    {repo.url && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => window.open(repo.url, '_blank')}
                        className="ml-4"
                        aria-label={`Open ${repo.name} URL`}
                      >
                        <ExternalLink className="w-4 h-4" />
                      </Button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </CardContent>
      </Card>
      <Suspense fallback={null}>
        <RepositoryManagementDialog open={showDialog} onOpenChange={setShowDialog} />
      </Suspense>
    </div>
  );
}
