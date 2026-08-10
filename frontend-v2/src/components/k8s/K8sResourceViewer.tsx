import { useState } from 'react';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  useClusterResources,
  useClusterNamespaces,
  useK8sResourceTypes,
  useCreateK8sResource,
  useUpdateK8sResource,
  useDeleteK8sResource,
  useScaleDeployment,
  useRestartPod,
} from '@/hooks/useK8s';
import { ResourceCreateDialog } from './ResourceCreateDialog';
import { ResourceEditDialog } from './ResourceEditDialog';
import { ResourceDeleteDialog } from './ResourceDeleteDialog';
import { ScaleDeploymentDialog } from './ScaleDeploymentDialog';
import { PodLogsViewer } from './PodLogsViewer';
import { ResourceDescribeViewer } from './ResourceDescribeViewer';
import { ResourceEventsViewer } from './ResourceEventsViewer';
import { ResourceMetricsViewer } from './ResourceMetricsViewer';
import { DeploymentRolloutManager } from './DeploymentRolloutManager';
import { NodeOperationsDialog } from './NodeOperationsDialog';
import type { K8sCluster, K8sResource, K8sContainerStatus, K8sServicePort, K8sCondition, K8sLoadBalancerIngress } from '@/types';
import {
  Loader2,
  RefreshCw,
  Search,
  Eye,
  Box,
  Database,
  Network,
  Shield,
  MoreVertical,
  Plus,
  Edit,
  Trash2,
  Scale,
  FileText,
  RotateCw,
  FileSearch,
  Activity,
  BarChart3,
  Columns,
  GitBranch,
  Server,
} from 'lucide-react';

