import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { SectionCard } from '@/components/ui/section-card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import {
  RefreshCw,
  Download,
  CheckCircle,
  AlertTriangle,
  Loader2,
  GitBranch,
  Clock
} from 'lucide-react';
import { notify } from '@/lib/notify';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

interface ModuleLibraryVersionInfo {
  synced_version: string | null;
  latest_version: string | null;
  update_available: boolean;
  last_synced_at: string | null;
  git_ref: string | null;
  error: string | null;
}

export default function ModuleLibraryVersion() {
  const queryClient = useQueryClient();
  const [lastChecked, setLastChecked] = useState<Date | null>(null);

  // Query for module library version info
  const { data: versionInfo, isLoading, refetch, isRefetching } = useQuery<ModuleLibraryVersionInfo>({
    queryKey: ['module-library-version'],
    queryFn: () => api.getModuleLibraryVersion(),
    staleTime: 60000,
  });

  // Mutation for triggering sync
  const syncMutation = useAppMutation({
    mutationFn: () => api.syncModuleLibrary(true),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['module-library-version'] });
      queryClient.invalidateQueries({ queryKey: ['module-library'] });
      const version = data.stats?.synced_version;
      notify.success(
        `Module library synced successfully. ${data.stats.total_modules} modules` +
        (version ? ` (${version})` : ''),
        undefined,
        { category: 'system' },
      );
    },
    onError: (error: Error) => {
      notify.error(`Sync failed: ${error.message}`);
    },
  });

  const handleCheckForUpdates = async () => {
    try {
      const result = await refetch();
      const data = result.data;
      setLastChecked(new Date());
      if (data?.error) {
        notify.error(`Version check failed: ${data.error}`);
      } else if (data?.update_available) {
        notify.success(`Module update available: ${data.latest_version}`, undefined, { category: 'system' });
      } else if (data?.synced_version) {
        notify.success(`Modules up to date (${data.synced_version})`, undefined, { category: 'system' });
      } else {
        notify.info('Module library not yet synced', undefined, { category: 'system' });
      }
    } catch {
      notify.error('Failed to check for module updates');
    }
  };

  const handleSync = () => {
    syncMutation.mutate();
  };

  const formatSyncTime = (isoString: string | null) => {
    if (!isoString) return null;
    try {
      const date = new Date(isoString + 'Z'); // Append Z for UTC
      return date.toLocaleString();
    } catch {
      return isoString;
    }
  };

  return (
    <SectionCard title="Module library">
      <p className="text-sm text-muted-foreground -mt-1 mb-4">
        Track and update your F5 BNK module library
      </p>
      <div className="space-y-4">
        {/* Version Info */}
        <div className="flex items-center justify-between p-4 rounded-lg border border-border bg-muted/50">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <GitBranch className="h-4 w-4 text-muted-foreground" />
              <span className="text-sm font-medium">
                {versionInfo?.git_ref ? `Branch: ${versionInfo.git_ref}` : 'Module Library'}
              </span>
            </div>
            {isLoading ? (
              <div className="flex items-center gap-2">
                <Loader2 className="h-4 w-4 animate-spin" />
                <span className="text-sm text-muted-foreground">Loading...</span>
              </div>
            ) : (
              <>
                <div className="flex items-center gap-2">
                  <span className="text-2xl font-bold">
                    {versionInfo?.synced_version ? `v${versionInfo.synced_version}` : 'Not synced'}
                  </span>
                  {versionInfo?.update_available && (
                    <Badge variant="warning">
                      Update Available
                    </Badge>
                  )}
                  {!versionInfo?.update_available && versionInfo?.synced_version && !versionInfo?.error && (
                    <Badge variant="success">
                      <CheckCircle className="h-3 w-3 mr-1" />
                      Up to date
                    </Badge>
                  )}
                </div>
                {versionInfo?.last_synced_at && (
                  <p className="text-xs text-muted-foreground">
                    Last synced: {formatSyncTime(versionInfo.last_synced_at)}
                  </p>
                )}
                {lastChecked && (
                  <p className="text-xs text-muted-foreground">
                    Last checked: {lastChecked.toLocaleTimeString()}
                  </p>
                )}
              </>
            )}
          </div>

          <div className="flex flex-col gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={handleCheckForUpdates}
              disabled={isRefetching || syncMutation.isPending}
            >
              {isRefetching ? (
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
              ) : (
                <RefreshCw className="h-4 w-4 mr-2" />
              )}
              {isRefetching ? 'Checking...' : 'Check for Updates'}
            </Button>
            <Button
              size="sm"
              onClick={handleSync}
              disabled={syncMutation.isPending || isRefetching}
            >
              {syncMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
              ) : (
                <Download className="h-4 w-4 mr-2" />
              )}
              {syncMutation.isPending ? 'Syncing...' : 'Sync Now'}
            </Button>
          </div>
        </div>

        {/* Update Available Alert */}
        {versionInfo?.update_available && !syncMutation.isPending && (
          <Alert className="border-l-2 border-l-warning">
            <Download className="h-4 w-4 text-warning" />
            <AlertTitle>
              Module Update Available: {versionInfo.latest_version}
            </AlertTitle>
            <AlertDescription>
              <p className="mt-1">
                A newer version of the module library is available.
                Click <strong>Sync Now</strong> to update.
              </p>
            </AlertDescription>
          </Alert>
        )}

        {/* Syncing Status */}
        {syncMutation.isPending && (
          <Alert className="border-l-2 border-l-info">
            <Loader2 className="h-4 w-4 animate-spin text-info" />
            <AlertTitle>
              Syncing Module Library
            </AlertTitle>
            <AlertDescription>
              <div className="flex items-center gap-2 text-sm mt-1">
                <Clock className="h-4 w-4" />
                <span>Pulling latest modules from repository...</span>
              </div>
            </AlertDescription>
          </Alert>
        )}

        {/* Error State */}
        {versionInfo?.error && (
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>Version Check Failed</AlertTitle>
            <AlertDescription>
              {versionInfo.error}
            </AlertDescription>
          </Alert>
        )}
      </div>
    </SectionCard>
  );
}
