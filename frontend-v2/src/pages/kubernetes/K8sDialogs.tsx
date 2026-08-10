/* eslint-disable react-refresh/only-export-components */
/**
 * Kubernetes Dialog Orchestrator — Manages all lazy-loaded dialogs
 * Extracted from KubernetesV2.tsx (FE-002)
 *
 * Wraps all dialogs in a single Suspense boundary and exposes
 * an imperative API for the parent page to trigger actions.
 */

import { useState, useCallback, Suspense, lazy } from 'react';
import { notify } from '@/lib/notify';
// IMP-005: Use mutation hooks instead of raw api.* calls
import { useDeleteK8sResource, useUpdateK8sResource, useScaleDeployment, useCreateK8sResource } from '@/hooks/useK8s';
import type { K8sResource } from '@/types';
import type { ResourceAction } from './K8sResourceTable';

// PERFORMANCE OPTIMIZATION (ADR-019): Lazy load heavy dialog components
const RolloutManagementDialog = lazy(() =>
  import('@/components/k8s/RolloutManagementDialog').then((m) => ({
    default: m.RolloutManagementDialog,
  }))
);
const NodeOperationsDialog = lazy(() =>
  import('@/components/k8s/NodeOperationsDialog').then((m) => ({
    default: m.NodeOperationsDialog,
  }))
);
const ResourceDescribeViewer = lazy(() =>
  import('@/components/k8s/ResourceDescribeViewer').then((m) => ({
    default: m.ResourceDescribeViewer,
  }))
);
const PodLogsViewer = lazy(() =>
  import('@/components/k8s/PodLogsViewer').then((m) => ({
    default: m.PodLogsViewer,
  }))
);
const PodExecTerminal = lazy(() =>
  import('@/components/k8s/PodExecTerminal').then((m) => ({
    default: m.PodExecTerminal,
  }))
);
const ResourceDeleteDialog = lazy(() =>
  import('@/components/k8s/ResourceDeleteDialog').then((m) => ({
    default: m.ResourceDeleteDialog,
  }))
);
const ResourceEditDialog = lazy(() =>
  import('@/components/k8s/ResourceEditDialog').then((m) => ({
    default: m.ResourceEditDialog,
  }))
);
const ScaleDeploymentDialog = lazy(() =>
  import('@/components/k8s/ScaleDeploymentDialog').then((m) => ({
    default: m.ScaleDeploymentDialog,
  }))
);
const ResourceEventsViewer = lazy(() =>
  import('@/components/k8s/ResourceEventsViewer').then((m) => ({
    default: m.ResourceEventsViewer,
  }))
);
const ResourceMetricsViewer = lazy(() =>
  import('@/components/k8s/ResourceMetricsViewer').then((m) => ({
    default: m.ResourceMetricsViewer,
  }))
);
const ResourceCreateDialog = lazy(() =>
  import('@/components/k8s/ResourceCreateDialog').then((m) => ({
    default: m.ResourceCreateDialog,
  }))
);
const ClusterScanResults = lazy(() =>
  import('@/components/k8s/ClusterScanResults').then((m) => ({
    default: m.ClusterScanResults,
  }))
);

// ---------------------------------------------------------------------------
// Dialog State Hook
// ---------------------------------------------------------------------------

interface DialogState {
  rolloutDialogOpen: boolean;
  nodeOpsDialogOpen: boolean;
  describeDialogOpen: boolean;
  logsDialogOpen: boolean;
  execTerminalOpen: boolean;
  deleteDialogOpen: boolean;
  editDialogOpen: boolean;
  scaleDialogOpen: boolean;
  eventsDialogOpen: boolean;
  metricsDialogOpen: boolean;
  createDialogOpen: boolean;
  selectedDeployment: K8sResource | null;
  selectedNode: K8sResource | null;
  resourceToDescribe: K8sResource | null;
  resourceForLogs: K8sResource | null;
  resourceToDelete: K8sResource | null;
  resourceToEdit: K8sResource | null;
  resourceToScale: K8sResource | null;
}