interface K8sResourceViewerProps {
  cluster: K8sCluster;
  defaultResourceType?: string;
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

const getExtendedInfo = (resource: K8sResource): string[] => {
  const info: string[] = [];
  const kind = resource.kind;

  // Pod-specific extended info
  if (kind === 'Pod') {
    if (resource.spec?.nodeName) {
      info.push(`Node: ${resource.spec.nodeName}`);
    }
    if (resource.status?.podIP) {
      info.push(`IP: ${resource.status.podIP}`);
    }
    if (resource.status?.hostIP) {
      info.push(`Host IP: ${resource.status.hostIP}`);
    }
    if (resource.status?.containerStatuses) {
      const restarts = resource.status.containerStatuses.reduce(
        (sum: number, cs: K8sContainerStatus) => sum + (cs.restartCount || 0),
        0
      );
      info.push(`Restarts: ${restarts}`);
    }
  }

  // Service-specific extended info
  if (kind === 'Service') {
    if (resource.spec?.type) {
      info.push(`Type: ${resource.spec.type}`);
    }
    if (resource.spec?.clusterIP) {
      info.push(`Cluster IP: ${resource.spec.clusterIP}`);
    }
    if (resource.status?.loadBalancer?.ingress?.[0]) {
      const ingress = resource.status.loadBalancer.ingress[0];
      info.push(`External IP: ${ingress.ip || ingress.hostname || 'Pending'}`);
    }
    if (resource.spec?.ports) {
      const ports = resource.spec.ports.map((p: K8sServicePort) =>
        `${p.port}${p.targetPort ? `:${p.targetPort}` : ''}/${p.protocol || 'TCP'}`
      ).join(', ');
      info.push(`Ports: ${ports}`);
    }
  }

  // Node-specific extended info
  if (kind === 'Node') {
    if (resource.metadata?.labels?.['kubernetes.io/role']) {
      info.push(`Role: ${resource.metadata.labels['kubernetes.io/role']}`);
    }
    if (resource.status?.nodeInfo?.kubeletVersion) {
      info.push(`Version: ${resource.status.nodeInfo.kubeletVersion}`);
    }
    if (resource.status?.nodeInfo?.osImage) {
      info.push(`OS: ${resource.status.nodeInfo.osImage}`);
    }
    if (resource.status?.conditions) {
      const readyCondition = resource.status.conditions.find((c: K8sCondition) => c.type === 'Ready');
      if (readyCondition) {
        info.push(`Status: ${readyCondition.status === 'True' ? 'Ready' : 'NotReady'}`);
      }
    }
  }

  // Deployment-specific extended info
  if (kind === 'Deployment') {
    if (resource.spec?.replicas !== undefined) {
      const ready = resource.status?.readyReplicas || 0;
      const desired = resource.spec.replicas;
      info.push(`Replicas: ${ready}/${desired}`);
    }
    if (resource.spec?.strategy?.type) {
      info.push(`Strategy: ${resource.spec.strategy.type}`);
    }
  }

  // ReplicaSet-specific extended info
  if (kind === 'ReplicaSet') {
    if (resource.spec?.replicas !== undefined) {
      const ready = resource.status?.readyReplicas || 0;
      const desired = resource.spec.replicas;
      info.push(`Replicas: ${ready}/${desired}`);
    }
  }

  // StatefulSet-specific extended info
  if (kind === 'StatefulSet') {
    if (resource.spec?.replicas !== undefined) {
      const ready = resource.status?.readyReplicas || 0;
      const desired = resource.spec.replicas;
      info.push(`Replicas: ${ready}/${desired}`);
    }
  }

  // DaemonSet-specific extended info
  if (kind === 'DaemonSet') {
    if (resource.status) {
      const desired = resource.status.desiredNumberScheduled || 0;
      const ready = resource.status.numberReady || 0;
      info.push(`Ready: ${ready}/${desired}`);
    }
  }

  // Ingress-specific extended info
  if (kind === 'Ingress') {
    if (resource.spec?.ingressClassName) {
      info.push(`Class: ${resource.spec.ingressClassName}`);
    }
    if (resource.status?.loadBalancer?.ingress) {
      const addresses = resource.status.loadBalancer.ingress
        .map((ing: K8sLoadBalancerIngress) => ing.ip || ing.hostname)
        .filter(Boolean)
        .join(', ');
      if (addresses) {
        info.push(`Address: ${addresses}`);
      }
    }
  }

  // PersistentVolume-specific extended info
  if (kind === 'PersistentVolume') {
    if (resource.spec?.capacity?.storage) {
      info.push(`Capacity: ${resource.spec.capacity.storage}`);
    }
    if (resource.spec?.accessModes) {
      info.push(`Access: ${resource.spec.accessModes.join(', ')}`);
    }
    if (resource.spec?.storageClassName) {
      info.push(`Class: ${resource.spec.storageClassName}`);
    }
  }

  // PersistentVolumeClaim-specific extended info
  if (kind === 'PersistentVolumeClaim') {
    if (resource.spec?.resources?.requests?.storage) {
      info.push(`Request: ${resource.spec.resources.requests.storage}`);
    }
    if (resource.spec?.volumeName) {
      info.push(`Volume: ${resource.spec.volumeName}`);
    }
    if (resource.spec?.storageClassName) {
      info.push(`Class: ${resource.spec.storageClassName}`);
    }
  }

  return info;
};

export function K8sResourceViewer({ cluster, defaultResourceType = 'pod' }: K8sResourceViewerProps) {
  const [selectedResourceType, setSelectedResourceType] = useState<string>(defaultResourceType);
  const [selectedNamespace, setSelectedNamespace] = useState<string>(cluster.default_namespace || 'default');
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedResource, setSelectedResource] = useState<K8sResource | null>(null);
  const [detailsDialogOpen, setDetailsDialogOpen] = useState(false);
  const [detailedView, setDetailedView] = useState(false);

  // Phase 5: Resource management dialogs
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [scaleDialogOpen, setScaleDialogOpen] = useState(false);
  const [logsDialogOpen, setLogsDialogOpen] = useState(false);
  const [describeDialogOpen, setDescribeDialogOpen] = useState(false);
  const [eventsDialogOpen, setEventsDialogOpen] = useState(false);
  const [metricsDialogOpen, setMetricsDialogOpen] = useState(false);
  const [rolloutDialogOpen, setRolloutDialogOpen] = useState(false);
  const [nodeOpsDialogOpen, setNodeOpsDialogOpen] = useState(false);

  const pollingEnabled = true;

  const { data: resourceTypes } = useK8sResourceTypes();
  const { data: namespacesResponse } = useClusterNamespaces(cluster.id);
  const { data: resourcesResponse, isLoading, refetch } = useClusterResources(
    cluster.id,
    selectedResourceType,
    { namespace: selectedNamespace },
    { pollingEnabled }
  );

