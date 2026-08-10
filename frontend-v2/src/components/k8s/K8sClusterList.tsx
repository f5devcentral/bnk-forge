import { useState, useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
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
import { useProjectClusters, useDeleteCluster, useTestClusterConnection, useDetectEKSClusters, useRefreshClusterKubeconfig, useAllTunnels, useOpenTunnel, useCloseTunnel, useBatchConnectivity } from '@/hooks/useK8s';
import { ClusterStatusBadge } from '@/components/ui/ClusterStatusBadge';

import { ClusterConfigDialog } from './ClusterConfigDialog';
import { ClusterPrerequisitesDialog } from './ClusterPrerequisitesDialog';
import type { K8sCluster, ClusterConnectivityResult } from '@/types';
import { api } from '@/lib/api';
import { notify } from '@/lib/notify';
import {
  getPlatformProfileLabel,
  shouldShowDetectManagedClustersFromContext,
} from '@/lib/platform-context';
import type { PlatformProfile } from '@/types';
import {
  Plus,
  MoreVertical,
  Edit,
  Trash2,
  TestTube2,
  Settings,
  Cloud,
  MapPin,
  Server,
  Loader2,
  CheckCircle2,
  Search,
  RefreshCw,
  Terminal,
  Plug,
  Unplug,
} from 'lucide-react';

interface K8sClusterListProps {
  projectId: number;
  cloudProvider?: string;
  targetPlatformProfile?: PlatformProfile | null;
  projectSshCredentialId?: number;
}

const formatRelativeTime = (timestamp: string) => {
  const date = new Date(timestamp);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return 'just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 30) return `${diffDays}d ago`;
  return date.toLocaleDateString();
};

export function K8sClusterList({
  projectId,
  cloudProvider,
  targetPlatformProfile,
  projectSshCredentialId,
}: K8sClusterListProps) {
  const isManagedCloudProject = shouldShowDetectManagedClustersFromContext(targetPlatformProfile, cloudProvider);
  const { data: clusters, isLoading } = useProjectClusters(projectId);
  const deleteMutation = useDeleteCluster();
  const testConnectionMutation = useTestClusterConnection();
  const detectEKSMutation = useDetectEKSClusters();
  const refreshKubeconfigMutation = useRefreshClusterKubeconfig();
  const { data: tunnelData } = useAllTunnels();
  const openTunnelMutation = useOpenTunnel();
  const closeTunnelMutation = useCloseTunnel();
  const { data: connectivityData, isLoading: isConnectivityLoading } = useBatchConnectivity({
    enabled: !!clusters && clusters.length > 0,
  });

  // Build a map of cluster_id -> tunnel info for quick lookup
  const tunnelMap = new Map<number, { local_port: number; ssh_host: string; is_healthy: boolean; idle_seconds: number }>();
  for (const t of tunnelData?.tunnels ?? []) {
    tunnelMap.set(t.cluster_id, t);
  }

  // Build a map of cluster_id -> connectivity result
  const connectivityMap = new Map<number, ClusterConnectivityResult>();
  for (const r of connectivityData?.results ?? []) {
    connectivityMap.set(r.cluster_id, r);
  }

  const queryClient = useQueryClient();
  const handleRefreshConnectivity = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['k8s', 'clusters', 'connectivity'] });
  }, [queryClient]);

  const [configDialogOpen, setConfigDialogOpen] = useState(false);
  const [selectedCluster, setSelectedCluster] = useState<K8sCluster | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [clusterToDelete, setClusterToDelete] = useState<K8sCluster | null>(null);
  const [prereqDialogOpen, setPrereqDialogOpen] = useState(false);
  const [prereqCluster, setPrereqCluster] = useState<K8sCluster | null>(null);
  const [isDetectingSSH, setIsDetectingSSH] = useState(false);
  const clusterSummaryDescription = targetPlatformProfile === 'ocp'
    ? 'Monitor resources across OpenShift/OKD and other Kubernetes clusters. Runtime support semantics follow detected platform context.'
    : 'Monitor resources across EKS, AKS, GKE, ROKS, and on-premises clusters';

  const handleAddCluster = () => {
    setSelectedCluster(null);
    setConfigDialogOpen(true);
  };

  /** Detect new K8s clusters via SSH — probe and filter out already-registered contexts */
  const handleDetectSSHClusters = async () => {
    if (!projectSshCredentialId) return;
    setIsDetectingSSH(true);
    try {
      const result = await api.probeKubeconfig(projectSshCredentialId);
      if (!result.success) {
        notify.error(result.error || 'SSH probe failed', undefined, { category: 'credentials' });
        return;
      }
      if (!result.contexts || result.contexts.length === 0) {
        notify.info('No Kubernetes contexts found on remote host', undefined, { category: 'credentials' });
        return;
      }
      // Filter out contexts that are already registered as clusters
      const existingContexts = new Set((clusters || []).map(c => c.context));
      const newContexts = result.contexts.filter(c => !existingContexts.has(c.name));
      if (newContexts.length === 0) {
        notify.info(
          `Found ${result.contexts.length} context(s), all already registered`,
          'No new clusters to add',
          { category: 'credentials' },
        );
        return;
      }
      // Open the dialog — it will auto-probe and show contexts
      notify.success(`Found ${newContexts.length} new context(s) on ${result.host}`, undefined, { category: 'credentials' });
      setSelectedCluster(null);
      setConfigDialogOpen(true);
    } catch {
      notify.error('Failed to probe remote host', undefined, { category: 'credentials' });
    } finally {
      setIsDetectingSSH(false);
    }
  };

  const handleEditCluster = (cluster: K8sCluster) => {
    setSelectedCluster(cluster);
    setConfigDialogOpen(true);
  };

  const handleDeleteClick = (cluster: K8sCluster) => {
    setClusterToDelete(cluster);
    setDeleteDialogOpen(true);
  };

  const handleDeleteConfirm = async () => {
    if (clusterToDelete) {
      await deleteMutation.mutateAsync(clusterToDelete.id);
      setDeleteDialogOpen(false);
      setClusterToDelete(null);
    }
  };

  const handleTestConnection = async (cluster: K8sCluster) => {
    await testConnectionMutation.mutateAsync(cluster.id);
  };

  const handleRefreshKubeconfig = async (cluster: K8sCluster) => {
    await refreshKubeconfigMutation.mutateAsync(cluster.id);
  };

  const handleDetectManagedClusters = () => {
    detectEKSMutation.mutate(projectId);
  };

  const getCloudProviderIcon = (provider?: string) => {
    switch (provider) {
      case 'aws':
        return '☁️';
      case 'azure':
        return '⛅';
      case 'gcp':
        return '🌤️';
      case 'ibm':
      case 'roks':
        return '🟦';
      case 'on-prem':
        return '🏢';
      default:
        return '☁️';
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold">Kubernetes Clusters</h3>
          <p className="text-sm text-muted-foreground">
            {clusterSummaryDescription}
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleRefreshConnectivity}
            disabled={isConnectivityLoading}
            title="Re-check connectivity for all clusters"
          >
            <RefreshCw className={`mr-2 h-4 w-4 ${isConnectivityLoading ? 'animate-spin' : ''}`} />
            {isConnectivityLoading ? 'Checking...' : 'Refresh'}
          </Button>
          {isManagedCloudProject && (
            <Button
              variant="outline"
              onClick={handleDetectManagedClusters}
              disabled={detectEKSMutation.isPending}
            >
              {detectEKSMutation.isPending ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Detecting...
                </>
              ) : (
                <>
                  <Search className="mr-2 h-4 w-4" />
                  Detect Managed Clusters
                </>
              )}
            </Button>
          )}
          {!!projectSshCredentialId && (
            <Button
              variant="outline"
              onClick={handleDetectSSHClusters}
              disabled={isDetectingSSH}
            >
              {isDetectingSSH ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Probing...
                </>
              ) : (
                <>
                  <Search className="mr-2 h-4 w-4" />
                  Detect Clusters
                </>
              )}
            </Button>
          )}
          <Button onClick={handleAddCluster}>
            <Plus className="mr-2 h-4 w-4" />
            Add Cluster
          </Button>
        </div>
      </div>

      {/* Clusters List */}
      {!clusters || clusters.length === 0 ? (
        <Card className="p-8 text-center">
          <div className="flex flex-col items-center justify-center space-y-4">
            <Cloud className="h-12 w-12 text-muted-foreground" />
            <div>
              <h4 className="font-semibold">No clusters configured</h4>
              <p className="text-sm text-muted-foreground mt-1">
                {projectSshCredentialId
                  ? 'Click below to auto-discover clusters via SSH'
                  : 'Add a Kubernetes cluster to monitor deployed resources'}
              </p>
            </div>
            <div className="flex gap-2">
              {projectSshCredentialId && (
                <Button onClick={handleDetectSSHClusters} disabled={isDetectingSSH}>
                  {isDetectingSSH ? (
                    <>
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      Discovering...
                    </>
                  ) : (
                    <>
                      <Terminal className="mr-2 h-4 w-4" />
                      Discover Clusters via SSH
                    </>
                  )}
                </Button>
              )}
              <Button variant={projectSshCredentialId ? "outline" : "default"} onClick={handleAddCluster}>
                <Plus className="mr-2 h-4 w-4" />
                {projectSshCredentialId ? 'Add Manually' : 'Add Your First Cluster'}
              </Button>
            </div>
          </div>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {clusters.map((cluster) => (
            <Card key={cluster.id} className="p-4 space-y-3">
              {/* Header */}
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-2xl">{getCloudProviderIcon(cluster.cloud_provider)}</span>
                  <div>
                    <h4 className="font-semibold">{cluster.name}</h4>
                    {cluster.cloud_provider && (
                      <p className="text-xs text-muted-foreground capitalize">
                        {cluster.cloud_provider}
                      </p>
                    )}
                    <p className="text-xs text-muted-foreground">
                      Detected: {getPlatformProfileLabel(cluster.detected_platform_profile)}
                    </p>
                  </div>
                </div>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="ghost" size="sm">
                      <MoreVertical className="h-4 w-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem onClick={() => handleTestConnection(cluster)}>
                      <TestTube2 className="mr-2 h-4 w-4" />
                      Test Connection
                    </DropdownMenuItem>
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <DropdownMenuItem onClick={() => handleRefreshKubeconfig(cluster)}>
                            <RefreshCw className="mr-2 h-4 w-4" />
                            Refresh Kubeconfig
                          </DropdownMenuItem>
                        </TooltipTrigger>
                        <TooltipContent side="left" className="max-w-xs">
                            <p>
                            {cluster.cloud_provider === 'aws' || cluster.cloud_provider === 'eks'
                              ? 'Regenerate kubeconfig using current AWS credentials from Cloud Provider settings'
                              : cluster.cloud_provider === 'ibm' || cluster.cloud_provider === 'roks'
                                ? 'IBM-managed clusters use provider-based access; refresh is not typically required'
                              : cluster.ssh_tunnel_enabled
                                ? 'Re-fetch kubeconfig from the remote host via SSH'
                                : 'Re-fetch or regenerate the kubeconfig for this cluster'}
                          </p>
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                    <DropdownMenuItem onClick={() => handleEditCluster(cluster)}>
                      <Edit className="mr-2 h-4 w-4" />
                      Edit
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onClick={() => {
                        setPrereqCluster(cluster);
                        setPrereqDialogOpen(true);
                      }}
                    >
                      <Settings className="mr-2 h-4 w-4" />
                      Prerequisite checks
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onClick={() => handleDeleteClick(cluster)}
                      className="text-destructive"
                    >
                      <Trash2 className="mr-2 h-4 w-4" />
                      Delete
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>

              {/* Status + Connectivity */}
              <div className="flex items-center gap-2 flex-wrap">
                <ClusterStatusBadge cluster={cluster} />
                {cluster.version && (
                  <Badge variant="secondary" className="text-xs">
                    v{cluster.version}
                  </Badge>
                )}
                {targetPlatformProfile && targetPlatformProfile !== 'unknown' && cluster.detected_platform_profile && cluster.detected_platform_profile !== 'unknown' && targetPlatformProfile !== cluster.detected_platform_profile && cluster.platform_capabilities && Object.values(cluster.platform_capabilities).some((v) => v !== null && v !== undefined) && (
                  <Badge variant="warning" className="text-xs">
                    Target mismatch
                  </Badge>
                )}
              </div>

              {/* Discovery progress — visible while connectivity probe is running */}
              {(isConnectivityLoading || !connectivityMap.has(cluster.id)) && cluster.status === 'active' && (
                <div className="rounded-md border border-border bg-muted/50 px-3 py-2 space-y-1.5">
                  <div className="flex items-center gap-2 text-xs font-medium text-info">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    Discovering cluster...
                  </div>
                  <div className="grid grid-cols-3 gap-1">
                    {[
                      { label: 'Connectivity', done: connectivityMap.has(cluster.id) },
                      { label: 'Platform', done: !!cluster.detected_platform_profile && cluster.detected_platform_profile !== 'unknown' },
                      { label: 'Version', done: !!cluster.version },
                    ].map((step) => (
                      <div key={step.label} className="flex items-center gap-1 text-[10px]">
                        {step.done ? (
                          <CheckCircle2 className="h-3 w-3 text-success shrink-0" />
                        ) : (
                          <span className="h-3 w-3 rounded-full border border-muted-foreground/30 shrink-0" />
                        )}
                        <span className={step.done ? 'text-muted-foreground' : 'text-muted-foreground/60'}>{step.label}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Details */}
              <div className="space-y-1 text-sm">
                {cluster.region && (
                  <div className="flex items-center text-muted-foreground">
                    <MapPin className="mr-2 h-3 w-3" />
                    {cluster.region}
                  </div>
                )}
                <div className="flex items-center text-muted-foreground">
                  <Server className="mr-2 h-3 w-3" />
                  <span className="truncate text-xs">{cluster.context}</span>
                </div>
                {cluster.ssh_tunnel_enabled && (() => {
                  const tunnel = tunnelMap.get(cluster.id);
                  const isActive = !!tunnel;
                  const isHealthy = tunnel?.is_healthy ?? false;
                  const isTunnelLoading = openTunnelMutation.isPending || closeTunnelMutation.isPending;
                  const statusTextClass = isActive && isHealthy
                    ? 'text-success'
                    : isActive
                      ? 'text-warning'
                      : 'text-muted-foreground';
                  const dotClass = isActive && isHealthy
                    ? 'bg-success'
                    : isActive
                      ? 'bg-warning'
                      : 'bg-muted-foreground/40';
                  return (
                    <div className="flex items-center justify-between rounded-md px-2 py-1.5 -mx-1 border border-border bg-card">
                      <div className={`flex items-center ${statusTextClass}`}>
                        <span className={`mr-2 h-1.5 w-1.5 rounded-full ${dotClass} ${isActive && isHealthy ? 'animate-pulse' : ''}`} />
                        <Terminal className="mr-2 h-3 w-3" />
                        <span className="text-xs font-medium">
                          SSH {isActive ? (isHealthy ? 'Connected' : 'Unhealthy') : 'Disconnected'}
                          {tunnel ? ` :${tunnel.local_port}` : ''}
                        </span>
                      </div>
                      <TooltipProvider>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-6 w-6 p-0"
                              disabled={isTunnelLoading}
                              onClick={(e) => {
                                e.stopPropagation();
                                if (isActive) {
                                  closeTunnelMutation.mutate(cluster.id);
                                } else {
                                  openTunnelMutation.mutate(cluster.id);
                                }
                              }}
                            >
                              {isTunnelLoading ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                              ) : isActive ? (
                                <Unplug className="h-3.5 w-3.5 text-muted-foreground hover:text-destructive" />
                              ) : (
                                <Plug className="h-3.5 w-3.5 text-muted-foreground hover:text-success" />
                              )}
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>
                            {isActive ? 'Disconnect SSH tunnel' : 'Connect SSH tunnel'}
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    </div>
                  );
                })()}
              </div>

              {/* Footer */}
              {cluster.last_synced_at && (
                <div className="text-xs text-muted-foreground border-t pt-2">
                  Last synced {formatRelativeTime(cluster.last_synced_at)}
                </div>
              )}
            </Card>
          ))}
        </div>
      )}

      {/* Config Dialog */}
      <ClusterConfigDialog
        open={configDialogOpen}
        onOpenChange={setConfigDialogOpen}
        projectId={projectId}
        cluster={selectedCluster}
        projectSshCredentialId={projectSshCredentialId}
      />

      {/* Per-cluster prerequisite check selection */}
      <ClusterPrerequisitesDialog
        open={prereqDialogOpen}
        cluster={prereqCluster}
        onOpenChange={setPrereqDialogOpen}
      />

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Cluster?</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to remove cluster "{clusterToDelete?.name}"? This will not affect your actual Kubernetes cluster, only remove it from this project.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDeleteConfirm}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {deleteMutation.isPending ? (
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
    </div>
  );
}