export function useK8sDialogs() {
  const [state, setState] = useState<DialogState>({
    rolloutDialogOpen: false,
    nodeOpsDialogOpen: false,
    describeDialogOpen: false,
    logsDialogOpen: false,
    execTerminalOpen: false,
    deleteDialogOpen: false,
    editDialogOpen: false,
    scaleDialogOpen: false,
    eventsDialogOpen: false,
    metricsDialogOpen: false,
    createDialogOpen: false,
    selectedDeployment: null,
    selectedNode: null,
    resourceToDescribe: null,
    resourceForLogs: null,
    resourceToDelete: null,
    resourceToEdit: null,
    resourceToScale: null,
  });

  const openCreateDialog = useCallback(
    () => setState((s) => ({ ...s, createDialogOpen: true })),
    []
  );

  const handleAction = useCallback((action: ResourceAction) => {
    const { type, resource } = action;
    switch (type) {
      case 'rollout':
        setState((s) => ({
          ...s,
          selectedDeployment: resource,
          rolloutDialogOpen: true,
        }));
        break;
      case 'nodeOps':
        setState((s) => ({
          ...s,
          selectedNode: resource,
          nodeOpsDialogOpen: true,
        }));
        break;
      case 'describe':
        setState((s) => ({
          ...s,
          resourceToDescribe: resource,
          describeDialogOpen: true,
        }));
        break;
      case 'edit':
        setState((s) => ({
          ...s,
          resourceToEdit: resource,
          editDialogOpen: true,
        }));
        break;
      case 'terminal':
        setState((s) => ({
          ...s,
          resourceForLogs: resource,
          execTerminalOpen: true,
        }));
        break;
      case 'logs':
        setState((s) => ({
          ...s,
          resourceForLogs: resource,
          logsDialogOpen: true,
        }));
        break;
      case 'restart':
        // Restart is handled inline — the parent will call restartPod
        break;
      case 'scale':
        setState((s) => ({
          ...s,
          resourceToScale: resource,
          scaleDialogOpen: true,
        }));
        break;
      case 'delete':
        setState((s) => ({
          ...s,
          resourceToDelete: resource,
          deleteDialogOpen: true,
        }));
        break;
    }
  }, []);

  const setDialogOpen = useCallback(
    (key: keyof DialogState, value: boolean | K8sResource | null) =>
      setState((s) => ({ ...s, [key]: value })),
    []
  );

  return { state, handleAction, openCreateDialog, setDialogOpen };
}

// ---------------------------------------------------------------------------
// Dialog Renderer Component
// ---------------------------------------------------------------------------

interface K8sDialogsProps {
  clusterId: number | null;
  selectedResourceType: string;
  selectedNamespace: string;
  errorContext?: {
    detectedPlatformProfile?: import('@/types/platform').PlatformProfile;
    platformCapabilities?: import('@/types/platform').PlatformCapabilities | null;
    platformConstraints?: import('@/types/platform').PlatformConstraints | null;
  };
  dialogState: DialogState;
  setDialogOpen: (key: keyof DialogState, value: boolean | K8sResource | null) => void;
  onResourceDeleted?: () => void;
}