  const resources = resourcesResponse?.resources || [];
  const namespaces = namespacesResponse?.namespaces || [];

  // Phase 5: Resource management mutations
  const createMutation = useCreateK8sResource();
  const updateMutation = useUpdateK8sResource();
  const deleteMutation = useDeleteK8sResource();
  const scaleMutation = useScaleDeployment();
  const restartMutation = useRestartPod();

  // Filter resources based on search query
  const filteredResources = resources.filter((resource) =>
    resource.metadata?.name?.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const handleViewDetails = (resource: K8sResource) => {
    setSelectedResource(resource);
    setDetailsDialogOpen(true);
  };

  // Phase 5: Resource management handlers
  const handleCreateResource = (resourceYaml: string, dryRun: boolean) => {
    createMutation.mutate(
      {
        clusterId: cluster.id,
        resourceType: selectedResourceType,
        resourceYaml,
        namespace: selectedNamespace,
        dryRun,
      },
      {
        onSuccess: () => {
          if (!dryRun) {
            setCreateDialogOpen(false);
          }
        },
      }
    );
  };

  const handleEditResource = (resourceYaml: string, dryRun: boolean) => {
    if (!selectedResource) return;

    updateMutation.mutate(
      {
        clusterId: cluster.id,
        resourceType: selectedResourceType,
        resourceName: selectedResource.metadata.name,
        resourceYaml,
        namespace: selectedResource.metadata.namespace || selectedNamespace,
        dryRun,
      },
      {
        onSuccess: () => {
          if (!dryRun) {
            setEditDialogOpen(false);
            setSelectedResource(null);
          }
        },
      }
    );
  };

  const handleDeleteResource = () => {
    if (!selectedResource) return;

    deleteMutation.mutate(
      {
        clusterId: cluster.id,
        resourceType: selectedResourceType,
        resourceName: selectedResource.metadata.name,
        namespace: selectedResource.metadata.namespace || selectedNamespace,
      },
      {
        onSuccess: () => {
          setDeleteDialogOpen(false);
          setSelectedResource(null);
        },
      }
    );
  };

  const handleScaleDeployment = (replicas: number) => {
    if (!selectedResource) return;

    scaleMutation.mutate(
      {
        clusterId: cluster.id,
        deploymentName: selectedResource.metadata.name,
        namespace: selectedResource.metadata.namespace || selectedNamespace,
        replicas,
      },
      {
        onSuccess: () => {
          setScaleDialogOpen(false);
          setSelectedResource(null);
        },
      }
    );
  };

  const handleRestartPod = (resource: K8sResource) => {
    restartMutation.mutate({
      clusterId: cluster.id,
      podName: resource.metadata.name,
      namespace: resource.metadata.namespace || selectedNamespace,
    });
  };

  const handleViewLogs = (resource: K8sResource) => {
    setSelectedResource(resource);
    setLogsDialogOpen(true);
  };

  const handleViewDescribe = (resource: K8sResource) => {
    setSelectedResource(resource);
    setDescribeDialogOpen(true);
  };

  const handleViewEvents = (resource: K8sResource) => {
    setSelectedResource(resource);
    setEventsDialogOpen(true);
  };

  const openEditDialog = (resource: K8sResource) => {
    setSelectedResource(resource);
    setEditDialogOpen(true);
  };

  const openDeleteDialog = (resource: K8sResource) => {
    setSelectedResource(resource);
    setDeleteDialogOpen(true);
  };

  const openScaleDialog = (resource: K8sResource) => {
    setSelectedResource(resource);
    setScaleDialogOpen(true);
  };

  const getResourceIcon = (kind?: string) => {
    switch (kind?.toLowerCase()) {
      case 'pod':
        return <Box className="h-4 w-4" />;
      case 'service':
        return <Network className="h-4 w-4" />;
      case 'deployment':
      case 'statefulset':
      case 'daemonset':
        return <Database className="h-4 w-4" />;
      case 'secret':
      case 'configmap':
        return <Shield className="h-4 w-4" />;
      default:
        return <Box className="h-4 w-4" />;
    }
  };

  const getStatusColor = (resource: K8sResource): 'default' | 'secondary' | 'destructive' | 'outline' | 'success' | 'warning' => {
    // Simple status detection based on resource status
    const status = resource.status;
    if (!status) return 'secondary';

    const phase = status.phase || status.state;
    if (phase === 'Running' || phase === 'Active' || phase === 'Ready') {
      return 'outline';
    }
    if (phase === 'Pending' || phase === 'Creating') {
      return 'secondary';
    }
    if (phase === 'Failed' || phase === 'Error') {
      return 'destructive';
    }
    return 'secondary';
  };

  const getResourceStatus = (resource: K8sResource) => {
    const status = resource.status;
    if (!status) return 'Unknown';

    // Try common status fields
    return status.phase || status.state || status.conditions?.[0]?.type || 'Unknown';
  };

  return (
    <div className="space-y-4">
      {/* Controls */}
      <Card className="p-4">
        <div className="space-y-4">
          <div className="flex flex-col md:flex-row gap-4">
            {/* Resource Type Selector */}
            <div className="flex-1">
              <label className="text-sm font-medium mb-1.5 block">Resource Type</label>
              <Select value={selectedResourceType} onValueChange={setSelectedResourceType}>
                <SelectTrigger>
                  <SelectValue placeholder="Select resource type" />
                </SelectTrigger>
                <SelectContent>
                  {resourceTypes?.map((type) => (
                    <SelectItem key={type.key} value={type.key}>
                      {type.display_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Namespace Selector - Made more prominent */}
            <div className="flex-1">
              <label className="text-sm font-medium mb-1.5 block">Namespace</label>
              <Select value={selectedNamespace} onValueChange={setSelectedNamespace}>
                <SelectTrigger className="font-semibold">
                  <SelectValue placeholder="Select namespace" />
                </SelectTrigger>
                <SelectContent>
                  {namespaces.map((ns) => (
                    <SelectItem key={ns.name} value={ns.name}>
                      {ns.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Search */}
            <div className="flex-1">
              <label className="text-sm font-medium mb-1.5 block">Search</label>
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder="Search resources..."
                  aria-label="Search Kubernetes resources"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="pl-9"
                />
              </div>
            </div>
          </div>

          <div className="flex gap-2 flex-wrap">
            {/* Detailed View Toggle */}
            <Button
              variant={detailedView ? 'default' : 'outline'}
              onClick={() => setDetailedView(!detailedView)}
              title={detailedView ? 'Switch to compact view' : 'Switch to detailed view'}
            >
              <Columns className="h-4 w-4 mr-2" />
              {detailedView ? 'Detailed' : 'Compact'}
            </Button>

            {/* Resource Metrics Button */}
            <Button variant="outline" onClick={() => {
              setMetricsDialogOpen(true);
            }}>
              <BarChart3 className="h-4 w-4 mr-2" />
              Metrics
            </Button>

            {/* Cluster Events Button */}
            <Button variant="outline" onClick={() => {
              setSelectedResource(null);
              setEventsDialogOpen(true);
            }}>
              <Activity className="h-4 w-4 mr-2" />
              Events
            </Button>

            {/* Refresh Button */}
            <Button variant="outline" onClick={() => refetch()} disabled={isLoading}>
              <RefreshCw className={`h-4 w-4 mr-2 ${isLoading ? 'animate-spin' : ''}`} />
              Refresh
            </Button>

            {/* Create Resource Button */}
            <Button onClick={() => setCreateDialogOpen(true)}>
              <Plus className="h-4 w-4 mr-2" />
              Create Resource
            </Button>
          </div>
        </div>
      </Card>

      {/* Resources List */}
      {isLoading && !resources.length ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      ) : filteredResources.length === 0 ? (
        <Card className="p-8 text-center">
          <div className="flex flex-col items-center justify-center space-y-2">
            <Box className="h-12 w-12 text-muted-foreground" />
            <div>
              <h4 className="font-semibold">No resources found</h4>
              <p className="text-sm text-muted-foreground mt-1">
                {searchQuery
                  ? `No resources matching "${searchQuery}"`
                  : `No ${selectedResourceType} resources in ${selectedNamespace}`}
              </p>
            </div>
          </div>
        </Card>
      ) : (
        <div className="space-y-2">
          <div className="text-sm text-muted-foreground">
            Found {filteredResources.length} {selectedResourceType}
            {filteredResources.length !== 1 ? 's' : ''} in {selectedNamespace}
          </div>
          <div className="grid gap-2">
            {filteredResources.map((resource, index) => (
              <Card key={index} className="p-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3 flex-1 min-w-0">
                    {getResourceIcon(resource.kind)}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <h4 className="font-semibold truncate">{resource.metadata.name}</h4>
                        <Badge variant={getStatusColor(resource)}>
                          {getResourceStatus(resource)}
                        </Badge>
                      </div>
                      <div className="flex items-center gap-4 mt-1 text-sm text-muted-foreground flex-wrap">
                        <span>{resource.kind}</span>
                        {resource.metadata.namespace && (
                          <span>ns: {resource.metadata.namespace}</span>
                        )}
                        {resource.metadata.creationTimestamp && (
                          <span>
                            Created {formatRelativeTime(resource.metadata.creationTimestamp)}
                          </span>
                        )}
                      </div>
                      {/* Extended info for detailed view */}
                      {detailedView && getExtendedInfo(resource).length > 0 && (
                        <div className="flex items-center gap-3 mt-2 text-xs text-muted-foreground flex-wrap">
                          {getExtendedInfo(resource).map((info, idx) => (
                            <Badge key={idx} variant="secondary" className="text-xs font-mono">
                              {info}
                            </Badge>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="flex gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleViewDetails(resource)}
                    >
                      <Eye className="h-4 w-4" />
                    </Button>

                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="sm">
                          <MoreVertical className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onClick={() => handleViewDescribe(resource)}>
                          <FileSearch className="mr-2 h-4 w-4" />
                          Describe
                        </DropdownMenuItem>

                        <DropdownMenuItem onClick={() => handleViewEvents(resource)}>
                          <Activity className="mr-2 h-4 w-4" />
                          View Events
                        </DropdownMenuItem>

                        <DropdownMenuSeparator />

                        <DropdownMenuItem onClick={() => openEditDialog(resource)}>
                          <Edit className="mr-2 h-4 w-4" />
                          Edit YAML
                        </DropdownMenuItem>

                        {resource.kind === 'Deployment' && (
                          <>
                            <DropdownMenuItem onClick={() => openScaleDialog(resource)}>
                              <Scale className="mr-2 h-4 w-4" />
                              Scale
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => {
                              setSelectedResource(resource);
                              setRolloutDialogOpen(true);
                            }}>
                              <GitBranch className="mr-2 h-4 w-4" />
                              Rollout Management
                            </DropdownMenuItem>
                          </>
                        )}

                        {(resource.kind === 'Pod' || selectedResourceType === 'pod') && (
                          <>
                            <DropdownMenuItem onClick={() => handleViewLogs(resource)}>
                              <FileText className="mr-2 h-4 w-4" />
                              View Logs {/* DEBUG: kind={resource.kind} */}
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => handleRestartPod(resource)}>
                              <RotateCw className="mr-2 h-4 w-4" />
                              Restart Pod
                            </DropdownMenuItem>
                          </>
                        )}

                        {(resource.kind === 'Node' || selectedResourceType === 'node') && (
                          <DropdownMenuItem onClick={() => {
                            setSelectedResource(resource);
                            setNodeOpsDialogOpen(true);
                          }}>
                            <Server className="mr-2 h-4 w-4" />
                            Node Operations
                          </DropdownMenuItem>
                        )}

                        <DropdownMenuSeparator />

                        <DropdownMenuItem
                          onClick={() => openDeleteDialog(resource)}
                          className="text-destructive focus:text-destructive"
                        >
                          <Trash2 className="mr-2 h-4 w-4" />
                          Delete
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                </div>
              </Card>
            ))}
          </div>
        </div>
      )}

      {/* Resource Details Dialog */}
      <Dialog open={detailsDialogOpen} onOpenChange={setDetailsDialogOpen}>
        <DialogContent className="max-w-4xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>
              {selectedResource?.kind}: {selectedResource?.metadata.name}
            </DialogTitle>
          </DialogHeader>
          {selectedResource && (
            <div className="space-y-4">
              {/* Metadata */}
              <div>
                <h4 className="font-semibold mb-2">Metadata</h4>
                <div className="bg-muted p-3 rounded text-sm space-y-1">
                  <div><strong>Name:</strong> {selectedResource.metadata.name}</div>
                  {selectedResource.metadata.namespace && (
                    <div><strong>Namespace:</strong> {selectedResource.metadata.namespace}</div>
                  )}
                  {selectedResource.metadata.uid && (
                    <div><strong>UID:</strong> <code className="text-xs">{selectedResource.metadata.uid}</code></div>
                  )}
                  {selectedResource.metadata.creationTimestamp && (
                    <div>
                      <strong>Created:</strong>{' '}
                      {new Date(selectedResource.metadata.creationTimestamp).toLocaleString()}
                    </div>
                  )}
                </div>
              </div>

              {/* Labels */}
              {selectedResource.metadata.labels && Object.keys(selectedResource.metadata.labels).length > 0 && (
                <div>
                  <h4 className="font-semibold mb-2">Labels</h4>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(selectedResource.metadata.labels).map(([key, value]) => (
                      <Badge key={key} variant="secondary">
                        {key}: {value}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              {/* Spec */}
              {selectedResource.spec && (
                <div>
                  <h4 className="font-semibold mb-2">Spec</h4>
                  <pre className="bg-muted p-3 rounded text-xs overflow-auto max-h-60">
                    {JSON.stringify(selectedResource.spec, null, 2)}
                  </pre>
                </div>
              )}

              {/* Status */}
              {selectedResource.status && (
                <div>
                  <h4 className="font-semibold mb-2">Status</h4>
                  <pre className="bg-muted p-3 rounded text-xs overflow-auto max-h-60">
                    {JSON.stringify(selectedResource.status, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Phase 5: Resource Management Dialogs */}
      <ResourceCreateDialog
        open={createDialogOpen}
        onOpenChange={setCreateDialogOpen}
        onSubmit={handleCreateResource}
        isLoading={createMutation.isPending}
        resourceType={selectedResourceType}
      />

      <ResourceEditDialog
        open={editDialogOpen}
        onOpenChange={setEditDialogOpen}
        resource={selectedResource}
        onSubmit={handleEditResource}
        isLoading={updateMutation.isPending}
      />

      <ResourceDeleteDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        resource={selectedResource}
        onConfirm={handleDeleteResource}
        isLoading={deleteMutation.isPending}
      />

      <ScaleDeploymentDialog
        open={scaleDialogOpen}
        onOpenChange={setScaleDialogOpen}
        deployment={selectedResource}
        onSubmit={handleScaleDeployment}
        isLoading={scaleMutation.isPending}
      />

      <PodLogsViewer
        open={logsDialogOpen}
        onOpenChange={setLogsDialogOpen}
        pod={selectedResource}
        clusterId={cluster.id}
        namespace={selectedResource?.metadata?.namespace || selectedNamespace}
      />

      <ResourceDescribeViewer
        open={describeDialogOpen}
        onOpenChange={setDescribeDialogOpen}
        resource={selectedResource}
        clusterId={cluster.id}
        namespace={selectedResource?.metadata?.namespace || selectedNamespace}
      />

      <ResourceEventsViewer
        open={eventsDialogOpen}
        onOpenChange={setEventsDialogOpen}
        clusterId={cluster.id}
        namespace={selectedResource?.metadata?.namespace || selectedNamespace}
        resourceType={selectedResource?.kind}
        resourceName={selectedResource?.metadata?.name}
      />

      <ResourceMetricsViewer
        open={metricsDialogOpen}
        onClose={() => setMetricsDialogOpen(false)}
        clusterId={cluster.id}
        namespace={selectedNamespace}
      />

      {selectedResource && (
        <>
          <DeploymentRolloutManager
            open={rolloutDialogOpen}
            onOpenChange={setRolloutDialogOpen}
            clusterId={cluster.id}
            deploymentName={selectedResource.metadata?.name || ''}
            namespace={selectedResource.metadata?.namespace || selectedNamespace}
          />

          <NodeOperationsDialog
            open={nodeOpsDialogOpen}
            onOpenChange={setNodeOpsDialogOpen}
            clusterId={cluster.id}
            nodeName={selectedResource.metadata?.name || ''}
            nodeStatus={selectedResource}
          />
        </>
      )}
    </div>
  );
}