export function K8sDialogs({
  clusterId,
  selectedResourceType,
  selectedNamespace,
  errorContext,
  dialogState: d,
  setDialogOpen,
  onResourceDeleted,
}: K8sDialogsProps) {
  // IMP-005: Use mutation hooks instead of raw api.* calls
  // Hooks handle cache invalidation and error notifications automatically
  const deleteMutation = useDeleteK8sResource(errorContext);
  const updateMutation = useUpdateK8sResource(errorContext);
  const scaleMutation = useScaleDeployment();
  const createMutation = useCreateK8sResource(errorContext);

  if (!clusterId) return null;

  return (
    <Suspense fallback={null}>
      {/* Rollout Management Dialog */}
      {d.selectedDeployment && (
        <RolloutManagementDialog
          open={d.rolloutDialogOpen}
          onOpenChange={(open: boolean) => setDialogOpen('rolloutDialogOpen', open)}
          clusterId={clusterId}
          namespace={d.selectedDeployment.metadata?.namespace || 'default'}
          deploymentName={d.selectedDeployment.metadata?.name || ''}
        />
      )}

      {/* Node Operations Dialog */}
      {d.selectedNode && (
        <NodeOperationsDialog
          open={d.nodeOpsDialogOpen}
          onOpenChange={(open: boolean) => setDialogOpen('nodeOpsDialogOpen', open)}
          clusterId={clusterId}
          nodeName={d.selectedNode.metadata?.name || ''}
          nodeStatus={d.selectedNode}
        />
      )}

      {/* Describe Dialog */}
      {d.resourceToDescribe && (
        <ResourceDescribeViewer
          open={d.describeDialogOpen}
          onOpenChange={(open: boolean) => setDialogOpen('describeDialogOpen', open)}
          resource={d.resourceToDescribe}
          clusterId={clusterId}
          namespace={d.resourceToDescribe.metadata?.namespace || 'default'}
        />
      )}

      {/* Logs Dialog (Pods only) */}
      {d.resourceForLogs && (
        <PodLogsViewer
          open={d.logsDialogOpen}
          onOpenChange={(open: boolean) => setDialogOpen('logsDialogOpen', open)}
          pod={d.resourceForLogs}
          clusterId={clusterId}
          namespace={d.resourceForLogs.metadata.namespace || 'default'}
        />
      )}

      {/* Exec Terminal Dialog (Pods only) */}
      {d.resourceForLogs && (
        <PodExecTerminal
          open={d.execTerminalOpen}
          onOpenChange={(open: boolean) => setDialogOpen('execTerminalOpen', open)}
          pod={d.resourceForLogs}
          clusterId={clusterId}
          namespace={d.resourceForLogs.metadata.namespace || 'default'}
        />
      )}

      {/* Delete Dialog */}
      <ResourceDeleteDialog
        open={d.deleteDialogOpen}
        onOpenChange={(open: boolean) => setDialogOpen('deleteDialogOpen', open)}
        resource={d.resourceToDelete}
        onConfirm={async () => {
          if (d.resourceToDelete && clusterId) {
            try {
              await deleteMutation.mutateAsync({
                clusterId,
                resourceType: d.resourceToDelete.kind.toLowerCase(),
                resourceName: d.resourceToDelete.metadata.name,
                namespace: d.resourceToDelete.metadata.namespace,
              });
              // Hook handles success notification and cache invalidation
              setDialogOpen('deleteDialogOpen', false);
              setDialogOpen('resourceToDelete', null);
              onResourceDeleted?.();
            } catch {
              // Hook handles error notification via onError
            }
          }
        }}
      />

      {/* Edit YAML Dialog */}
      {d.resourceToEdit && (
        <ResourceEditDialog
          open={d.editDialogOpen}
          onOpenChange={(open: boolean) => setDialogOpen('editDialogOpen', open)}
          resource={d.resourceToEdit}
          onSubmit={async (resourceYaml: string, dryRun: boolean) => {
            try {
              await updateMutation.mutateAsync({
                clusterId,
                resourceType: d.resourceToEdit!.kind.toLowerCase(),
                resourceName: d.resourceToEdit!.metadata.name,
                resourceYaml,
                namespace: d.resourceToEdit!.metadata.namespace,
                dryRun,
              });
              if (dryRun) {
                notify.success('Dry run successful - no changes applied', undefined, { category: 'cluster' });
              } else {
                // Hook (useUpdateK8sResource) posts success bell + invalidates cache (hooks/k8s/useResources.ts)
                setDialogOpen('editDialogOpen', false);
                setDialogOpen('resourceToEdit', null);
              }
            } catch {
              // Hook handles error notification via onError
            }
          }}
        />
      )}

      {/* Scale Deployment Dialog */}
      {d.resourceToScale && (
        <ScaleDeploymentDialog
          open={d.scaleDialogOpen}
          onOpenChange={(open: boolean) => setDialogOpen('scaleDialogOpen', open)}
          deployment={d.resourceToScale}
          onSubmit={async (replicas: number) => {
            try {
              await scaleMutation.mutateAsync({
                clusterId,
                deploymentName: d.resourceToScale!.metadata.name,
                namespace: d.resourceToScale!.metadata.namespace || 'default',
                replicas,
              });
              // Hook handles success notification and cache invalidation
              setDialogOpen('scaleDialogOpen', false);
              setDialogOpen('resourceToScale', null);
            } catch {
              // Hook handles error notification via onError
            }
          }}
        />
      )}

      {/* Events Dialog */}
      <ResourceEventsViewer
        open={d.eventsDialogOpen}
        onOpenChange={(open: boolean) => setDialogOpen('eventsDialogOpen', open)}
        clusterId={clusterId}
        namespace={selectedNamespace}
      />

      {/* Metrics Dialog */}
      <ResourceMetricsViewer
        clusterId={clusterId}
        namespace={selectedNamespace}
        open={d.metricsDialogOpen}
        onClose={() => setDialogOpen('metricsDialogOpen', false)}
      />

      {/* Create Resource Dialog */}
      <ResourceCreateDialog
        open={d.createDialogOpen}
        onOpenChange={(open: boolean) => setDialogOpen('createDialogOpen', open)}
        resourceType={selectedResourceType}
        namespace={selectedNamespace}
        onSubmit={async (resourceYaml: string, dryRun: boolean) => {
          try {
            await createMutation.mutateAsync({
              clusterId: clusterId!,
              resourceType: selectedResourceType,
              resourceYaml,
              namespace: selectedNamespace,
              dryRun,
            });
            if (dryRun) {
              notify.success('Dry run successful - no changes applied', undefined, { category: 'cluster' });
            } else {
              // Hook (useCreateK8sResource) posts success bell + invalidates cache (hooks/k8s/useResources.ts)
              setDialogOpen('createDialogOpen', false);
            }
          } catch {
            // Hook handles error notification via onError
          }
        }}
      />
    </Suspense>
  );
}

// ---------------------------------------------------------------------------
// Cluster Scan View (also lazy-loaded)
// ---------------------------------------------------------------------------

interface K8sClusterScanViewProps {
  clusterId: number;
  clusterName?: string;
}

export function K8sClusterScanView({
  clusterId,
  clusterName,
}: K8sClusterScanViewProps) {
  return (
    <div className="flex-1 overflow-auto p-6">
      <Suspense
        fallback={
          <div className="flex items-center justify-center py-20">
            <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          </div>
        }
      >
        <ClusterScanResults clusterId={clusterId} clusterName={clusterName} />
      </Suspense>
    </div>
  );
}

// ---------------------------------------------------------------------------
// DPU Infrastructure View (also lazy-loaded)
// ---------------------------------------------------------------------------

const DPFInfrastructurePanel = lazy(() =>
  import('@/components/k8s/DPFInfrastructurePanel').then((m) => ({ default: m.DPFInfrastructurePanel }))
);

interface K8sDpfInfraViewProps {
  clusterId: number;
}

export function K8sDpfInfraView({ clusterId }: K8sDpfInfraViewProps) {
  return (
    <div className="flex-1 overflow-auto p-6">
      <Suspense
        fallback={
          <div className="flex items-center justify-center py-20">
            <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          </div>
        }
      >
        <DPFInfrastructurePanel clusterId={clusterId} />
      </Suspense>
    </div>
  );
}
